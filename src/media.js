#!/usr/bin/osascript -l JavaScript
// Media Toolkit for Alfred — image, video and audio operations without bundled binaries.
// Usage:
//   osascript -l JavaScript media.js img|vid|all [query]  Script Filter (files from the Finder selection or $mt_ua_files)
//   osascript -l JavaScript media.js apply <op>           run an image operation on the files in $mt_files (JSON)
//   osascript -l JavaScript media.js enqueue <op>         queue a video/audio operation for worker.sh
// File paths never reach a shell or code string: they travel as JSON in environment variables and as argv.
ObjC.import("Foundation");
ObjC.import("AppKit");
ObjC.import("CoreGraphics");
ObjC.import("ImageIO");
ObjC.import("CoreImage");

const ENV = $.NSProcessInfo.processInfo.environment;
function env(name, fallback) {
  const v = ENV.objectForKey(name);
  return v.isNil() ? fallback : v.js;
}
const FM = $.NSFileManager.defaultManager;
// Set by the tests: nothing real (Finder selection, Alfred, Finder windows, Downloads) is touched without an override
const TEST = env("MT_TEST", "") !== "";

// Look up a user-controlled key (query, file extension) without hitting Object.prototype ("constructor", "__proto__")
function lookup(obj, key) {
  return Object.prototype.hasOwnProperty.call(obj, key) ? obj[key] : undefined;
}
// Display text: no control characters or bidi overrides (a file name can hold both); the real value stays in arg
function clean(s) {
  return String(s).replace(/[\u0000-\u001f\u007f-\u009f]+/g, " ").replace(/[\u202a-\u202e\u2066-\u2069\u200e\u200f\u061c]/g, "");
}

// ---------- file types ----------

const IMAGE_EXT = "jpg jpeg jpe jfif png heic heif hif tif tiff gif bmp webp avif jp2 j2k psd tga ico icns dng cr2 cr3 nef nrw arw srf sr2 raf orf rw2 pef srw 3fr erf mos raw".split(" ");
// (not .ts: too often TypeScript)
const VIDEO_EXT = "mp4 m4v mov qt mkv webm avi wmv flv mpg mpeg m2v mts m2ts 3gp 3g2 ogv vob mxf".split(" ");
const AUDIO_EXT = "mp3 m4a m4b aac wav wave aif aiff aifc flac ogg oga opus wma caf alac amr ac3 mka".split(" ");
// What AVFoundation (avconvert) and Core Audio (afconvert) read, for when ffmpeg is missing
const AVF_VIDEO_EXT = "mp4 m4v mov qt 3gp 3g2".split(" ");
const CA_AUDIO_EXT = "mp3 m4a m4b aac wav wave aif aiff aifc flac caf alac ac3 amr".split(" ");

function extOf(p) {
  const name = p.split("/").pop();
  const i = name.lastIndexOf(".");
  return i > 0 ? name.slice(i + 1).toLowerCase() : "";
}
function baseName(p) {
  return p.split("/").pop();
}
function stemOf(p) {
  const name = baseName(p);
  const i = name.lastIndexOf(".");
  return i > 0 ? name.slice(0, i) : name;
}
function dirOf(p) {
  const i = p.lastIndexOf("/");
  return i > 0 ? p.slice(0, i) : "/";
}
function kindOf(p) {
  const e = extOf(p);
  if (IMAGE_EXT.includes(e)) return "image";
  if (VIDEO_EXT.includes(e)) return "video";
  if (AUDIO_EXT.includes(e)) return "audio";
  return null;
}
function isDir(p) {
  const d = Ref();
  return FM.fileExistsAtPathIsDirectory(p, d) && d[0];
}
function exists(p) {
  return FM.fileExistsAtPath(p);
}

// ---------- config ----------

function suffix() {
  // No path separators or colons in the suffix; an empty suffix is allowed.
  // (sliced by code point, so an emoji is never cut in half)
  return Array.from(env("output_suffix", "-edited").replace(/[\/:\0\n\r\t]/g, "")).slice(0, 60).join("");
}
// Workflow Configuration checkboxes arrive as "1"/"0"; accept "true"/"yes" too (set by hand or by an older Alfred)
function checked(name) {
  return /^(1|true|yes)$/i.test(env(name, "0").trim());
}
// "Keep the original dates": results get the source's modification and creation dates
function keepDates() {
  return checked("keep_dates");
}
function replaceOriginals() {
  return checked("replace_originals");
}
function quality() {
  const q = parseInt(env("image_quality", "85"), 10);
  return isFinite(q) ? Math.min(100, Math.max(10, q)) / 100 : 0.85;
}
function cacheDir() {
  const dir = env("alfred_workflow_cache", `${$.NSTemporaryDirectory().js}media-toolkit`);
  FM.createDirectoryAtPathWithIntermediateDirectoriesAttributesError(dir, true, $(), $());
  return dir;
}
function fallbackDir() {
  const t = env("MT_TEST_FALLBACK_DIR", "");
  if (t) return t;
  if (TEST) return cacheDir();
  const u = FM.URLsForDirectoryInDomains(15 /* NSDownloadsDirectory */, 1 /* user */).firstObject;
  return u.isNil() ? $.NSHomeDirectory().js : u.path.js;
}

// ---------- output paths ----------

function writableDir(dir) {
  return isDir(dir) && FM.isWritableFileAtPath(dir);
}

// Where to write the result of `src` with extension `ext`.
// Returns { path, replace, note }: `replace` means the original gets replaced (overwrite mode, same format).
function plannedOutput(src, ext, opts = {}) {
  const srcExt = extOf(src);
  const same = normExt(srcExt) === normExt(ext);
  const outExt = same ? baseName(src).slice(baseName(src).lastIndexOf(".") + 1) : ext; // keep the original spelling (.JPG, .jpeg)
  let dir = dirOf(src), note = "";
  if (!writableDir(dir)) {
    dir = fallbackDir();
    note = "saved to Downloads because the folder is read-only";
  } else if (same && replaceOriginals() && !opts.neverReplace && FM.isWritableFileAtPath(src)) {
    return { path: src, replace: true, note: "" };
  }
  const stem = stemOf(src) + (same || opts.alwaysSuffix || dir !== dirOf(src) ? suffix() : "");
  return { path: uniquePath(dir, stem, outExt), replace: false, note };
}
function normExt(e) {
  e = String(e).toLowerCase();
  return lookup({ jpeg: "jpg", jpe: "jpg", jfif: "jpg", tif: "tiff", heif: "heic", hif: "heic", aif: "aiff", aifc: "aiff", wave: "wav", qt: "mov" }, e) || e;
}
function uniquePath(dir, stem, ext, taken = []) {
  const make = (n) => `${dir}/${stem}${n > 1 ? `-${n}` : ""}${ext ? "." + ext : ""}`;
  let n = 1;
  while (exists(make(n)) || taken.includes(make(n))) n++;
  return make(n);
}
let tmpSeq = 0;
function tempPathFor(finalPath) {
  const dir = dirOf(finalPath);
  const ext = extOf(finalPath);
  const pid = $.NSProcessInfo.processInfo.processIdentifier;
  return `${dir}/.mt-${pid}-${Date.now()}-${tmpSeq++}${ext ? "." + ext : ""}`;
}
// The dates to give a result of `src` (null unless "Keep the original dates" is on). Read before the
// original may be replaced.
function sourceDates(src) {
  if (!src || !keepDates()) return null;
  const a = FM.attributesOfItemAtPathError(src, $());
  if (a.isNil()) return null;
  const d = $.NSMutableDictionary.dictionary;
  if (!a.fileModificationDate.isNil()) d.setObjectForKey(a.fileModificationDate, $.NSFileModificationDate);
  const c = a.objectForKey($.NSFileCreationDate);
  if (!c.isNil()) d.setObjectForKey(c, $.NSFileCreationDate);
  return d;
}
// Best effort: a volume that can't store the date (some network shares) keeps the conversion anyway
function applyDates(path, dates) {
  if (dates) FM.setAttributesOfItemAtPathError(dates, path, $());
}

// Move a finished temp file into place, never clobbering anything except an intentionally replaced original.
// `src` is the original, for "Keep the original dates".
function commit(tmp, plan, src) {
  const dates = sourceDates(src);
  if (plan.replace) {
    const err = Ref();
    const ok = FM.replaceItemAtURLWithItemAtURLBackupItemNameOptionsResultingItemURLError(
      $.NSURL.fileURLWithPath(plan.path), $.NSURL.fileURLWithPath(tmp), $(), 0, $(), err);
    if (!ok) {
      FM.removeItemAtPathError(tmp, $());
      throw new Error("could not replace the original");
    }
    applyDates(plan.path, dates);
    return plan.path;
  }
  let target = plan.path;
  const stem = stemOf(plan.path), ext = baseName(plan.path).includes(".") ? baseName(plan.path).split(".").pop() : "";
  for (let i = 0; i < 100; i++) {
    if (FM.moveItemAtPathToPathError(tmp, target, $())) {
      applyDates(target, dates);
      return target;
    }
    if (!exists(target)) break; // failed for another reason (permissions, disk full)
    target = uniquePath(dirOf(plan.path), stem, ext);
  }
  FM.removeItemAtPathError(tmp, $());
  throw new Error("could not save the result");
}

// ---------- ObjC helpers ----------

function num(n) {
  return Number.isInteger(n) ? $.NSNumber.numberWithLongLong(n) : $.NSNumber.numberWithDouble(n);
}
function bool(b) {
  return $.NSNumber.numberWithBool(!!b);
}
function dict(obj) {
  const d = $.NSMutableDictionary.dictionary;
  for (const [k, v] of Object.entries(obj)) {
    if (v === undefined || v === null) continue;
    let val = v;
    if (typeof v === "number") val = num(v);
    else if (typeof v === "boolean") val = bool(v);
    else if (typeof v === "string") val = $(v);
    else if (v && v.__plain) val = dict(v.__plain);
    d.setObjectForKey(val, $(k));
  }
  return d;
}
function plain(obj) {
  return { __plain: obj };
}
// JXA never releases what CoreFoundation "Create"/"Copy" functions return: a batch of 12 MP photos grew to
// gigabytes. Such objects are registered with own() and released with releaseOwned() after each file.
let owned = [];
let cfReleaseBound = false;
function own(ref) {
  if (ref) owned.push(ref);
  return ref;
}
function releaseOwned() {
  if (!cfReleaseBound) {
    ObjC.bindFunction("CFRelease", ["void", ["void *"]]);
    cfReleaseBound = true;
  }
  const list = owned;
  owned = [];
  for (const r of list) $.CFRelease(r);
}
function cfToJS(ref) {
  if (!ref) return null;
  const o = ObjC.castRefToObject(ref);
  return o.isNil() ? null : ObjC.deepUnwrap(o);
}

// ---------- image formats ----------

const FORMATS = {
  png: { uti: "public.png", ext: "png", name: "PNG", alpha: true, lossy: false },
  jpeg: { uti: "public.jpeg", ext: "jpg", name: "JPEG", alpha: false, lossy: true },
  heic: { uti: "public.heic", ext: "heic", name: "HEIC", alpha: true, lossy: true },
  tiff: { uti: "public.tiff", ext: "tiff", name: "TIFF", alpha: true, lossy: false },
  gif: { uti: "com.compuserve.gif", ext: "gif", name: "GIF", alpha: true, lossy: false, frames: true, noOrientation: true },
  bmp: { uti: "com.microsoft.bmp", ext: "bmp", name: "BMP", alpha: false, lossy: false, noOrientation: true },
  webp: { uti: "org.webmproject.webp", ext: "webp", name: "WebP", alpha: true, lossy: true },
  avif: { uti: "public.avif", ext: "avif", name: "AVIF", alpha: true, lossy: true },
};
const FORMAT_ALIASES = { png: "png", jpg: "jpeg", jpeg: "jpeg", heic: "heic", heif: "heic", tif: "tiff", tiff: "tiff", gif: "gif", bmp: "bmp", webp: "webp", avif: "avif" };
const UTI_FORMAT = {};
for (const [k, f] of Object.entries(FORMATS)) UTI_FORMAT[f.uti] = k;

