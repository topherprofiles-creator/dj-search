#!/usr/bin/env python3
"""write_crate.py - final dj-search report: ranked crate CSV + import-ready M3U8.

Reads the discovery candidates and (optionally) the ownership scan, then writes
into the output directory:

  crate_<window>d_<date>.csv   full ranked table (Excel-friendly UTF-8 BOM)
  crate_<window>d_<date>.m3u8  every track with a real local file - downloaded
                               here or matched on the PC (Serato / rekordbox /
                               Engine import-ready)

Called from SKILL.md step 7. A same-day re-run overwrites the same report file
on purpose (it is a report, not a download); audio files are never touched.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scan_library import normalize  # noqa: E402  (shared de-dup normalisation)

CSV_COLUMNS = ["Rank", "Artist", "Title", "Genre", "BPM", "Key", "Release Date",
               "Why Trending", "Owned", "Owned From", "Status", "Local Path",
               "Source URL", "Recommendation"]


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))  # tolerate BOM'd files (PowerShell)


def key_of(artist, title) -> str:
    return normalize(f"{artist} {title}")


def status_of(cand: dict, have_rec) -> str:
    if have_rec and have_rec.get("owned"):
        return "owned"
    ds = (cand.get("download_status") or "").strip().lower()
    if ds in ("downloaded", "promo", "buy_only", "failed"):
        return ds
    local = (cand.get("local_path") or "").strip()
    if local and Path(local).exists():
        return "downloaded"
    return "missing"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Write the ranked crate CSV + M3U8 for a dj-search run.")
    ap.add_argument("--candidates", required=True, help="candidates.json from the run")
    ap.add_argument("--have", help="have_pc.json from the ownership scan (optional)")
    ap.add_argument("--outdir", help="default: the candidates.json folder")
    ap.add_argument("--window", type=int, default=7, help="7 / 14 / 30 (used in the file name)")
    ap.add_argument("--date", default=date.today().isoformat(),
                    help="report date, ISO YYYY-MM-DD (default: today)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.date):
        raise SystemExit("--date must be ISO YYYY-MM-DD")

    cand_path = Path(args.candidates)
    candidates = load_json(cand_path)
    if not isinstance(candidates, list):
        raise SystemExit("candidates file must be a JSON array")
    outdir = Path(args.outdir) if args.outdir else cand_path.parent
    outdir.mkdir(parents=True, exist_ok=True)

    have_list, have_by_id, have_by_key = [], {}, {}
    if args.have and Path(args.have).exists():
        have_list = load_json(Path(args.have)).get("results", [])
        have_by_id = {str(r.get("id")): r for r in have_list}
        have_by_key = {key_of(r.get("artist", ""), r.get("title", "")): r for r in have_list}

    rows = []
    for i, cand in enumerate(candidates, 1):
        if not isinstance(cand, dict):
            continue
        have_rec = (have_by_id.get(str(cand.get("id")))
                    or have_by_key.get(key_of(cand.get("artist", ""), cand.get("title", ""))))
        if have_rec is None and have_list and len(have_list) == len(candidates):
            have_rec = have_list[i - 1]
        owned = bool(have_rec and have_rec.get("owned"))
        local = (cand.get("local_path") or "").strip()
        if not local and have_rec:
            local = have_rec.get("match_path") or ""
            if not local:
                dp = have_rec.get("drive_path") or ""
                local = dp if dp and Path(dp).is_absolute() else ""  # listing paths are not local files
        rows.append({
            "rank": i,
            "artist": cand.get("artist", ""),
            "title": cand.get("title", ""),
            "genre": cand.get("genre", ""),
            "bpm": cand.get("bpm") if cand.get("bpm") else "",
            "key": str(cand.get("key", "") or ""),
            "release_date": cand.get("release_date", ""),
            "why": cand.get("why_trending", ""),
            "owned": "yes" if owned else "no",
            "owned_from": "/".join(have_rec.get("owned_from") or []) if owned else "",
            "status": status_of(cand, have_rec),
            "local": local,
            "url": cand.get("download_url", "") or "",
            "rec": cand.get("recommendation", "") or "",
        })

    stem = f"crate_{args.window}d_{args.date}"
    csv_path = outdir / (stem + ".csv")
    with csv_path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(CSV_COLUMNS)
        for r in rows:
            writer.writerow([r["rank"], r["artist"], r["title"], r["genre"], r["bpm"],
                             r["key"], r["release_date"], r["why"], r["owned"],
                             r["owned_from"], r["status"], r["local"], r["url"], r["rec"]])

    m3u_path = outdir / (stem + ".m3u8")
    playlist, skipped = [], 0
    for r in rows:
        if r["local"] and os.path.exists(r["local"]):
            playlist.append((r["artist"], r["title"], os.path.abspath(r["local"])))
        elif r["local"] or r["status"] in ("owned", "downloaded"):
            skipped += 1
    with m3u_path.open("w", encoding="utf-8") as fh:
        fh.write("#EXTM3U\n")
        for artist, title, path in playlist:
            fh.write(f"#EXTINF:-1,{artist} - {title}\n")
            fh.write(path.replace("\\", "/") + "\n")

    if not args.quiet:
        counts = {"downloaded": 0, "owned": 0, "promo": 0, "buy_only": 0, "failed": 0, "missing": 0}
        pc = dr = 0
        for r in rows:
            if r["status"] in counts:
                counts[r["status"]] += 1
            if r["owned"] == "yes":
                pc += int("pc" in r["owned_from"])
                dr += int("drive" in r["owned_from"])
        print(f"Owned: {counts['owned']} (PC {pc} / Drive {dr})"
              f"   Downloaded: {counts['downloaded']}   Promo: {counts['promo']}"
              f"   Buy-only: {counts['buy_only']}"
              f"   Failed: {counts['failed']}   Missing: {counts['missing']}")
        print(f"Playlist: {len(playlist)} playable file(s)"
              f"{f', {skipped} owned/downloaded without a local file (cloud or moved)' if skipped else ''}.")

        buckets, keys = {}, {}
        for r in rows:
            try:
                bpm = int(r["bpm"])
            except (TypeError, ValueError):
                bpm = None
            label = ("no BPM" if bpm is None else
                     "<100" if bpm < 100 else "100-109" if bpm < 110 else
                     "110-119" if bpm < 120 else "120-129" if bpm < 130 else "130+")
            buckets.setdefault(label, []).append(r)
            if r["key"]:
                keys[r["key"]] = keys.get(r["key"], 0) + 1
        print("BPM groups:")
        for label in ("<100", "100-109", "110-119", "120-129", "130+", "no BPM"):
            items = buckets.get(label)
            if not items:
                continue
            listing = "; ".join(
                f'{r["artist"]} - {r["title"]}' + (f' [{r["key"]}]' if r["key"] else "")
                for r in items)
            print(f"  {label}: {listing}")
        if keys:
            print("Camelot keys: " + ", ".join(f"{k} x{v}" for k, v in sorted(keys.items())))
        print(f"Wrote {csv_path}")
        print(f"Wrote {m3u_path}")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main())
