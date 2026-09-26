# Media Toolkit — Plan

**Priority tier:** 1 · **Bundle ID:** `io.github.x-o-r-r-o.media-toolkit` · **Keywords:** `img`, `vid`

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
- **Stack:** zsh + JXA using the ObjC bridge to Vision for background removal (no compiled binary to sign and notarise).
- **Dependencies:** ffmpeg optional (for video).
- Output via Alfred Script Filter JSON; settings via Workflow Configuration (`userconfigurationconfig`).
- Secrets (API keys/tokens) in the macOS Keychain, never in `prefs.plist`.
- Target: macOS 13+ on Apple Silicon and Intel.

## Milestones
1. Script filter prototype for the main keyword
2. Actions + modifiers, Universal Actions / File Actions where relevant
3. Workflow Configuration, icons, error states (no network / missing dependency)
4. README with screenshots, `build.sh` release, forum post, then Gallery submission when invited

## Release checklist (Alfred forum + Gallery)
Sources: alfred.app/submit, alfred.app/submit/styleguide, alfred.app/submit/screenshots, alfredforum.com topics 23976 and 23388.

- [ ] README starts with `## Usage`; each paragraph ends "via the `kw` keyword" / "via the Universal Action"
- [ ] A clean screenshot (window only, transparent background, real-looking data, no other workflows) after each paragraph, stored in `images/`
- [ ] Modifiers listed as `* <kbd>⌘</kbd><kbd>↩</kbd> Action.`; Quick Look written as <kbd>⌘</kbd><kbd>Y</kbd>
- [ ] `## Setup` only for genuine manual steps (no app installs or API keys; the Gallery lists those)
- [ ] Every keyword is ≥ 3 characters and configurable via `{var:keyword_*}`
- [ ] Settings in Workflow Configuration; the info.plist `readme` (About This Workflow) matches README.md
- [ ] Main icon ≥ 256×256 px
- [ ] No self-updater; never download or install software (no pip/brew/curl of binaries); dependencies declared for Alfred to handle
- [ ] Any compiled binary is Developer ID signed + notarised; never strip quarantine
- [ ] No hard-coded paths; `prefs.plist` is git-ignored; secrets stay in Keychain
- [ ] AI assistance disclosed in the README and the forum post
- [ ] Version bumped in `src/info.plist`; `./build.sh`; GitHub release with the `.alfredworkflow` attached
- [ ] Forum post in "Share your Workflows" with a screenshot, keywords, and the GitHub link
