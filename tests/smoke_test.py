#!/usr/bin/env python3
"""Smoke test for dj-search (offline - no network, no audio decoding).

Builds a synthetic library in a temp dir:
  library/Example Artist - Lagos Night Drive.mp3                 (ID3v2.3 tags)
  library/2026/02 Example Artist - Lagos Night Drive (Clean).mp3 (filename only)
  library/Unrelated File.mp3                                     (tags, no candidate)
  drive.txt: Music/Crates/Drive Artist - Cloud Crate.mp3         (rclone-style listing)
plus a 3-entry candidates.json, then asserts:
  - cand-001 is owned on the PC with high confidence
  - cand-002 is owned via the Drive listing only
  - cand-003 is missing
  - write_crate produces the CSV + M3U8; the playlist holds the local file only
  - download.source_kind() classifies legal/youtube/other URLs, and
    resolve_sources.youtube_match_score() accepts artist/topic/duration-consistent
    results while rejecting wrong edits and unverifiable strangers (no network)

Run:  python tests/smoke_test.py
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN = ROOT / "scripts" / "scan_library.py"
CRATE = ROOT / "scripts" / "write_crate.py"

FAKE_AUDIO = b"\xff\xfb\x90\x00" + b"\x00" * 400  # header-ish bytes; never decoded


def syncsafe(n: int) -> bytes:
    return bytes(((n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F))


def id3v23(tags: dict) -> bytes:
    frames = b""
    for fid, value in tags.items():
        payload = b"\x00" + value.encode("latin-1", "replace")
        frames += fid.encode("ascii") + len(payload).to_bytes(4, "big") + b"\x00\x00" + payload
    return b"ID3\x03\x00\x00" + syncsafe(len(frames)) + frames


def write_mp3(path: Path, tags=None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((id3v23(tags) if tags else b"") + FAKE_AUDIO)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="dj-search-smoke-"))
    try:
        lib = tmp / "library"
        write_mp3(lib / "Example Artist - Lagos Night Drive.mp3",
                  {"TIT2": "Lagos Night Drive", "TPE1": "Example Artist"})
        write_mp3(lib / "2026" / "02 Example Artist - Lagos Night Drive (Clean).mp3")
        write_mp3(lib / "Unrelated File.mp3",
                  {"TIT2": "Unrelated File", "TPE1": "Nobody At All"})
        (tmp / "drive.txt").write_text("Music/Crates/Drive Artist - Cloud Crate.mp3\n",
                                       encoding="utf-8")

        candidates = [
            {"id": "cand-001", "artist": "Example Artist", "title": "Lagos Night Drive",
             "genre": "Afrobeats", "why_trending": "test", "release_date": "2026-09-30",
             "bpm": 104, "key": "8A", "download_status": "missing"},
            {"id": "cand-002", "artist": "Drive Artist", "title": "Cloud Crate",
             "genre": "Amapiano", "why_trending": "test", "release_date": "2026-10-01",
             "bpm": 112, "key": "5B", "download_status": "missing"},
            {"id": "cand-003", "artist": "Missing One", "title": "Nowhere Tune",
             "genre": "Afrobeats", "why_trending": "test", "release_date": "2026-10-02",
             "download_status": "missing"},
        ]
        cpath = tmp / "candidates.json"
        cpath.write_text(json.dumps(candidates, indent=2), encoding="utf-8")

        have = tmp / "have_pc.json"
        subprocess.run([sys.executable, str(SCAN), "--candidates", str(cpath),
                        "--roots", str(lib), "--drive-listing", str(tmp / "drive.txt"),
                        "--out", str(have), "--quiet"], check=True)
        results = {r["id"]: r for r in json.loads(have.read_text(encoding="utf-8"))["results"]}

        r1 = results["cand-001"]
        assert r1["owned"] and "pc" in r1["owned_from"], r1
        assert r1["confidence"] >= 0.95, r1
        assert "Lagos Night Drive" in (r1["match_path"] or ""), r1

        r2 = results["cand-002"]
        assert r2["owned"] and r2["owned_from"] == ["drive"], r2
        assert r2["matched_by"] == "drive", r2

        assert not results["cand-003"]["owned"], results["cand-003"]

        outdir = tmp / "out"
        subprocess.run([sys.executable, str(CRATE), "--candidates", str(cpath),
                        "--have", str(have), "--outdir", str(outdir),
                        "--window", "7", "--date", "2026-10-06", "--quiet"], check=True)
        csv_path = outdir / "crate_7d_2026-10-06.csv"
        m3u_path = outdir / "crate_7d_2026-10-06.m3u8"
        assert csv_path.exists() and m3u_path.exists(), "crate files missing"
        csv_text = csv_path.read_text(encoding="utf-8-sig")
        assert "Lagos Night Drive" in csv_text and "Nowhere Tune" in csv_text, csv_text
        m3u = m3u_path.read_text(encoding="utf-8")
        assert "Lagos Night Drive" in m3u, m3u
        assert "Cloud Crate" not in m3u, "cloud-only track must not claim a local path"

        # --- script guards + YouTube matching (offline, no network) -----------
        sys.path.insert(0, str(ROOT / "scripts"))
        import download as dl          # noqa: E402
        import resolve_sources as rs   # noqa: E402

        assert dl.source_kind("https://soundcloud.com/a/b") == "legal"
        assert dl.source_kind("https://artist.bandcamp.com/track/x") == "legal"
        assert dl.source_kind("https://www.youtube.com/watch?v=x") == "youtube"
        assert dl.source_kind("https://youtu.be/x") == "youtube"
        assert dl.source_kind("https://open.spotify.com/track/x") == ""
        assert dl.source_kind("https://naijaloaded.com.ng/x") == ""

        def ytv(title, channel, duration, views=0, live=None):
            return {"title": title, "channel": channel, "duration": duration,
                    "view_count": views, "live_status": live,
                    "webpage_url": "https://www.youtube.com/watch?v=test"}

        artist, title = "Rema", "Calm Down"
        topic = ytv("Rema - Calm Down", "Rema - Topic", 237)
        own = ytv("Rema - Calm Down (Official Music Video)", "Rema", 237)
        label = ytv("Rema - Calm Down (Official Video)", "Mavin Records", 237, 5_000_000)
        slowed = ytv("Rema - Calm Down (Sped Up)", "Rema", 190)
        long_edit = ytv("Rema - Calm Down", "Rema", 301)
        live = ytv("Rema - Calm Down (Live)", "Rema", 237, live="is_live")
        stranger = ytv("Calm Down (Official Audio)", "Afrobeats Central", 237)

        s_topic, c_topic = rs.youtube_match_score(artist, title, topic, 237)
        s_own, _ = rs.youtube_match_score(artist, title, own, 237)
        assert s_topic > s_own > 0 and c_topic == "high"
        s_label, c_label = rs.youtube_match_score(artist, title, label, 237)
        assert s_label > 0 and c_label == "high"       # duration vouches for label uploads
        s_lab2, c_lab2 = rs.youtube_match_score(artist, title, label, None)
        assert s_lab2 > 0 and c_lab2 == "medium"       # no reference: artist named in title
        assert rs.youtube_match_score(artist, title, slowed, 237) == (0, "")
        assert rs.youtube_match_score(artist, title, long_edit, 237) == (0, "")
        assert rs.youtube_match_score(artist, title, live, 237) == (0, "")
        assert rs.youtube_match_score(artist, title, stranger, None) == (0, "")
        assert rs.youtube_match_score(artist, title, stranger, 237)[0] > 0  # duration-only OK

        print("PASS - scan de-dup (pc / drive / missing), crate outputs, "
              "download allowlist and YouTube match scoring verified")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