let encodableCache = null;
function encodableFormats() {
  if (encodableCache) return encodableCache;
  const hidden = env("MT_TEST_HIDE_FORMATS", "").split(",");
  const ids = cfToJS($.CGImageDestinationCopyTypeIdentifiers()) || [];
  encodableCache = Object.keys(FORMATS).filter((k) => ids.includes(FORMATS[k].uti) && !hidden.includes(k));
  return encodableCache;
}
function formatOfFile(p) {
  return lookup(FORMAT_ALIASES, normExt(extOf(p))) || null;
}

function backgroundRemovalAvailable() {
  if (env("MT_TEST_NO_VISION", "") === "1") return false;
  try {
    ObjC.import("Vision");
    return typeof $.VNGenerateForegroundInstanceMaskRequest !== "undefined" && !!$.VNGenerateForegroundInstanceMaskRequest.alloc;
  } catch (e) {
    return false;
  }
}

// ---------- image engine ----------

// size_t results come back from the JXA bridge as strings
function imgW(img) {
  return Number($.CGImageGetWidth(img));
}
function imgH(img) {
  return Number($.CGImageGetHeight(img));
}
function srcCount(src) {
  return Number($.CGImageSourceGetCount(src));
}

const MAX_PIXELS = Number(env("MT_TEST_MAX_PIXELS", "")) || 400e6; // refuse full decodes above 400 megapixels (about 1.6 GB of memory)
// 16-bit images take twice the memory per pixel
function pixelLimit(info) {
  return info && info.deep ? MAX_PIXELS / 2 : MAX_PIXELS;
}

function openImage(path) {
  const src = own($.CGImageSourceCreateWithURL($.NSURL.fileURLWithPath(path), null));
  if (!src || srcCount(src) < 1) throw new Error("not a readable image");
  const props = cfToJS(own($.CGImageSourceCopyPropertiesAtIndex(src, 0, null))) || {};
  const count = srcCount(src);
  const uti = ObjC.castRefToObject($.CGImageSourceGetType(src)).js;
  const w = props.PixelWidth || 0, h = props.PixelHeight || 0;
  if (!w || !h) throw new Error("not a readable image");
  const orientation = props.Orientation || 1;
  const rotated = orientation >= 5 && orientation <= 8;
  return {
    path, src, props, count, uti,
    width: rotated ? h : w, // displayed (oriented) size
    height: rotated ? w : h,
    orientation,
    hasAlpha: !!props.HasAlpha,
    deep: (props.Depth || 8) > 8, // 16-bit or float: twice the memory per pixel
    animated: count > 1 && (uti === "com.compuserve.gif" || uti === "public.png" || uti === "public.heics" || uti === "org.webmproject.webp"),
  };
}

// Decode frame `i` with its orientation applied, optionally downscaled so the longest side is <= maxSide.
function orientedFrame(info, i, maxSide) {
  const MAX_PIXELS = pixelLimit(info);
  const full = Math.max(info.width, info.height);
  const target = maxSide ? Math.min(full, Math.max(1, Math.round(maxSide))) : full;
  if (!maxSide && info.orientation === 1) {
    if (info.width * info.height > MAX_PIXELS) throw new Error(`image too large (${Math.round((info.width * info.height) / 1e6)} MP)`);
    const img = own($.CGImageSourceCreateImageAtIndex(info.src, i, dict({ kCGImageSourceShouldCacheImmediately: true })));
    if (!img) throw new Error("could not decode the image");
    return img;
  }
  const scale = target / full;
  const area = info.width * info.height * scale * scale;
  if (area > MAX_PIXELS) throw new Error(`image too large (${Math.round(area / 1e6)} MP)`);
  const img = own($.CGImageSourceCreateThumbnailAtIndex(info.src, i, dict({
    kCGImageSourceCreateThumbnailFromImageAlways: true,
    kCGImageSourceCreateThumbnailWithTransform: true,
    kCGImageSourceShouldCacheImmediately: true,
    kCGImageSourceThumbnailMaxPixelSize: target,
  })));
  if (!img) throw new Error("could not decode the image");
  return img;
}

function imageHasAlpha(img) {
  const a = $.CGImageGetAlphaInfo(img); // 0 none, 5 noneSkipLast, 6 noneSkipFirst
  return !(a === 0 || a === 5 || a === 6);
}

// Draw `img` into a new bitmap of outW×outH, applying rotation (clockwise degrees), flips, scaling and flattening.
function draw(img, outW, outH, t = {}) {
  const alpha = imageHasAlpha(img) && !t.flatten;
  const srcCS = $.CGImageGetColorSpace(img);
  const model = srcCS ? $.CGColorSpaceGetModel(srcCS) : -1;
  // 16-bit (and float) sources keep 16 bits per channel unless that would take too much memory
  const deep = Number($.CGImageGetBitsPerComponent(img)) > 8 && outW * outH <= MAX_PIXELS / 4;
  const bpc = deep ? 16 : 8;
  let ctx = null;
  if (model === 0 /* monochrome */ && !alpha) {
    // Greyscale stays greyscale (there is no grey + alpha bitmap context, so grey with alpha becomes RGB)
    ctx = own($.CGBitmapContextCreate(null, outW, outH, bpc, 0, srcCS, 0 /* alpha none */));
  }
  if (!ctx) {
    // RGB colour spaces (sRGB, Display P3, Adobe RGB…) keep their ICC profile; CMYK, Lab and indexed go to sRGB
    const cs = model === 1 /* RGB */ ? srcCS : own($.CGColorSpaceCreateWithName($.kCGColorSpaceSRGB));
    const info = alpha ? 1 /* premultipliedLast */ : 5 /* noneSkipLast */;
    ctx = own($.CGBitmapContextCreate(null, outW, outH, bpc, 0, cs, info));
    if (!ctx && bpc === 16) ctx = own($.CGBitmapContextCreate(null, outW, outH, 8, 0, cs, info));
    if (!ctx) ctx = own($.CGBitmapContextCreate(null, outW, outH, 8, 0, own($.CGColorSpaceCreateWithName($.kCGColorSpaceSRGB)), info));
  }
  if (!ctx) throw new Error("not enough memory for this image");
  if (t.flatten || !alpha) {
    $.CGContextSetGrayFillColor(ctx, 1, 1); // white in any colour space
    $.CGContextFillRect(ctx, $.CGRectMake(0, 0, outW, outH));
  }
  $.CGContextSetInterpolationQuality(ctx, 3 /* high */);
  const rot = (((t.rotate || 0) % 360) + 360) % 360;
  // Size of the drawn (unrotated) image
  const dw = rot === 90 || rot === 270 ? outH : outW;
  const dh = rot === 90 || rot === 270 ? outW : outH;
  if (rot === 90) {
    $.CGContextTranslateCTM(ctx, 0, outH);
    $.CGContextRotateCTM(ctx, -Math.PI / 2);
  } else if (rot === 180) {
    $.CGContextTranslateCTM(ctx, outW, outH);
    $.CGContextRotateCTM(ctx, Math.PI);
  } else if (rot === 270) {
    $.CGContextTranslateCTM(ctx, outW, 0);
    $.CGContextRotateCTM(ctx, Math.PI / 2);
  }
  if (t.flipH) {
    $.CGContextTranslateCTM(ctx, dw, 0);
    $.CGContextScaleCTM(ctx, -1, 1);
  }
  if (t.flipV) {
    $.CGContextTranslateCTM(ctx, 0, dh);
    $.CGContextScaleCTM(ctx, 1, -1);
  }
  $.CGContextDrawImage(ctx, $.CGRectMake(0, 0, dw, dh), img);
  const out = own($.CGBitmapContextCreateImage(ctx));
  if (!out) throw new Error("could not render the image");
  return out;
}

// Source metadata for frame i, made safe to write on a re-rendered image (orientation baked in, sizes dropped).
function carriedProps(info, i, fmt, stripAll) {
  const d = $.NSMutableDictionary.dictionary;
  if (!stripAll) {
    const ref = own($.CGImageSourceCopyPropertiesAtIndex(info.src, i, null));
    if (ref) {
      const src = ObjC.castRefToObject(ref);
      const keys = ObjC.deepUnwrap(src.allKeys) || [];
      for (const k of keys) {
        if (["PixelWidth", "PixelHeight", "Orientation", "Depth", "HasAlpha", "ColorModel", "ProfileName", "IsFloat", "IsIndexed", "PrimaryImage"].includes(k)) continue;
        let v = src.objectForKey(k);
        if (k === "{TIFF}" || k === "{Exif}") {
          v = v.mutableCopy;
          v.removeObjectForKey("Orientation");
          v.removeObjectForKey("PixelXDimension");
          v.removeObjectForKey("PixelYDimension");
        }
        d.setObjectForKey(v, $(k));
      }
    }
  }
  if (!fmt.noOrientation) d.setObjectForKey(num(1), $("Orientation"));
  if (fmt.lossy) d.setObjectForKey(num(quality()), $("kCGImageDestinationLossyCompressionQuality"));
  return d;
}

// Every page of a multi-page file has the size and orientation of the first
function samePageSize(info) {
  for (let i = 1; i < info.count; i++) {
    const p = cfToJS(own($.CGImageSourceCopyPropertiesAtIndex(info.src, i, null))) || {};
    if (p.PixelWidth !== info.props.PixelWidth || p.PixelHeight !== info.props.PixelHeight || (p.Orientation || 1) !== info.orientation) return false;
  }
  return true;
}

function loopCount(info) {
  const fp = cfToJS(own($.CGImageSourceCopyProperties(info.src, null))) || {};
  return (fp["{GIF}"] && fp["{GIF}"].LoopCount) || 0;
}

function gifFrameProps(info, i) {
  const p = cfToJS(own($.CGImageSourceCopyPropertiesAtIndex(info.src, i, null))) || {};
  const g = p["{GIF}"] || p["{PNG}"] || p["{HEICS}"] || p["{WebP}"] || {};
  const delay = g.UnclampedDelayTime || g.DelayTime || 0.1;
  return plain({ DelayTime: delay, UnclampedDelayTime: delay });
}

// Write frames to `path` as `fmtKey`. frames: [{ img, props: NSDictionary }]
function writeFrames(path, fmtKey, frames, fileProps) {
  const fmt = FORMATS[fmtKey];
  const dest = own($.CGImageDestinationCreateWithURL($.NSURL.fileURLWithPath(path), $(fmt.uti), frames.length, null));
  if (!dest) throw new Error(`can't write ${fmt.name} here`);
  if (fileProps) $.CGImageDestinationSetProperties(dest, fileProps);
  for (const f of frames) $.CGImageDestinationAddImage(dest, f.img, f.props);
  if (!$.CGImageDestinationFinalize(dest)) {
    FM.removeItemAtPathError(path, $());
    throw new Error(`could not write ${fmt.name}`);
  }
}

// Target size for a resize spec, given the oriented size. Returns null when nothing would change.
function resizeTarget(spec, w, h) {
  let s;
  if (spec.mode === "pct") s = spec.value / 100;
  else if (spec.mode === "max") s = Math.min(1, spec.value / Math.max(w, h));
  else if (spec.mode === "w") s = spec.value / w;
  else if (spec.mode === "h") s = spec.value / h;
  else if (spec.mode === "fit") s = Math.min(1, spec.w / w, spec.h / h);
  const tw = Math.max(1, Math.round(w * s)), th = Math.max(1, Math.round(h * s));
  if (tw === w && th === h) return null;
  return { w: tw, h: th };
}

