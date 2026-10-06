---
name: dj-search
description: A crate-digging assistant for DJs. Finds what is trending in the last 7/14/30 days (Afrobeats, Amapiano, Nigerian/naija pop, Gen-Z TikTok sounds, or any genre you name), checks whether you already own each track on your PC and your Google Drive so you never re-download, downloads the ones you are missing — artist-enabled free sources first (SoundCloud, Bandcamp, artist/label promo, your DJ pools), then a matched YouTube audio fallback — as clean or dirty MP3s, and hands you a ranked crate with set-placement and BPM/key notes. Use this whenever the user types /dj-search or asks to find trending songs, build a crate, update their music library, dig for new Afrobeats/Amapiano/naija tracks, find DJ-ready downloads, or check what new music they are missing.
---

# dj-search

A crate-digging run for a working DJ. One command: find what is trending now, skip what you
already have (on the PC **and** on Drive), download the rest (artist-enabled free sources first,
then a matched YouTube audio fallback), and give a DJ's opinion on how to play it.

Built for the Nigerian / Afrobeats / Amapiano / Gen-Z scene by default, but the genre is just an
input — set it to anything.


### 1. STOP — ask first (strict rule, no exceptions)

**Do nothing else until the DJ has answered: no chart reading, no browsing, no scanning, no
downloads — not even opening a page.** Ask these directly in the chat as plain numbered questions
and wait for the reply:

