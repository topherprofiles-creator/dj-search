#!/usr/bin/env python3
"""download.py - download fetcher for dj-search (yt-dlp library).

Fetches what the resolver wrote: artist-enabled free sources first - SoundCloud
(Free Download enabled), Bandcamp (free / name-your-price) - plus, when the
resolver matched one, a YouTube fallback URL (download_source "youtube:<channel>";
matching rules in resolve_sources.py and SKILL.md step 5). Every other host is
still refused - Spotify, Apple Music, Boomplay, Audiomack, leech sites. The
allowlist below is enforced per URL; --no-youtube switches back to
legal-sources-only.

Audiomack was dropped from the allowlist 2026-10: its web player no longer
offers per-song downloads at all (downloads moved to the mobile app / Plus) and
yt-dlp's Audiomack endpoint is dead.

Runs yt-dlp in-process via the `yt_dlp` Python package (no CLI subprocess), so
interactive runs show a live download percentage on stderr.

Requirements: yt-dlp installed for this interpreter
(`python -m pip install -U yt-dlp`); ffmpeg on PATH for the MP3 320 transcode.

Manifest mode (SKILL.md step 5) - downloads every candidate that is still
missing and has a download_url, updating the manifest in place:

  python scripts/download.py --manifest "<save>/_dj-search/candidates.json" \
      --have "<save>/_dj-search/have_pc.json" --outdir "<save>" \
      --confirm-free-download [--jobs N] [--browser chrome]

Single-URL mode:

  python scripts/download.py <url> --name "Artist - Title (Clean)" \
      --outdir "<save>" --confirm-free-download
"""

from __future__ import annotations

import argparse
import glob as globmod
import json
import os
import random
import re
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

try:
    from yt_dlp import YoutubeDL
except ImportError:  # reported as an install hint in main()
    YoutubeDL = None

LEGAL_HOSTS = ("soundcloud.com", "bandcamp.com")
YOUTUBE_HOSTS = ("youtube.com", "youtu.be", "music.youtube.com")


def source_kind(url: str) -> str:
    """Classify a URL: 'legal' (artist-enabled free-download sources), 'youtube'
    (the resolver's matched fallback), or '' for everything else (refused)."""
    host = (urlparse(url).hostname or "").lower()
    for kind, hosts in (("legal", LEGAL_HOSTS), ("youtube", YOUTUBE_HOSTS)):
        if any(host == h or host.endswith("." + h) for h in hosts):
            return kind
    return ""


def sanitize(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", name or "")
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:120].rstrip(" .") or "track"  # Windows component limit headroom


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


def save_manifest(manifest_path: Path, data) -> None:
    """Atomic replace - a crash mid-write never corrupts the shared manifest."""
    tmp = manifest_path.with_name(manifest_path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, manifest_path)


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


