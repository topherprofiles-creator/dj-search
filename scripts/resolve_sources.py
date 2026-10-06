#!/usr/bin/env python3
"""resolve_sources.py - find legal, artist-enabled download URLs for dj-search candidates.

The resolver half of the skill's no-track-left-behind loop. For every candidate
that is still missing and has no download_url, it searches SoundCloud's public
API and keeps a hit only when BOTH hold:

  1. the uploader account itself looks like the artist (their own account), and
  2. the track has the free download enabled (downloadable=true and
     has_downloads_left not False).

Hits are written back into candidates.json (download_url + download_source)
so scripts/download.py can fetch them. The source list never widens: leech /
"free MP3" blogs and Google download results stay excluded - see SKILL.md.

Usage:
  python scripts/resolve_sources.py --candidates "<save>/_dj-search/candidates.json" \
      [--have "<save>/_dj-search/have_pc.json"] [--dry-run] [--max N]

  python scripts/resolve_sources.py --song "Artist - Title"    # one JSON line out

Requirements: stdlib only; talks to soundcloud.com + api-v2.soundcloud.com.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
CLIENT_ID_RE = re.compile(r'client_id\s*[:=]\s*"?([0-9a-zA-Z]{32})')
ASSET_RE = re.compile(r'(https://a-v2\.sndcdn\.com/assets/[^"\']+\.js)')
NOISE = re.compile(r"\b(feat|ft|with|prod|official|video|audio|visualizer|lyrics?|free|download|dl|out now)\b\.?")
VARIANT_NOISE = re.compile(r"\b(instrumental|karaoke|acapella|acappella|open verse|preview|snippet|type beat|remake|cover)\b")


def http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def get_client_id() -> str:
    """Scrape the public client_id the SoundCloud web player itself uses."""
    page = http_get("https://soundcloud.com/").decode("utf-8", "replace")
    m = CLIENT_ID_RE.search(page)
    if m:
        return m.group(1)
    for asset in list(dict.fromkeys(ASSET_RE.findall(page)))[-4:]:  # app bundles
        try:
            js = http_get(asset).decode("utf-8", "replace")
        except Exception:
            continue
        m = CLIENT_ID_RE.search(js)
        if m:
            return m.group(1)
    raise SystemExit("Could not find a SoundCloud client_id (site change?). "
                     "Skip the resolver and run download.py only.")


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = s.lower()
    s = re.sub(r"[\(\[\{].*?[\)\]\}]", " ", s)   # drop bracketed parts
    s = NOISE.sub(" ", s)                        # drop promo noise words
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def tokens(s: str) -> set:
    return {t for t in normalize(s).split() if len(t) > 1}


def main_artist(artist: str) -> str:
    """First name of a collab list: 'A & B' -> 'A', 'A ft. C' -> 'A'."""
    return re.split(r",|&|\bx\b|\bfeat\b|\bft\b", artist or "", 1)[0].strip()


def uploader_is_artist(artist: str, track: dict) -> bool:
    """Strict guard: the account itself must look like the artist - this is what
    keeps fan re-uploads of label songs out of the download set."""
    a = main_artist(artist)
    if not a:
        return False
    uploader_name = (track.get("user") or {}).get("username", "") or ""
    compact_artist = normalize(a).replace(" ", "")
    if compact_artist and compact_artist in normalize(uploader_name).replace(" ", ""):
        return True
    at, ut = tokens(a), tokens(uploader_name)
    return bool(at) and len(at & ut) / len(at) >= 0.8


def title_matches(title: str, track: dict) -> bool:
    """The right song, not a sibling artifact (its instrumental / open verse /
    preview). Variant words only disqualify when the candidate title lacks them."""
    nt = normalize(title)
    track_norm = normalize(track.get("title", "") or "")
    if VARIANT_NOISE.search(track_norm) and not VARIANT_NOISE.search(nt):
        return False
    if nt and nt in track_norm:
        return True
    tt = tokens(title)
    return bool(tt) and len(tt & tokens(track_norm)) / len(tt) >= 0.8


def is_free_download(track: dict) -> bool:
    return bool(track.get("downloadable")) and track.get("has_downloads_left") is not False


def search_tracks(client_id: str, query: str, limit: int = 30) -> list:
    url = ("https://api-v2.soundcloud.com/search/tracks?q=" +
           urllib.parse.quote(query) +
           f"&client_id={client_id}&limit={limit}&app_locale=en")
    try:
        return json.loads(http_get(url).decode("utf-8", "replace")).get("collection", [])
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise SystemExit("SoundCloud rejected the client_id (HTTP %d). The script refetches "
                             "it every run - try again; if it persists SoundCloud changed something."
                             % exc.code)
        if exc.code == 429:  # rate limited - one polite retry after a pause
            time.sleep(5)
            return json.loads(http_get(url).decode("utf-8", "replace")).get("collection", [])
        raise


def resolve_one(client_id: str, artist: str, title: str):
    """Best artist-enabled free-download hit for one track, or None."""
    query = f"{main_artist(artist)} {title}".strip()
    tracks = search_tracks(client_id, query)
    hits = [t for t in tracks
            if is_free_download(t) and uploader_is_artist(artist, t) and title_matches(title, t)]
    if not hits:
        return None
    hits.sort(key=lambda t: (normalize(title) in normalize(t.get("title", "")),
                             t.get("playback_count") or 0), reverse=True)
    t = hits[0]
    return {
        "download_url": t.get("permalink_url", ""),
        "uploader": (t.get("user") or {}).get("username", ""),
        "soundcloud_title": t.get("title", ""),
        "plays": t.get("playback_count"),
        "duration_s": round((t.get("duration") or 0) / 1000),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Resolve legal, artist-enabled SoundCloud download URLs for dj-search.")
    ap.add_argument("--candidates", help="candidates.json to scan and update in place")
    ap.add_argument("--have", help="have_pc.json - owned candidates are skipped")
    ap.add_argument("--song", help="single mode: 'Artist - Title' (prints one JSON line)")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    ap.add_argument("--max", type=int, help="stop after N candidate lookups (quick runs)")
    args = ap.parse_args(argv)

    if bool(args.candidates) == bool(args.song):
        ap.error("give exactly one of --candidates or --song")

    client_id = get_client_id()

    if args.song:
        artist, _, title = args.song.partition(" - ")
        artist, title = artist.strip(), (title.strip() or artist.strip())
        hit = resolve_one(client_id, artist, title)
        print(json.dumps({"artist": artist, "title": title,
                          **(hit or {"download_url": ""})}, ensure_ascii=False))
        return 0

    path = Path(args.candidates)
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, list):
        raise SystemExit("candidates must be a JSON array")

    owned_ids = set()
    if args.have and Path(args.have).exists():
        have = json.loads(Path(args.have).read_text(encoding="utf-8-sig"))
        owned_ids = {str(r.get("id")) for r in have.get("results", []) if r.get("owned")}

    resolved = skipped = lookups = 0
    no_source = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        label = f"{entry.get('artist', '')} - {entry.get('title', '')}".strip(" -")
        status = (entry.get("download_status") or "missing").strip().lower()
        if (str(entry.get("id")) in owned_ids or status in ("owned", "downloaded", "promo", "buy_only")
                or (entry.get("download_url") or "").strip()):
            skipped += 1
            continue
        if args.max is not None and lookups >= args.max:
            break
        lookups += 1
        failed_lookup = False
        try:
            hit = resolve_one(client_id, entry.get("artist", ""), entry.get("title", ""))
        except SystemExit:
            raise
        except Exception as exc:
            print(f"ERROR  {label}: {type(exc).__name__}: {exc}")
            failed_lookup = True
            hit = None
        finally:
            time.sleep(0.6)  # stay polite with the API, even after errors
        if failed_lookup:
            continue
        if not hit or not hit["download_url"]:
            no_source.append(label)
            print(f"NO ARTIST-ENABLED FREE SOURCE  {label}")
            continue
        resolved += 1
        print(f"RESOLVED  {label}\n"
              f"  {hit['download_url']}\n"
              f"  uploader: {hit['uploader']}  plays: {hit['plays']}  "
              f"len: {hit['duration_s']}s  sc-title: \"{hit['soundcloud_title']}\"")
        if not args.dry_run:
            entry["download_url"] = hit["download_url"]
            entry["download_source"] = f"soundcloud:{hit['uploader']}"

    if not args.dry_run and resolved:
        tmp = path.with_name(path.name + ".tmp")  # atomic replace: never corrupt the manifest
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        print(f"Updated {path}")

    print(f"Done: {resolved} resolved, {skipped} skipped (already handled), "
          f"{len(no_source)} with no artist-enabled free source.")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main())
