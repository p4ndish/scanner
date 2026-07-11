#!/usr/bin/env python3
"""
verify_prompts.py — Send a test prompt to every discovered opencode instance
and check if the AI responds. Uses urllib + ThreadPoolExecutor for reliability.

Usage:
  python3 verify_prompts.py [--concurrency 15] [--timeout-per 60] [--results results/results.json]
"""

import argparse
import json
import ssl
import sys
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

TEST_PROMPT = "Reply with EXACTLY the word 'alive' and nothing else."


def load_targets(results_path: str) -> list[dict]:
    with open(results_path) as f:
        data = json.load(f)
    matches = data.get("matches", data)
    targets = []
    for m in matches:
        ip = m["ip"]
        port = m["port"]
        version = m.get("details", {}).get("health", {}).get("version", "?")
        targets.append({
            "ip": ip, "port": port, "version": str(version),
            "score": m.get("score", 0),
            "provider": m.get("provider", "unknown"),
            "region": m.get("region", "?"),
        })
    return sorted(targets, key=lambda t: _version_key(t["version"]))


def _version_key(v: str) -> tuple:
    try:
        return tuple(int(x) for x in str(v).split("."))
    except (ValueError, AttributeError):
        return (0,)


def _req(method: str, url: str, body: dict = None, timeout: int = 15):
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data,
        headers={"Content-Type": "application/json", "User-Agent": "opencode-scanner/1.0"},
        method=method)
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        resp = urllib.request.urlopen(req, context=ctx, timeout=timeout)
        ct = resp.headers.get("Content-Type", "")
        if "json" in ct:
            return json.loads(resp.read())
        return {"__html": True, "__body": resp.read().decode("utf-8", errors="replace")[:300]}
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return {"__status": e.code, "__body": e.read().decode("utf-8", errors="replace")[:300]}
        try:
            return json.loads(e.read())
        except:
            return {"__http_error": e.code}
    except Exception as e:
        return {"__error": str(e)}


def _extract_response(data, _depth: int = 0) -> str:
    """Extract assistant text content from various opencode response formats."""
    if _depth > 3 or not isinstance(data, dict):
        return ""

    # Check info field (inline prompt response on v1.17)
    info = data.get("info", {})
    if isinstance(info, dict) and info.get("role") == "assistant":
        return _extract_text(info)

    # Check for messages in items
    items = data.get("items", [])
    if items:
        for msg in items:
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                return _extract_text(msg)

    # Check if this is a message itself
    if data.get("role") == "assistant":
        return _extract_text(data)

    # Check list of messages
    if isinstance(data, list):
        return ""

    # Check nested data structures (guard against circular refs)
    seen = set()
    for key in ("data", "message", "result"):
        nested = data.get(key, {})
        if isinstance(nested, dict) and id(nested) not in seen:
            seen.add(id(nested))
            content = _extract_response(nested, _depth + 1)
            if content:
                return content

    return ""


def _extract_text(msg: dict) -> str:
    """Extract text content from an assistant message."""
    content = msg.get("content", "")
    if content and isinstance(content, str) and content.strip():
        return content
    parts = msg.get("parts", [])
    texts = []
    for p in parts:
        if isinstance(p, dict) and p.get("type") == "text":
            t = p.get("text", "")
            if t.strip():
                texts.append(t)
        elif isinstance(p, str):
            texts.append(p)
    if texts:
        return "\n".join(texts)
    return ""