function sizeText(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1048576).toFixed(1)} MB`;
}
function fileSize(p) {
  const a = FM.attributesOfItemAtPathError(p, $());
  return a.isNil() ? 0 : Number(a.fileSize); // unsigned long long arrives as a string
}

// Process one image. Returns { out, detail, note } or { skipped: reason }.
function processImage(path, op) {
  const info = openImage(path);
  const srcFmt = UTI_FORMAT[info.uti] || null;
  const [kind, ...args] = op.split(":");
  const notes = [];

  if (kind === "removebg") return removeBackground(info, args[0] === "crop"); // a new cut-out: never replaces the original
  if (kind === "strip") return stripMetadata(info, srcFmt, args[0] === "gps");

  // Output format: the source's own format when it can be written, otherwise PNG/JPEG
  let fmtKey = kind === "convert" ? args[0] : srcFmt;
  if (kind === "convert" && !lookup(FORMATS, fmtKey)) throw new Error(`unknown format ${fmtKey}`);
  if (kind === "convert" && srcFmt === fmtKey) return { skipped: `already ${FORMATS[fmtKey].name}` };
  if (!fmtKey || !encodableFormats().includes(fmtKey)) {
    if (kind === "convert") throw new Error(`this Mac can't write ${FORMATS[fmtKey] ? FORMATS[fmtKey].name : fmtKey}`);
    if (kind === "optimize") return { skipped: `can't re-encode ${extOf(path).toUpperCase() || "this format"} on this Mac` };
    fmtKey = info.hasAlpha ? "png" : "jpeg"; // e.g. RAW, WebP, PSD sources
    notes.push(`saved as ${FORMATS[fmtKey].name}`);
  }
  const fmt = FORMATS[fmtKey];
  const plan = plannedOutput(path, fmt.ext);
  if (plan.note) notes.push(plan.note);
  const tmp = tempPathFor(plan.path);

  // Which frames to keep: all when both sides are animated GIFs, all pages of a multi-page TIFF saved as TIFF
  // (when they share one size), otherwise the first
  const keepAnim = info.animated && fmt.frames;
  const keepPages = !info.animated && info.count > 1 && fmtKey === "tiff" && (kind === "optimize" || samePageSize(info));
  const frameCount = keepAnim || keepPages ? info.count : 1;
  if (info.count > 1 && frameCount === 1) notes.push(info.animated ? "first frame only" : "first page only");
  const flatten = info.hasAlpha && !fmt.alpha;
  let detail = "";

  try {
    if (kind === "optimize" || (kind === "convert" && !flatten && info.orientation === 1 && !fmt.noOrientation)) {
      // Re-encode straight from the source: keeps metadata, orientation, colour profile and depth
      const dest = own($.CGImageDestinationCreateWithURL($.NSURL.fileURLWithPath(tmp), $(fmt.uti), frameCount, null));
      if (!dest) throw new Error(`can't write ${fmt.name}`);
      if (keepAnim) $.CGImageDestinationSetProperties(dest, dict({ "{GIF}": plain({ LoopCount: loopCount(info) }) }));
      for (let i = 0; i < frameCount; i++) {
        if (kind === "optimize") {
          // ImageIO copies JPEG data untouched when the format doesn't change, so decode and encode again,
          // with every property (orientation, EXIF, GIF frame delays) carried over as it was
          const img = own($.CGImageSourceCreateImageAtIndex(info.src, i, null));
          if (!img) throw new Error("could not decode the image");
          const props = ObjC.castRefToObject(own($.CGImageSourceCopyPropertiesAtIndex(info.src, i, null))).mutableCopy;
          if (fmt.lossy) props.setObjectForKey(num(quality()), $("kCGImageDestinationLossyCompressionQuality"));
          $.CGImageDestinationAddImage(dest, img, props);
        } else {
          $.CGImageDestinationAddImageFromSource(dest, info.src, i, dict({ kCGImageDestinationLossyCompressionQuality: fmt.lossy ? quality() : undefined }));
        }
      }
      if (!$.CGImageDestinationFinalize(dest)) throw new Error(`could not write ${fmt.name}`);
      if (kind === "optimize") {
        const before = fileSize(path), after = fileSize(tmp);
        if (after >= before) {
          FM.removeItemAtPathError(tmp, $());
          return { skipped: "already optimized" };
        }
        detail = `${sizeText(before)} → ${sizeText(after)}, −${Math.round((1 - after / before) * 100)}%`;
      }
    } else {
      // Re-render pixels
      let tw = info.width, th = info.height, maxSide = null, t = { flatten };
      if (kind === "resize") {
        const spec = parseResizeOp(args);
        const target = resizeTarget(spec, info.width, info.height);
        if (!target) return { skipped: `already ${info.width}×${info.height}` };
        tw = target.w;
        th = target.h;
        if (tw <= info.width && th <= info.height) maxSide = Math.max(tw, th);
      } else if (kind === "rotate") {
        t.rotate = parseInt(args[0], 10);
        if (t.rotate === 90 || t.rotate === 270) [tw, th] = [info.height, info.width];
      } else if (kind === "flip") {
        t.flipH = args[0] === "h";
        t.flipV = args[0] === "v";
      } else if (kind !== "convert") {
        throw new Error(`unknown operation ${kind}`);
      }
      if (!maxSide && tw * th > pixelLimit(info)) throw new Error(`image too large (${Math.round((tw * th) / 1e6)} MP)`);
      const frames = [];
      for (let i = 0; i < frameCount; i++) {
        let img = orientedFrame(info, i, maxSide);
        const iw = imgW(img), ih = imgH(img);
        const quarter = t.rotate === 90 || t.rotate === 270;
        const ew = quarter ? th : tw, eh = quarter ? tw : th; // expected size before rotation
        // The decoder may round one pixel differently when downscaling: keep its size then
        if (t.rotate || t.flipH || t.flipV || flatten || Math.abs(iw - ew) > 1 || Math.abs(ih - eh) > 1) img = draw(img, tw, th, t);
        let props = carriedProps(info, i, fmt, false);
        if (keepAnim) {
          props = dict({ "{GIF}": gifFrameProps(info, i) });
        }
        frames.push({ img, props });
      }
      let fileProps = null;
      if (keepAnim) fileProps = dict({ "{GIF}": plain({ LoopCount: loopCount(info) }) });
      writeFrames(tmp, fmtKey, frames, fileProps);
      detail = `${imgW(frames[0].img)}×${imgH(frames[0].img)}`;
    }
  } catch (e) {
    FM.removeItemAtPathError(tmp, $());
    throw e;
  }
  const out = commit(tmp, plan, path);
  return { out, detail: detail || sizeText(fileSize(out)), note: notes.join(", ") };
}

function parseResizeOp(args) {
  const [mode, value] = args;
  if (mode === "fit") {
    const [w, h] = value.split("x").map(Number);
    return { mode, w, h };
  }
  return { mode, value: Number(value) };
}

// Strip metadata losslessly where ImageIO allows it (JPEG, PNG, TIFF, HEIF), keeping the orientation so the
// photo still displays upright. Other formats are re-encoded without metadata.
function stripMetadata(info, srcFmt, gpsOnly) {
  const fmtKey = srcFmt && encodableFormats().includes(srcFmt) ? srcFmt : info.hasAlpha ? "png" : "jpeg";
  const fmt = FORMATS[fmtKey];
  const plan = plannedOutput(info.path, fmt.ext);
  const notes = [plan.note];
  if (fmtKey !== srcFmt) notes.push(`saved as ${fmt.name}`);
  const tmp = tempPathFor(plan.path);
  let ok = false;
  try {
    if (fmtKey === srcFmt && info.count === 1) {
      const dest = own($.CGImageDestinationCreateWithURL($.NSURL.fileURLWithPath(tmp), $.CGImageSourceGetType(info.src), 1, null));
      const opts = $.NSMutableDictionary.dictionary;
      let meta;
      if (gpsOnly) {
        meta = own($.CGImageSourceCopyMetadataAtIndex(info.src, 0, null)) || own($.CGImageMetadataCreateMutable());
        opts.setObjectForKey(bool(true), $("kCGImageMetadataShouldExcludeGPS"));
      } else {
        meta = own($.CGImageMetadataCreateMutable());
        if (info.orientation !== 1) $.CGImageMetadataSetValueMatchingImageProperty(meta, $("{TIFF}"), $("Orientation"), num(info.orientation));
      }
      opts.setObjectForKey(ObjC.castRefToObject(meta), $("kCGImageDestinationMetadata"));
      opts.setObjectForKey(bool(false), $("kCGImageDestinationMergeMetadata"));
      ok = !!dest && $.CGImageDestinationCopyImageSource(dest, info.src, opts, Ref());
      if (ok) ok = gpsOnly ? !hasGPS(tmp) : !hasPrivateMetadata(tmp);
    }
    if (!ok) {
      // Re-encode: orientation is baked into the pixels, nothing else is carried over except (for GPS-only) the rest
      FM.removeItemAtPathError(tmp, $());
      const frames = [];
      const n = (info.animated && fmt.frames) || (!info.animated && fmtKey === "tiff") ? info.count : 1;
      if (info.count > 1 && n === 1) notes.push(info.animated ? "first frame only" : "first page only");
      for (let i = 0; i < n; i++) {
        let img = orientedFrame(info, i, null);
        if (info.hasAlpha && !fmt.alpha) img = draw(img, imgW(img), imgH(img), { flatten: true });
        let props = carriedProps(info, i, fmt, !gpsOnly);
        if (gpsOnly) props.removeObjectForKey("{GPS}");
        if (n > 1 && info.animated) props = dict({ "{GIF}": gifFrameProps(info, i) });
        frames.push({ img, props });
      }
      writeFrames(tmp, fmtKey, frames, n > 1 && info.animated ? dict({ "{GIF}": plain({ LoopCount: loopCount(info) }) }) : null);
    }
  } catch (e) {
    FM.removeItemAtPathError(tmp, $());
    throw e;
  }
  const out = commit(tmp, plan, info.path);
  return { out, detail: gpsOnly ? "location removed" : "metadata removed", note: notes.filter(Boolean).join(", ") };
}

function hasGPS(path) {
  const s = own($.CGImageSourceCreateWithURL($.NSURL.fileURLWithPath(path), null));
  const p = (s && cfToJS(own($.CGImageSourceCopyPropertiesAtIndex(s, 0, null)))) || {};
  return !!p["{GPS}"];
}

function hasPrivateMetadata(path) {
  const s = own($.CGImageSourceCreateWithURL($.NSURL.fileURLWithPath(path), null));
  const p = cfToJS(own($.CGImageSourceCopyPropertiesAtIndex(s, 0, null))) || {};
  const exif = p["{Exif}"] || {};
  const tiff = p["{TIFF}"] || {};
  return !!(p["{GPS}"] || exif.DateTimeOriginal || exif.UserComment || exif.LensModel || exif.BodySerialNumber || tiff.Make || tiff.Model || tiff.Artist || p["{IPTC}"]);
}

// Offline background removal with Vision (macOS 14+): foreground instance mask → transparent PNG.
let ciContext = null;
function removeBackground(info, crop) {
  if (!backgroundRemovalAvailable()) throw new Error("background removal needs macOS 14 or later");
  const plan = plannedOutput(info.path, "png", { neverReplace: true });
  const tmp = tempPathFor(plan.path);
  const img = orientedFrame(info, 0, null);
  const handler = $.VNImageRequestHandler.alloc.initWithCGImageOptions(img, $());
  const req = $.VNGenerateForegroundInstanceMaskRequest.alloc.init;
  if (!handler.performRequestsError($([req]), Ref())) throw new Error("Vision could not analyse the image");
  const results = req.results;
  // NSUInteger results arrive from the bridge as strings
  if (results.isNil() || Number(results.count) === 0) throw new Error("no subject found");
  const obs = results.objectAtIndex(0);
  if (Number(obs.allInstances.count) === 0) throw new Error("no subject found");
  const buf = obs.generateMaskedImageOfInstancesFromRequestHandlerCroppedToInstancesExtentError(obs.allInstances, handler, crop, Ref());
  if (!buf) throw new Error("could not build the mask");
  const ci = $.CIImage.imageWithCVPixelBuffer(buf);
  if (!ciContext) ciContext = $.CIContext.contextWithOptions($()); // one per run: creating it is slow
  const ctx = ciContext;
  // Keep the photo's own RGB colour space (Display P3 from iPhones, Adobe RGB…) and 16-bit depth;
  // grey, CMYK and other models are written as sRGB
  const cs = $.CGImageGetColorSpace(img);
  const outCS = cs && $.CGColorSpaceGetModel(cs) === 1 ? cs : own($.CGColorSpaceCreateWithName($.kCGColorSpaceSRGB));
  const format = Number($.CGImageGetBitsPerComponent(img)) > 8 ? $.kCIFormatRGBA16 : $.kCIFormatRGBA8;
  const ok = ctx.writePNGRepresentationOfImageToURLFormatColorSpaceOptionsError(
    ci, $.NSURL.fileURLWithPath(tmp), format, outCS, $(), Ref());
  if (!ok) {
    FM.removeItemAtPathError(tmp, $());
    throw new Error("could not write the PNG");
  }
  const out = commit(tmp, plan, info.path);
  const ext = ci.extent;
  const note = [plan.note, info.count > 1 ? (info.animated ? "first frame only" : "first page only") : ""].filter(Boolean).join(", ");
  return { out, detail: `${Math.round(ext.size.width)}×${Math.round(ext.size.height)}, transparent PNG`, note };
}

