---
name: dj-search
description: A crate-digging assistant for DJs. Finds what is trending in the last 7/14/30 days (Afrobeats, Amapiano, Nigerian/naija pop, Gen-Z TikTok sounds, or any genre you name), checks whether you already own each track on your PC and your Google Drive so you never re-download, pulls the ones you are missing from legal free sources (Audiomack, SoundCloud, Bandcamp, artist/label promo, your DJ pools) as clean or dirty MP3s, and hands you a ranked crate with set-placement and BPM/key notes. Use this whenever the user types /dj-search or asks to find trending songs, build a crate, update their music library, dig for new Afrobeats/Amapiano/naija tracks, find DJ-ready downloads, or check what new music they are missing.
---

# dj-search

A crate-digging run for a working DJ. One command: find what is trending now, skip what you
already have (on the PC **and** on Drive), download the rest from legal free sources, and give a
DJ's opinion on how to play it.

Built for the Nigerian / Afrobeats / Amapiano / Gen-Z scene by default, but the genre is just an
input — set it to anything.

## Legal line (do not cross it — it keeps this repo alive and keeps the DJ safe)

This skill downloads **only** from sources that offer the track for free download, or that the DJ
is licensed for:

- **Audiomack** — free download when the artist enabled it (most Afrobeats/Amapiano promo lives here).
- **SoundCloud** — tracks with a "Free Download" / "Buy" → free link.
- **Bandcamp** — free or name-your-price downloads.
- **Official artist/label promo** — Linktree/Hypeddit/ToneDen gates, newsletter drops.
- **The DJ's own pool subscriptions** — BPM Supreme, DJcity, ZIPDJ, DigitalDJPool, Beatport (paid, logged in).

Do **not** rip audio from YouTube, Spotify, Apple Music or Boomplay, and do **not** touch
piracy/warez MP3 sites. Those are used for *discovery only* (seeing what charts), never as a
download source. When the only place a track exists is streaming/paywalled, say so and point the
DJ at where to buy or pull it — do not download it. If the user explicitly asks to rip a
streaming-only track, decline that track and offer the legal alternative; keep doing the rest.

## The run, in order

### 1. Ask two things (the skill always asks before digging)

- **Window** — trending over the last **7**, **14**, or **30** days? (default 7)
- **Save path** — where do the MP3s and the crate file go? (e.g. `D:\DJ\Crates\2026-10`)

Then confirm two more with sensible defaults, in one line, and move on unless they correct you:

- **Genre/vibe** — default: *Afrobeats + Amapiano + Nigerian trending + Gen-Z TikTok*. Accept anything.
- **Count** — default: top **25** candidates. **Clean or dirty** — default: **clean** (radio edit) where it exists.

### 2. Discover what's trending (browser + web search — discovery only)

Use the owner's live Chrome via chrome-devtools (`--autoConnect`; never kill/relaunch/copy it — see
`use-live-chrome`). Pull from several charts and intersect, so you get what is *actually* moving, not
one site's bias. Sources and exact reading tips are in `references/sources.md`. In short:

