# Media Toolkit — Plan

**Priority tier:** 1 · **Bundle ID:** `io.github.x-o-r-r-o.media-toolkit` · **Keywords:** `img`, `vid`, `media` · Universal Action “Media Toolkit”

## Why build it
Raycast demand this workflow replaces (downloads, 2026-09-26):

| Raycast extension | Downloads |
|---|---|
| Image Modification (sips) | 69,552 |
| Media Converter | 27,889 |
| Remove Background | 16,638 |
| **Total** | **114,079** |

**Alfred today:** 'Resize Image' (2015), 'Convert Video to MP4 - FFMPEG' (2018), Clop (needs paid app); no offline background removal.

## Features (v1.0)
- [x] Universal Action (files, multi) + `img` / `vid` / `media` keywords on the Finder selection; folders expand to their media
- [x] Images: resize (%, max px, width, height, fit box), convert (PNG/JPEG/HEIC/TIFF/GIF/BMP, WebP/AVIF when ImageIO can encode them), rotate/flip, strip metadata (lossless, keeps orientation) or GPS only, optimize — via ImageIO/CoreGraphics in JXA (no `sips` needed); EXIF orientation, alpha and animated GIFs handled
- [x] Remove background offline using macOS Vision (VNGenerateForegroundInstanceMaskRequest, macOS 14+) from JXA, optional crop to subject
- [x] Video/audio convert (MP4 H.264/HEVC via VideoToolbox, WebM, MOV, GIF with palettegen), extract audio (MP3/M4A/WAV/FLAC), compress (CRF presets), trim, mute, resize 1080p/720p/480p — via ffmpeg if installed; avconvert/afconvert fallbacks otherwise
- [x] Background queue with progress, cancel, log and notifications; multiple files queued sequentially
- [x] Batch on multiple selected files; output next to source with suffix or replace originals; never clobber; read-only folders fall back to Downloads
- [ ] Optional TinyPNG compress when API key set (deferred: v1 stays offline)

## Known limitations
- The HDR gain map of iPhone photos is dropped: edited photos are standard dynamic range. HDR video keeps 10 bits only with HEVC; other formats are 8-bit without tone mapping.
- Background removal needs macOS 14 (Vision's foreground instance mask); it is hidden on macOS 13.
- WebP and AVIF output depends on what ImageIO on that macOS version can encode (checked at runtime).
- Without ffmpeg only QuickTime/MP4 video (avconvert) and Core Audio formats (afconvert) are handled; WebM, GIF, MP3, compress and mute need ffmpeg.
- Folders expand one level deep, up to 5,000 media files.
- Cancelling stops the running conversion and clears the queue; files of that batch already converted are kept but not announced.
- The Universal Action receives tab-separated paths, so a file name that contains a tab or newline is split (very rare).
- Only the first frame of an animated GIF/HEICS is used when the output format can't animate; only the first page of a multi-page file unless it stays TIFF.

## Verify in real Alfred
- [ ] First run asks for Automation permission for Finder; the "Can't read the Finder selection" item appears when it is denied.
- [ ] Typing after the keyword doesn't query Finder again (`mt_sel` is passed back between keystrokes).
- [ ] The Universal Action on 1 and on many files (tab-separated `{query}`), and on a folder.
- [ ] <kbd>⌘</kbd><kbd>↩</kbd> reveals results; <kbd>⌘</kbd><kbd>Y</kbd>, <kbd>⌘</kbd><kbd>C</kbd>, <kbd>⌘</kbd><kbd>L</kbd> on an operation row.
- [ ] A long conversion keeps running after Alfred closes (own process group); the `vid` progress row refreshes (`rerun`), ↩ cancels with one notification, ⌘↩ reveals the log.
- [ ] The "finished" notification arrives through the External Trigger.
- [ ] ffmpeg from Homebrew is found although Alfred's PATH lacks /opt/homebrew/bin.
- [ ] 10+ images show the early "Processing…" notification.

## Tech
- **Stack:** bash + JXA using the ObjC bridge to ImageIO, CoreGraphics, CoreImage and Vision (no compiled binary to sign and notarise).
- **Dependencies:** ffmpeg optional (for video); `avconvert` and `afconvert` ship with macOS.
- Output via Alfred Script Filter JSON; settings via Workflow Configuration (`userconfigurationconfig`).
- Secrets (API keys/tokens) in the macOS Keychain, never in `prefs.plist`.
- Target: macOS 13+ on Apple Silicon and Intel.

## Milestones
1. [x] Script filter prototype for the main keyword
2. [x] Actions + modifiers, Universal Actions / File Actions where relevant
3. [x] Workflow Configuration, icons, error states (no network / missing dependency)
4. [ ] README with screenshots, `tools/build.py --package` release, forum post, then Gallery submission when invited

## Release checklist (Alfred forum + Gallery)
Sources: alfred.app/submit, alfred.app/submit/styleguide, alfred.app/submit/screenshots, alfredforum.com topics 23976 and 23388.

- [x] README starts with `## Usage`; each paragraph ends "via the `kw` keyword" / "via the Universal Action"
- [ ] A clean screenshot (window only, transparent background, real-looking data, no other workflows) after each paragraph, stored in `images/`
- [x] Modifiers listed as `* <kbd>⌘</kbd><kbd>↩</kbd> Action.`; Quick Look written as <kbd>⌘</kbd><kbd>Y</kbd>
- [x] `## Setup` only for genuine manual steps (no app installs or API keys; the Gallery lists those)
- [x] Every keyword is ≥ 3 characters and configurable via `{var:keyword_*}`
- [x] Settings in Workflow Configuration; the info.plist `readme` (About This Workflow) matches README.md
- [x] Main icon ≥ 256×256 px
- [x] No self-updater; never download or install software (no pip/brew/curl of binaries); dependencies declared for Alfred to handle
- [x] Any compiled binary is Developer ID signed + notarised; never strip quarantine (there is none)
- [x] No hard-coded paths; `prefs.plist` is git-ignored; secrets stay in Keychain
- [ ] AI assistance disclosed in the README and the forum post
- [ ] Sync `tools/build.py` from alfred-devtoolbox (it lacks the audited fixes, e.g. `argumenttreatemptyqueryasnil`), then rebuild
- [ ] Version bumped in `workflow.json`; `python3 tools/build.py --package`; GitHub release with the `.alfredworkflow` attached
- [ ] Forum post in "Share your Workflows" with a screenshot, keywords, and the GitHub link
