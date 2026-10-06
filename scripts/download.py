#!/usr/bin/env python3
"""download.py - legal free-download fetcher for dj-search (yt-dlp library).

Only for sources that actually offer the track for free or under the DJ's own
license: Audiomack (artist enabled), SoundCloud (Free Download enabled),
Bandcamp (free / name-your-price). Every other host is refused - YouTube,
Spotify, Apple Music, Boomplay and piracy sites are discovery-only (see the
legal line in SKILL.md). The allowlist below is enforced per URL.

Runs yt-dlp in-process via the `yt_dlp` Python package (no CLI subprocess), so
interactive runs show a live download percentage on stderr.

Requirements: yt-dlp installed for this interpreter
(`python -m pip install -U yt-dlp`); ffmpeg on PATH for the MP3 320 transcode.

Manifest mode (SKILL.md step 5) - downloads every candidate that is still
missing and has a download_url, updating the manifest in place:

  python scripts/download.py --manifest "<save>/_dj-search/candidates.json" \
      --have "<save>/_dj-search/have_pc.json" --outdir "<save>" \
      --confirm-free-download [--browser chrome]

Single-URL mode:

  python scripts/download.py <url> --name "Artist - Title (Clean)" \
      --outdir "<save>" --confirm-free-download
"""

from __future__ import annotations

import argparse
import glob as globmod
import json
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import urlparse

try:
    from yt_dlp import YoutubeDL
except ImportError:  # reported as an install hint in main()
    YoutubeDL = None

ALLOWED_HOSTS = ("audiomack.com", "soundcloud.com", "bandcamp.com")