def download_one(url, outdir: Path, base: str, quality: str, browser, show_progress: bool = True):
    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(outdir / (base + ".%(ext)s")),
        "noplaylist": True,
        "overwrites": False,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "progress_hooks": [progress_hook] if show_progress else [],
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
    if not produced.exists() and re.search(
            r"HTTP Error 403|timed out|Temporary failure|Connection reset|Read error",
            failure or ""):
        time.sleep(4)  # transient by nature - the live run saw a 403 clear on attempt 2
        try:
            with YoutubeDL(opts) as ydl:
                ydl.download([url])
            failure = None
        except Exception as exc:
            failure = str(exc)

    if produced.exists():
        return True, produced
    leftovers = [p for p in globmod.glob(str(outdir / (globmod.escape(base) + ".*")))
                 if not p.lower().endswith(".mp3")]
    parts = [p for p in leftovers if p.lower().endswith((".part", ".ytdl"))]
    for p in parts:  # a half-fetched file must not poison the next run's names
        try:
            Path(p).unlink()
        except OSError:
            pass
    real = [p for p in leftovers if p not in parts]
    if parts and not real:
        return False, "download interrupted (partial file cleaned up - rerun with --retry-failed to retry)"
    if real:
        return False, f"postprocessing failed (is ffmpeg on PATH?) - leftover {Path(real[0]).name}"
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
    ap.add_argument("--no-youtube", action="store_true",
                    help="legal sources only - skip tracks resolved via the YouTube fallback")
    ap.add_argument("--jobs", type=int, default=6,
                    help="parallel downloads (default 6; 1 = serial)")
    ap.add_argument("--confirm-free-download", action="store_true",
                    help="confirm each source offers a free/licensed download (SKILL.md step 5)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--retry-failed", action="store_true",
                    help="re-attempt candidates previously marked failed (no-track-left-behind loop)")
    args = ap.parse_args(argv)

    if not args.manifest and not args.urls:
        ap.error("give --manifest or one or more URLs")
    if not args.dry_run and not args.confirm_free_download:
        raise SystemExit(
            "Refusing to download without --confirm-free-download.\n"
            "Confirm the sources first - artist-enabled free downloads, or the\n"
            "matched YouTube fallback the resolver wrote (SKILL.md step 5).")

    outdir = Path(args.outdir).resolve()
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
        "promo (gate handed to the DJ)": [],
        "buy_only (no legal free source)": [],
        "failed earlier (not retried)": [],
        "no download_url resolved": [],
        "youtube fallback disabled (--no-youtube)": [],
    }
    if args.manifest:
        manifest_path = Path(args.manifest)
        data = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, list):
            raise SystemExit("manifest must be a candidates.json array")
        owned_ids = set()
        if args.have and Path(args.have).exists():
            have = json.loads(Path(args.have).read_text(encoding="utf-8-sig"))
            owned_ids = {str(r.get("id")) for r in have.get("results", []) if r.get("owned")}
        for entry in data:
            if not isinstance(entry, dict):
                continue
            label = f"{entry.get('artist', '')} - {entry.get('title', '')}".strip(" -")
            status = (entry.get("download_status") or "missing").strip().lower()
            if str(entry.get("id")) in owned_ids or status == "owned":
                skipped["owned (already on PC/Drive)"].append(label)
                continue
            local = (entry.get("local_path") or "").strip()
            if status == "downloaded" or (local and Path(local).exists()):
                skipped["already downloaded"].append(label)
                continue
            if status == "promo":
                skipped["promo (gate handed to the DJ)"].append(label)
                continue
            if status == "buy_only":
                skipped["buy_only (no legal free source)"].append(label)
                continue
            if status == "failed" and not args.retry_failed:
                skipped["failed earlier (not retried)"].append(label)
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

    # Triage + name reservation runs single-threaded, so workers never race on names.
    run_jobs = []
    reserved = set()
    for label, url, name, entry in jobs:
        kind = source_kind(url)
        if kind == "youtube" and args.no_youtube:
            skipped["youtube fallback disabled (--no-youtube)"].append(label)
            continue
        if not kind:
            host = urlparse(url).hostname or url
            print(f"REFUSED  {label}: {host} is not an allowed source "
                  f"(free: {', '.join(LEGAL_HOSTS)}; fallback: matched YouTube) - see SKILL.md.")
            refused += 1
            continue
        if args.dry_run:
            print(f"WOULD DOWNLOAD  {label}\n  {url}\n  -> {outdir / (sanitize(name) + '.mp3')}")
            continue
        base = unique_base(outdir, sanitize(name))
        n = 2
        while base in reserved:  # identical labels in one run must not share a name
            base = f"{sanitize(name)} ({n})"
            n += 1
        reserved.add(base)
        run_jobs.append((label, url, kind, base, entry))

    def finish(label, entry, ok, result):
        nonlocal ok_count, fail_count
        if ok:
            ok_count += 1
            downloaded.append((label, str(result)))
            print(f"  ok -> {label}: {result}")
            if entry is not None:
                entry["download_status"] = "downloaded"
                entry["local_path"] = str(result)
                entry["error"] = ""
        else:
            fail_count += 1
            failed_list.append((label, str(result)))
            print(f"  FAILED: {label}: {result}")
            if entry is not None:
                entry["download_status"] = "failed"
                entry["error"] = str(result)
        if entry is not None and manifest_path is not None:
            save_manifest(manifest_path, data)  # keep progress on interrupt

    workers = max(1, min(args.jobs, len(run_jobs))) if run_jobs else 1
    if workers <= 1:
        for label, url, kind, base, entry in run_jobs:
            tag = "  (YouTube fallback)" if kind == "youtube" else ""
            print(f"Downloading{tag}  {label}  <- {url}")
            ok, result = download_one(url, outdir, base, args.quality, args.browser)
            finish(label, entry, ok, result)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            pending = {}
            for label, url, kind, base, entry in run_jobs:
                time.sleep(random.uniform(0.2, 1.0))  # stagger starts, be polite
                tag = "  (YouTube fallback)" if kind == "youtube" else ""
                print(f"Downloading{tag}  {label}  <- {url}")
                fut = pool.submit(download_one, url, outdir, base, args.quality,
                                  args.browser, False)
                pending[fut] = (label, entry)
            for fut in as_completed(pending):
                label, entry = pending[fut]
                try:
                    ok, result = fut.result()
                except Exception as exc:  # a worker crash is one failed track, not the run
                    ok, result = False, f"{type(exc).__name__}: {exc}"
                finish(label, entry, ok, result)

    if manifest_path is not None and data is not None and not args.dry_run:
        save_manifest(manifest_path, data)
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
    print_skipped(skipped)  # dry-runs report skips too - nothing may go unsaid
    return 1 if fail_count else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main())