// ---------- apply (image action) ----------

const OP_VERBS = {
  resize: ["Resized", "resize"], convert: ["Converted", "convert"], rotate: ["Rotated", "rotate"], flip: ["Flipped", "flip"],
  strip: ["Cleaned", "clean"], optimize: ["Optimized", "optimize"], removebg: ["Removed the background of", "process"],
};

// Large file lists are kept in the cache and passed as "@<path>" so the Script Filter JSON stays small
const INLINE_FILES_MAX = 8000;
function filesVar(files) {
  const json = JSON.stringify(files);
  if (json.length <= INLINE_FILES_MAX) return json;
  let h = 5381;
  for (let i = 0; i < json.length; i++) h = ((h * 33) ^ json.charCodeAt(i)) >>> 0;
  const dir = `${cacheDir()}/selections`;
  FM.createDirectoryAtPathWithIntermediateDirectoriesAttributesError(dir, true, $(), $());
  const path = `${dir}/${h.toString(16)}-${files.length}.json`;
  if (!exists(path)) {
    pruneSelections(dir);
    $(json).writeToFileAtomicallyEncodingError(path, true, $.NSUTF8StringEncoding, $());
  }
  return "@" + path;
}
function pruneSelections(dir) {
  const list = FM.contentsOfDirectoryAtPathError(dir, $());
  if (list.isNil()) return;
  const now = Date.now();
  for (const name of ObjC.deepUnwrap(list) || []) {
    const a = FM.attributesOfItemAtPathError(`${dir}/${name}`, $());
    if (!a.isNil() && now - a.fileModificationDate.timeIntervalSince1970 * 1000 > 86400000) FM.removeItemAtPathError(`${dir}/${name}`, $());
  }
}

function filesFromEnv(name = "mt_files") {
  try {
    let raw = env(name, "[]");
    if (raw.startsWith("@")) {
      const path = raw.slice(1);
      // only lists this workflow wrote
      if (!path.startsWith(`${cacheDir()}/selections/`) || path.includes("/../")) return [];
      raw = readText(path) || "[]";
    }
    const f = JSON.parse(raw);
    return Array.isArray(f) ? f.filter((x) => typeof x === "string" && x) : [];
  } catch (e) {
    return [];
  }
}

// Files per osascript process. JXA keeps what Vision and Core Image return until the process ends (about
// 50 MB per 12 MP photo; autorelease pools can't be drained from JXA), so big batches run in chunks, each in
// a fresh process. A crash on one broken file also only loses its chunk.
const CHUNK = Number(env("MT_TEST_CHUNK", "")) || 10;

function applyImages(op) {
  const files = filesFromEnv();
  if (!files.length) return "No files to process";
  const kind = op.split(":")[0];
  const [verb] = OP_VERBS[kind] || ["Processed"];
  if (env("MT_CHUNK", "") === "1") return JSON.stringify(processFiles(files, op));
  if (files.length >= 10) notifyAlfred(`Processing ${files.length} images…`);
  let res;
  if (files.length <= CHUNK) res = processFiles(files, op);
  else {
    res = { done: [], failed: [], skipped: [], notes: [] };
    for (let i = 0; i < files.length; i += CHUNK) {
      const r = runChunk(files.slice(i, i + CHUNK), op);
      for (const k of Object.keys(res)) res[k].push(...r[k]);
    }
  }
  const { done, failed, skipped } = res;
  appendLog(`${new Date().toISOString()} ${op}: ${done.length} ok, ${skipped.length} skipped, ${failed.length} failed` +
    failed.map(([f, m]) => `\n  ${f}: ${m}`).join(""));
  if (env("mt_reveal", "0") === "1" && done.length) reveal(done.map(([, r]) => r.out));
  let copied = "";
  if (env("mt_copy", "0") === "1" && done.length) {
    copyFiles(done.map(([, r]) => r.out));
    copied = ` · ${done.length === 1 ? "copied" : `${done.length} files copied`} to the clipboard`;
  }
  return summary(verb, done, failed, skipped, new Set(res.notes)) + copied;
}

function processFiles(files, op) {
  const done = [], failed = [], skipped = [], notes = new Set();
  for (const f of files) {
    try {
      if (!exists(f)) throw new Error("file not found");
      if (isDir(f)) throw new Error("it's a folder");
      const r = processImage(f, op);
      if (r.skipped) skipped.push([f, r.skipped]);
      else {
        done.push([f, r]);
        if (r.note) notes.add(r.note);
      }
    } catch (e) {
      failed.push([f, e && e.message ? e.message : String(e)]);
    } finally {
      releaseOwned();
    }
  }
  return { done, failed, skipped, notes: [...notes] };
}

// Process `files` in a child osascript running this same script, and return its results
function runChunk(files, op) {
  const args = ObjC.deepUnwrap($.NSProcessInfo.processInfo.arguments);
  let script = args.find((a) => /(^|\/)media\.js$/.test(a));
  if (!script) return processFiles(files, op);
  if (!script.startsWith("/")) script = `${FM.currentDirectoryPath.js}/${script}`;
  const t = $.NSTask.alloc.init;
  t.executableURL = $.NSURL.fileURLWithPath("/usr/bin/osascript");
  t.arguments = $(["-l", "JavaScript", script, "apply", op]);
  const e = ENV.mutableCopy;
  e.setObjectForKey($(JSON.stringify(files)), $("mt_files"));
  e.setObjectForKey($("1"), $("MT_CHUNK"));
  t.environment = e;
  const p = $.NSPipe.pipe;
  t.standardOutput = p;
  t.standardError = $.NSFileHandle.fileHandleWithNullDevice;
  const crashed = (why) => ({ done: [], failed: files.map((f) => [f, why]), skipped: [], notes: [] });
  if (!t.launchAndReturnError($())) return crashed("could not start");
  const d = p.fileHandleForReading.readDataToEndOfFile;
  t.waitUntilExit;
  try {
    const r = JSON.parse($.NSString.alloc.initWithDataEncoding(d, $.NSUTF8StringEncoding).js);
    if (r && Array.isArray(r.done)) return r;
  } catch (err) {
    // fall through
  }
  return crashed("the image engine crashed");
}

function summary(verb, done, failed, skipped, notes) {
  const parts = [];
  if (done.length === 1 && !failed.length && !skipped.length) {
    const [f, r] = done[0];
    parts.push(`${verb} ${baseName(f)} → ${baseName(r.out)} (${r.detail})`);
  } else if (done.length) {
    parts.push(`${verb} ${done.length} file${done.length === 1 ? "" : "s"}`);
  }
  if (skipped.length) {
    parts.push(skipped.length === 1 && !done.length && !failed.length
      ? `Nothing to do for ${baseName(skipped[0][0])}: ${skipped[0][1]}`
      : `${skipped.length} skipped (${baseName(skipped[0][0])}: ${skipped[0][1]}${skipped.length > 1 ? "…" : ""})`);
  }
  if (failed.length) {
    parts.push(failed.length === 1
      ? `Failed: ${baseName(failed[0][0])}: ${failed[0][1]}`
      : `${failed.length} failed (${baseName(failed[0][0])}: ${failed[0][1]}…)`);
  }
  let msg = parts.join(" · ");
  if (notes.size) msg += ` · ${[...notes].join(", ")}`;
  return msg;
}

function appendLog(line) {
  const p = `${cacheDir()}/media-toolkit.log`;
  if (fileSize(p) > 2 * 1048576) FM.removeItemAtPathError(p, $());
  if (!exists(p)) FM.createFileAtPathContentsAttributes(p, $(), $());
  const h = $.NSFileHandle.fileHandleForWritingAtPath(p);
  if (h.isNil()) return;
  h.seekToEndOfFile;
  h.writeData($(line + "\n").dataUsingEncoding($.NSUTF8StringEncoding));
  h.closeFile;
}

// Post a notification through the workflow's External Trigger (the action's own notification comes at the end)
function notifyAlfred(msg) {
  const t = env("MT_TEST_NOTIFY_FILE", "");
  if (t) {
    const prev = readText(t) || "";
    $(prev + msg + "\n").writeToFileAtomicallyEncodingError(t, true, $.NSUTF8StringEncoding, $());
    return;
  }
  if (TEST) return;
  try {
    Application("com.runningwithcrayons.Alfred").runTrigger("notify", { inWorkflow: env("alfred_workflow_bundleid", ""), withArgument: msg });
  } catch (e) {
    // Alfred not reachable: the final notification still arrives
  }
}

// Put the files on the clipboard, ready to paste into Finder, Mail or a chat
function copyFiles(paths) {
  const t = env("MT_TEST_CLIPBOARD_FILE", "");
  if (t) {
    $(paths.join("\n")).writeToFileAtomicallyEncodingError(t, true, $.NSUTF8StringEncoding, $());
    return;
  }
  if (TEST) return;
  const pb = $.NSPasteboard.generalPasteboard;
  pb.clearContents;
  pb.writeObjects($(paths.map((p) => $.NSURL.fileURLWithPath(p))));
}

function reveal(paths) {
  const t = env("MT_TEST_REVEAL_FILE", "");
  if (t) {
    $(paths.join("\n")).writeToFileAtomicallyEncodingError(t, true, $.NSUTF8StringEncoding, $());
    return;
  }
  if (TEST) return;
  $.NSWorkspace.sharedWorkspace.activateFileViewerSelectingURLs($(paths.map((p) => $.NSURL.fileURLWithPath(p))));
}

// ---------- video / audio ----------

function which(name) {
  const custom = env("ffmpeg_path", "").trim().replace(/^~(?=\/)/, $.NSHomeDirectory().js);
  const test = env("MT_TEST_FFMPEG", "");
  if (test === "none") return null;
  if (test) return name === "ffmpeg" ? test : `${dirOf(test)}/${name}`;
  const dirs = [];
  if (custom && name === "ffmpeg" && !isDir(custom) && FM.isExecutableFileAtPath(custom)) return custom;
  if (custom) dirs.push(isDir(custom) ? custom : dirOf(custom));
  // Homebrew (Apple Silicon, Intel), MacPorts, Nix; Alfred's own PATH doesn't include them
  const home = $.NSHomeDirectory().js;
  dirs.push("/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin", `${home}/.nix-profile/bin`, "/run/current-system/sw/bin", "/nix/var/nix/profiles/default/bin",
    ...env("PATH", "").split(":").filter(Boolean));
  for (const d of dirs) {
    const p = `${d}/${name}`;
    if (FM.isExecutableFileAtPath(p) && !isDir(p)) return p;
  }
  return null;
}
let appleSiliconCache = null;
function isAppleSilicon() {
  if (appleSiliconCache !== null) return appleSiliconCache;
  const t = env("MT_TEST_ARCH", "");
  if (t) return (appleSiliconCache = t === "arm64");
  const u = $.NSTask.alloc.init;
  u.executableURL = $.NSURL.fileURLWithPath("/usr/sbin/sysctl");
  u.arguments = $(["-n", "hw.optional.arm64"]);
  const p = $.NSPipe.pipe;
  u.standardOutput = p;
  u.standardError = $.NSFileHandle.fileHandleWithNullDevice;
  if (!u.launchAndReturnError($())) return (appleSiliconCache = false);
  const d = p.fileHandleForReading.readDataToEndOfFile;
  u.waitUntilExit;
  return (appleSiliconCache = $.NSString.alloc.initWithDataEncoding(d, $.NSUTF8StringEncoding).js.trim() === "1");
}