def probe_instance(target: dict, timeout_per: int = 60) -> dict:
    ip = target["ip"]
    port = target["port"]
    version = str(target["version"])
    base = f"http://{ip}:{port}"
    start = time.time()

    result = {
        "ip": ip, "port": port, "version": version,
        "provider": target["provider"], "region": target["region"],
        "status": "unknown", "response": None, "error": None, "elapsed_ms": 0,
    }

    # Step 1: Create session
    r1 = _req("POST", f"{base}/session", {"name": "opencode-probe"})
    if isinstance(r1, dict) and r1.get("__error"):
        result["status"] = "unreachable"
        result["error"] = r1["__error"][:200]
        result["elapsed_ms"] = int((time.time() - start) * 1000)
        return result
    if isinstance(r1, dict) and r1.get("__status") in (401, 403):
        result["status"] = "auth_protected"
        result["error"] = f"HTTP {r1['__status']}"
        result["elapsed_ms"] = int((time.time() - start) * 1000)
        return result
    if isinstance(r1, dict) and r1.get("__html"):
        result["status"] = "web_ui_only"
        result["error"] = "POST /session returned web UI (behind proxy)"
        result["elapsed_ms"] = int((time.time() - start) * 1000)
        return result
    if isinstance(r1, dict) and r1.get("__http_error"):
        result["status"] = f"http_{r1['__http_error']}"
        result["error"] = f"HTTP {r1['__http_error']}"
        result["elapsed_ms"] = int((time.time() - start) * 1000)
        return result

    session_id = r1.get("id", "") if isinstance(r1, dict) else ""
    if not session_id:
        result["status"] = "api_error"
        result["error"] = f"no session id: {json.dumps(r1)[:200]}"
        result["elapsed_ms"] = int((time.time() - start) * 1000)
        return result

    # Step 2: Send prompt
    vkey = _version_key(version)
    if vkey >= (1, 15):
        prompt_body = {
            "prompt": {"text": TEST_PROMPT},
            "delivery": "steer",
            "model": {"providerID": "opencode", "modelID": "deepseek-v4-flash-free"},
        }
        r2 = _req("POST", f"{base}/api/session/{session_id}/prompt", prompt_body, timeout=60)
    elif vkey >= (1, 0):
        prompt_body = {
            "prompt": {"text": TEST_PROMPT},
            "delivery": "immediate",
            "model": {"providerID": "opencode", "modelID": "deepseek-v4-flash-free"},
        }
        r2 = _req("POST", f"{base}/api/session/{session_id}/prompt", prompt_body, timeout=60)
        if isinstance(r2, dict) and r2.get("_tag") == "ServiceUnavailableError":
            msg_body = {
                "parts": [{"type": "text", "text": TEST_PROMPT}],
                "model": {"providerID": "opencode", "modelID": "deepseek-v4-flash-free"},
            }
            r2 = _req("POST", f"{base}/session/{session_id}/message", msg_body)
    else:
        msg_body = {
            "parts": [{"type": "text", "text": TEST_PROMPT}],
            "model": {"providerID": "opencode", "modelID": "deepseek-v4-flash-free"},
        }
        r2 = _req("POST", f"{base}/session/{session_id}/message", msg_body)

    # Check if AI response came inline
    ai_content = _extract_response(r2)
    if ai_content:
        result["status"] = "ai_responded"
        result["response"] = ai_content[:500]
        result["elapsed_ms"] = int((time.time() - start) * 1000)
        return result

    # Check if response is an admission (message accepted but async)
    admitted = False
    if isinstance(r2, dict):
        msg_id = r2.get("id") or r2.get("data", {}).get("id", "")
        if msg_id:
            result["message_id"] = msg_id
            result["status"] = "prompt_accepted"
            admitted = True

    if not admitted:
        if isinstance(r2, dict):
            if r2.get("_tag", "").startswith("Invalid") or r2.get("name", "").startswith("Bad"):
                result["status"] = "prompt_rejected"
                result["error"] = json.dumps(r2)[:200]
            elif r2.get("__status"):
                result["status"] = f"prompt_http_{r2['__status']}"
                result["error"] = str(r2.get("__body", ""))[:200]
            elif r2.get("__html"):
                result["status"] = "prompt_not_supported"
                result["error"] = "endpoint returns web UI"
            elif r2.get("__http_error"):
                result["status"] = f"prompt_http_{r2['__http_error']}"
                result["error"] = f"HTTP {r2['__http_error']}"
            elif r2.get("__error"):
                result["status"] = "prompt_error"
                result["error"] = r2["__error"][:200]
            else:
                result["status"] = f"prompt_{r2.get('_tag', 'unknown')}"
                result["error"] = json.dumps(r2)[:300]
        else:
            result["status"] = "prompt_error"
            result["error"] = f"unexpected: {type(r2).__name__}"

    # Step 3: Poll for AI response
    if admitted:
        deadline = time.time() + timeout_per
        while time.time() < deadline:
            time.sleep(3)
            r3 = _req("GET", f"{base}/session/{session_id}/message", timeout=10)

            ai_content = _extract_response(r3)
            if ai_content:
                result["status"] = "ai_responded"
                result["response"] = ai_content[:500]
                result["elapsed_ms"] = int((time.time() - start) * 1000)
                return result

        result["status"] = "no_response"
        result["error"] = f"no assistant message after {timeout_per}s"

    result["elapsed_ms"] = int((time.time() - start) * 1000)
    return result


