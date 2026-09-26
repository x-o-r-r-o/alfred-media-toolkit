# Media Toolkit — Plan

**Priority tier:** 1 · **Bundle ID:** `com.xorro.media-toolkit`

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
- [ ] File Action on images: resize (%, px), convert (png/jpg/heic/webp/avif), rotate/flip, strip metadata — via `sips`
- [ ] Remove background offline using macOS Vision (VNGenerateForegroundInstanceMaskRequest, macOS 14+)
- [ ] Video/audio convert, trim, extract audio, make GIF — via ffmpeg if installed
- [ ] Batch on multiple selected files; output next to source with suffix
- [ ] Optional TinyPNG compress when API key set

## Tech
- **Stack:** zsh + small universal Swift binary for Vision background removal.
- **Dependencies:** ffmpeg optional (for video).
- Output via Alfred Script Filter JSON; settings via Workflow Configuration (`userconfigurationconfig`).
- Secrets (API keys/tokens) in the macOS Keychain, never in `prefs.plist`.
- Target: macOS 13+ on Apple Silicon and Intel (universal binaries for any Swift helpers).

## Milestones
1. Script filter prototype for the main keyword
2. Actions + modifiers, Universal Actions / File Actions where relevant
3. Workflow Configuration, icons, error states (no network / missing dependency)
4. README with screenshots, `build.sh` release, submit to Alfred Gallery + forum post
