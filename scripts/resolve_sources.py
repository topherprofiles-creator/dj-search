#!/usr/bin/env python3
"""resolve_sources.py - find download URLs for dj-search candidates.

The resolver half of the skill's no-track-left-behind loop. For every candidate
that is still missing and has no download_url it runs two stages:

  1. SoundCloud (preferred): search the public API and keep a hit only when BOTH
     hold - the uploader account itself looks like the artist (their own
     account), and the track has the free download enabled (downloadable=true
     and has_downloads_left not False).

  2. YouTube fallback (default on; --no-youtube disables): search YouTube via
     yt-dlp and score every result the way sunnify-style downloaders do. The
     video title must carry the track (speed/remix/cover edits are rejected
     unless the candidate itself names them), the channel must look like the
     artist's own account or their "<Artist> - Topic" auto-catalog, and when a
     reference duration is known - the artist's own SoundCloud upload, even
     when its free download is off, or the candidate's own duration_s - the
     video length must agree within a few seconds. That length check is what
     rejects wrong edits of the right song.

Hits are written back into candidates.json (download_url + download_source:
'soundcloud:<uploader>' or 'youtube:<channel>') so scripts/download.py can
fetch them. Leech / "free MP3" blogs and Google download results stay excluded
- see SKILL.md.

Usage:
  python scripts/resolve_sources.py --candidates "<save>/_dj-search/candidates.json" \
      [--have "<save>/_dj-search/have_pc.json"] [--dry-run] [--max N] [--no-youtube]

  python scripts/resolve_sources.py --song "Artist - Title"    # one JSON line out

Requirements: stdlib only for the SoundCloud stage; the YouTube fallback
additionally needs the yt-dlp package (`python -m pip install -U yt-dlp`) and
is skipped with a note when it is missing.
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
# YouTube-only edit markers: same rule as VARIANT_NOISE - they only disqualify
# when the candidate title does not name the edit itself.
YT_VARIANT_NOISE = re.compile(
    r"\b(sped[\s-]?up|slowed|nightcore|8d audio|bass[\s-]?boosted|mashup|medley|"
    r"dj mix|extended mix|loop|reverb|chopped|screwed|remix)\b")
YT_SEARCH_LIMIT = 8


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
    """Best artist-enabled free-download hit for one track, plus a reference
    duration. Returns (hit_or_None, ref_duration_s_or_None). The reference
    duration comes from the artist's own SoundCloud upload of the same song
    (even when its free download is off) and feeds the YouTube fallback's
    length check."""
    query = f"{main_artist(artist)} {title}".strip()
    tracks = search_tracks(client_id, query)
    owned = [t for t in tracks
             if uploader_is_artist(artist, t) and title_matches(title, t)]
    ref_s = None
    if owned:
        owned.sort(key=lambda t: (normalize(title) in normalize(t.get("title", "")),
                                  t.get("playback_count") or 0), reverse=True)
        ref_s = round((owned[0].get("duration") or 0) / 1000) or None
    hits = [t for t in owned if is_free_download(t)]
    if not hits:
        return None, ref_s
    t = hits[0]
    return {
        "download_url": t.get("permalink_url", ""),
        "uploader": (t.get("user") or {}).get("username", ""),
        "soundcloud_title": t.get("title", ""),
        "plays": t.get("playback_count"),
        "duration_s": round((t.get("duration") or 0) / 1000),
    }, ref_s


def yt_import():
    """yt-dlp is a soft dependency: without it only the SoundCloud stage runs."""
    try:
        from yt_dlp import YoutubeDL
        return YoutubeDL
    except ImportError:
        return None


def search_youtube(artist: str, title: str, limit: int = YT_SEARCH_LIMIT) -> list:
    """One yt-dlp search (search only - nothing is downloaded here)."""
    YoutubeDL = yt_import()
    if YoutubeDL is None:
        raise RuntimeError("yt-dlp not installed (python -m pip install -U yt-dlp)")
    query = f"{main_artist(artist)} {title}".strip()
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True,
            "skip_download": True, "socket_timeout": 30}
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
    return [e for e in ((info or {}).get("entries") or []) if isinstance(e, dict)]


def youtube_match_score(artist: str, title: str, info: dict, ref_duration=None):
    """(score, confidence) for one YouTube result, or (0, "") to reject it.

    The sunnify recipe: the video title must carry the track (variant edits -
    sped up, slowed, remix, cover ... - are rejected unless the candidate itself
    names the edit), the channel must look like the artist's own account or
    their "<Artist> - Topic" catalog, and when a reference duration is known
    the video length must agree within a few seconds - that length check is the
    strongest signal and what keeps wrong edits out. Without a reference, a
    label upload is still accepted when the artist is named in the title."""
    video_title = info.get("title") or ""
    channel = info.get("channel") or info.get("uploader") or ""
    dur = info.get("duration")
    if not video_title or not channel or not dur or info.get("live_status") == "is_live":
        return 0, ""
    nt, vt = normalize(title), normalize(video_title)
    if YT_VARIANT_NOISE.search(vt) and not YT_VARIANT_NOISE.search(nt):
        return 0, ""
    if not title_matches(title, {"title": video_title}):
        return 0, ""
    artist_owned = uploader_is_artist(artist, {"user": {"username": channel}})
    topic = artist_owned and channel.strip().lower().endswith("- topic")
    score, conf = 1, "medium"
    if artist_owned:
        score += 2
    if topic:
        score += 2
        conf = "high"
    if ref_duration:
        if abs(dur - ref_duration) <= 5:
            score += 3
            conf = "high"
        else:
            return 0, ""
    elif not (artist_owned or normalize(main_artist(artist)) in vt):
        return 0, ""
    return score, conf


def resolve_youtube(artist: str, title: str, ref_duration=None):
    """Best duration-checked YouTube match for one track, or None."""
    scored = []
    for info in search_youtube(artist, title):
        score, conf = youtube_match_score(artist, title, info, ref_duration)
        if score:
            scored.append((score, info.get("view_count") or 0, conf, info))
    if not scored:
        return None
    scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
    _, _, conf, info = scored[0]
    return {
        "download_url": (info.get("webpage_url")
                         or f"https://www.youtube.com/watch?v={info.get('id', '')}"),
        "channel": info.get("channel") or info.get("uploader") or "",
        "youtube_title": info.get("title") or "",
        "duration_s": int(info.get("duration") or 0),
        "confidence": conf,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Resolve legal, artist-enabled SoundCloud download URLs for dj-search.")
    ap.add_argument("--candidates", help="candidates.json to scan and update in place")
    ap.add_argument("--have", help="have_pc.json - owned candidates are skipped")
    ap.add_argument("--song", help="single mode: 'Artist - Title' (prints one JSON line)")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    ap.add_argument("--max", type=int, help="stop after N candidate lookups (quick runs)")
    ap.add_argument("--no-youtube", action="store_true",
                    help="skip the YouTube fallback (artist-enabled free sources only)")
    args = ap.parse_args(argv)

    if bool(args.candidates) == bool(args.song):
        ap.error("give exactly one of --candidates or --song")

    client_id = get_client_id()

    if args.song:
        artist, _, title = args.song.partition(" - ")
        artist, title = artist.strip(), (title.strip() or artist.strip())
        hit, ref = resolve_one(client_id, artist, title)
        if hit and hit["download_url"]:
            print(json.dumps({"artist": artist, "title": title, "source": "soundcloud",
                              **hit}, ensure_ascii=False))
            return 0
        if not args.no_youtube and yt_import() is not None:
            yt_hit = resolve_youtube(artist, title, ref)
            if yt_hit:
                print(json.dumps({"artist": artist, "title": title, "source": "youtube",
                                  **yt_hit}, ensure_ascii=False))
                return 0
        print(json.dumps({"artist": artist, "title": title, "download_url": ""},
                         ensure_ascii=False))
        return 0

    path = Path(args.candidates)
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, list):
        raise SystemExit("candidates must be a JSON array")

    owned_ids = set()
    if args.have and Path(args.have).exists():
        have = json.loads(Path(args.have).read_text(encoding="utf-8-sig"))
        owned_ids = {str(r.get("id")) for r in have.get("results", []) if r.get("owned")}

    resolved = yt_resolved = skipped = lookups = 0
    no_source = []
    warned_no_ytdlp = False
    skip_statuses = {"owned", "downloaded", "promo"}
    if args.no_youtube:
        skip_statuses.add("buy_only")  # legal-only runs leave them parked
    for entry in data:
        if not isinstance(entry, dict):
            continue
        label = f"{entry.get('artist', '')} - {entry.get('title', '')}".strip(" -")
        status = (entry.get("download_status") or "missing").strip().lower()
        if (str(entry.get("id")) in owned_ids or status in skip_statuses
                or (entry.get("download_url") or "").strip()):
            skipped += 1
            continue
        if args.max is not None and lookups >= args.max:
            break
        lookups += 1

        ref = entry.get("duration_s") or None
        hit = None
        lookup_failed = False
        try:
            hit, sc_ref = resolve_one(client_id, entry.get("artist", ""), entry.get("title", ""))
            ref = ref or sc_ref
        except SystemExit:
            raise
        except Exception as exc:
            print(f"ERROR  {label}: {type(exc).__name__}: {exc}")
            lookup_failed = True
        finally:
            time.sleep(0.6)  # stay polite with the API, even after errors

        yt_hit = None
        if not hit and not args.no_youtube:
            if yt_import() is None:
                if not warned_no_ytdlp:
                    print("yt-dlp not installed - YouTube fallback skipped "
                          "(python -m pip install -U yt-dlp)")
                    warned_no_ytdlp = True
            else:
                try:
                    yt_hit = resolve_youtube(entry.get("artist", ""), entry.get("title", ""), ref)
                except Exception as exc:
                    print(f"ERROR  {label} (youtube): {type(exc).__name__}: {exc}")

        if hit and hit["download_url"]:
            resolved += 1
            print(f"RESOLVED  {label}\n"
                  f"  {hit['download_url']}\n"
                  f"  uploader: {hit['uploader']}  plays: {hit['plays']}  "
                  f"len: {hit['duration_s']}s  sc-title: \"{hit['soundcloud_title']}\"")
            if not args.dry_run:
                entry["download_url"] = hit["download_url"]
                entry["download_source"] = f"soundcloud:{hit['uploader']}"
                if status == "buy_only":  # now resolvable - back into play
                    entry["download_status"] = "missing"
        elif yt_hit:
            yt_resolved += 1
            print(f"RESOLVED (YouTube fallback)  {label}\n"
                  f"  {yt_hit['download_url']}\n"
                  f"  channel: {yt_hit['channel']}  len: {yt_hit['duration_s']}s  "
                  f"confidence: {yt_hit['confidence']}  yt-title: \"{yt_hit['youtube_title']}\"")
            if not args.dry_run:
                entry["download_url"] = yt_hit["download_url"]
                entry["download_source"] = f"youtube:{yt_hit['channel']}"
                entry["youtube_title"] = yt_hit["youtube_title"]
                entry["match_confidence"] = yt_hit["confidence"]
                if ref:
                    entry["duration_s"] = int(ref)
                if status == "buy_only":  # now resolvable - back into play
                    entry["download_status"] = "missing"
        elif lookup_failed:
            continue  # the error line above is the record; not a "no source" verdict
        else:
            no_source.append(label)
            if args.no_youtube:
                print(f"NO ARTIST-ENABLED FREE SOURCE  {label}")
            else:
                print(f"NO ARTIST-ENABLED FREE SOURCE AND NO YOUTUBE MATCH  {label}")

    if not args.dry_run and (resolved or yt_resolved):
        tmp = path.with_name(path.name + ".tmp")  # atomic replace: never corrupt the manifest
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        print(f"Updated {path}")

    print(f"Done: {resolved} resolved from SoundCloud, "
          f"{yt_resolved} from the YouTube fallback, {skipped} skipped (already handled), "
          f"{len(no_source)} with no source.")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main())
