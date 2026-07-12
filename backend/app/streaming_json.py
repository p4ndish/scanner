"""Stream match objects from large JSON files without loading them into memory."""
import json
import mmap
import os


def stream_matches_from_file(filepath: str):
    """
    Yield match dicts one at a time from a potentially huge results.json.
    Uses mmap so the OS handles paging — Python heap stays small.
    """
    file_size = os.path.getsize(filepath)
    if file_size == 0:
        return

    decoder = json.JSONDecoder()

    with open(filepath, "r+b") as f:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            # Find the actual "matches" array (the last occurrence in case stats has one too)
            matches_key = b'"matches":'
            idx = mm.rfind(matches_key)
            if idx < 0:
                raise ValueError("No 'matches' key found in JSON")

            bracket = mm.find(b"[", idx)
            if bracket < 0:
                raise ValueError("No opening bracket for matches array")

            pos = bracket + 1
            file_len = len(mm)

            while pos < file_len:
                # Skip whitespace / commas
                while pos < file_len and chr(mm[pos]) in " \t\n,":
                    pos += 1
                if pos >= file_len:
                    break
                if chr(mm[pos]) == "]":
                    break

                # Try to decode with progressively larger windows
                obj = None
                end_idx = 0
                for window in (65536, 262144, 1048576, 4194304):
                    try:
                        chunk = mm[pos : pos + window].decode("utf-8")
                        obj, end_idx = decoder.raw_decode(chunk)
                        break
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        if window >= 4194304:
                            raise ValueError(f"Cannot decode match object near byte {pos}")
                        continue

                if obj is None:
                    break

                if isinstance(obj, dict) and "ip" in obj:
                    yield obj

                pos += end_idx
        finally:
            mm.close()