// The CPU architectures of a Mach-O executable (["arm64"], ["x86_64"], both for a universal binary),
// or null when it can't be told (a wrapper script, unreadable). Reads only the header.
function machOArchs(path) {
  const real = $(path).stringByResolvingSymlinksInPath.js;
  const h = $.NSFileHandle.fileHandleForReadingAtPath(real);
  if (h.isNil()) return null;
  const data = h.readDataOfLength(4096);
  h.closeFile;
  const str = $.NSString.alloc.initWithDataEncoding(data, $.NSISOLatin1StringEncoding);
  if (str.isNil()) return null;
  const b = str.js;
  const be = (o) => ((b.charCodeAt(o) << 24) | (b.charCodeAt(o + 1) << 16) | (b.charCodeAt(o + 2) << 8) | b.charCodeAt(o + 3)) >>> 0;
  const le = (o) => ((b.charCodeAt(o + 3) << 24) | (b.charCodeAt(o + 2) << 16) | (b.charCodeAt(o + 1) << 8) | b.charCodeAt(o)) >>> 0;
  const name = (cpu) => (cpu === 0x0100000c ? "arm64" : cpu === 0x01000007 ? "x86_64" : "other");
  if (b.length < 8) return null;
  if (be(0) === 0xcafebabe || be(0) === 0xcafebabf) {
    // universal binary: big-endian fat header, 20 (or 32 for fat64) bytes per architecture
    const n = be(4), size = be(0) === 0xcafebabf ? 32 : 20;
    const out = [];
    for (let i = 0; i < n && 8 + i * size + 4 <= b.length; i++) out.push(name(be(8 + i * size)));
    return out.length ? out : null;
  }
  if (le(0) === 0xfeedfacf || le(0) === 0xfeedface) return [name(le(4))];
  return null;
}

// Constant-quality VideoToolbox encoding (-q:v) exists only in ffmpeg builds for arm64: an Intel ffmpeg
// (an old /usr/local Homebrew under Rosetta) fails with "-q:v qscale not available for encoder".
function ffmpegRunsNative(ffmpeg) {
  const t = env("MT_TEST_FFMPEG_ARCHS", "");
  const archs = t ? t.split(",") : machOArchs(ffmpeg);
  if (!isAppleSilicon()) return false;
  return archs === null ? true : archs.includes("arm64");
}

// Operation catalogue for video and audio. `needs`: "ffmpeg" or a fallback tool per kind.
const AV_OPS = [
  { id: "mp4", kinds: ["video"], title: "Convert to MP4 (H.264)", sub: "Plays everywhere · hardware encoder", words: "convert mp4 h264 h.264" },
  { id: "hevc", kinds: ["video"], title: "Convert to MP4 (HEVC)", sub: "About half the size of H.264 · hardware encoder", words: "convert mp4 hevc h265 h.265" },
  { id: "webm", kinds: ["video"], title: "Convert to WebM (VP9)", sub: "For the web", words: "convert webm vp9" },
  { id: "mov", kinds: ["video"], title: "Convert to MOV", sub: "QuickTime movie, H.264", words: "convert mov quicktime" },
  { id: "gif", kinds: ["video"], title: "Convert to GIF", sub: "", words: "convert gif animated" },
  { id: "compress:23", kinds: ["video"], title: "Compress (high quality)", sub: "H.264 CRF 23", words: "compress smaller reduce size high" },
  { id: "compress:28", kinds: ["video"], title: "Compress (balanced)", sub: "H.264 CRF 28", words: "compress smaller reduce size balanced medium" },
  { id: "compress:32", kinds: ["video"], title: "Compress (smallest)", sub: "H.264 CRF 32", words: "compress smaller reduce size smallest low" },
  { id: "scale:1080", kinds: ["video"], title: "Resize to 1080p", sub: "Short side at most 1080 px", words: "resize scale 1080p 1080 full hd" },
  { id: "scale:720", kinds: ["video"], title: "Resize to 720p", sub: "Short side at most 720 px", words: "resize scale 720p 720 hd" },
  { id: "scale:480", kinds: ["video"], title: "Resize to 480p", sub: "Short side at most 480 px", words: "resize scale 480p 480 sd" },
  { id: "mute", kinds: ["video"], title: "Remove audio", sub: "Copies the video stream without re-encoding", words: "mute remove audio silent strip sound" },
  { id: "mp3", kinds: ["video", "audio"], title: "MP3", sub: "", words: "mp3 extract audio convert" },
  { id: "m4a", kinds: ["video", "audio"], title: "M4A (AAC)", sub: "", words: "m4a aac extract audio convert" },
  { id: "wav", kinds: ["video", "audio"], title: "WAV", sub: "", words: "wav wave extract audio convert" },
  { id: "flac", kinds: ["video", "audio"], title: "FLAC", sub: "", words: "flac lossless extract audio convert" },
  { id: "aiff", kinds: ["audio"], title: "AIFF", sub: "", words: "aiff aif convert" },
];
const AUDIO_OUT = { mp3: "mp3", m4a: "m4a", wav: "wav", flac: "flac", aiff: "aiff" };