def main():
    parser = argparse.ArgumentParser(description="Verify opencode instances by sending a test prompt")
    parser.add_argument("--results", "-r", default="results/results.json")
    parser.add_argument("--concurrency", "-c", type=int, default=15)
    parser.add_argument("--timeout-per", "-t", type=int, default=60)
    parser.add_argument("--output", "-o", default="results/verify.json")
    parser.add_argument("--limit", "-n", type=int, default=None)

    args = parser.parse_args()

    targets = load_targets(args.results)
    if args.limit:
        targets = targets[:args.limit]

    print(f"Loaded {len(targets)} instances from {args.results}")
    print(f"Versions present: {sorted(set(t['version'] for t in targets), key=_version_key)}")
    print(f"Probing with {args.concurrency} concurrent workers, {args.timeout_per}s per instance...")
    print()

    results = []

    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = {executor.submit(probe_instance, t, args.timeout_per): t for t in targets}
        completed = 0
        for future in as_completed(futures):
            completed += 1
            result = future.result()
            results.append(result)

            status_symbol = {
                "ai_responded": "✓", "prompt_accepted": "○", "no_response": "✗",
                "auth_protected": "🔒", "prompt_rejected": "✗", "prompt_not_supported": "—",
                "unreachable": "✗", "web_ui_only": "🌐",
            }
            symbol = status_symbol.get(result["status"], "?")
            resp = result.get("response", "")
            preview = f" → \"{resp[:60]}\"" if resp else ""

            print(f"  [{completed:>3}/{len(targets)}] {symbol} {result['ip']:>18s}:{result['port']:<5d} "
                  f"v{result['version']:<10s} {result['status']:<22s} "
                  f"({result['provider']}){preview}")

    # Summary
    print()
    print("=" * 70)
    print("  VERIFICATION SUMMARY")
    print("=" * 70)
    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    for status in ["ai_responded", "prompt_accepted", "no_response", "auth_protected",
                     "web_ui_only", "prompt_not_supported", "prompt_rejected", "unreachable"]:
        if status in counts:
            print(f"  {status:<25s} {counts[status]:>4d}")
    print("=" * 70)
    print(f"  Total: {len(results)}")

    # Show AI responses
    responders = [r for r in results if r["status"] == "ai_responded"]
    if responders:
        print()
        print("  === AI RESPONSES ===")
        for r in responders:
            print(f"  {r['ip']}:{r['port']} (v{r['version']}): {r['response'][:120]}")

    # Show prompt_accepted
    accepted = [r for r in results if r["status"] == "prompt_accepted"]
    if accepted:
        print()
        print(f"  === PROMPT ACCEPTED ({len(accepted)}) — no response after {args.timeout_per}s ===")
        for r in accepted[:10]:
            print(f"  {r['ip']}:{r['port']} v{r['version']} ({r['provider']})")

    output = {
        "$meta": {
            "tool": "opencode-scanner verify_prompts",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "params": {"concurrency": args.concurrency, "timeout_per": args.timeout_per,
                       "prompt": TEST_PROMPT, "model": "deepseek-v4-flash-free"},
        },
        "summary": {"total": len(results), "by_status": counts},
        "results": results,
    }
    with open(args.output, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to: {args.output}")


if __name__ == "__main__":
    main()