- TurnTable Charts (Nigeria's official chart), Apple Music NG Top 100, Spotify NG Top 50 + Viral 50,
  Audiomack Trending (Afrobeats / Amapiano), Boomplay NG, Shazam NG Top 200, TikTok trending sounds.
- Keep only releases/entries inside the chosen window. Favour tracks rising on 2+ sources.
- De-duplicate across sources on normalised `artist - title`.

Produce a candidate list: `id` (`cand-001`…), `artist`, `title`, `genre`, `why_trending` (which
charts, rising/new), `release_date` (ISO `YYYY-MM-DD`), `bpm`/`key` when a source gives them,
`sources`, and `variant` (`clean`/`dirty`/empty). Later steps fill in `download_status` (`missing` →
`downloaded`/`buy_only`/`failed`), `download_url`, `local_path`, `recommendation`, `error`. Write it
to `<save_path>/_dj-search/candidates.json`.

### 3. Check what you already own — the whole PC first

Run the library scanner across the entire PC and any plugged-in flash drives, plus the save path:

```
python scripts/scan_library.py --candidates "<save_path>/_dj-search/candidates.json" \
  --all-drives --roots "<save_path>" \
  --out "<save_path>/_dj-search/have_pc.json"
```

`--all-drives` walks every fixed disk and every removable/flash drive (system folders — Windows,
Program Files, AppData, node_modules — are pruned automatically). Add extra `--roots` for anything
it cannot see on its own (e.g. a network share). A whole-PC first run takes a few minutes; that's fine.

It walks for audio files (`.mp3 .wav .flac .m4a .aac .ogg .opus .aiff`), reads ID3v2/ID3v1, MP4/M4A
and FLAC tags (filename fallback) and normalises names (drops
`feat.`, brackets, punctuation, case) and fuzzy-matches each candidate. Output marks every
candidate `owned` / `missing` with the matched file path and a confidence score. Treat <0.82 as a
soft match — show it, let the DJ decide, do not silently skip.

### 4. Check Google Drive — so you don't re-download what's already in the cloud

In order of preference (details in `references/sources.md`):

1. **Google Drive for Desktop mounted drive** — if a Drive letter is mounted (often `G:`), just add
   it as another `--roots` entry in step 3's command. Fastest and most reliable.
2. **rclone** — if the DJ has an `rclone` remote for Drive, list it and match offline:
   `rclone lsf gdrive: --recursive --include "*.{mp3,wav,flac,m4a,aac}"` → feed to the scanner's
   `--drive-listing` flag.
3. **Browser** — otherwise open `drive.google.com`, search each missing track title, and read the
   results. Slower; use only for the ones still missing after the PC scan.

Merge PC + Drive into one `owned` set. Only genuinely-missing tracks go to step 5.

### 5. Download the missing ones — legal sources only

For each missing track, resolve a **legal** download URL (section order above) and fetch it.
See `references/sources.md` for the per-source recipe. Summary:

- Prefer the **clean/radio** version when the DJ chose clean and one exists; else the explicit edit.
- Audiomack/SoundCloud/Bandcamp: open the track page, confirm a real Download/Free affordance, use it.
  Files land in the browser's Downloads folder; the skill then moves+renames them.
- `scripts/download.py` wraps `yt-dlp` for sources that *offer* a free download (SoundCloud/Bandcamp
  free links, Audiomack). It is **not** for streaming/paywalled rips — see the legal line. Run it as
  `python scripts/download.py --manifest "<save_path>/_dj-search/candidates.json" --have "<save_path>/_dj-search/have_pc.json" --outdir "<save_path>" --confirm-free-download`
  (it refuses any URL outside its allowlist and updates `download_status`/`local_path` per track).
  Its final report lists every track **downloaded** (with path) and everything **skipped** (owned /
  buy_only / no source found) — relay both to the DJ.
- Name every file `Artist - Title (Clean).mp3` / `(Dirty).mp3`, 320 kbps where the source allows,
  written straight into `<save_path>`.
- If no legal free source exists, mark the track `buy_only` with where to get it (pool/Beatport/iTunes)
  and move on. Never fail the whole run over one track.

### 6. Recommend like a DJ, not a database

Close with a short, opinionated read — this is the part a DJ actually wants:

- **Crate picks** — the 5-8 that are worth adding now and why (peak-time, opener, transition glue).
- **Group by BPM and key** so sets build themselves; flag easy harmonic/mashup pairs.
- **Rising vs peaked** — call what is still climbing so they get ahead of it, and what is already
  saturated on every dancefloor.
- Keep it real and specific. No filler, no hype copy.

### 7. Save the crate + report

- Write `scripts/write_crate.py` output: `<save_path>/_dj-search/crate_<window>d_<date>.csv` and `.m3u8`
  (playlist of the downloaded files, import-ready into Serato/rekordbox/Engine).
- Print a compact table: downloaded / already-owned (PC vs Drive) / buy-only / failed, plus the
  recommendations. Then stop.

## Never

- Never download from YouTube/Spotify/Apple/Boomplay or piracy sites. Discovery only.
- Never enter the DJ's passwords or solve captchas for them — pause and ask them to log in.
- Never kill, relaunch or copy Chrome (`use-live-chrome`).
- Never overwrite an existing file in the save path without renaming (` (2)`); never delete library files.
- Never let one unavailable track abort the run.
