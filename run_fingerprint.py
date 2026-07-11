import asyncio, json, sys, glob
sys.path.insert(0, '/root/opencode-scanner')
from fingerprint import FingerprintEngine

# Collect all 4096 port hits
pairs = set()
for f in sorted(glob.glob('/root/opencode-scanner/results/scans/masscan_batch_*.json')):
    with open(f) as fh:
        for line in fh:
            line = line.strip()
            if not line: continue
            try:
                rec = json.loads(line)
                for p in rec.get('ports', []):
                    if p['port'] == 4096:
                        pairs.add((rec['ip'], 4096))
            except: pass

print(f"Candidates (port 4096 only): {len(pairs)}")
engine = FingerprintEngine(concurrency=200, timeout=3.0, score_threshold=1)

async def main():
    matches = await engine.probe_candidates(list(pairs))
    print(f"\nMatches found: {len(matches)}")
    for m in matches:
        print(f"\n  {m['ip']}:{m['port']}  score={m['score']}  confidence={m['confidence']}%")
        print(f"    Methods: {m['methods_hit']}")
        for k, v in m.get('details', {}).items():
            if isinstance(v, dict):
                print(f"    {k}: {json.dumps(v, indent=6)[:200]}")
            else:
                print(f"    {k}: {v}")

if __name__ == '__main__':
    asyncio.run(main())