- **Where to save** — the save path for the MP3s and the crate file. You may offer a suggestion (the
  DJ's kit drive if one is known, otherwise `~/Music/dj-search/<date>`), but never assume it.
- **How many days** — trending over the last **7**, **14**, or **30** days?
- **Which genre** — the usual *Afrobeats + Amapiano + Nigerian trending + Gen-Z*, or **general**
  (whatever is charting overall), or any genre/taste the DJ names.

In the same ask, cover the finishing options (these may keep defaults if the DJ doesn't care):
top **25** candidates, and **clean** (radio edit) where it exists, else the explicit edit.

Only when the answers are in hand does step 2 begin.

### 2. Discover what's trending (browser + web search — discovery only)

Use the user's already-running Chrome via chrome-devtools (`--autoConnect`) — never close, kill,
relaunch or copy the browser profile. Pull from several charts and intersect, so you get what is
*actually* moving, not one site's bias. Sources and exact reading tips are in `references/sources.md`. In short:

- TurnTable Charts (Nigeria's official chart), Apple Music NG Top 100, Spotify NG Top 50 + Viral 50,
  Audiomack Trending (Afrobeats / Amapiano), Boomplay NG, Shazam NG Top 200, TikTok trending sounds.
- Keep only releases/entries inside the chosen window. Favour tracks rising on 2+ sources.
- De-duplicate across sources on normalised `artist - title`.

Produce a candidate list: `id` (`cand-001`…), `artist`, `title`, `genre`, `why_trending` (which
charts, rising/new), `release_date` (ISO `YYYY-MM-DD`), `bpm`/`key` when a source gives them,
`sources`, and `variant` (`clean`/`dirty`/empty). Add `duration_s` when a chart page shows it — the
resolver uses it to length-check its YouTube fallback. Later steps fill in `download_status`
(`missing` → `downloaded`/`promo`/`buy_only`/`failed`), `download_url`, `download_source` (set by the
resolver: `soundcloud:<uploader>` or `youtube:<channel>`), `match_confidence`, `local_path`,
`recommendation`, `error`. Write it to `<save_path>/_dj-search/candidates.json`.

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

### 5. Download the missing ones — free sources, YouTube, then the browser lookup

For each missing track, resolve a download URL and fetch it, in this order:

1. **Artist-enabled free sources** — SoundCloud Free Download, Bandcamp free / name-your-price,
   official promo gates. Check the artist's own profile (not just site search) — artist
   Linktree/bios/social posts often carry legit Free Download links. Recipes: `references/sources.md`.
2. **YouTube audio fallback (default on, `--no-youtube` disables)** — for tracks with no free source,
   the resolver searches YouTube via yt-dlp and matches the way sunnify-style downloaders do: the
   video title must carry the track (speed/remix/cover edits are rejected unless the candidate names
   them), the channel must look like the artist's own account or their `<Artist> - Topic` catalog,
   and when a reference duration is known (the artist's own SoundCloud upload — even a stream-only
   one — or the candidate's `duration_s`) the video length must agree within a few seconds. That
   length check is what rejects wrong edits of the right song. Matches are labeled
   `download_source: "youtube:<channel>"` in the manifest, `match_confidence` is recorded, and the
   downloader prints `(YouTube fallback)` for each so the report stays honest about where files came
   from. Tracks a previous run parked as `buy_only` are automatically reconsidered.
3. **Browser lookup (agent step — only for the resolver's no-match lines)** — when the resolver
   prints `NO ARTIST-ENABLED FREE SOURCE AND NO YOUTUBE MATCH`, do what a human would: search it
   yourself in the DJ's already-open Chrome (chrome-devtools `--autoConnect` — never close, kill,
   relaunch or copy the browser). Open `youtube.com/results?search_query=<artist title>`, prefer
   `<Artist> - Topic` / the artist's own channel, open the video and check the length on the watch
   page against the reference (`duration_s`, or the artist's SoundCloud length) — reject
   sped-up/slowed/remix/cover edits the same way the matcher would. Two equally good results → show
   the DJ both titles/channels and let them pick. Record the pick — no network, atomic manifest
   write — and fetch it through the **same** downloader, so it lands as `youtube-browser`,
   `match_confidence: "browser"`, and is reported like any other track:
   `python scripts/resolve_sources.py --candidates "<save_path>/_dj-search/candidates.json" --set-url "cand-007=<video URL>"`
   then the normal `download.py --manifest ...` run. (YouTube has no browser "save file" — the
   browser step finds and verifies the video; the downloader is what pulls the MP3 320.)

**Absolute rule — no track ends unresolved (the streak rule: keep going until it's downloaded).**
Every missing track must finish as exactly one of:

- **downloaded** — fetched from an artist-enabled free source, or via the matched YouTube fallback
  (the manifest records which, in `download_source`)
- **promo** — an official artist/label promo link was found; set `download_status: "promo"` and put
  the gate link in `download_url` (no file is fetched — the DJ completes the gate)
- **buy_only** — no free/artist-enabled source, no automatic YouTube match, and the browser lookup
  found nothing either; list it in the report with this status and move on. This skill **never**
  pushes payments and never carries store links.

Never silently drop a track. Keep looping **resolve → download → retry** until every track lands in
one of those buckets: on reruns pass `--retry-failed` to `download.py` so previously failed tracks get
another shot. `buy_only` is a resolved end state, not a miss — it now means the free-source hunt,
the automatic YouTube match, and the browser lookup all came up empty. And it never widens the
source list to leeching: **no
9jaflavour-style blogs, no "free mp3" sites from Google results** — the fallbacks beyond
artist-enabled sources are the matched YouTube audio pull above (yt-dlp, title/channel/length-checked,
labeled in the manifest) and the browser lookup. Spotify / Apple Music / Boomplay / Audiomack stay
discovery-only. Summary:

- Prefer the **clean/radio** version when the DJ chose clean and one exists; else the explicit edit.
- `scripts/resolve_sources.py` auto-resolves download URLs first (SoundCloud, then the YouTube
  fallback), writing `download_url` + `download_source` into candidates.json — run it after the
  scan/Drive steps:
  `python scripts/resolve_sources.py --candidates "<save_path>/_dj-search/candidates.json" --have "<save_path>/_dj-search/have_pc.json"`
  (add `--no-youtube` for a free-sources-only run). It needs the `yt-dlp` package for the fallback;
  without it, only the SoundCloud stage runs. Lookups run in parallel (`--jobs N`, default 6).
- `scripts/download.py` drives the `yt-dlp` library in-process and fetches what the resolver wrote:
  SoundCloud/Bandcamp free links plus the matched YouTube fallback URLs. Run it as
  `python scripts/download.py --manifest "<save_path>/_dj-search/candidates.json" --have "<save_path>/_dj-search/have_pc.json" --outdir "<save_path>" --confirm-free-download`
  (it refuses any other host, prints `(YouTube fallback)` per such track, and updates
  `download_status`/`local_path` per track; `--no-youtube` skips fallback tracks). Downloads run in
  parallel too (`--jobs N`, default 6) with one automatic retry on transient 403s. Its final report
  lists every track **downloaded** (with path) and everything **skipped** — relay both to the DJ.
- **Big crates (50–100 tracks)** are the same flow — both scripts parallelize, so at the defaults
  100 tracks is ~10 min of resolving + ~5–10 min of downloading. For big lists read each chart in
  one page-pull (whole Top 100 at once) instead of track-by-track, and expect a handful of YouTube
  403s under load: one more `download.py --retry-failed` pass (or the browser step) sweeps them.
- What the two scripts could not get, hunt by hand: SoundCloud/Bandcamp artist profiles and official
  promo gates. Confirm a real Download/Free affordance before using it (Audiomack has no web downloads
  anymore — only follow a download link the artist themselves posted). Files land in the browser's
  Downloads folder; then move+rename them into `<save_path>`.
- Name every file `Artist - Title (Clean).mp3` / `(Dirty).mp3`, 320 kbps where the source allows,
  written straight into `<save_path>`.
- If neither route finds anything: mark the track `buy_only` and list it in the final report. Never
  fail the whole run over one track, and never leave one unresolved either.

### 6. Recommend like a DJ, not a database

Close with a short, opinionated read — this is the part a DJ actually wants:

- **Crate picks** — the 5-8 that are worth adding now and why (peak-time, opener, transition glue).
- **Group by BPM and key** so sets build themselves; flag easy harmonic/mashup pairs.
- **Rising vs peaked** — call what is still climbing so they get ahead of it, and what is already
  saturated on every dancefloor.
- Keep it real and specific. No filler, no hype copy.

### 7. Save the crate + report

- Write `scripts/write_crate.py` output: `<save_path>/_dj-search/crate_<window>d_<date>.csv` and `.m3u8`
  (the .m3u8 holds every track with a real local file — downloaded here or already on the PC —
  import-ready into Serato/rekordbox/Engine).
- Print a compact table: downloaded / already-owned (PC vs Drive) / buy-only / failed, plus the
  recommendations. Then stop.

## Never

- Never download from leech blogs or "free mp3" sites; the only fallback past artist-enabled free
  sources is the matched, labeled YouTube pull in step 5. Spotify / Apple Music / Boomplay / Audiomack:
  discovery only.
- Never enter the DJ's passwords or solve captchas for them — pause and ask them to log in.
- Never close, kill, relaunch or copy the user's Chrome — work in the browser that is already open.
- Never overwrite an existing file in the save path without renaming (` (2)`); never delete library files.
- Never let one unavailable track abort the run.
