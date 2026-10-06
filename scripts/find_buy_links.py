#!/usr/bin/env python3
"""find_buy_links.py - fill a legal buy link for every track dj-search couldn't
download for free (uses Apple's public iTunes Search API - no key, no account).

For each candidate with `download_status` buy_only/missing and no `download_url`,
queries the store (default country: ng), fuzzy-matches the result against
artist+title using the same normalisation as scan_library.py, and writes the
exact store URL into `download_url`. Unmatched tracks keep `error` explaining it
and are listed in the final report - nothing is ever silently dropped.

  python scripts/find_buy_links.py --candidates "<save>/_dj-search/candidates.json"
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scan_library import normalize, score, _ratio  # noqa: E402

API = "https://itunes.apple.com/search"
HEADERS = {"User-Agent": "dj-search/1.0 (+https://github.com/topherprofiles-creator/dj-search)"}
MATCH_THRESHOLD = 0.75


def query(term: str, country: str):
    url = API + "?" + urllib.parse.urlencode({
        "term": term, "country": country, "media": "music", "entity": "song", "limit": 8})
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8", "replace")).get("results", [])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Fill store buy-links for every dj-search track with no legal free download.")
    ap.add_argument("--candidates", required=True, help="candidates.json to update in place")
    ap.add_argument("--country", default="ng", help="storefront country code (default ng)")
    ap.add_argument("--min-score", type=float, default=MATCH_THRESHOLD,
                    help=f"fuzzy match acceptance score (default {MATCH_THRESHOLD})")
    args = ap.parse_args(argv)

    path = Path(args.candidates)
    data = json.loads(path.read_text(encoding="utf-8"))

    filled = unmatched = skipped = 0
    miss_lines = []
    for c in data:
        if not isinstance(c, dict):
            continue
        status = (c.get("download_status") or "").lower()
        if status not in ("buy_only", "missing") or (c.get("download_url") or "").strip():
            skipped += 1
            continue
        artist = (c.get("artist") or "").strip()
        title = (c.get("title") or "").strip()
        cand = {"na": normalize(artist), "nt": normalize(title),
                "nall": normalize(f"{artist} {title}")}
        best, best_rec, best_sc = None, None, 0.0
        c["error"] = ""
        rows = []
        for _attempt in range(2):  # public API: one retry if the call comes back empty
            try:
                rows = query(f"{artist} {title}", args.country)
                if rows:
                    break
            except Exception as exc:
                c["error"] = f"store lookup failed: {exc}"[:160]
            time.sleep(3.0)
        for r in rows:
            rec = {
                "artist": r.get("artistName", ""),
                "title": r.get("trackName", ""),
                "na": normalize(r.get("artistName", "")),
                "nt": normalize(r.get("trackName", "")),
                "nall": normalize(f'{r.get("artistName", "")} {r.get("trackName", "")}'),
            }
            s = score(cand, rec)
            if s > best_sc:
                best, best_rec, best_sc = r, rec, s
        title_sim = (max(_ratio(cand["nt"], best_rec["nt"]), _ratio(cand["nt"], best_rec["nall"]))
                     if best_rec else 0.0)
        if best and best_sc >= args.min_score and title_sim >= 0.8:
            c["download_url"] = best.get("trackViewUrl", "")
            c["download_status"] = "buy_only"
            c["error"] = ""
            filled += 1
            print(f"link  {artist} - {title}  ({best_sc:.2f})  ->  "
                  f"{best.get('trackName')} / {best.get('artistName')}")
        else:
            unmatched += 1
            if not c.get("error"):
                c["error"] = "no store match found"
            miss_lines.append(f"  no match: {artist} - {title}")
        time.sleep(2.0)  # be polite to the public API

    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nDone: {filled} buy links filled, {unmatched} unmatched, "
          f"{skipped} skipped (owned or already sourced).")
    for line in miss_lines[:20]:
        print(line)
    print(f"Updated {path}")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main())