function parseTime(s) {
  s = s.trim();
  if (!s) return null;
  if (!/^\d+(:\d{1,2}){0,2}(\.\d+)?$/.test(s)) return NaN;
  const parts = s.split(":").map(parseFloat);
  if (parts.slice(1).some((p) => p >= 60)) return NaN; // 0:75 is a typo, not 1:15
  return parts.reduce((acc, part) => acc * 60 + part, 0);
}
function parseTrim(text) {
  const m = text.trim().match(/^(\S*)\s*(?:-|–|—|to)\s*(\S*)$/);
  if (!m) return null;
  const a = m[1] ? parseTime(m[1]) : 0, b = m[2] ? parseTime(m[2]) : null;
  if (isNaN(a) || (b !== null && isNaN(b))) return null;
  if (b !== null && b <= a) return null;
  return { start: a, end: b };
}
function fmtTime(t) {
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
  const ss = (Math.round(s * 1000) / 1000).toString().replace(/^(\d)(\.|$)/, "0$1$2");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

// The tool that will run `op` for a file of `kind`/`ext`, or null.
// Without ffmpeg: avconvert (AVFoundation) for MP4/MOV/M4V video, afconvert (Core Audio) for audio.
// The encoders ffmpeg needs for an operation on a file of `kind`/`ext`
function encodersFor(opId, kind, ext) {
  const base = opId.split(":")[0];
  if (base === "trim") {
    if (kind === "video") return ["h264_videotoolbox"];
    return ext === "mp3" ? ["libmp3lame"] : [];
  }
  return { mp4: ["h264_videotoolbox"], mov: ["h264_videotoolbox"], scale: ["h264_videotoolbox"], hevc: ["hevc_videotoolbox"],
    webm: ["libvpx-vp9", "libopus"], compress: ["libx264"], mp3: ["libmp3lame"] }[base] || [];
}

// The encoders of this ffmpeg build (null when unknown). Builds differ: a minimal or LGPL ffmpeg has no
// libx264, libvpx or LAME, and the conversion would fail with "Unknown encoder". Cached per binary.
let encoderCache;
function ffmpegEncoders(ffmpeg) {
  if (encoderCache !== undefined) return encoderCache;
  const t = env("MT_TEST_FFMPEG_ENCODERS", "");
  if (t) return (encoderCache = t.split(","));
  encoderCache = null;
  const real = $(ffmpeg).stringByResolvingSymlinksInPath.js;
  const a = FM.attributesOfItemAtPathError(real, $());
  if (a.isNil()) return null;
  const stamp = `${real}\n${a.fileModificationDate.timeIntervalSince1970}\n${Number(a.fileSize)}`;
  const cachePath = `${cacheDir()}/ffmpeg-encoders.txt`;
  const cached = readText(cachePath);
  if (cached && cached.startsWith(stamp + "\n")) return (encoderCache = cached.slice(stamp.length + 1).split(" ").filter(Boolean));
  // ffmpeg -encoders into a file, with a 5 s limit so that a broken binary can't hang the Script Filter
  const out = `${cacheDir()}/.encoders-${$.NSProcessInfo.processInfo.processIdentifier}.txt`;
  FM.createFileAtPathContentsAttributes(out, $(), $());
  const fh = $.NSFileHandle.fileHandleForWritingAtPath(out);
  const task = $.NSTask.alloc.init;
  task.executableURL = $.NSURL.fileURLWithPath(ffmpeg);
  task.arguments = $(["-hide_banner", "-nostdin", "-encoders"]);
  task.standardOutput = fh;
  task.standardError = $.NSFileHandle.fileHandleWithNullDevice;
  task.standardInput = $.NSFileHandle.fileHandleWithNullDevice;
  if (task.launchAndReturnError($())) {
    const end = Date.now() + 5000;
    while (task.isRunning && Date.now() < end) $.NSThread.sleepForTimeInterval(0.02);
    if (task.isRunning) task.terminate;
    else {
      const text = readText(out) || "";
      const list = [...text.matchAll(/^ [VAS][A-Z.]{5} (\S+)/gm)].map((m) => m[1]);
      if (list.length) {
        encoderCache = list;
        $(`${stamp}\n${list.join(" ")}`).writeToFileAtomicallyEncodingError(cachePath, true, $.NSUTF8StringEncoding, $());
      }
    }
  }
  if (!fh.isNil()) fh.closeFile;
  FM.removeItemAtPathError(out, $());
  return encoderCache;
}

// Encoders this op needs that the installed ffmpeg lacks ([] when all there, or when that can't be told)
function missingEncoders(opId, kind, ext, ffmpeg) {
  const need = encodersFor(opId, kind, ext);
  if (!ffmpeg || !need.length) return [];
  const have = ffmpegEncoders(ffmpeg);
  return have ? need.filter((e) => !have.includes(e)) : [];
}

function toolFor(opId, kind, ext, ffmpeg) {
  if (ffmpeg && !missingEncoders(opId, kind, ext, ffmpeg).length) return "ffmpeg";
  const base = opId.split(":")[0];
  if (kind === "video" && AVF_VIDEO_EXT.includes(ext) && ["mp4", "hevc", "mov", "scale", "m4a", "trim"].includes(base)) return "avconvert";
  if (kind === "audio" && CA_AUDIO_EXT.includes(ext)) {
    if (["m4a", "wav", "flac", "aiff"].includes(base)) return "afconvert";
    if (base === "trim") return "avconvert";
  }
  return null;
}

function outputExtFor(opId, src, kind, tool) {
  const base = opId.split(":")[0];
  if (base === "trim" && kind === "audio" && tool === "avconvert") return "m4a";
  const e = extOf(src);
  if (AUDIO_OUT[base]) return AUDIO_OUT[base];
  if (base === "hevc" || base === "mp4" || base === "compress" || base === "scale") return "mp4";
  if (base === "webm" || base === "gif" || base === "mov") return base;
  if (base === "mute") return e;
  if (base === "trim") {
    if (kind === "audio") return ["mp3", "m4a", "wav", "flac", "aiff", "aif"].includes(e) ? e : "m4a";
    return ["mp4", "mov", "m4v"].includes(e) ? e : "mp4";
  }
  return e;
}

function vtArgs(codec, ffmpeg) {
  const hevc = codec === "hevc";
  // allow_sw: fall back to Apple's software encoder when there is no hardware encoder for the codec (older
  // Intel Macs have no HEVC encoder) instead of failing with "cannot create compression session"
  const a = ["-c:v", hevc ? "hevc_videotoolbox" : "h264_videotoolbox", "-allow_sw", "1"];
  if (ffmpegRunsNative(ffmpeg)) a.push("-q:v", hevc ? "60" : "65");
  else a.push("-b:v", hevc ? "5M" : "8M");
  // HEVC keeps 10-bit sources (HDR from iPhones) in 10 bits (Main 10) through the "format" filter below;
  // H.264 is always 8-bit 4:2:0, the only kind every player decodes
  if (hevc) a.push("-tag:v", "hvc1");
  else a.push("-pix_fmt", "yuv420p");
  return a;
}

// Separates the two ffmpeg runs of a job (palette, then GIF) in the job file; never a real argument
const THEN = "::then::";

// Build the argv that converts `src` into `tmp`.
function buildCommand(tool, opId, src, tmp, kind, progress, trim) {
  const [base, arg] = opId.split(":");
  if (tool === "ffmpeg") {
    const ff = which("ffmpeg");
    const head = [ff, "-hide_banner", "-nostdin", "-y", "-loglevel", "error"];
    const pre = [...head, "-progress", progress, "-nostats"];
    const inp = [];
    // -ss before -i seeks the input; when re-encoding, ffmpeg decodes from the previous keyframe and drops the
    // frames before the start, so the cut is frame-accurate. -t after -i is the length of the output.
    if (trim) inp.push("-ss", String(trim.start));
    inp.push("-i", "file:" + src);
    if (trim && trim.end !== null) inp.push("-t", String(trim.end - trim.start));
    const out = "file:" + tmp;
    const aac = ["-c:a", "aac", "-b:a", "192k"];
    // The first real video stream (V: not cover art) and every audio stream; subtitles and data streams are left
    // out because MP4/WebM can't take most of them (bitmap subtitles from MKV would fail the whole conversion)
    const vmap = ["-map", "0:V:0", "-map", "0:a?", "-sn", "-dn"];
    const fast = ["-movflags", "+faststart"];
    // H.264/HEVC 4:2:0 need even dimensions
    const even = "scale=trunc(iw/2)*2:trunc(ih/2)*2";
    const hevcVf = `${even},format=yuv420p|p010le`; // 8-bit stays 8-bit, 10-bit stays 10-bit
    switch (base) {
      case "mp4": return [...pre, ...inp, ...vmap, ...vtArgs("h264", ff), "-vf", even, ...aac, ...fast, out];
      case "hevc": return [...pre, ...inp, ...vmap, ...vtArgs("hevc", ff), "-vf", hevcVf, ...aac, ...fast, out];
      case "mov": return [...pre, ...inp, ...vmap, ...vtArgs("h264", ff), "-vf", even, ...aac, out];
      // yuv420p: browsers only play 8-bit 4:2:0 VP9 reliably. -ac 2: libopus rejects the common 5.1(side)
      // layout of AC-3/DTS tracks ("Invalid channel layout 5.1(side)"), and stereo suits the web anyway.
      case "webm": return [...pre, ...inp, ...vmap, "-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-row-mt", "1", "-deadline", "good", "-cpu-used", "4", "-pix_fmt", "yuv420p", "-c:a", "libopus", "-b:a", "128k", "-ac", "2", out];
      case "gif": {
        // Two runs with a palette file in between: a one-pass split/palettegen/paletteuse graph keeps every
        // frame in memory until the end of the video (gigabytes for a few minutes of 1080p)
        const w = parseInt(env("gif_width", "480"), 10);
        const fps = parseInt(env("gif_fps", "15"), 10) || 15;
        const scale = w > 0 ? `,scale=w='min(${w},iw)':h=-1:flags=lanczos` : "";
        const palette = `${tmp}-aux-palette.png`;
        return [
          ...head, ...inp, "-map", "0:V:0", "-an", "-sn", "-dn", "-vf", `fps=${fps}${scale},palettegen=stats_mode=diff`, "-frames:v", "1", "-update", "1", "file:" + palette,
          THEN,
          ...pre, ...inp, "-i", "file:" + palette, "-filter_complex", `[0:V:0]fps=${fps}${scale}[v];[v][1:v]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle`,
          "-an", "-sn", "-dn", "-loop", "0", out,
        ];
      }
      case "compress": return [...pre, ...inp, ...vmap, "-c:v", "libx264", "-crf", arg, "-preset", "medium", "-pix_fmt", "yuv420p", "-vf", even, "-c:a", "aac", "-b:a", "128k", ...fast, out];
      case "scale": {
        const n = parseInt(arg, 10);
        const vf = `scale=w='if(gte(iw,ih),-2,trunc(min(${n},iw)/2)*2)':h='if(gte(iw,ih),trunc(min(${n},ih)/2)*2,-2)'`;
        return [...pre, ...inp, ...vmap, ...vtArgs("h264", ff), "-vf", vf, ...aac, ...fast, out];
      }
      case "mute": return [...pre, ...inp, "-map", "0:V", "-map", "0:s?", "-c", "copy", "-an", out];
      case "mp3": return [...pre, ...inp, "-vn", "-map", "0:a:0", "-c:a", "libmp3lame", "-q:a", "2", out];
      case "m4a": return [...pre, ...inp, "-vn", "-map", "0:a:0", ...aac, out];
      case "wav": return [...pre, ...inp, "-vn", "-map", "0:a:0", "-c:a", "pcm_s16le", out];
      case "flac": return [...pre, ...inp, "-vn", "-map", "0:a:0", "-c:a", "flac", out];
      case "aiff": return [...pre, ...inp, "-vn", "-map", "0:a:0", "-c:a", "pcm_s16be", out];
      case "trim": {
        if (kind === "audio") {
          const codec = { mp3: ["-c:a", "libmp3lame", "-q:a", "2"], wav: ["-c:a", "pcm_s16le"], flac: ["-c:a", "flac"], aiff: ["-c:a", "pcm_s16be"], aif: ["-c:a", "pcm_s16be"] }[extOf(tmp)] || aac;
          return [...pre, ...inp, "-vn", "-map", "0:a:0", ...codec, out];
        }
        return [...pre, ...inp, ...vmap, ...vtArgs("h264", ff), "-vf", even, ...aac, ...fast, out];
      }
    }
    return null;
  }
  if (tool === "avconvert") {
    const preset = {
      mp4: "PresetHighestQuality", mov: "PresetHighestQuality", hevc: "PresetHEVCHighestQuality", m4a: "PresetAppleM4A",
      scale: AVCONVERT_SCALE[arg], trim: kind === "audio" ? "PresetAppleM4A" : "PresetHighestQuality",
    }[base];
    if (!preset) return null;
    const a = ["/usr/bin/avconvert", "--source", src, "--preset", preset, "--output", tmp, "--progress"];
    if (trim) {
      a.push("--start", String(trim.start));
      if (trim.end !== null) a.push("--duration", String(trim.end - trim.start));
    }
    return a;
  }
  if (tool === "afconvert") {
    // VBR AAC (a fixed bitrate fails for low sample rates and mono sources). vbrq 91 is about 192 kbit/s for
    // stereo music, like the ffmpeg path; the default (64) is about 128 kbit/s.
    const fmt = { m4a: ["-f", "m4af", "-d", "aac", "-s", "3", "-q", "127", "-ue", "vbrq", "91"], wav: ["-f", "WAVE", "-d", "LEI16"], flac: ["-f", "flac", "-d", "flac"], aiff: ["-f", "AIFF", "-d", "BEI16"] }[base];
    if (!fmt) return null;
    return ["/usr/bin/afconvert", ...fmt, src, tmp];
  }
  return null;
}
const AVCONVERT_SCALE = { 1080: "Preset1920x1080", 720: "Preset1280x720", 480: "Preset640x480" };

// Queue video/audio jobs for worker.sh. Each job file holds NUL-terminated fields:
// batch, index, count, label, source, final path, temp path, replace (0/1), reveal (0/1), tool, argv…
function enqueue(opId) {
  const files = filesFromEnv();
  if (!files.length) return { queued: 0, message: "No files to process" };
  const ffmpeg = which("ffmpeg");
  const dir = `${cacheDir()}/queue`;
  FM.createDirectoryAtPathWithIntermediateDirectoriesAttributesError(dir, true, $(), $());
  // Only operations the Script Filters offer: the id ends up in ffmpeg arguments
  if (!/^(?:mp4|hevc|webm|mov|gif|gif:.+|mute|mp3|m4a|wav|flac|aiff|compress:(?:[1-4]\d|5[01])|scale:[1-9]\d{1,3}|trim:.*)$/.test(opId)) {
    return { queued: 0, message: `Unknown operation: ${opId}` };
  }
  const base = opId.split(":")[0];
  // trim:<range>, and gif:<range> for a GIF of part of the video (":" is written "_" in the id)
  const ranged = base === "trim" || (base === "gif" && opId.includes(":"));
  const trim = ranged ? parseTrim(opId.slice(base.length + 1).replace(/_/g, ":")) : null;
  if (ranged && !trim) return { queued: 0, message: `Invalid ${base} range` };
  const batch = `${Date.now()}-${$.NSProcessInfo.processInfo.processIdentifier}`;
  // What to do with the results: 1 reveal in Finder, 2 copy to the clipboard, 0 nothing (v1.0.0 wrote only 0/1)
  const after = env("mt_reveal", "0") === "1" ? "1" : env("mt_copy", "0") === "1" ? "2" : "0";
  const jobs = [], failed = [], taken = [], notes = new Set();
  for (const f of files) {
    const kind = kindOf(f);
    if (!exists(f) || isDir(f) || (kind !== "video" && kind !== "audio")) {
      failed.push(`${baseName(f)}: not a video or audio file`);
      continue;
    }
    const tool = toolFor(opId, kind, extOf(f), ffmpeg);
    if (!tool) {
      const lacks = missingEncoders(opId, kind, extOf(f), ffmpeg);
      failed.push(`${baseName(f)}: ${lacks.length ? `this ffmpeg has no ${lacks.join(" or ")} encoder` : "needs ffmpeg"}`);
      continue;
    }
    const ext = outputExtFor(opId, f, kind, tool);
    // a trimmed clip always gets the suffix, even in another format: "song.m4a" would look like the whole song
    const plan = plannedOutput(f, ext, { neverReplace: base === "trim", alwaysSuffix: base === "trim" });
    if (!plan.replace && taken.includes(plan.path)) plan.path = uniquePath(dirOf(plan.path), stemOf(plan.path), extOf(plan.path), taken);
    taken.push(plan.path);
    const tmp = tempPathFor(plan.path);
    const cmd = buildCommand(tool, opId, f, tmp, kind, `file:${cacheDir()}/progress.txt`, trim);
    if (!cmd) {
      failed.push(`${baseName(f)}: can't do this with ${tool}`);
      continue;
    }
    if (plan.note) notes.add(plan.note);
    jobs.push([baseName(f), f, plan.path, tmp, plan.replace ? "1" : "0", after, tool, ...cmd]);
  }
  jobs.forEach((j, i) => {
    const fields = [batch, String(i + 1), String(jobs.length), ...j];
    const name = `${dir}/${batch}-${String(i).padStart(5, "0")}.job`;
    const tmpName = `${dir}/.${batch}-${i}.tmp`;
    $(fields.join("\0") + "\0").writeToFileAtomicallyEncodingError(tmpName, false, $.NSUTF8StringEncoding, $());
    FM.moveItemAtPathToPathError(tmpName, name, $());
  });
  let message = "";
  if (jobs.length) message = `Queued ${jobs.length} file${jobs.length === 1 ? "" : "s"}${failed.length ? ` · ${failed.length} skipped (${failed[0]})` : ""}${notes.size ? ` · ${[...notes].join(", ")}` : ""}`;
  else message = `Nothing queued: ${failed[0] || "no matching files"}`;
  return { queued: jobs.length, message };
}

// ---------- worker status ----------

function readText(p) {
  const s = $.NSString.stringWithContentsOfFileEncodingError(p, $.NSUTF8StringEncoding, $());
  return s.isNil() ? null : s.js;
}
// The last `bytes` bytes of a file as text ("" when missing)
function readTail(p, bytes) {
  const h = $.NSFileHandle.fileHandleForReadingAtPath(p);
  if (h.isNil()) return "";
  const size = Number(h.seekToEndOfFile);
  h.seekToFileOffset(Math.max(0, size - bytes));
  const d = h.readDataToEndOfFile;
  h.closeFile;
  const s = $.NSString.alloc.initWithDataEncoding(d, $.NSISOLatin1StringEncoding); // never fails on a cut UTF-8 sequence
  return s.isNil() ? "" : s.js;
}
let killBound = false;
function processAlive(pid) {
  if (!killBound) {
    ObjC.bindFunction("kill", ["int", ["int", "int"]]);
    killBound = true;
  }
  return $.kill(pid, 0) === 0;
}
// A pid survives in the lock after a crash or restart and may since belong to another process
function isWorker(pid) {
  if (!processAlive(pid)) return false;
  const t = $.NSTask.alloc.init;
  t.executableURL = $.NSURL.fileURLWithPath("/bin/ps");
  t.arguments = $(["-p", String(pid), "-o", "command="]);
  const p = $.NSPipe.pipe;
  t.standardOutput = p;
  t.standardError = $.NSFileHandle.fileHandleWithNullDevice;
  if (!t.launchAndReturnError($())) return false;
  const d = p.fileHandleForReading.readDataToEndOfFile;
  t.waitUntilExit;
  return /worker\.sh/.test($.NSString.alloc.initWithDataEncoding(d, $.NSUTF8StringEncoding).js);
}
function workerStatus() {
  const cache = cacheDir();
  const pid = readText(`${cache}/worker.lock/pid`);
  if (!pid) return null;
  const n = parseInt(pid, 10);
  if (!n || !isWorker(n)) return null;
  const state = (readText(`${cache}/worker.lock/state`) || "").split("\n");
  const duration = parseFloat(state[0]) || 0;
  const label = clean(state[1] || "");
  const tool = state[2] || "";
  let pct = null;
  // Only the end of the file: ffmpeg appends a block twice a second, megabytes for a long movie
  const prog = readTail(`${cache}/progress.txt`, 4096);
  if (tool === "ffmpeg" && duration > 0) {
    const all = [...prog.matchAll(/out_time_(?:us|ms)=(\d+)/g)];
    if (all.length) pct = Math.min(99, Math.max(0, Math.round((parseInt(all[all.length - 1][1], 10) / 1e6 / duration) * 100)));
  } else if (tool === "avconvert") {
    const all = [...prog.matchAll(/(\d+(?:\.\d+)?)%\s*complete/g)];
    if (all.length) pct = Math.min(99, Math.round(parseFloat(all[all.length - 1][1])));
  }
  let queued = 0;
  const list = FM.contentsOfDirectoryAtPathError(`${cache}/queue`, $());
  if (!list.isNil()) queued = (ObjC.deepUnwrap(list) || []).filter((x) => x.endsWith(".job")).length;
  return { label, pct, queued };
}

// ---------- Script Filter ----------

function info(title, subtitle, icon = "info", extra = {}) {
  return Object.assign({ title, subtitle: subtitle || "", valid: false, icon: { path: `icons/${icon}.png` } }, extra);
}
function output(items, extra = {}) {
  return JSON.stringify(Object.assign({ skipknowledge: true, items }, extra));
}

function finderSelection() {
  const test = env("MT_TEST_SELECTION", null);
  if (test !== null) {
    try {
      return JSON.parse(test);
    } catch (e) {
      return test.split("\t").filter(Boolean);
    }
  }
  // One Apple Event for the whole selection: asking each item for its URL costs an event per file
  // (seconds for a few hundred files)
  const testScript = env("MT_TEST_SELECTION_SCRIPT", "");
  if (TEST && !testScript) return [];
  const paths = aliasListPaths(testScript || 'tell application id "com.apple.finder" to return selection as alias list');
  if (paths || testScript) return paths;
  try {
    const finder = Application("Finder");
    const sel = finder.selection();
    return sel.map((i) => $.NSURL.URLWithString(i.url()).path.js).filter(Boolean);
  } catch (e) {
    return null; // Finder not running or no Automation permission
  }
}

// Run AppleScript `source` that returns a list of aliases, and return their paths (null on any error)
function aliasListPaths(source) {
  try {
    const script = $.NSAppleScript.alloc.initWithSource($(source));
    const d = script.executeAndReturnError(Ref());
    if (!d || d.isNil()) return null;
    const out = [];
    const n = Number(d.numberOfItems);
    for (let i = 1; i <= n; i++) {
      // alias → 'furl' descriptor, whose data is the file URL in UTF-8 (the fileURL property isn't bridged)
      const u = d.descriptorAtIndex(i).coerceToDescriptorType(0x6675726c /* 'furl' */);
      if (!u || u.isNil()) return null;
      const s = $.NSString.alloc.initWithDataEncoding(u.data, $.NSUTF8StringEncoding);
      const url = s.isNil() ? null : $.NSURL.URLWithString(s);
      if (!url || url.isNil()) return null;
      out.push(url.path.js);
    }
    return out;
  } catch (e) {
    return null;
  }
}

function uaFiles() {
  const raw = env("mt_ua_files", "");
  if (!raw) return null;
  return raw.split(/\t|\n/).filter((p) => p.startsWith("/"));
}

function describe(files) {
  const first = clean(baseName(files[0]));
  return files.length === 1 ? first : `${first} + ${files.length - 1} more`;
}
function plural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

// A runnable operation item. Files travel in variables as JSON; the op id is the arg.
function opItem(title, subtitle, op, files, icon, extra = {}) {
  const vars = { mt_files: filesVar(files), mt_op: op };
  return Object.assign({
    uid: undefined,
    title,
    subtitle: `${subtitle ? subtitle + " · " : ""}${describe(files)}`,
    arg: op,
    valid: true,
    icon: { path: `icons/${icon}.png` },
    quicklookurl: files[0],
    variables: Object.assign({ mt_reveal: "0", mt_copy: "0" }, vars),
    mods: {
      cmd: { arg: op, valid: true, subtitle: "Run, then reveal the result in Finder", variables: Object.assign({ mt_reveal: "1", mt_copy: "0" }, vars) },
      alt: { arg: op, valid: true, subtitle: "Run, then copy the result to the clipboard", variables: Object.assign({ mt_reveal: "0", mt_copy: "1" }, vars) },
    },
    text: files.length <= 200 ? { copy: files.join("\n"), largetype: files.map(baseName).join("\n") } : { largetype: `${files.length} files` },
  }, extra);
}

function matchWords(q, words) {
  const qs = q.toLowerCase().split(/\s+/).filter(Boolean);
  const ws = words.toLowerCase().split(/[\s()·,.]+/).filter(Boolean);
  return qs.every((w) => ws.some((x) => x.startsWith(w)));
}

function imageItems(files, query) {
  const q = query.trim().toLowerCase();
  const n = files.length;
  const noun = n === 1 ? "image" : `${n} images`;
  const fmts = encodableFormats();
  const items = [];
  let m;
  const resize = (title, op, sub) => opItem(title, sub || "Keeps the aspect ratio and orientation", op, files, "resize");

  if ((m = q.match(/^(?:resize\s+|scale\s+)?(\d+(?:\.\d+)?)\s*%$/))) {
    const p = parseFloat(m[1]);
    if (p <= 0 || p > 1000) return [info("Percentage must be between 0 and 1000", "", "error")];
    return [resize(`Resize ${noun} to ${p}%`, `resize:pct:${p}`)];
  }
  if ((m = q.match(/^(?:resize\s+)?(\d+)\s*(?:px)?$/)) && q !== "") {
    const v = parseInt(m[1], 10);
    if (v < 1 || v > 30000) return [info("Size must be between 1 and 30000 px", "", "error")];
    return [
      resize(`Fit ${noun} within ${v}×${v} px`, `resize:max:${v}`, "Longest side at most " + v + " px · never enlarges"),
      resize(`Resize ${noun} to ${v} px wide`, `resize:w:${v}`, "Height follows the aspect ratio"),
      resize(`Resize ${noun} to ${v} px high`, `resize:h:${v}`, "Width follows the aspect ratio"),
    ];
  }
  if ((m = q.match(/^(?:resize\s+)?(w|width|h|height)\s*[:=]?\s*(\d+)\s*(?:px)?$/))) {
    const v = parseInt(m[2], 10);
    if (v < 1 || v > 30000) return [info("Size must be between 1 and 30000 px", "", "error")];
    return m[1][0] === "w"
      ? [resize(`Resize ${noun} to ${v} px wide`, `resize:w:${v}`, "Height follows the aspect ratio")]
      : [resize(`Resize ${noun} to ${v} px high`, `resize:h:${v}`, "Width follows the aspect ratio")];
  }
  if ((m = q.match(/^(?:resize\s+|fit\s+)?(\d+)\s*(?:px)?\s*[x×*]\s*(\d+)\s*(?:px)?$/))) {
    const w = parseInt(m[1], 10), h = parseInt(m[2], 10);
    if (w < 1 || h < 1 || w > 30000 || h > 30000) return [info("Size must be between 1 and 30000 px", "", "error")];
    return [resize(`Fit ${noun} within ${w}×${h} px`, `resize:fit:${w}x${h}`, "Keeps the aspect ratio · never enlarges")];
  }
  if ((m = q.match(/^(?:rotate|rot)\s*(-?\d+)\s*°?$/))) {
    const d = ((parseInt(m[1], 10) % 360) + 360) % 360;
    if (![90, 180, 270].includes(d)) return [info("Rotate by 90, 180 or 270 degrees", "Negative angles rotate to the left, e.g. rotate -90", "error")];
    return [opItem(`Rotate ${noun} ${d === 270 ? "90° left" : d === 90 ? "90° right" : "180°"}`, "Clockwise angle " + d + "°", `rotate:${d}`, files, "rotate")];
  }
  const fmtAsked = lookup(FORMAT_ALIASES, q.replace(/^(?:convert\s+(?:to\s+)?|to\s+)/, ""));
  if (fmtAsked && files.every((f) => formatOfFile(f) === fmtAsked)) {
    return [info(`Already ${FORMATS[fmtAsked].name}`, `Try optimize to re-encode at quality ${Math.round(quality() * 100)}%`, "info", { autocomplete: "optimize" })];
  }
  if (fmtAsked && !fmts.includes(fmtAsked)) {
    return [info(`This Mac can't write ${FORMATS[fmtAsked].name} images`, `ImageIO on this macOS version only encodes ${fmts.map((k) => FORMATS[k].name).join(", ")}`, "error")];
  }

  const all = [];
  all.push(resize(`Resize ${noun} to 50%`, "resize:pct:50", "Type a size: 25%, 1200px, w800, h600 or 800x600", ), "resize 50% scale smaller half");
  all.push(resize(`Fit ${noun} within 1920 px`, "resize:max:1920", "Longest side at most 1920 px · never enlarges"), "resize fit 1920 max smaller");
  const srcFormats = new Set(files.map(formatOfFile));
  for (const k of fmts) {
    if (srcFormats.size === 1 && srcFormats.has(k)) continue;
    const f = FORMATS[k];
    const sub = f.lossy ? `Quality ${Math.round(quality() * 100)}%` : f.alpha ? "Keeps transparency" : "Transparency becomes white";
    all.push(opItem(`Convert ${noun} to ${f.name}`, sub, `convert:${k}`, files, "convert"), `convert to ${k} ${f.name} ${k === "jpeg" ? "jpg" : ""} ${k === "tiff" ? "tif" : ""} ${k === "heic" ? "heif" : ""}`);
  }
  all.push(opItem(`Rotate ${noun} 90° right`, "Clockwise", "rotate:90", files, "rotate"), "rotate right clockwise 90 turn");
  all.push(opItem(`Rotate ${noun} 90° left`, "Counter-clockwise", "rotate:270", files, "rotate"), "rotate left counterclockwise anticlockwise 90 270 -90 turn");
  all.push(opItem(`Rotate ${noun} 180°`, "Upside down", "rotate:180", files, "rotate"), "rotate 180 upside down turn");
  all.push(opItem(`Flip ${noun} horizontally`, "Mirror left to right", "flip:h", files, "flip"), "flip horizontal horizontally mirror");
  all.push(opItem(`Flip ${noun} vertically`, "Mirror top to bottom", "flip:v", files, "flip"), "flip vertical vertically mirror");
  all.push(opItem(`Strip metadata from ${noun}`, "Removes EXIF, GPS, camera and XMP data · keeps orientation", "strip:all", files, "strip"), "strip metadata exif gps location privacy clean remove");
  all.push(opItem(`Remove location from ${noun}`, "Removes GPS data only", "strip:gps", files, "location"), "remove location gps strip privacy");
  all.push(opItem(`Optimize ${noun}`, `Re-encode at quality ${Math.round(quality() * 100)}% · keeps the file only if it gets smaller`, "optimize", files, "optimize"), "optimize compress smaller reduce size re-encode");
  if (backgroundRemovalAvailable()) {
    all.push(opItem(`Remove background from ${noun}`, "On-device with Vision · transparent PNG", "removebg", files, "removebg"), "remove background bg cutout transparent subject isolate");
    all.push(opItem(`Remove background and crop ${noun}`, "Crops to the subject · transparent PNG", "removebg:crop", files, "removebg"), "remove background bg crop cutout transparent subject isolate");
  }
  // all is [item, words, item, words…]
  for (let i = 0; i < all.length; i += 2) {
    const [item, words] = [all[i], all[i + 1]];
    if (!q || matchWords(q, `${item.title} ${words}`)) items.push(item);
  }
  if (!items.length) {
    const bgHint = /background|bg/.test(q) ? "Background removal needs macOS 14 or later" : "Try 50%, 1200px, 800x600, png, rotate 90, strip, optimize";
    return [info("No matching image operation", bgHint, "info")];
  }
  return items;
}

function avItems(files, query) {
  const q = query.trim().toLowerCase();
  const ffmpeg = which("ffmpeg");
  const items = [];
  const byKind = { video: files.filter((f) => kindOf(f) === "video"), audio: files.filter((f) => kindOf(f) === "audio") };
  const status = workerStatus();
  if (status && !q) {
    items.push({
      title: `Converting ${status.label}${status.pct !== null ? ` · ${status.pct}%` : "…"}`,
      subtitle: `${status.queued ? `${status.queued} more queued · ` : ""}↩ Cancel all · ⌘↩ Show the log`,
      arg: "cancel",
      valid: true,
      icon: { path: "icons/progress.png" },
      variables: { mt_files: "[]", mt_op: "cancel" },
      mods: {
        cmd: { arg: "log", valid: true, subtitle: "Reveal the conversion log in Finder", variables: { mt_files: "[]", mt_op: "log" } },
        alt: { arg: "cancel", valid: true, subtitle: "Cancel all", variables: { mt_files: "[]", mt_op: "cancel" } },
      },
    });
  }

  // Trim needs a range
  let m;
  if ((m = q.match(/^(?:trim|cut|clip)\b\s*(.*)$/))) {
    const targets = [...byKind.video, ...byKind.audio];
    if (!targets.length) return items.concat([info("Select a video or audio file to trim", "", "info")]);
    const r = parseTrim(m[1]);
    if (!m[1].trim()) return items.concat([info("Trim: type start-end", "e.g. trim 0:10-0:25, trim 90-120 or trim 1:00- (to the end)", "trim", { autocomplete: "trim " })]);
    if (!r) return items.concat([info("Invalid range", "Use start-end with seconds or [h:]mm:ss, e.g. trim 0:10-0:25", "error", { autocomplete: "trim " })]);
    const usable = targets.filter((f) => toolFor("trim", kindOf(f), extOf(f), ffmpeg));
    if (!usable.length) {
      return items.concat([ffmpeg
        ? info("Trim: this ffmpeg can't encode it", `It has no ${missingEncoders("trim", kindOf(targets[0]), extOf(targets[0]), ffmpeg).join(" or ")} encoder · reinstall ffmpeg, or pick another build in the Workflow’s Configuration`, "error")
        : installItem("Trimming these files needs ffmpeg")]);
    }
    const id = `trim:${fmtTime(r.start).replace(/:/g, "_")}-${r.end === null ? "" : fmtTime(r.end).replace(/:/g, "_")}`;
    return items.concat([opItem(`Trim ${usable.length === 1 ? clean(baseName(usable[0])) : plural(usable.length, "file")} from ${fmtTime(r.start)} to ${r.end === null ? "the end" : fmtTime(r.end)}`,
      "Frame-accurate, re-encoded", id, usable, "trim")]);
  }

  // A GIF of part of the video: gif 0:10-0:15
  if ((m = q.match(/^gif\s+(\S.*)$/))) {
    if (!byKind.video.length) return items.concat([info("Select a video to make a GIF", "", "info")]);
    const r = parseTrim(m[1]);
    if (!r) return items.concat([info("Invalid range", "Use start-end with seconds or [h:]mm:ss, e.g. gif 0:10-0:15", "error", { autocomplete: "gif " })]);
    if (!ffmpeg) return items.concat([installItem("Making a GIF needs ffmpeg")]);
    const id = `gif:${fmtTime(r.start).replace(/:/g, "_")}-${r.end === null ? "" : fmtTime(r.end).replace(/:/g, "_")}`;
    const w = parseInt(env("gif_width", "480"), 10);
    const vids = byKind.video;
    return items.concat([opItem(`GIF of ${vids.length === 1 ? clean(baseName(vids[0])) : plural(vids.length, "video")} from ${fmtTime(r.start)} to ${r.end === null ? "the end" : fmtTime(r.end)}`,
      `${w > 0 ? `Up to ${w} px wide` : "Original size"}, ${parseInt(env("gif_fps", "15"), 10) || 15} fps`, id, vids, "gif")]);
  }

  const missing = [];
  for (const op of AV_OPS) {
    const targets = files.filter((f) => op.kinds.includes(kindOf(f)));
    if (!targets.length) continue;
    const isAudioOp = !!AUDIO_OUT[op.id];
    // An audio file already in the target format has nothing to convert
    const usable = targets.filter((f) => toolFor(op.id, kindOf(f), extOf(f), ffmpeg) && !(isAudioOp && kindOf(f) === "audio" && normExt(extOf(f)) === op.id));
    let title = op.title;
    if (isAudioOp) {
      const allAudio = targets.every((f) => kindOf(f) === "audio");
      title = allAudio ? `Convert to ${op.title}` : `Extract audio as ${op.title}`;
      if (allAudio && targets.every((f) => normExt(extOf(f)) === op.id)) continue;
    }
    let sub = op.sub;
    if (op.id === "gif") {
      const w = parseInt(env("gif_width", "480"), 10);
      sub = `${w > 0 ? `Up to ${w} px wide` : "Original size"}, ${parseInt(env("gif_fps", "15"), 10) || 15} fps, optimised palette`;
    }
    const words = `${title} ${op.words}`;
    if (q && !matchWords(q, words)) continue;
    if (!usable.length) {
      missing.push(op.title);
      const lacks = ffmpeg ? missingEncoders(op.id, kindOf(targets[0]), extOf(targets[0]), ffmpeg) : [];
      if (lacks.length) items.push(info(`${title}: this ffmpeg can't encode it`, `It has no ${lacks.join(" or ")} encoder · reinstall ffmpeg, or pick another build in the Workflow’s Configuration`, "error"));
      continue;
    }
    const count = usable.length === 1 ? "" : ` (${usable.length} files)`;
    const tool = toolFor(op.id, kindOf(usable[0]), extOf(usable[0]), ffmpeg);
    const via = tool === "ffmpeg" ? "" : `via ${tool}`;
    // avconvert's size presets fit the video in a box rather than capping the short side
    if (tool === "avconvert" && op.id.startsWith("scale:")) sub = `Fits within ${AVCONVERT_SCALE[op.id.slice(6)].slice(6)}`;
    items.push(opItem(`${title}${count}`, [sub, via].filter(Boolean).join(" · "), op.id, usable, isAudioOp ? "audio" : op.id.split(":")[0]));
  }
  if (!q || "trim".startsWith(q.split(/\s+/)[0])) {
    const targets = [...byKind.video, ...byKind.audio];
    if (targets.length) items.push(info("Trim…", "Type trim start-end, e.g. trim 0:10-0:25", "trim", { autocomplete: "trim ", valid: false }));
  }
  if (!ffmpeg && (missing.length || !q)) items.push(installItem(missing.length ? `Needed for ${missing.slice(0, 4).join(", ")}${missing.length > 4 ? "…" : ""}` : "Adds WebM, GIF, MP3, compression and more"));
  if (!items.length) return [info("No matching operation", "Try mp4, gif, mp3, compress, 720p, mute or trim 0:10-0:25", "info")];
  return items;
}

function installItem(sub) {
  return {
    title: "Install ffmpeg with Homebrew",
    subtitle: `${sub} · ↩ Copy “brew install ffmpeg”`,
    arg: "brew install ffmpeg",
    valid: true,
    icon: { path: "icons/download.png" },
    variables: { mt_op: "copy", mt_files: "[]" },
    mods: {
      cmd: { arg: "brew install ffmpeg", valid: true, subtitle: "Copy “brew install ffmpeg”", variables: { mt_op: "copy", mt_files: "[]" } },
      alt: { arg: "brew install ffmpeg", valid: true, subtitle: "Copy “brew install ffmpeg”", variables: { mt_op: "copy", mt_files: "[]" } },
    },
    text: { copy: "brew install ffmpeg" },
  };
}

// Selected folders stand for the media files directly inside them
function expandFolders(files) {
  const out = [];
  for (const f of files) {
    if (!isDir(f)) {
      out.push(f);
      continue;
    }
    const list = FM.contentsOfDirectoryAtPathError(f, $());
    if (list.isNil()) continue;
    const names = (ObjC.deepUnwrap(list) || []).filter((n) => !n.startsWith(".")).sort((a, b) => a.localeCompare(b));
    for (const n of names) {
      const p = `${f}/${n}`;
      if (kindOf(p) && !isDir(p)) out.push(p);
      if (out.length >= 5000) break;
    }
  }
  return out;
}

let sessionFiles = null; // the selection, remembered for the rest of this Alfred session
function scriptFilter(mode, query) {
  let files = mode === "all" ? uaFiles() : null;
  if (files === null && env("mt_sel", "")) files = filesFromEnv("mt_sel");
  if (files === null) {
    files = finderSelection();
    if (files === null) return [info("Can't read the Finder selection", "Allow Alfred to control Finder in System Settings → Privacy & Security → Automation", "error")];
    sessionFiles = files;
  }
  files = expandFolders([...new Set(files)]);
  const images = files.filter((f) => kindOf(f) === "image");
  const avs = files.filter((f) => kindOf(f) === "video" || kindOf(f) === "audio");
  if (mode === "img") {
    if (!images.length) {
      if (avs.length) return [info("No images selected", `Use the ${env("keyword_vid", "vid")} keyword for video and audio`, "info", { autocomplete: undefined })];
      return [info("Select images in Finder first", "Then type a size, format or operation, e.g. 50%, 1200px, webp, rotate 90", "info")];
    }
    return imageItems(images, query);
  }
  if (mode === "vid") {
    if (!avs.length) {
      const st = workerStatus();
      const res = [];
      if (st) res.push(...avItems([], "").filter((i) => i.arg === "cancel")); // only the progress row
      res.push(images.length
        ? info("No video or audio selected", `Use the ${env("keyword_img", "img")} keyword for images`, "info")
        : info("Select video or audio files in Finder first", "Then pick a format, e.g. mp4, gif, mp3, compress or trim 0:10-0:25", "info"));
      return res;
    }
    return avItems(avs, query);
  }
  // Universal Action: everything that applies to the selection
  if (!images.length && !avs.length) {
    return [files.length
      ? info("No image, video or audio files", describe(files), "info")
      : info("Select images, videos or audio in Finder first", "Or use the Universal Action on any files", "info")];
  }
  const out = [];
  if (images.length) {
    const it = imageItems(images, query);
    if (it.some((i) => i.valid !== false) || !avs.length) out.push(...it);
  }
  if (avs.length) {
    const it = avItems(avs, query);
    if (it.some((i) => i.valid !== false) || !out.length) out.push(...it);
  }
  return out;
}

// ---------- entry ----------

function run(argv) {
  const [cmd, ...rest] = argv;
  const query = rest.join(" ");
  try {
    switch (cmd) {
      case "img":
      case "vid":
      case "all": {
        const items = scriptFilter(cmd, query);
        const extra = {};
        if (items.some((i) => i.arg === "cancel")) extra.rerun = 1;
        // Alfred passes these back on every keystroke, so Finder is asked only once per session
        if (sessionFiles && sessionFiles.length) extra.variables = { mt_sel: filesVar(sessionFiles) };
        return output(items, extra);
      }
      case "apply": return applyImages(query);
      case "enqueue": return enqueue(query).message;
      case "status": return JSON.stringify(workerStatus());
      default: return output([info(`Unknown command: ${cmd}`, "", "error")]);
    }
  } catch (e) {
    const msg = String(e && e.message ? e.message : e);
    if (cmd === "apply" || cmd === "enqueue") return `Media Toolkit error: ${msg}`;
    return output([info("Media Toolkit error", msg, "error")]);
  }
}
