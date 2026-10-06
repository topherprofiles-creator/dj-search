# dj-search

**A crate-digging skill for [Claude Code](https://claude.com/claude-code) — built for Afrobeats, Amapiano, Nigerian and Gen-Z DJs, works for any genre.**

One run: find what's trending right now, skip what you already own (the whole PC and every plugged-in flash drive), download the rest (artist-enabled free sources first, matched YouTube fallback after), and get a ranked crate with BPM/key notes and set-placement recommendations.

## What it does

Type `/dj-search` in Claude Code and the skill:

1. **Stops and asks first** — save path, window (7 / 14 / 30 days), and genre (or *general*) — nothing runs until you answer; count and clean/dirty ride along with sensible defaults.
2. **Discovers** what's moving — reads TurnTable Charts, Apple Music NG, Spotify NG + Viral, Audiomack, Boomplay, Shazam NG and TikTok trending sounds in your live Chrome, intersects the charts, and keeps only entries moving inside your window.
3. **Scans your PC** — `scripts/scan_library.py` walks your **entire PC and every plugged-in flash/removable drive** (`--all-drives`; system folders are pruned automatically), reads ID3v2/ID3v1, MP4/M4A atoms and FLAC tags offline (zero dependencies), and fuzzy-matches every candidate against what's there.
4. **Downloads what's missing** — `resolve_sources.py` auto-resolves artist-enabled SoundCloud free downloads, then falls back to a matched YouTube audio pull (yt-dlp search; title + artist/`Topic` channel + duration check); `download.py` fetches, transcodes to MP3 320 and labels every track with its `download_source`. Tracks both stages miss get a browser lookup: the agent searches YouTube in your already-open Chrome, verifies the video by channel + length, records it with `--set-url`, and the same downloader fetches it. Only when all three routes come up empty does a track end as `buy_only` — never silently dropped.
5. **Recommends like a DJ** — crate picks, rising vs. peaked, BPM/key groupings, harmonic-pair flags.
6. **Writes the crate** — `crate_<window>d_<date>.csv` + `.m3u8`, import-ready for Serato / rekordbox / Engine.

## Where downloads come from

Downloads run in this order, and every file is labeled with its origin (`download_source` in candidates.json):

1. **Artist-enabled free sources** — SoundCloud "Free Download" / link-gated promos, Bandcamp free
   or name-your-price, official artist/label promo gates (Linktree, Hypeddit, ToneDen), your own paid
   pool subscriptions (BPM Supreme, DJcity, ZIPDJ, …). *(Audiomack is **discovery only** since Oct
   2026 — its web player no longer offers per-song downloads; downloads moved to the app / Plus.)*
2. **Matched YouTube fallback** (default; `--no-youtube` turns it off) — `resolve_sources.py`
   searches YouTube via yt-dlp and accepts a video only when the title carries the track, the channel
   looks like the artist's own account / `<Artist> - Topic`, and — when a reference duration is
   known — the length agrees within a few seconds, which rejects sped-up / slowed / remix edits.
   These show as `youtube:<channel>` in the manifest, never silently mixed in.
3. **Browser lookup** (agent step, for tracks both automated stages miss) — the agent searches
   YouTube in your already-open Chrome the way a human would, verifies the video by channel and
   length, and records it with `--set-url` (`youtube-browser` in the manifest). The same downloader
   fetches it. *(YouTube has no browser "save file" — the browser finds and verifies the video;
   yt-dlp pulls the audio and transcodes.)*

Still off-limits: 9jaflavour/naijaloaded-style leech blogs and "free mp3" Google results — and this
skill never includes payment paths; when nothing matches, the track is listed `buy_only` and left there.


## Requirements

| | |
|---|---|
| [Claude Code](https://claude.com/claude-code) | hosts the skill |
| Python 3.9+ | scripts are stdlib-first — only `yt-dlp`/`ffmpeg` extend the download path |
| A browser automation MCP (chrome-devtools recommended) | chart reading + downloads |
| `yt-dlp` *(recommended)* | `python -m pip install -U yt-dlp` — automated downloads + the YouTube fallback (used as an in-process library) |
| `ffmpeg` *(optional)* | MP3 320 transcode for downloaded files |

## Install

Clone straight into your Claude Code skills folder:

```sh
# macOS / Linux
git clone https://github.com/topherprofiles-creator/dj-search.git ~/.claude/skills/dj-search

# Windows (PowerShell)
git clone https://github.com/topherprofiles-creator/dj-search.git "$env:USERPROFILE\.claude\skills\dj-search"
```

Then in Claude Code:

```
/dj-search
```

It asks for the window and save path, then does the rest.

## Repo layout

```
dj-search/
├─ SKILL.md                     the skill itself (Claude Code entry point)
├─ scripts/
│  ├─ scan_library.py           offline tag reader + fuzzy de-dup (zero deps)
│  ├─ resolve_sources.py        SoundCloud resolver + YouTube fallback (stdlib; yt-dlp for the fallback)
│  ├─ download.py               legal-source downloader (yt-dlp library, allowlisted)
│  └─ write_crate.py            ranked CSV + M3U8 writer
├─ references/
│  └─ sources.md                how to read each chart; per-source download recipes
├─ examples/
│  └─ candidates.example.json   synthetic candidates.json (safe to play with)
└─ tests/
   └─ smoke_test.py             offline: builds a fake library, asserts de-dup verdicts
```

## Output

Inside your save path you get the MP3s (`Artist - Title (Clean).mp3`) plus:

```
<save>/_dj-search/
├─ candidates.json             what's trending + per-track status
├─ have_pc.json                owned / missing per track (PC)
├─ crate_7d_2026-10-06.csv
└─ crate_7d_2026-10-06.m3u8
```

## Testing

```sh
python tests/smoke_test.py
```

Builds a synthetic library (fake ID3 tags, a filename-only file, an offline-listing match) in a temp dir and asserts the scanner and crate writer behave. No network, no audio decoding.

## Troubleshooting

- **`yt-dlp` not installed** — `python -m pip install -U yt-dlp`; it must be importable by the same Python that runs the script (a pipx/CLI-only install won't work).
- **ffmpeg missing / no MP3 out** — install ffmpeg and make sure it's on PATH.
- **SoundCloud asks for a login** — the skill pauses and asks *you* to log in; it never enters credentials. For yt-dlp you can pass `--browser chrome` (close Chrome first — its cookie database is locked while running).
- **Audiomack has no download button** — expected since Oct 2026: web downloads were removed (app / Plus only) and the old yt-dlp endpoint is dead, so the skill treats Audiomack as discovery-only.
- **The whole-PC scan takes a while on the first run** — every audio file on every drive is tag-read once (system folders are skipped); matching itself is indexed and fast. Narrow it with `--roots <folder>` if you want it quicker.
- **Big crate runs (50–100 tracks)** — `resolve_sources.py` and `download.py` work in parallel (`--jobs N`, default 6; 1 = serial) and retry transient YouTube 403s automatically. A 100-track crate is roughly a 15-minute job; a few tracks may still need one `--retry-failed` pass.

## Contributing

PRs welcome — keep `scripts/` stdlib-first (`yt-dlp` stays an optional import) and run `python tests/smoke_test.py` before submitting.

## License

MIT — see [LICENSE](LICENSE).
