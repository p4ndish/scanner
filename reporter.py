import json
import os
import time
from pathlib import Path
from typing import Any


class Reporter:
    def __init__(self, output_dir: str = "results"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.matches: list[dict] = []
        self.stats: dict[str, Any] = {
            "scan_started": None,
            "scan_finished": None,
            "hosts_scanned": 0,
            "ports_probed": 0,
            "candidates_found": 0,
            "matches_found": 0,
            "phases": {},
        }

    def start(self):
        self.stats["scan_started"] = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
        )

    def finish(self):
        self.stats["scan_finished"] = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
        )
        if self.stats["scan_started"]:
            start = time.mktime(
                time.strptime(self.stats["scan_started"], "%Y-%m-%dT%H:%M:%SZ")
            )
            # adjust for local timezone
            import calendar
            start = calendar.timegm(time.strptime(self.stats["scan_started"], "%Y-%m-%dT%H:%M:%SZ"))
            end = calendar.timegm(time.strptime(self.stats["scan_finished"], "%Y-%m-%dT%H:%M:%SZ"))
            self.stats["scan_duration_seconds"] = end - start

    def add_phase_stat(self, phase: str, data: dict = None, **kwargs):
        merged = {}
        if data:
            merged.update(data)
        merged.update(kwargs)
        self.stats["phases"][phase] = merged

    def add_match(self, entry: dict):
        self.matches.append(entry)
        self.stats["matches_found"] = len(self.matches)

    def add_candidates(self, count: int):
        self.stats["candidates_found"] = count

    def save_matches(self, filename: str = "results.json") -> str:
        path = self.output_dir / filename
        output = {
            "$meta": {
                "tool": "opencode-scanner",
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
            "stats": self.stats,
            "matches": self.matches,
        }
        with open(path, "w") as f:
            json.dump(output, f, indent=2)
        return str(path)

    def save_raw(self, data: Any, filename: str):
        path = self.output_dir / filename
        with open(path, "w") as f:
            if isinstance(data, (dict, list)):
                json.dump(data, f, indent=2)
            else:
                f.write(str(data))
        return str(path)
