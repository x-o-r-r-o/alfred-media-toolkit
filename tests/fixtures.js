// Test helper (not shipped): make and inspect fixture images with CoreGraphics/ImageIO.
//   osascript -l JavaScript fixtures.js make <path> <uti> <width> <height> '<json options>'
//     options: orientation (1-8), gps (bool), exif (bool), alpha (transparent background + circle),
//              quadrant ("tl"|"bl"): red quadrant on blue (top-left origin), frames (n, animated), delay,
//              subject (a shape on a plain background), noise (hard to compress), quality (0-1),
//              gray / p3 / depth16 (colour space and bits per channel; frames on TIFF makes pages)
//   osascript -l JavaScript fixtures.js probe <path> '[[x,y],…]'   → JSON with size, metadata and pixel colours
ObjC.import("Foundation"); ObjC.import("AppKit"); ObjC.import("CoreGraphics"); ObjC.import("ImageIO");

function n(v) { return Number.isInteger(v) ? $.NSNumber.numberWithLongLong(v) : $.NSNumber.numberWithDouble(v); }
function dict(o) {
  const d = $.NSMutableDictionary.dictionary;
  for (const [k, v] of Object.entries(o)) {
    d.setObjectForKey(typeof v === "number" ? n(v) : typeof v === "object" ? dict(v) : typeof v === "boolean" ? $.NSNumber.numberWithBool(v) : $(v), $(k));
  }
  return d;
}

function frame(w, h, o, i) {
  // gray: greyscale; p3: Display P3; depth16: 16 bits per channel
  const cs = $.CGColorSpaceCreateWithName(o.gray ? $.kCGColorSpaceGenericGrayGamma2_2 : o.p3 ? $.kCGColorSpaceDisplayP3 : $.kCGColorSpaceSRGB);
  const ctx = $.CGBitmapContextCreate(null, w, h, o.depth16 ? 16 : 8, 0, cs, o.gray ? 0 : o.alpha ? 1 : 5);
  if (o.plain) {
    $.CGContextSetRGBFillColor(ctx, 0.5, 0.5, 0.5, 1);
    $.CGContextFillRect(ctx, $.CGRectMake(0, 0, w, h));
  } else if (o.alpha) {
    $.CGContextClearRect(ctx, $.CGRectMake(0, 0, w, h));
    $.CGContextSetRGBFillColor(ctx, 0.9, 0.2, 0.1, 1);
    $.CGContextFillEllipseInRect(ctx, $.CGRectMake(w / 4, h / 4, w / 2, h / 2));
  } else if (o.subject) {
    $.CGContextSetRGBFillColor(ctx, 0.85, 0.9, 0.95, 1);
    $.CGContextFillRect(ctx, $.CGRectMake(0, 0, w, h));
    $.CGContextSetRGBFillColor(ctx, 0.9, 0.2, 0.1, 1);
    $.CGContextFillEllipseInRect(ctx, $.CGRectMake(w / 3, h / 4, w / 3, h / 2));
  } else if (o.noise) {
    $.CGContextSetRGBFillColor(ctx, 1, 1, 1, 1);
    $.CGContextFillRect(ctx, $.CGRectMake(0, 0, w, h));
    let seed = 7;
    for (let k = 0; k < 4000; k++) {
      seed = (seed * 1103515245 + 12345) % 2147483648;
      $.CGContextSetRGBFillColor(ctx, (seed % 255) / 255, ((seed >> 8) % 255) / 255, ((seed >> 16) % 255) / 255, 1);
      $.CGContextFillRect(ctx, $.CGRectMake(seed % w, (seed >> 4) % h, 6, 6));
    }
  } else {
    const shade = o.frames ? i / o.frames : 0;
    $.CGContextSetRGBFillColor(ctx, 0, shade, 1, 1);
    $.CGContextFillRect(ctx, $.CGRectMake(0, 0, w, h));
    $.CGContextSetRGBFillColor(ctx, 1, 0, 0, 1);
    // CG origin is bottom-left
    if (o.quadrant === "bl") $.CGContextFillRect(ctx, $.CGRectMake(0, 0, w / 2, h / 2));
    else $.CGContextFillRect(ctx, $.CGRectMake(0, h / 2, w / 2, h / 2));
  }
  return $.CGBitmapContextCreateImage(ctx);
}

function make(path, uti, w, h, o) {
  const count = o.frames || 1;
  const dest = $.CGImageDestinationCreateWithURL($.NSURL.fileURLWithPath(path), $(uti), count, null);
  if (count > 1) $.CGImageDestinationSetProperties(dest, dict({ "{GIF}": { LoopCount: o.loop || 0 } }));
  for (let i = 0; i < count; i++) {
    const p = {};
    if (o.orientation) p.Orientation = o.orientation;
    if (o.gps) p["{GPS}"] = { Latitude: 48.8584, LatitudeRef: "N", Longitude: 2.2945, LongitudeRef: "E" };
    if (o.exif) {
      p["{Exif}"] = { DateTimeOriginal: "2021:06:01 12:00:00", UserComment: "secret comment" };
      p["{TIFF}"] = { Make: "TestCam", Model: "T1000" };
    }
    if (count > 1) p["{GIF}"] = { DelayTime: o.delay || 0.2 };
    if (o.quality) p.kCGImageDestinationLossyCompressionQuality = o.quality;
    $.CGImageDestinationAddImage(dest, frame(w, h, o, i), dict(p));
  }
  return $.CGImageDestinationFinalize(dest) ? "ok" : "failed";
}

function probe(path, points) {
  const src = $.CGImageSourceCreateWithURL($.NSURL.fileURLWithPath(path), null);
  if (!src) return JSON.stringify({ error: "unreadable" });
  const props = ObjC.deepUnwrap(ObjC.castRefToObject($.CGImageSourceCopyPropertiesAtIndex(src, 0, null))) || {};
  const fileProps = ObjC.deepUnwrap(ObjC.castRefToObject($.CGImageSourceCopyProperties(src, null))) || {};
  const out = {
    uti: ObjC.castRefToObject($.CGImageSourceGetType(src)).js,
    count: Number($.CGImageSourceGetCount(src)),
    width: props.PixelWidth, height: props.PixelHeight,
    orientation: props.Orientation || 1,
    hasAlpha: !!props.HasAlpha,
    depth: props.Depth, model: props.ColorModel, profile: props.ProfileName || null,
    gps: !!props["{GPS}"],
    exif: props["{Exif}"] || {},
    tiff: props["{TIFF}"] || {},
    gif: props["{GIF}"] || null,
    loop: fileProps["{GIF}"] ? fileProps["{GIF}"].LoopCount : null,
    colors: [],
  };
  if (points.length) {
    const img = $.CGImageSourceCreateImageAtIndex(src, 0, null);
    const rep = $.NSBitmapImageRep.alloc.initWithCGImage(img).bitmapImageRepByConvertingToColorSpaceRenderingIntent($.NSColorSpace.sRGBColorSpace, 0);
    for (const [x, y] of points) {
      const c = rep.colorAtXY(x, y);
      out.colors.push([c.redComponent, c.greenComponent, c.blueComponent, c.alphaComponent].map((v) => Math.round(v * 255)));
    }
  }
  return JSON.stringify(out);
}

function run(argv) {
  if (argv[0] === "make") return make(argv[1], argv[2], Number(argv[3]), Number(argv[4]), JSON.parse(argv[5] || "{}"));
  if (argv[0] === "probe") return probe(argv[1], JSON.parse(argv[2] || "[]"));
  return "usage";
}