def host_allowed(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


def sanitize(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", name or "")
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name or "track"


def _taken(outdir: Path, base: str) -> bool:
    return bool(globmod.glob(str(outdir / (globmod.escape(base) + ".*"))))


def unique_base(outdir: Path, base: str) -> str:
    """Never overwrite: 'Name' -> 'Name (2)' -> 'Name (3)' ... (SKILL.md rule)."""
    if not _taken(outdir, base):
        return base
    n = 2
    while _taken(outdir, f"{base} ({n})"):
        n += 1
    return f"{base} ({n})"


def print_skipped(skipped: dict) -> None:
    """Final report: what was skipped and why (owned / buy-only / no source found)."""
    total = sum(len(v) for v in skipped.values())
    if not total:
        return
    print(f"Skipped: {total}")
    for reason, items in skipped.items():
        if items:
            names = ", ".join(items[:8]) + (" …" if len(items) > 8 else "")
            print(f"  {reason}: {len(items)} — {names}")


def progress_hook(status: dict) -> None:
    """Live percent on stderr - only when interactive, so redirected logs stay clean."""
    if not sys.stderr.isatty():
        return
    if status.get("status") == "downloading":
        total = status.get("total_bytes") or status.get("total_bytes_estimate") or 0
        got = status.get("downloaded_bytes") or 0
        if total:
            sys.stderr.write(f"\r    {got * 100 // total:3d}%")
        else:
            sys.stderr.write(f"\r    {got // 1024} KiB")
    elif status.get("status") == "finished":
        sys.stderr.write("\r" + " " * 16 + "\r")


def browser_tuple(spec: str):
    """yt-dlp's BROWSER[:PROFILE] syntax -> the library's cookiesfrombrowser tuple."""
    browser, _, profile = spec.partition(":")
    return (browser.strip(), profile.strip() or None)


def normalize_quality(spec: str) -> str:
    """Mirror the CLI's --audio-quality normalization: it strips a trailing K
    ('320K' -> '320') before float_or_none() turns the value into `-b:a 320k`.
    Without this the postprocessor sees float('320K') = None and ffmpeg falls
    back to its 128k default."""
    return (spec or "").strip().strip("k").strip("K")


def download_one(url, outdir: Path, base: str, quality: str, browser):
    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(outdir / (base + ".%(ext)s")),
        "noplaylist": True,
        "overwrites": False,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "progress_hooks": [progress_hook],
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": normalize_quality(quality),
        }],
    }
    if browser:
        opts["cookiesfrombrowser"] = browser_tuple(browser)

    failure = None
    try:
        with YoutubeDL(opts) as ydl:
            ydl.download([url])
    except Exception as exc:  # DownloadError for almost everything - the on-disk
        failure = str(exc)   # check below decides, same as the old CLI version.

    produced = outdir / (base + ".mp3")
    if produced.exists():
        return True, produced
    leftovers = [p for p in globmod.glob(str(outdir / (globmod.escape(base) + ".*")))
                 if not p.lower().endswith(".mp3")]
    if leftovers:
        return False, f"postprocessing failed (is ffmpeg on PATH?) - leftover {Path(leftovers[0]).name}"
    err = (failure or "").strip().splitlines()
    return False, (err[-1][:200] if err else "download failed (no file produced)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Fetch missing dj-search tracks from legal free-download sources only.")
    ap.add_argument("urls", nargs="*", help="single-URL mode: source page URL(s)")
    ap.add_argument("--manifest", help="candidates.json to process and update in place")
    ap.add_argument("--have", help="have_pc.json - owned candidates are skipped")
    ap.add_argument("--outdir", required=True, help="where MP3s are written (the save path)")
    ap.add_argument("--name", help="single-URL mode: file name, e.g. 'Artist - Title (Clean)'")
    ap.add_argument("--quality", default="320K", help="MP3 bitrate for the transcode (default 320K)")
    ap.add_argument("--browser", help="load cookies from this browser, e.g. chrome or "
                                      "'chrome:Profile 2' (close Chrome first)")
    ap.add_argument("--confirm-free-download", action="store_true",
                    help="confirm each source offers a free/licensed download (SKILL.md step 5)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if not args.manifest and not args.urls:
        ap.error("give --manifest or one or more URLs")
    if not args.dry_run and not args.confirm_free_download:
        raise SystemExit(
            "Refusing to download without --confirm-free-download.\n"
            "Only fetch tracks whose page actually offers a free/licensed download\n"
            "(Audiomack / SoundCloud Free Download / Bandcamp / promo gate) - see SKILL.md.")

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    if not args.dry_run:
        if YoutubeDL is None:
            raise SystemExit("yt-dlp is not installed for this Python. Install it:\n"
                             "  python -m pip install -U yt-dlp")
        if shutil.which("ffmpeg") is None:
            raise SystemExit("ffmpeg not found on PATH - needed for the MP3 320 transcode.")

    data = None
    manifest_path = None
    jobs = []  # (label, url, requested_name, manifest_entry_or_None)
    skipped = {
        "owned (already on PC/Drive)": [],
        "already downloaded": [],
        "buy_only (no legal free source)": [],
        "failed earlier (not retried)": [],
        "no download_url resolved": [],
    }
    if args.manifest:
        manifest_path = Path(args.manifest)
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise SystemExit("manifest must be a candidates.json array")
        owned_ids = set()
        if args.have and Path(args.have).exists():
            have = json.loads(Path(args.have).read_text(encoding="utf-8"))
            owned_ids = {r.get("id") for r in have.get("results", []) if r.get("owned")}
        for entry in data:
            if not isinstance(entry, dict):
                continue
            label = f"{entry.get('artist', '')} - {entry.get('title', '')}".strip(" -")
            status = (entry.get("download_status") or "missing").strip().lower()
            if entry.get("id") in owned_ids or status == "owned":
                skipped["owned (already on PC/Drive)"].append(label)
                continue
            if status in ("downloaded", "buy_only", "failed"):
                skipped[{"downloaded": "already downloaded",
                         "buy_only": "buy_only (no legal free source)",
                         "failed": "failed earlier (not retried)"}[status]].append(label)
                continue
            url = (entry.get("download_url") or "").strip()
            if not url:
                skipped["no download_url resolved"].append(label)
                continue
            variant = (entry.get("variant") or "").strip()
            name = label + (f" ({variant.capitalize()})" if variant else "")
            jobs.append((label, url, name, entry))
    else:
        if not args.name:
            ap.error("single-URL mode needs --name (e.g. --name \"Artist - Title (Clean)\")")
        for url in args.urls:
            jobs.append((args.name, url, args.name, None))

    if not jobs:
        print("Nothing to download (no still-missing candidate has a download_url).")
        print_skipped(skipped)
        return 0

    ok_count = fail_count = refused = 0
    downloaded, failed_list = [], []
    for label, url, name, entry in jobs:
        if not host_allowed(url):
            host = urlparse(url).hostname or url
            print(f"REFUSED  {label}: {host} is not a legal free-download source "
                  f"(allowed: {', '.join(ALLOWED_HOSTS)}) - see SKILL.md legal line.")
            refused += 1
            continue
        if args.dry_run:
            print(f"WOULD DOWNLOAD  {label}\n  {url}\n  -> {outdir / (sanitize(name) + '.mp3')}")
            continue
        base = unique_base(outdir, sanitize(name))
        print(f"Downloading  {label}  <- {url}")
        ok, result = download_one(url, outdir, base, args.quality, args.browser)
        if ok:
            ok_count += 1
            downloaded.append((label, str(result)))
            print(f"  ok -> {result}")
            if entry is not None:
                entry["download_status"] = "downloaded"
                entry["local_path"] = str(result)
                entry["error"] = ""
        else:
            fail_count += 1
            failed_list.append((label, str(result)))
            print(f"  FAILED: {result}")
            if entry is not None:
                entry["download_status"] = "failed"
                entry["error"] = str(result)

    if manifest_path is not None and data is not None and not args.dry_run:
        manifest_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                                 encoding="utf-8")
        print(f"Updated {manifest_path}")

    if not args.dry_run:
        if downloaded:
            print("Downloaded:")
            for label, path in downloaded:
                print(f"  {label} -> {path}")
        if failed_list:
            print("Failed:")
            for label, err in failed_list:
                print(f"  {label}: {err}")
        print(f"Done: {ok_count} downloaded, {fail_count} failed, {refused} refused.")
        print_skipped(skipped)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main())
