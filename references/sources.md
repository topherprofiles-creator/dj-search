# Sources — discovery (read-only) and downloads (legal only)

Two separate lists. **Discovery** sources are where you *see* what is trending — you never download
from them. **Download** sources are where a track is actually offered for free/licensed download.

---

## Discovery sources (read only — never download from these)

Read each in the owner's live Chrome. Intersect them: a track on 2+ lists inside the window is a
real trend, not one platform's quirk.

| Source | URL | How to read it |
|---|---|---|
| TurnTable Charts | `turntablecharts.com` | Nigeria's official chart (Top 100, Top Streaming). Note movement arrows for rising/new. |
| Apple Music NG | `music.apple.com/ng/charts` | Top 100 Songs / Daily Top 100: Nigeria. Dates on new entries. |
| Spotify NG | `open.spotify.com` → Top 50 Nigeria, Viral 50 Nigeria | Viral 50 surfaces Gen-Z/TikTok breakouts before they chart. |
| Audiomack | `audiomack.com/trending` + `/afrobeats` + `/amapiano` | Trending Now + genre pages. **Discovery only since Oct 2026** — web downloads removed; see below. |
| Boomplay NG | `boomplay.com` → Charts → Nigeria | Big for street-pop/local trends Spotify misses. |
| Shazam NG | `shazam.com/charts/top-200/nigeria` | What people are hearing out and tagging — leading indicator. |
| TikTok sounds | `tiktok.com` → search a sound/hashtag, or the Creative Center trending-sounds page | Gen-Z vibes and breakout snippets; note the exact track behind a viral sound. |

Reading tips:
- Prefer each site's own "new/rising" markers over raw rank — you want *motion* inside the window.
- For release dates, the track's Audiomack/Spotify page shows the release date; drop anything older
  than the chosen window unless it is re-surging (a TikTok revival is still a valid "trending now").
- Normalise names before de-duping: lowercase, strip `feat.`/`ft.`/`x`, drop `(Official Video)` etc.
- `ERR_INTERNET_DISCONNECTED` on navigate is transient — retry once.

---

## Download sources (legal only)

Resolve each missing track to the first of these that has it. Stop at the first legal hit.

### 1. Audiomack — discovery only (web downloads removed 2026-10)
- Verified 2026-10-06 while logged in: track pages no longer show any download affordance at all —
  Audiomack moved downloads to its mobile app / Plus. yt-dlp's old Audiomack endpoint
  (`/api/music/url/song/...`) is dead, and `scripts/download.py` refuses audiomack.com.
- Use Audiomack to find what's moving and where a track lives; fetch the file from the artist's own
  links instead (SoundCloud free download, promo gate, or the DJ's pool).

### 2. SoundCloud
- Many Afrobeats/edit/remix uploads carry a **Free Download** button or a "Buy"/"Download" link in the
  description that resolves to a free Hypeddit/ToneDen gate. Use those.
- A track with no download affordance is stream-only — skip it here.

### 3. Bandcamp
- Free or name-your-price (enter 0) downloads. Choose MP3 320 on the format page.

### 4. Official artist/label promo
- Linktree / Hypeddit / ToneDen / newsletter "free download" gates the artist posted. Follow the gate
  (follow/email) only if the DJ is OK with it; otherwise skip.

### 5. The DJ's own pool subscriptions (paid, already licensed)
- BPM Supreme, DJcity, ZIPDJ, DigitalDJPool, Beatport. The DJ must already be logged in. These give
  proper clean/dirty/intro edits — best quality for actual sets.

### The resolver
`scripts/resolve_sources.py` searches SoundCloud for each still-missing track and writes a `download_url`
into candidates.json only when the uploader account itself looks like the artist and the track's free
download is enabled (API `downloadable` flag). Fan re-uploads of label songs are rejected by the uploader
guard. Run it before the downloader so fewer tracks end as `buy_only` — it never widens the source list.

### The downloader script
`scripts/download.py` drives the `yt-dlp` library (in-process) and is for the sources above that **offer** a free download
(SoundCloud free links, Bandcamp). It extracts best audio and
transcodes to MP3 320. It must **not** be pointed at YouTube/Spotify/Apple/Boomplay or any
stream-only URL — that is the line in SKILL.md.

### 6. Official promo gates (the legal "free" downloads that actually exist)
- Check the artist's own links first: X/IG bio → Linktree/Beacons → Hypeddit/ToneDen/newsletter drops.
  Search patterns: `"<artist> <title>" linktree`, `<artist> promo download`, the artist's pinned post.
- These are artist-sanctioned free downloads; complete the follow/email gate only if the DJ is OK
  with it, otherwise skip.
- SoundCloud: check the **artist's profile**, not only site search — search misses many street/indie
  uploads. On Audiomack, an artist profile only matters if it links their own download elsewhere.

**Never:** 9jaflavour/naijaloaded-style leech blogs, generic "free mp3 download" Google results, or
YouTube/Spotify rips. If it is not artist-enabled, licensed, or a store purchase — it is off-limits.
This skill never includes payment paths: when nothing free exists, the track is simply listed as
`buy_only` in the report and left there.

---

## Google Drive de-dup

Order of preference:

1. **Mounted Drive (Google Drive for Desktop)** — check for a mounted letter: in PowerShell
   `Get-PSDrive -PSProvider FileSystem`. If you see e.g. `G:` labelled Google Drive, pass it as an
   extra `--roots` to `scan_library.py`. Done — same offline fuzzy match as the PC.
2. **rclone** — if `rclone listremotes` shows a Drive remote:
   `rclone lsf <remote>: --recursive --include "*.{mp3,wav,flac,m4a,aac}" > drive.txt`, then
   `scan_library.py --drive-listing drive.txt`.
3. **Browser** — only for tracks still missing after PC+mount. Open `drive.google.com`, search the
   title, eyeball matches. Slow and manual; last resort.
