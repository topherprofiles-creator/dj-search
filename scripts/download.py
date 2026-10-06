#!/usr/bin/env python3
"""download.py - legal free-download fetcher for dj-search (yt-dlp wrapper).

Only for sources that actually offer the track for free or under the DJ's own
license: Audiomack (artist enabled), SoundCloud (Free Download enabled),
Bandcamp (free / name-your-price). Every other host is refused - YouTube,
Spotify, Apple Music, Boomplay and piracy sites are discovery-only (see the
legal line in SKILL.md). The allowlist below is enforced per URL.

Requirements: yt-dlp (`python -m pip install -U yt-dlp`); ffmpeg on PATH for
the MP3 320 transcode.

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
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

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


def find_ytdlp():
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    try:
        subprocess.run([sys.executable, "-m", "yt_dlp", "--version"],
                       check=True, capture_output=True)
        return [sys.executable, "-m", "yt_dlp"]
    except Exception:
        return None


def download_one(url, outdir: Path, base: str, quality: str, browser, ytdlp):
    cmd = ytdlp + ["-x", "--audio-format", "mp3", "--audio-quality", quality,
                   "--no-playlist", "--no-overwrites", "--newline",
                   "-o", str(outdir / (base + ".%(ext)s")), url]
    if browser:
        cmd += ["--cookies-from-browser", browser]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    produced = outdir / (base + ".mp3")
    if proc.returncode == 0 and produced.exists():
        return True, produced
    leftovers = [p for p in globmod.glob(str(outdir / (globmod.escape(base) + ".*")))
                 if not p.lower().endswith(".mp3")]
    if leftovers:
        return False, f"postprocessing failed (is ffmpeg on PATH?) - leftover {Path(leftovers[0]).name}"
    err = (proc.stderr or proc.stdout or "").strip().splitlines()
    return False, (err[-1][:200] if err else f"yt-dlp exited {proc.returncode}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Fetch missing dj-search tracks from legal free-download sources only.")
    ap.add_argument("urls", nargs="*", help="single-URL mode: source page URL(s)")
    ap.add_argument("--manifest", help="candidates.json to process and update in place")
    ap.add_argument("--have", help="have_pc.json - owned candidates are skipped")
    ap.add_argument("--outdir", required=True, help="where MP3s are written (the save path)")
    ap.add_argument("--name", help="single-URL mode: file name, e.g. 'Artist - Title (Clean)'")
    ap.add_argument("--quality", default="320K", help="MP3 quality for yt-dlp (default 320K)")
    ap.add_argument("--browser", help="pass --cookies-from-browser, e.g. chrome (close Chrome first)")
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
        ytdlp = find_ytdlp()
        if ytdlp is None:
            raise SystemExit("yt-dlp not found. Install it:  python -m pip install -U yt-dlp")
        if shutil.which("ffmpeg") is None:
            raise SystemExit("ffmpeg not found on PATH - needed for the MP3 320 transcode.")

    data = None
    manifest_path = None
    jobs = []  # (label, url, requested_name, manifest_entry_or_None)
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
            status = (entry.get("download_status") or "missing").strip().lower()
            if entry.get("id") in owned_ids or status in ("owned", "downloaded", "buy_only", "failed"):
                continue
            url = (entry.get("download_url") or "").strip()
            if not url:
                continue
            label = f"{entry.get('artist', '')} - {entry.get('title', '')}".strip(" -")
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
        return 0

    ok_count = fail_count = refused = 0
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
        ok, result = download_one(url, outdir, base, args.quality, args.browser, ytdlp)
        if ok:
            ok_count += 1
            print(f"  ok -> {result}")
            if entry is not None:
                entry["download_status"] = "downloaded"
                entry["local_path"] = str(result)
                entry["error"] = ""
        else:
            fail_count += 1
            print(f"  FAILED: {result}")
            if entry is not None:
                entry["download_status"] = "failed"
                entry["error"] = str(result)

    if manifest_path is not None and data is not None and not args.dry_run:
        manifest_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                                 encoding="utf-8")
        print(f"Updated {manifest_path}")

    if not args.dry_run:
        print(f"Done: {ok_count} downloaded, {fail_count} failed, {refused} refused.")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main())
