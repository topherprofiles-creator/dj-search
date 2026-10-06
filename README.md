# dj-search

**A crate-digging skill for [Claude Code](https://claude.com/claude-code) — built for Afrobeats, Amapiano, Nigerian and Gen-Z DJs, works for any genre.**

One run: find what's trending right now, skip what you already own (PC **and** Google Drive), download the rest from legal free sources, and get a ranked crate with BPM/key notes and set-placement recommendations.

## What it does

Type `/dj-search` in Claude Code and the skill:

1. **Stops and asks first** — save path, window (7 / 14 / 30 days), and genre (or *general*) — nothing runs until you answer; count and clean/dirty ride along with sensible defaults.
2. **Discovers** what's moving — reads TurnTable Charts, Apple Music NG, Spotify NG + Viral, Audiomack, Boomplay, Shazam NG and TikTok trending sounds in your live Chrome, intersects the charts, and keeps only entries moving inside your window.
3. **Scans your PC** — `scripts/scan_library.py` walks your **entire PC and every plugged-in flash/removable drive** (`--all-drives`; system folders are pruned automatically), reads ID3v2/ID3v1, MP4/M4A atoms and FLAC tags offline (zero dependencies), and fuzzy-matches every candidate against what's there.
4. **Checks Google Drive** — mounted Drive letter, an `rclone` listing, or a browser search — so you never re-download what's already in the cloud.
5. **Downloads what's missing** from legal free sources only: Audiomack (artist-enabled), SoundCloud (Free Download), Bandcamp, artist/label promo gates, or your own DJ pool.
6. **Recommends like a DJ** — crate picks, rising vs. peaked, BPM/key groupings, harmonic-pair flags.
7. **Writes the crate** — `crate_<window>d_<date>.csv` + `.m3u8`, import-ready for Serato / rekordbox / Engine.

## The legal line

This repo only downloads tracks that are offered for **free download** or that **you are licensed for**:

- Audiomack, when the artist enabled the download
- SoundCloud "Free Download" / link-gated promos
- Bandcamp free or name-your-price
- Official artist/label promo gates (Linktree, Hypeddit, ToneDen)
- Your own paid pool subscriptions (BPM Supreme, DJcity, ZIPDJ, …)

It will **never** rip YouTube, Spotify, Apple Music or Boomplay, and never touches piracy sites — those are used for *discovery only*. `scripts/download.py` enforces this: any URL whose host isn't on its allowlist is refused.

## Requirements

| | |
|---|---|
| [Claude Code](https://claude.com/claude-code) | hosts the skill |
| Python 3.9+ | scripts are stdlib-only — no pip installs |
| A browser automation MCP (chrome-devtools recommended) | chart reading + downloads |
| `yt-dlp` *(optional)* | `python -m pip install -U yt-dlp` — automated downloads |
| `ffmpeg` *(optional)* | MP3 320 transcode for downloaded files |
| `rclone` *(optional)* | offline Google Drive de-dup |

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

## Google Drive de-dup (pick one)

1. **Mounted drive (best)** — with Google Drive for Desktop running, pass the mount (`G:\My Drive`, etc.) as an extra `--roots` to the scanner.
2. **rclone** — `rclone lsf gdrive: --recursive --include "*.mp3" > drive.txt`, then scan with `--drive-listing drive.txt`.
3. **Browser** — last resort, only for tracks still missing after 1 and 2.

## Repo layout

```
dj-search/
├─ SKILL.md                     the skill itself (Claude Code entry point)
├─ scripts/
│  ├─ scan_library.py           offline tag reader + fuzzy de-dup (zero deps)
│  ├─ download.py               legal-source downloader (yt-dlp wrapper, allowlisted)
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
├─ have_pc.json                owned / missing per track (PC + Drive)
├─ crate_7d_2026-10-06.csv
└─ crate_7d_2026-10-06.m3u8
```

## Testing

```sh
python tests/smoke_test.py
```

Builds a synthetic library (fake ID3 tags, a filename-only file, a Drive-only track) in a temp dir and asserts the scanner and crate writer behave. No network, no audio decoding.

## Troubleshooting

- **`yt-dlp` not found** — `python -m pip install -U yt-dlp` (or `pipx install yt-dlp`).
- **ffmpeg missing / no MP3 out** — install ffmpeg and make sure it's on PATH.
- **SoundCloud/Audiomack ask for a login** — the skill pauses and asks *you* to log in; it never enters credentials. For yt-dlp you can pass `--browser chrome` (close Chrome first — its cookie database is locked while running).
- **Drive letter missing** — Google Drive for Desktop may mount under a different letter; check `Get-PSDrive` (Windows) or the Finder sidebar (macOS), or use the rclone route.
- **The whole-PC scan takes a while on the first run** — every audio file on every drive is tag-read once (system folders are skipped); matching itself is indexed and fast. Narrow it with `--roots <folder>` if you want it quicker.

## Contributing

PRs welcome — keep `scripts/` stdlib-only and run `python tests/smoke_test.py` before submitting.

## License

MIT — see [LICENSE](LICENSE).
