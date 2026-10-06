#!/usr/bin/env python3
"""scan_library.py - offline audio-library scanner + fuzzy de-dup for dj-search.

Walks the DJ's music folders and (optionally) a Google Drive mount or an
`rclone lsf` listing, reads what tags it can with zero third-party packages
(ID3v2/ID3v1 for MP3, MP4/M4A atoms, FLAC Vorbis comments, filename fallback),
and fuzzy-matches every entry in candidates.json so nothing already owned is
downloaded again.

Called by SKILL.md step 3 (PC scan) and step 4 (Drive de-dup). Read-only:
it never writes to, moves, renames or deletes any audio file.

  python scripts/scan_library.py \
      --candidates "C:/DJ/Crates/_dj-search/candidates.json" \
      --roots "C:/DJ/Crates" "C:/Users/USER/Music" \
      --out "C:/DJ/Crates/_dj-search/have_pc.json"

  rclone lsf gdrive: --recursive --include "*.mp3" > drive.txt
  python scripts/scan_library.py --candidates candidates.json \
      --drive-listing drive.txt --out have_pc.json

Exit code is 0 even when nothing matches; non-zero only on bad input.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import string
import sys
import time
import unicodedata
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

AUDIO_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".aiff", ".aif"}

# Folder names never worth walking into (pruned at every depth).
PRUNE_DIRS = {
    "$recycle.bin", "$windows.~bt", "$windows.~ws", "system volume information",
    "node_modules", ".git", "windows", "program files", "program files (x86)",
    "programdata", "appdata", "packages", "perflogs", "msocache", "recovery",
    "__pycache__", ".venv", "venv", ".cache",
}

# Tokens that carry no identity for matching — dropped during normalisation.
NOISE_TOKENS = {
    "feat", "ft", "featuring", "with", "prod", "produced", "by", "official", "video",
    "audio", "lyric", "lyrics", "visualizer", "hq", "hd", "4k", "1080p", "clean",
    "dirty", "explicit", "master", "mastered", "version", "kbps", "mp3", "wav",
    "128", "192", "256", "320",
}

_BRACKETS_RE = re.compile(r"[\(\[\{][^\(\)\[\]\{\}]*[\)\]\}]")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_TRACKNUM_RE = re.compile(r"^\s*\d{1,3}(?:[ ._-]\d{1,3})?[ ._-]+")
_SPLIT_RE = re.compile(r"\s+[-\u2013\u2014]\s+")


# --------------------------------------------------------------------------- text

def normalize(text: str) -> str:
    """Lowercase, strip accents/brackets/punctuation and noise tokens.

    'Burna Boy Ft. Ed - Last Last (Official Video) [320]'
        -> 'burna boy ed last last'
    """
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("&", " and ")
    text = _BRACKETS_RE.sub(" ", text)
    text = _NON_ALNUM_RE.sub(" ", text)
    return " ".join(t for t in text.split() if t not in NOISE_TOKENS)


def parse_filename(stem: str):
    """'02. Burna Boy - Last Last (Clean)' -> ('Burna Boy', 'Last Last (Clean)')"""
    stem = _TRACKNUM_RE.sub("", stem, count=1).strip()
    parts = _SPLIT_RE.split(stem, maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        return parts[0].strip(), parts[1].strip()
    return "", stem


# --------------------------------------------------------------------------- tags

def _syncsafe(b: bytes) -> int:
    return ((b[0] & 0x7F) << 21) | ((b[1] & 0x7F) << 14) | ((b[2] & 0x7F) << 7) | (b[3] & 0x7F)


def _decode_text(frame: bytes) -> str:
    if not frame:
        return ""
    enc, rest = frame[0], frame[1:]
    try:
        if enc == 0:
            text = rest.decode("latin-1")
        elif enc == 1:
            text = rest.decode("utf-16")
        elif enc == 2:
            text = rest.decode("utf-16-be")
        else:
            text = rest.decode("utf-8", "replace")
    except Exception:
        text = rest.decode("latin-1", "replace")
    return text.split("\x00")[0].strip()


_ID3_FRAMES = {
    b"TIT2": "title", b"TT2": "title",
    b"TPE1": "artist", b"TP1": "artist",
    b"TALB": "album", b"TAL": "album",
    b"TDRC": "date", b"TYER": "date", b"TYE": "date",
    b"TCON": "genre", b"TCO": "genre",
}


def read_id3(path: Path) -> dict:
    tags = {}
    with path.open("rb") as f:
        head = f.read(10)
        if len(head) >= 10 and head[:3] == b"ID3":
            major = head[3]
            body = f.read(_syncsafe(head[6:10]))
            pos = 0
            if head[5] & 0x40 and len(body) >= 4:  # extended header
                if major == 3:
                    pos = 4 + int.from_bytes(body[:4], "big")
                elif major == 4:
                    pos = _syncsafe(body[:4])
            hdr_len = 6 if major == 2 else 10
            while pos + hdr_len <= len(body):
                if major == 2:
                    fid = body[pos:pos + 3]
                    size = int.from_bytes(body[pos + 3:pos + 6], "big")
                else:
                    fid = body[pos:pos + 4]
                    size = (_syncsafe(body[pos + 4:pos + 8]) if major == 4
                            else int.from_bytes(body[pos + 4:pos + 8], "big"))
                pos += hdr_len
                if not fid.strip(b"\x00") or size <= 0 or pos + size > len(body):
                    break
                key = _ID3_FRAMES.get(fid)
                if key and key not in tags:
                    tags[key] = _decode_text(body[pos:pos + size])
                pos += size
    if not (tags.get("title") and tags.get("artist")):
        try:  # ID3v1 fallback (last 128 bytes)
            with path.open("rb") as f:
                f.seek(-128, os.SEEK_END)
                v1 = f.read(128)
            if len(v1) == 128 and v1[:3] == b"TAG":
                def clean(b: bytes) -> str:
                    return b.rstrip(b"\x00 ").decode("latin-1", "replace").strip()
                tags.setdefault("title", clean(v1[3:33]))
                tags.setdefault("artist", clean(v1[33:63]))
                tags.setdefault("album", clean(v1[63:93]))
        except OSError:
            pass
    return tags


def read_mp4(path: Path) -> dict:
    """Minimal MP4/M4A atom walk: moov > udta > meta > ilst > (c)nam/(c)ART/..."""
    tags = {}
    attr = {b"\xa9nam": "title", b"\xa9ART": "artist", b"\xa9alb": "album",
            b"\xa9day": "date", b"\xa9gen": "genre"}

    with path.open("rb") as f:
        end_of_file = path.stat().st_size

        def read_ilst(end_off: int) -> None:
            while f.tell() < end_off:
                start = f.tell()
                hdr = f.read(8)
                if len(hdr) < 8:
                    return
                size = int.from_bytes(hdr[:4], "big")
                name = hdr[4:8]
                if size < 8:
                    return
                body_end = start + size
                key = attr.get(name)
                if key and key not in tags:
                    child = f.read(8)
                    if len(child) == 8 and child[4:8] == b"data":
                        csize = int.from_bytes(child[:4], "big")
                        f.read(8)  # data type + locale
                        payload = f.read(max(0, csize - 16))
                        tags[key] = payload.decode("utf-8", "replace").strip("\x00").strip()
                f.seek(body_end)

        def walk(end_off: int, depth: int) -> None:
            while f.tell() < end_off and depth < 6:
                start = f.tell()
                hdr = f.read(8)
                if len(hdr) < 8:
                    return
                size = int.from_bytes(hdr[:4], "big")
                name = hdr[4:8]
                header = 8
                if size == 1:
                    ext = f.read(8)
                    if len(ext) < 8:
                        return
                    size = int.from_bytes(ext, "big")
                    header = 16
                elif size == 0:
                    size = end_off - start
                if size < header:
                    return
                body_end = start + size
                if name in (b"moov", b"udta"):
                    walk(body_end, depth + 1)
                elif name == b"meta":
                    f.read(4)  # version + flags
                    walk(body_end, depth + 1)
                elif name == b"ilst":
                    read_ilst(body_end)
                f.seek(body_end)

        walk(end_of_file, 0)
    return tags


def read_flac(path: Path) -> dict:
    tags = {}
    keys = {"title": "title", "artist": "artist", "album": "album",
            "date": "date", "genre": "genre"}
    with path.open("rb") as f:
        if f.read(4) != b"fLaC":
            return {}
        while True:
            hdr = f.read(4)
            if len(hdr) < 4:
                break
            last, btype = hdr[0] & 0x80, hdr[0] & 0x7F
            data = f.read(int.from_bytes(hdr[1:4], "big"))
            if btype == 4:  # VORBIS_COMMENT
                try:
                    pos = 0
                    vlen = int.from_bytes(data[pos:pos + 4], "little")
                    pos += 4 + vlen
                    count = int.from_bytes(data[pos:pos + 4], "little")
                    pos += 4
                    for _ in range(min(count, 128)):
                        if pos + 4 > len(data):
                            break
                        length = int.from_bytes(data[pos:pos + 4], "little")
                        pos += 4
                        item = data[pos:pos + length].decode("utf-8", "replace")
                        pos += length
                        if "=" in item:
                            k, v = item.split("=", 1)
                            key = keys.get(k.strip().lower())
                            if key:
                                tags.setdefault(key, v.strip())
                except Exception:
                    pass
            if last:
                break
    return tags


def read_tags(path: Path) -> dict:
    ext = path.suffix.lower()
    try:
        if ext == ".mp3":
            return read_id3(path)
        if ext in (".m4a", ".mp4", ".aac"):
            return read_mp4(path)
        if ext == ".flac":
            return read_flac(path)
    except Exception:
        pass
    return {}


# --------------------------------------------------------------------------- records

def make_record(path_str: str, read_file: bool) -> dict:
    tag_artist = tag_title = ""
    if read_file:
        tags = read_tags(Path(path_str))
        tag_artist = (tags.get("artist") or "").strip()
        tag_title = (tags.get("title") or "").strip()
    fa, ft = parse_filename(Path(path_str).stem)
    artist = tag_artist or fa
    title = tag_title or ft
    if not read_file:
        source = "drive"
    elif tag_artist and tag_title:
        source = "tags"
    else:
        source = "filename"
    return {
        "path": path_str,
        "artist": artist,
        "title": title,
        "source": source,
        "na": normalize(artist),
        "nt": normalize(title),
        "nall": normalize(f"{artist} {title}"),
    }


def scan_roots(roots) -> list:
    files, seen = [], set()
    for root in roots:
        rp = Path(root)
        if not rp.exists():
            print(f"  ! drive/folder not ready or not found, skipping: {root}", file=sys.stderr)
            continue
        for dirpath, dirnames, filenames in os.walk(rp):
            dirnames[:] = [d for d in dirnames
                           if d.lower() not in PRUNE_DIRS and not d.startswith(".")]
            for fn in filenames:
                if Path(fn).suffix.lower() in AUDIO_EXTS:
                    full = os.path.join(dirpath, fn)
                    key = os.path.normcase(full)
                    if key not in seen:
                        seen.add(key)
                        files.append(full)
    return sorted(files, key=lambda p: p.lower())


def discover_drives() -> list:
    """Every filesystem worth scanning: on Windows, fixed + removable drives
    (the whole PC and any flash drives); elsewhere the usual mount points."""
    roots = []
    if sys.platform == "win32":
        try:
            import ctypes
            bitmask = ctypes.windll.kernel32.GetLogicalDrives()
            for i, letter in enumerate(string.ascii_uppercase):
                if not (bitmask >> i) & 1:
                    continue
                root = f"{letter}:\\"
                dtype = ctypes.windll.kernel32.GetDriveTypeW(root)
                if dtype in (2, 3):  # DRIVE_REMOVABLE (flash) + DRIVE_FIXED
                    roots.append(root)
        except Exception:
            pass
        return roots
    for parent in ("/Volumes", "/media", "/run/media"):
        if Path(parent).is_dir():
            roots.append(parent)
    return roots


def load_drive_listing(path: str) -> list:
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    out, seen = [], set()
    for line in lines:
        line = line.strip()
        if line and line.lower().endswith(tuple(AUDIO_EXTS)) and line not in seen:
            seen.add(line)
            out.append(line)
    return out


# --------------------------------------------------------------------------- matching

def build_index(records) -> dict:
    index = {}
    for i, rec in enumerate(records):
        for token in set(rec["nall"].split()):
            if len(token) >= 4:
                index.setdefault(token, set()).add(i)
    return index


def candidate_subset(tokens, index, total):
    sets = sorted((index[t] for t in tokens if t in index), key=len)
    if not sets:
        return range(total)
    subset = set(sets[0])
    for s in sets[1:]:
        nxt = subset & s
        if not nxt:
            break
        subset = nxt
    return subset


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _containment(na: str, target_tokens: set) -> float:
    parts = na.split()
    if not parts:
        return 0.0
    return sum(1 for p in parts if p in target_tokens) / len(parts)


def score(cand: dict, rec: dict) -> float:
    """Blend: title carries 65%, artist 35% — artist guards false positives on
    generic titles. Exact normalised equality short-circuits to 1.0."""
    if cand["nall"] and cand["nall"] == rec["nall"]:
        return 1.0
    title = max(_ratio(cand["nt"], rec["nt"]), _ratio(cand["nt"], rec["nall"]))
    if not cand["na"]:
        return title
    target_tokens = set(rec["nall"].split())
    artist = _containment(cand["na"], target_tokens)
    if rec["na"]:
        artist = max(artist, _ratio(cand["na"], rec["na"]))
    return 0.65 * title + 0.35 * artist


def best_match(cand: dict, records, index, soft_threshold: float):
    tokens = [t for t in cand["nt"].split() if len(t) >= 4]
    subset = candidate_subset(tokens, index, len(records))
    best_score, best_rec = 0.0, None
    soft = []
    for i in subset:
        rec = records[i]
        sc = score(cand, rec)
        if sc > best_score:
            best_score, best_rec = sc, rec
        if sc >= soft_threshold:
            soft.append((sc, rec))
    soft.sort(key=lambda pair: pair[0], reverse=True)
    return best_score, best_rec, soft[:5]


# --------------------------------------------------------------------------- main

def load_candidates(path: str) -> list:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and "candidates" in data:
        data = data["candidates"]
    if not isinstance(data, list):
        raise SystemExit("candidates file must be a JSON array (or {\"candidates\": [...]})")
    out = []
    for i, c in enumerate(data, 1):
        if not isinstance(c, dict):
            continue
        artist = str(c.get("artist") or "")
        title = str(c.get("title") or "")
        out.append({
            "id": str(c.get("id") or f"cand-{i:03d}"),
            "artist": artist,
            "title": title,
            "na": normalize(artist),
            "nt": normalize(title),
            "nall": normalize(f"{artist} {title}"),
        })
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Scan a DJ's audio library (PC folders and/or a Drive listing) "
                    "and mark which discovery candidates are already owned.")
    ap.add_argument("--candidates", help="candidates.json from the discovery step")
    ap.add_argument("--roots", nargs="*", default=[], help="folders to walk (PC + mounted Drive)")
    ap.add_argument("--all-drives", action="store_true",
                    help="scan every fixed and removable drive (whole PC + flash drives)")
    ap.add_argument("--drive-listing", help="text file, one path per line (rclone lsf output)")
    ap.add_argument("--out", required=True, help="output JSON path (have_pc.json)")
    ap.add_argument("--owned-threshold", type=float, default=0.82,
                    help="score at/above which a candidate counts as owned (default 0.82)")
    ap.add_argument("--soft-threshold", type=float, default=0.62,
                    help="score at/above which a match is shown for review (default 0.62)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    roots = list(args.roots)
    if args.all_drives:
        for drive in discover_drives():
            if drive not in roots:
                roots.append(drive)
    if not roots and not args.drive_listing:
        ap.error("give at least one --roots folder, --all-drives and/or a --drive-listing file")

    started = time.time()

    pc_paths = scan_roots(roots)
    drive_paths = load_drive_listing(args.drive_listing) if args.drive_listing else []

    pc_records, drive_records = [], []
    if not args.quiet:
        print(f"Scanned {len(pc_paths):,} audio files, reading tags...")
    for n, p in enumerate(pc_paths, 1):
        pc_records.append(make_record(p, read_file=True))
        if not args.quiet and n % 500 == 0:
            print(f"\r  tags: {n:,}/{len(pc_paths):,}", end="", flush=True)
    if not args.quiet and pc_paths:
        print(f"\r  tags: {len(pc_paths):,}/{len(pc_paths):,}")
    for p in drive_paths:
        drive_records.append(make_record(p, read_file=False))

    pc_index = build_index(pc_records)
    drive_index = build_index(drive_records)
    candidates = load_candidates(args.candidates) if args.candidates else []

    results, soft_lines = [], []
    summary = {"candidates": len(candidates), "owned": 0, "owned_pc": 0,
               "owned_drive": 0, "missing": 0, "soft": 0}

    for cand in candidates:
        pc_sc, pc_rec, pc_soft = best_match(cand, pc_records, pc_index, args.soft_threshold)
        dr_sc, dr_rec, dr_soft = best_match(cand, drive_records, drive_index, args.soft_threshold)
        pc_owned = pc_rec is not None and pc_sc >= args.owned_threshold
        dr_owned = dr_rec is not None and dr_sc >= args.owned_threshold
        owned = pc_owned or dr_owned
        owned_from = (["pc"] if pc_owned else []) + (["drive"] if dr_owned else [])
        conf = max(pc_sc if pc_owned else 0.0, dr_sc if dr_owned else 0.0)

        soft_matches, seen_paths = [], set()
        for sc, rec in (pc_soft + dr_soft):
            if pc_owned and rec["path"] == pc_rec["path"]:
                continue
            if dr_owned and rec["path"] == dr_rec["path"]:
                continue
            if rec["path"] in seen_paths:
                continue
            seen_paths.add(rec["path"])
            soft_matches.append({
                "path": rec["path"],
                "confidence": round(sc, 3),
                "where": "drive" if rec["source"] == "drive" else "pc",
            })
            if len(soft_matches) >= 5:
                break

        if owned:
            summary["owned"] += 1
            summary["owned_pc"] += int(pc_owned)
            summary["owned_drive"] += int(dr_owned)
        else:
            summary["missing"] += 1
            if soft_matches:
                summary["soft"] += 1
                if len(soft_lines) < 12:
                    soft_lines.append(
                        f"  SOFT {soft_matches[0]['confidence']:.2f}  "
                        f"{cand['artist']} - {cand['title']}  ~  {soft_matches[0]['path']}")

        matched_by = (pc_rec["source"] if pc_owned
                      else (dr_rec["source"] if dr_owned else None))
        results.append({
            "id": cand["id"],
            "artist": cand["artist"],
            "title": cand["title"],
            "owned": owned,
            "owned_from": owned_from,
            "match_path": pc_rec["path"] if pc_owned else None,
            "drive_path": dr_rec["path"] if dr_owned else None,
            "confidence": round(conf, 3) if owned else (round(max(pc_sc, dr_sc), 3) or None),
            "matched_by": matched_by,
            "soft_matches": soft_matches,
        })

    output = {
        "generated": date.today().isoformat(),
        "roots": [os.path.abspath(r) for r in roots],
        "drive_listing": os.path.abspath(args.drive_listing) if args.drive_listing else None,
        "thresholds": {"owned": args.owned_threshold, "soft": args.soft_threshold},
        "scanned": {"audio_files": len(pc_records), "drive_entries": len(drive_records)},
        "summary": summary,
        "results": results,
    }
    if not candidates:
        output["library_index"] = [{"path": r["path"], "artist": r["artist"],
                                    "title": r["title"]} for r in pc_records]

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")

    if not args.quiet:
        took = time.time() - started
        print(f"Scanned {len(pc_records):,} audio files"
              f"{f' (+ {len(drive_records):,} Drive entries)' if drive_records else ''}"
              f" in {took:.1f}s.")
        if candidates:
            print(f"Candidates: {summary['candidates']} — owned {summary['owned']} "
                  f"(PC {summary['owned_pc']}, Drive {summary['owned_drive']}), "
                  f"missing {summary['missing']}, soft hits {summary['soft']}.")
            for line in soft_lines:
                print(line)
            if summary["soft"]:
                print("  (soft hits are shown, not counted as owned — let the DJ decide)")
        print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main())
