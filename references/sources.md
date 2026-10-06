# Sources — discovery (read-only) and downloads (free sources first, YouTube fallback last)

Two separate lists. **Discovery** sources are where you *see* what is trending — you never download
from them. **Download** sources are where a track is actually offered for free/licensed download.

---

## Discovery sources (read only — never download from these)

Read each in the already-open live Chrome. Intersect them: a track on 2+ lists inside the window is a
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

## Download sources (free first, YouTube fallback last)

Resolve each missing track to the first of these that has it. Stop at the first hit.

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
guard. When SoundCloud has nothing it runs the YouTube fallback below. Run it before the downloader so
fewer tracks end as `buy_only`; `--no-youtube` stops at SoundCloud. Its `--set-url` mode records a
browser-picked URL (see the browser step below) and exits without any network lookups.

### The YouTube fallback (resolver stage 2, default on)
When SoundCloud has no artist-enabled free download, the resolver searches YouTube via yt-dlp
(search only — nothing downloads at this stage) and scores every result:
- the video title must carry the track; sped-up/slowed/nightcore/remix/cover-style edits are
  rejected unless the candidate title itself names the edit
- the channel must look like the artist's own account or their `<Artist> - Topic` auto-catalog
  (label uploads pass when the artist is named in the video title)
- with a reference duration (the artist's own SoundCloud upload, or the candidate's `duration_s`)
  the video length must agree within a few seconds — the strongest check against wrong edits

Pick: best score, then most views. Written into the manifest as
`download_source: "youtube:<channel>"` with `match_confidence` recorded.

### The browser step (when both automated stages miss)
For tracks the resolver reports as no-match, search by eye in the already-open Chrome
(chrome-devtools `--autoConnect`; never close, kill, relaunch or copy the browser):

1. `youtube.com/results?search_query=<artist> <title>` — read the result list.
2. Accept a video only when the channel is the artist's own account or `<Artist> - Topic` (label
   uploads need the artist named in the title) *and* the length on the watch page matches the
   reference (`duration_s`, or the artist's SoundCloud upload length, ±5s). Skip
   sped-up/slowed/nightcore/remix/cover edits; two equal candidates → ask the DJ to pick.
3. Record the URL — offline, atomic manifest write:
   `python scripts/resolve_sources.py --candidates <candidates.json> --set-url "<id>=<video URL>"`
   → sets `download_url`, `download_source: "youtube-browser"`, `match_confidence: "browser"`, and
   un-parks `buy_only`/`failed` rows back to `missing`.
4. Fetch with the normal `download.py --manifest ...` run — same pipeline, same report.
   Age/sign-in-walled videos: if a recorded URL then fails to download, it needs browser cookies
   (`download.py --browser chrome`, Chrome closed) — otherwise surface it to the DJ and move on.

### The downloader script
`scripts/download.py` drives the `yt-dlp` library (in-process) and fetches what the resolver wrote:
free-download sources (SoundCloud free links, Bandcamp) plus the matched YouTube fallback URLs. It
extracts best audio and transcodes to MP3 320, prints `(YouTube fallback)` for fallback tracks, and
still refuses every other host (Spotify/Apple/Boomplay/leech sites stay discovery-only or
off-limits). Pass `--no-youtube` for a free-sources-only run.

### 6. Official promo gates (the legal "free" downloads that actually exist)
- Check the artist's own links first: X/IG bio → Linktree/Beacons → Hypeddit/ToneDen/newsletter drops.
  Search patterns: `"<artist> <title>" linktree`, `<artist> promo download`, the artist's pinned post.
- These are artist-sanctioned free downloads; complete the follow/email gate only if the DJ is OK
  with it, otherwise skip.
- SoundCloud: check the **artist's profile**, not only site search — search misses many street/indie
  uploads. On Audiomack, an artist profile only matters if it links their own download elsewhere.

**Never:** 9jaflavour/naijaloaded-style leech blogs or generic "free mp3 download" Google results.
The only fallback past artist-enabled sources is the matched, labeled YouTube pull above —
Spotify/Apple/Boomplay/Audiomack remain discovery-only. This skill never includes payment paths:
when nothing matches, the track is listed `buy_only` in the report and left there.
