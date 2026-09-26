#!/usr/bin/env python3
"""End-to-end tests: run the Script Filters and the action the way Alfred does, on generated fixtures.

Images are made with CoreGraphics (tests/fixtures.js). Movies are made with AVFoundation (tests/avtool.swift)
for the avconvert/afconvert paths, and with ffmpeg only if it is installed; the ffmpeg tests are skipped otherwise.
"""
import json, os, plistlib, shutil, stat, subprocess, sys, tempfile, time, unittest, wave, struct, math

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
TESTS = os.path.join(ROOT, "tests")
TMP = tempfile.mkdtemp(prefix="media-toolkit-test-")
CACHE = os.path.join(TMP, "cache dir")
os.makedirs(CACHE)
AVTOOL = os.path.join(TMP, "avtool")
FFMPEG = next((p for p in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", shutil.which("ffmpeg") or "") if p and os.access(p, os.X_OK)), None)
FFPROBE = os.path.join(os.path.dirname(FFMPEG), "ffprobe") if FFMPEG else None
# A folder name that exercises quoting: spaces, unicode, quotes, $, backticks, and a leading dash
WEIRD = "-Fotos ü 'q\" $(x) `y` & ;"


def setUpModule():
    r = subprocess.run(["swiftc", "-O", os.path.join(TESTS, "avtool.swift"), "-o", AVTOOL], capture_output=True, text=True)
    if r.returncode != 0:
        print("\nNOTE: swiftc failed; avconvert tests will be skipped\n", r.stderr[-500:])


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


def base_env(**extra):
    e = dict(os.environ, alfred_workflow_cache=CACHE, alfred_workflow_bundleid="io.github.x-o-r-r-o.media-toolkit",
             MT_TEST_NOTIFY_FILE=os.path.join(CACHE, "notify.txt"), MT_TEST_REVEAL_FILE=os.path.join(CACHE, "reveal.txt"))
    for k in ("output_suffix", "replace_originals", "image_quality", "mt_files", "mt_op", "mt_reveal", "mt_ua_files"):
        e.pop(k, None)
    e.update({k: str(v) for k, v in extra.items()})
    return e


def validate(data):
    assert isinstance(data.get("items"), list)
    for it in data["items"]:
        assert isinstance(it.get("title"), str) and it["title"], it
        if "icon" in it:
            assert os.path.exists(os.path.join(SRC, it["icon"]["path"])), it["icon"]
        if it.get("valid", True) is not False:
            assert "arg" in it, it
        for m in (it.get("mods") or {}).values():
            assert "subtitle" in m and "arg" in m, m


def sf(mode, query="", selection=None, **env):
    e = base_env(**env)
    if selection is not None:
        e["MT_TEST_SELECTION"] = json.dumps(selection)
    out = subprocess.run(["osascript", "-l", "JavaScript", "./media.js", mode, query], cwd=SRC, env=e,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    data = json.loads(out.stdout)
    validate(data)
    return data


def items(*a, **k):
    return sf(*a, **k)["items"]


def act(op, files, **env):
    """Run the action like Alfred: arg in argv, files and op in variables."""
    e = base_env(mt_op=op, mt_files=json.dumps(files), **env)
    out = subprocess.run(["./action.sh", op], cwd=SRC, env=e, capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def make(path, uti="public.jpeg", w=300, h=200, **opts):
    r = subprocess.run(["osascript", "-l", "JavaScript", os.path.join(TESTS, "fixtures.js"), "make", path, uti, str(w), str(h), json.dumps(opts)],
                       capture_output=True, text=True)
    assert r.stdout.strip() == "ok", r.stderr
    return path


def probe(path, points=()):
    r = subprocess.run(["osascript", "-l", "JavaScript", os.path.join(TESTS, "fixtures.js"), "probe", path, json.dumps(list(points))],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def sips(path):
    out = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", "-g", "format", path], capture_output=True, text=True).stdout
    vals = dict(l.strip().split(": ", 1) for l in out.splitlines()[1:] if ": " in l)
    return int(vals["pixelWidth"]), int(vals["pixelHeight"]), vals["format"]


def red(c):
    return c[0] > 200 and c[1] < 60 and c[2] < 60


def blue(c):
    return c[2] > 200 and c[0] < 60


def avprobe(path):
    return json.loads(subprocess.run([AVTOOL, "probe", path], capture_output=True, text=True).stdout)


def ffprobe(path):
    out = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration:stream=codec_type,codec_name,width,height",
                          "-of", "json", path], capture_output=True, text=True).stdout
    return json.loads(out)


def write_wav(path, seconds=2.0, rate=22050):
    with wave.open(path, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / rate))) for i in range(int(rate * seconds))))
    return path


def notifications():
    p = os.path.join(CACHE, "notify.txt")
    if not os.path.exists(p):
        return []
    with open(p) as f:
        return f.read().splitlines()


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="case-", dir=TMP)
        self.d = os.path.join(self.dir, WEIRD)
        os.makedirs(self.d)
        for f in ("notify.txt", "reveal.txt"):
            p = os.path.join(CACHE, f)
            if os.path.exists(p):
                os.remove(p)

    def tearDown(self):
        for base, dirs, files in os.walk(self.dir):
            os.chmod(base, 0o755)
        shutil.rmtree(self.dir, ignore_errors=True)

    def p(self, name):
        return os.path.join(self.d, name)

    def listdir(self):
        return sorted(os.listdir(self.d))


# ---------------------------------------------------------------- Script Filters

class ScriptFilterTests(Base):
    def test_empty_selection_and_wrong_kind(self):
        self.assertEqual(items("img", selection=[])[0]["title"], "Select images in Finder first")
        mov = self.p("clip.mov")
        open(mov, "w").close()
        it = items("img", selection=[mov])
        self.assertEqual(it[0]["title"], "No images selected")
        self.assertIn("vid", it[0]["subtitle"])
        jpg = make(self.p("a.jpg"))
        self.assertEqual(items("vid", selection=[jpg], MT_TEST_FFMPEG="none")[-1]["title"], "No video or audio selected")
        self.assertEqual(items("vid", selection=[], MT_TEST_FFMPEG="none")[-1]["title"], "Select video or audio files in Finder first")
        self.assertEqual(items("img", selection=[self.d])[0]["title"], "Select images in Finder first")  # folders are ignored

    def test_default_image_list(self):
        jpg = make(self.p("photo é.jpg"))
        it = items("img", selection=[jpg])
        titles = [i["title"] for i in it]
        self.assertIn("Resize image to 50%", titles)
        self.assertIn("Convert image to PNG", titles)
        self.assertNotIn("Convert image to JPEG", titles)  # already JPEG
        self.assertIn("Strip metadata from image", titles)
        self.assertIn("Remove background from image", titles)
        for i in it:
            self.assertEqual(json.loads(i["variables"]["mt_files"]), [jpg])
            self.assertEqual(i["variables"]["mt_reveal"], "0")
            self.assertEqual(i["mods"]["cmd"]["variables"]["mt_reveal"], "1")
            self.assertEqual(i["mods"]["cmd"]["arg"], i["arg"])

    def test_query_parsing(self):
        jpg = make(self.p("a.jpg"))
        self.assertEqual([i["arg"] for i in items("img", "50%", [jpg])], ["resize:pct:50"])
        self.assertEqual([i["arg"] for i in items("img", "1200px", [jpg])], ["resize:max:1200", "resize:w:1200", "resize:h:1200"])
        self.assertEqual([i["arg"] for i in items("img", "1200", [jpg])][0], "resize:max:1200")
        self.assertEqual([i["arg"] for i in items("img", "w800", [jpg])], ["resize:w:800"])
        self.assertEqual([i["arg"] for i in items("img", "h 600px", [jpg])], ["resize:h:600"])
        self.assertEqual([i["arg"] for i in items("img", "800x600", [jpg])], ["resize:fit:800x600"])
        self.assertEqual([i["arg"] for i in items("img", "800 × 600", [jpg])], ["resize:fit:800x600"])
        self.assertEqual([i["arg"] for i in items("img", "rotate 90", [jpg])], ["rotate:90"])
        self.assertEqual([i["arg"] for i in items("img", "rotate -90", [jpg])], ["rotate:270"])
        self.assertEqual(items("img", "rotate 45", [jpg])[0]["valid"], False)
        self.assertEqual([i["arg"] for i in items("img", "png", [jpg])], ["convert:png"])
        self.assertEqual([i["arg"] for i in items("img", "tif", [jpg])], ["convert:tiff"])
        self.assertIn("strip:gps", [i["arg"] for i in items("img", "gps", [jpg])])
        self.assertEqual(items("img", "0%", [jpg])[0]["valid"], False)
        self.assertEqual(items("img", "99999px", [jpg])[0]["valid"], False)
        self.assertEqual(items("img", "zzz", [jpg])[0]["title"], "No matching image operation")

    def test_hostile_queries(self):
        jpg = make(self.p("a.jpg"))
        for q in ['"; rm -rf ~ #', "$(touch /tmp/pwned)", "`id`", "'\n\t", "🙂 ünïcödé", "\\", "%", "x" * 5000]:
            it = items("img", q, [jpg])
            self.assertTrue(it)
            it = items("vid", q, [jpg], MT_TEST_FFMPEG="none")
            self.assertTrue(it)
        self.assertFalse(os.path.exists("/tmp/pwned"))

    def test_encoders_detected_at_runtime(self):
        jpg = make(self.p("a.jpg"))
        it = items("img", "webp", [jpg], MT_TEST_HIDE_FORMATS="webp")
        self.assertEqual(it[0]["title"], "This Mac can't write WebP images")
        self.assertEqual(it[0]["valid"], False)
        titles = [i["title"] for i in items("img", "convert", [jpg], MT_TEST_HIDE_FORMATS="webp,avif")]
        self.assertNotIn("Convert image to AVIF", titles)
        self.assertNotIn("Convert image to WebP", titles)
        self.assertIn("Convert image to HEIC", titles)

    def test_background_removal_hidden_without_vision(self):
        jpg = make(self.p("a.jpg"))
        titles = [i["title"] for i in items("img", "", [jpg], MT_TEST_NO_VISION=1)]
        self.assertFalse(any("background" in t for t in titles))
        self.assertIn("macOS 14", items("img", "background", [jpg], MT_TEST_NO_VISION=1)[0]["subtitle"])

    def test_multiple_and_mixed_selection(self):
        a, b = make(self.p("a.jpg")), make(self.p("b.png"), "public.png")
        mov, wav = self.p("c.mov"), write_wav(self.p("d.wav"))
        open(mov, "w").close()
        it = items("img", "50%", [a, b, mov, a])  # duplicates removed, video ignored
        self.assertEqual(it[0]["title"], "Resize 2 images to 50%")
        self.assertEqual(json.loads(it[0]["variables"]["mt_files"]), [a, b])
        # Universal Action: tab-separated files, every kind gets its operations
        it = items("all", "", mt_ua_files="\t".join([a, mov, wav, self.p("notes.pdf")]), MT_TEST_FFMPEG="none")
        by_arg = {i.get("arg"): i for i in it}
        self.assertEqual(json.loads(by_arg["resize:pct:50"]["variables"]["mt_files"]), [a])
        self.assertEqual(json.loads(by_arg["mp4"]["variables"]["mt_files"]), [mov])
        self.assertNotIn("wav", by_arg)  # the movie needs ffmpeg for WAV, and the WAV is WAV already
        self.assertEqual(json.loads(by_arg["flac"]["variables"]["mt_files"]), [wav])  # afconvert; the movie needs ffmpeg
        self.assertIn("brew install ffmpeg", by_arg)
        it = items("all", "", mt_ua_files="\t".join([a, mov, wav]), MT_TEST_FFMPEG="/fake/ffmpeg")
        by_arg = {i.get("arg"): i for i in it}
        self.assertEqual(json.loads(by_arg["wav"]["variables"]["mt_files"]), [mov])
        self.assertEqual(json.loads(by_arg["flac"]["variables"]["mt_files"]), [mov, wav])
        self.assertEqual(by_arg["flac"]["title"], "Extract audio as FLAC (2 files)")
        # the media keyword falls back to the Finder selection
        it = items("all", "png", selection=[a])
        self.assertEqual(it[0]["arg"], "convert:png")

    def test_video_items_without_ffmpeg(self):
        mov, mkv = self.p("clip.mov"), self.p("clip.mkv")
        for f in (mov, mkv):
            open(f, "w").close()
        it = items("vid", "", [mov], MT_TEST_FFMPEG="none")
        args = [i.get("arg") for i in it]
        for a in ("mp4", "hevc", "mov", "scale:1080", "scale:720", "m4a"):
            self.assertIn(a, args)
        for a in ("webm", "gif", "mp3", "compress:28", "mute"):
            self.assertNotIn(a, args)
        inst = next(i for i in it if i["title"] == "Install ffmpeg with Homebrew")
        self.assertEqual(inst["arg"], "brew install ffmpeg")
        self.assertIn("avconvert", next(i for i in it if i.get("arg") == "mp4")["subtitle"])
        # asking for an ffmpeg-only format explains how to get it
        it = items("vid", "webm", [mov], MT_TEST_FFMPEG="none")
        self.assertEqual([i["title"] for i in it], ["Install ffmpeg with Homebrew"])
        self.assertIn("WebM", it[0]["subtitle"])
        # AVFoundation can't read MKV: only the install item
        it = items("vid", "", [mkv], MT_TEST_FFMPEG="none")
        self.assertEqual([i["title"] for i in it if i.get("valid") is not False], ["Install ffmpeg with Homebrew"])

    def test_video_items_with_ffmpeg(self):
        mov = self.p("clip.mkv")
        open(mov, "w").close()
        it = items("vid", "", [mov], MT_TEST_FFMPEG="/fake/bin/ffmpeg")
        args = [i.get("arg") for i in it]
        for a in ("mp4", "hevc", "webm", "mov", "gif", "compress:23", "compress:28", "compress:32", "scale:720", "mute", "mp3", "m4a", "wav", "flac"):
            self.assertIn(a, args)
        self.assertNotIn("brew install ffmpeg", args)
        self.assertEqual([i["arg"] for i in items("vid", "gif", [mov], MT_TEST_FFMPEG="/fake/bin/ffmpeg")], ["gif"])
        self.assertEqual([i["arg"] for i in items("vid", "720", [mov], MT_TEST_FFMPEG="/fake/bin/ffmpeg")], ["scale:720"])
        gif = items("vid", "gif", [mov], MT_TEST_FFMPEG="/fake/bin/ffmpeg", gif_width=640, gif_fps=24)[0]
        self.assertIn("640 px", gif["subtitle"])
        self.assertIn("24 fps", gif["subtitle"])

    def test_trim_parsing(self):
        mov = self.p("clip.mov")
        open(mov, "w").close()
        env = dict(MT_TEST_FFMPEG="/fake/bin/ffmpeg")
        it = items("vid", "trim 00:10-00:25", [mov], **env)
        self.assertEqual(it[0]["arg"], "trim:0_10-0_25")
        self.assertEqual(it[0]["title"], "Trim clip.mov from 0:10 to 0:25")
        self.assertEqual(items("vid", "trim 1:02:03.5-1:05:00", [mov], **env)[0]["arg"], "trim:1_02_03.5-1_05_00")
        self.assertEqual(items("vid", "trim 90-", [mov], **env)[0]["title"], "Trim clip.mov from 1:30 to the end")
        self.assertEqual(items("vid", "trim 5 to 7.25", [mov], **env)[0]["arg"], "trim:0_05-0_07.25")
        self.assertEqual(items("vid", "trim", [mov], **env)[0]["title"], "Trim: type start-end")
        self.assertEqual(items("vid", "trim 20-10", [mov], **env)[0]["title"], "Invalid range")
        self.assertEqual(items("vid", "trim a-b", [mov], **env)[0]["title"], "Invalid range")
        self.assertEqual(items("vid", "trim 0:75-0:80", [mov], **env)[0]["title"], "Invalid range")

    def test_worker_status_item(self):
        lock = os.path.join(CACHE, "worker.lock")
        os.makedirs(lock, exist_ok=True)
        try:
            with open(os.path.join(lock, "pid"), "w") as f:
                f.write(str(os.getpid()))
            with open(os.path.join(lock, "state"), "w") as f:
                f.write("200\nholiday ü.mov\nffmpeg\n")
            with open(os.path.join(CACHE, "progress.txt"), "w") as f:
                f.write("out_time_us=50000000\nprogress=continue\nout_time_us=100000000\nprogress=continue\n")
            data = sf("vid", "", [], MT_TEST_FFMPEG="none")
            self.assertEqual(data["items"][0]["title"], "Converting holiday ü.mov · 50%")
            self.assertEqual(data["items"][0]["arg"], "cancel")
            self.assertEqual(data.get("rerun"), 1)
            with open(os.path.join(lock, "pid"), "w") as f:
                f.write("999999")  # dead worker: no status
            self.assertNotEqual(items("vid", "", [], MT_TEST_FFMPEG="none")[0]["arg"] if items("vid", "", [], MT_TEST_FFMPEG="none")[0].get("arg") else "", "cancel")
        finally:
            shutil.rmtree(lock, ignore_errors=True)


# ---------------------------------------------------------------- image operations

class ImageTests(Base):
    def test_resize_percent_and_naming(self):
        src = make(self.p("photo.jpg"), w=400, h=300)
        msg = act("resize:pct:50", [src])
        self.assertEqual(msg, "Resized photo.jpg → photo-edited.jpg (200×150)")
        self.assertEqual(sips(self.p("photo-edited.jpg"))[:2], (200, 150))
        self.assertEqual(sips(src)[:2], (400, 300))  # original untouched
        act("resize:pct:50", [src])  # never clobbers
        self.assertEqual(self.listdir(), ["photo-edited-2.jpg", "photo-edited.jpg", "photo.jpg"])
        act("resize:pct:200", [src])
        self.assertEqual(sips(self.p("photo-edited-3.jpg"))[:2], (800, 600))

    def test_resize_modes(self):
        src = make(self.p("p.png"), "public.png", 400, 200)
        act("resize:w:100", [src])
        self.assertEqual(sips(self.p("p-edited.png"))[:2], (100, 50))
        act("resize:h:100", [src])
        self.assertEqual(sips(self.p("p-edited-2.png"))[:2], (200, 100))
        act("resize:fit:300x300", [src])
        self.assertEqual(sips(self.p("p-edited-3.png"))[:2], (300, 150))
        act("resize:max:100", [src])
        self.assertEqual(sips(self.p("p-edited-4.png"))[:2], (100, 50))
        # max/fit never enlarge
        msg = act("resize:max:1000", [src])
        self.assertEqual(msg, "Nothing to do for p.png: already 400×200")
        self.assertEqual(len(self.listdir()), 5)
        self.assertFalse(probe(self.p("p-edited.png"))["hasAlpha"])  # opaque stays opaque

    def test_resize_applies_exif_orientation(self):
        # Stored 300×200 with orientation 6 (displayed 200×300); red is displayed top-left
        src = make(self.p("rot.jpg"), w=300, h=200, orientation=6, quadrant="bl", exif=True)
        self.assertEqual(probe(src)["orientation"], 6)
        act("resize:max:150", [src])
        out = self.p("rot-edited.jpg")
        info = probe(out, [(10, 10), (90, 140)])
        self.assertEqual((info["width"], info["height"]), (100, 150))
        self.assertEqual(info["orientation"], 1)
        self.assertTrue(red(info["colors"][0]), info["colors"])
        self.assertTrue(blue(info["colors"][1]), info["colors"])
        self.assertEqual(info["tiff"].get("Make"), "TestCam")  # other metadata carried over
        # width means displayed width
        act("resize:w:100", [src])
        self.assertEqual(sips(self.p("rot-edited-2.jpg"))[:2], (100, 150))

    def test_rotate_and_flip(self):
        src = make(self.p("q.png"), "public.png", 200, 100, quadrant="tl")
        cases = {
            "rotate:90": ((100, 200), [(90, 10)]),  # top-left → top-right
            "rotate:180": ((200, 100), [(190, 90)]),  # → bottom-right
            "rotate:270": ((100, 200), [(10, 190)]),  # → bottom-left
            "flip:h": ((200, 100), [(190, 10)]),
            "flip:v": ((200, 100), [(10, 90)]),
        }
        for op, (size, pts) in cases.items():
            with self.subTest(op=op):
                before = set(self.listdir())
                act(op, [src])
                (new,) = set(self.listdir()) - before
                info = probe(self.p(new), pts + [(10, 10)] if op != "rotate:180" and op != "flip:h" and op != "flip:v" else pts)
                self.assertEqual((info["width"], info["height"]), size)
                self.assertTrue(red(info["colors"][0]), (op, info["colors"]))

    def test_rotate_oriented_jpeg(self):
        src = make(self.p("rot.jpg"), w=300, h=200, orientation=6, quadrant="bl")
        act("rotate:90", [src])
        info = probe(self.p("rot-edited.jpg"), [(290, 10)])
        self.assertEqual((info["width"], info["height"], info["orientation"]), (300, 200, 1))
        self.assertTrue(red(info["colors"][0]), info["colors"])

    def test_convert_formats(self):
        src = make(self.p("c.jpg"), w=120, h=80)
        for op, fmt in (("convert:png", "png"), ("convert:heic", "heic"), ("convert:tiff", "tiff"), ("convert:gif", "gif"), ("convert:bmp", "bmp")):
            act(op, [src])
            ext = {"tiff": "tiff"}.get(fmt, fmt)
            self.assertEqual(sips(self.p(f"c.{ext}")), (120, 80, fmt))
        self.assertIn("already JPEG", act("convert:jpeg", [src]))

    def test_convert_optional_encoders(self):
        src = make(self.p("c.png"), "public.png", 64, 48)
        out = act("convert:avif", [src])
        if "can't write" in out:
            self.skipTest("this Mac can't encode AVIF")
        self.assertEqual(probe(self.p("c.avif"))["uti"], "public.avif")
        self.assertIn("can't write WebP", act("convert:webp", [src], MT_TEST_HIDE_FORMATS="webp"))

    def test_convert_oriented_jpeg_bakes_orientation(self):
        src = make(self.p("o.jpg"), w=300, h=200, orientation=6, quadrant="bl")
        act("convert:png", [src])
        info = probe(self.p("o.png"), [(10, 10)])
        self.assertEqual((info["width"], info["height"], info["orientation"]), (200, 300, 1))
        self.assertTrue(red(info["colors"][0]))

    def test_alpha_heic_and_png(self):
        heic = make(self.p("alpha.heic"), "public.heic", 200, 100, alpha=True)
        self.assertTrue(probe(heic)["hasAlpha"])
        act("convert:png", [heic])
        info = probe(self.p("alpha.png"), [(2, 2), (100, 50)])
        self.assertTrue(info["hasAlpha"])
        self.assertEqual(info["colors"][0][3], 0)  # transparent corner
        self.assertTrue(red(info["colors"][1]))
        act("convert:jpeg", [heic])
        info = probe(self.p("alpha.jpg"), [(2, 2), (100, 50)])
        self.assertGreater(min(info["colors"][0][:3]), 240)  # flattened on white, not black
        self.assertTrue(red(info["colors"][1]))
        act("resize:pct:50", [heic])
        info = probe(self.p("alpha-edited.heic"), [(1, 1)])
        self.assertEqual((info["width"], info["height"]), (100, 50))
        self.assertTrue(info["hasAlpha"])
        self.assertEqual(info["colors"][0][3], 0)
        png = make(self.p("t.png"), "public.png", 100, 100, alpha=True)
        act("convert:bmp", [png])
        self.assertGreater(min(probe(self.p("t.bmp"), [(1, 1)])["colors"][0][:3]), 240)

    def test_strip_metadata_lossless_keeps_orientation(self):
        src = make(self.p("m.jpg"), w=300, h=200, orientation=6, gps=True, exif=True)
        before = probe(src)
        self.assertTrue(before["gps"])
        self.assertIn("DateTimeOriginal", before["exif"])
        self.assertIn("Cleaned m.jpg", act("strip:all", [src]))
        info = probe(self.p("m-edited.jpg"))
        self.assertFalse(info["gps"])
        self.assertNotIn("DateTimeOriginal", info["exif"])
        self.assertNotIn("UserComment", info["exif"])
        self.assertNotIn("Make", info["tiff"])
        self.assertEqual(info["orientation"], 6)  # still displays upright
        self.assertEqual((info["width"], info["height"]), (300, 200))  # not re-rendered
        with open(self.p("m-edited.jpg"), "rb") as f:
            data = f.read()
        self.assertNotIn(b"secret comment", data)
        self.assertNotIn(b"TestCam", data)

    def test_remove_location_only(self):
        src = make(self.p("g.jpg"), gps=True, exif=True)
        act("strip:gps", [src])
        info = probe(self.p("g-edited.jpg"))
        self.assertFalse(info["gps"])
        self.assertEqual(info["tiff"].get("Make"), "TestCam")
        self.assertIn("DateTimeOriginal", info["exif"])
        png = make(self.p("g.png"), "public.png", gps=True, exif=True)
        act("strip:all", [png])
        info = probe(self.p("g-edited.png"))
        self.assertFalse(info["gps"])
        self.assertNotIn("Make", info["tiff"])

    def test_optimize(self):
        big = make(self.p("big.jpg"), w=600, h=400, noise=True, quality=1.0, exif=True)
        msg = act("optimize", [big], image_quality=60)
        self.assertIn("Optimized big.jpg → big-edited.jpg", msg)
        self.assertLess(os.path.getsize(self.p("big-edited.jpg")), os.path.getsize(big))
        self.assertEqual(probe(self.p("big-edited.jpg"))["tiff"].get("Make"), "TestCam")
        small = make(self.p("small.jpg"), w=100, h=100, quality=0.2)
        msg = act("optimize", [small], image_quality=100)
        self.assertEqual(msg, "Nothing to do for small.jpg: already optimized")
        self.assertFalse(os.path.exists(self.p("small-edited.jpg")))

    def test_animated_gif(self):
        gif = make(self.p("anim.gif"), "com.compuserve.gif", 200, 100, frames=5, delay=0.3)
        self.assertEqual(probe(gif)["count"], 5)
        act("resize:pct:50", [gif])
        info = probe(self.p("anim-edited.gif"))
        self.assertEqual((info["count"], info["width"], info["height"]), (5, 100, 50))
        self.assertAlmostEqual(info["gif"]["DelayTime"], 0.3, places=2)
        self.assertEqual(info["loop"], 0)
        act("rotate:90", [gif])
        info = probe(self.p("anim-edited-2.gif"))
        self.assertEqual((info["count"], info["width"], info["height"]), (5, 100, 200))
        msg = act("convert:png", [gif])
        self.assertIn("first frame only", msg)
        self.assertEqual(probe(self.p("anim.png"))["count"], 1)
        act("optimize", [gif])  # either smaller (all frames kept) or skipped
        if os.path.exists(self.p("anim-edited-3.gif")):
            self.assertEqual(probe(self.p("anim-edited-3.gif"))["count"], 5)

    def test_remove_background(self):
        src = make(self.p("subject.png"), "public.png", 600, 400, subject=True)
        msg = act("removebg", [src])
        self.assertIn("transparent PNG", msg)
        info = probe(self.p("subject-edited.png"), [(5, 5), (300, 200)])
        self.assertEqual((info["width"], info["height"]), (600, 400))
        self.assertEqual(info["colors"][0][3], 0)
        self.assertEqual(info["colors"][1][3], 255)
        self.assertTrue(red(info["colors"][1]))
        act("removebg:crop", [src])
        w, h, _ = sips(self.p("subject-edited-2.png"))
        self.assertLess(w, 300)
        self.assertLess(h, 300)
        jpg = make(self.p("subject.jpg"), "public.jpeg", 600, 400, subject=True, orientation=6)
        act("removebg", [jpg])
        self.assertEqual(sips(self.p("subject-2.png"))[:2], (400, 600))  # oriented; subject.png exists already
        self.assertIn("macOS 14", act("removebg", [src], MT_TEST_NO_VISION=1))

    def test_replace_originals(self):
        src = make(self.p("r.jpg"), w=400, h=200)
        msg = act("resize:pct:50", [src], replace_originals=1)
        self.assertEqual(msg, "Resized r.jpg → r.jpg (200×100)")
        self.assertEqual(self.listdir(), ["r.jpg"])
        self.assertEqual(sips(src)[:2], (200, 100))
        act("convert:png", [src], replace_originals=1)  # other format: never replaces
        self.assertEqual(self.listdir(), ["r.jpg", "r.png"])
        os.chmod(src, 0o444)  # read-only file: a copy instead
        act("rotate:90", [src], replace_originals=1)
        self.assertEqual(self.listdir(), ["r-edited.jpg", "r.jpg", "r.png"])

    def test_read_only_folder_falls_back(self):
        src = make(self.p("ro.jpg"))
        fallback = os.path.join(self.dir, "Downloads")
        os.makedirs(fallback)
        os.chmod(self.d, 0o555)
        msg = act("resize:pct:50", [src], MT_TEST_FALLBACK_DIR=fallback)
        self.assertIn("saved to Downloads because the folder is read-only", msg)
        self.assertEqual(os.listdir(fallback), ["ro-edited.jpg"])
        msg = act("convert:png", [src], MT_TEST_FALLBACK_DIR=fallback)
        self.assertIn("ro-edited.png", sorted(os.listdir(fallback)))

    def test_partial_failures(self):
        good = make(self.p("good.jpg"), w=100, h=100)
        bad = self.p("bad.jpg")
        with open(bad, "w") as f:
            f.write("not an image")
        missing = self.p("missing.png")
        msg = act("resize:pct:50", [good, bad, missing], mt_reveal=1)
        self.assertTrue(msg.startswith("Resized 1 file · 2 failed (bad.jpg: not a readable image"), msg)
        self.assertTrue(os.path.exists(self.p("good-edited.jpg")))
        with open(os.path.join(CACHE, "reveal.txt")) as f:
            self.assertEqual(f.read().split("\n"), [self.p("good-edited.jpg")])
        self.assertEqual(act("rotate:90", [missing]), "Failed: missing.png: file not found")
        self.assertEqual(act("rotate:90", []), "No files to process")
        # no stray temp files
        self.assertFalse([f for f in os.listdir(self.d) if f.startswith(".mt-")])

    def test_unusual_file_names(self):
        names = ["ünï cødé 🙂.jpg", "quote's \"double\".jpg", "$(touch pwned).jpg", "-dash.jpg", "tab\tand\nnewline.jpg", "noext", ".hidden.jpg"]
        files = [make(self.p(n), w=40, h=40) for n in names]
        files[-2] = self.p("noext")
        msg = act("resize:pct:50", files[:-2] + [files[-1]])
        self.assertEqual(msg, "Resized 6 files")
        for n in names[:-2]:
            stem = n.rsplit(".", 1)[0]
            self.assertEqual(sips(self.p(f"{stem}-edited.jpg"))[:2], (20, 20))
        self.assertEqual(sips(self.p(".hidden-edited.jpg"))[:2], (20, 20))
        self.assertFalse(os.path.exists(os.path.join(SRC, "pwned")))
        self.assertFalse(os.path.exists(self.p("pwned")))

    def test_suffix_config(self):
        src = make(self.p("s.jpg"))
        act("resize:pct:50", [src], output_suffix="_small")
        self.assertTrue(os.path.exists(self.p("s_small.jpg")))
        act("resize:pct:50", [src], output_suffix="/../../evil")
        self.assertTrue(os.path.exists(self.p("s....evil.jpg")))
        act("resize:pct:50", [src], output_suffix="")
        self.assertTrue(os.path.exists(self.p("s-2.jpg")))

    def test_huge_image_guard(self):
        src = make(self.p("h.png"), "public.png", 400, 300)
        self.assertIn("image too large", act("rotate:90", [src], MT_TEST_MAX_PIXELS=10000))
        # downscaling doesn't need a full decode
        self.assertIn("Resized", act("resize:max:50", [src], MT_TEST_MAX_PIXELS=10000))

    def test_non_writable_formats_fall_back(self):
        # Sources ImageIO can read but not write (like WebP) are saved as PNG/JPEG; optimize refuses
        src = make(self.p("w.tiff"), "public.tiff", 100, 100)
        msg = act("rotate:90", [src], MT_TEST_HIDE_FORMATS="tiff")
        self.assertIn("saved as JPEG", msg)
        self.assertTrue(os.path.exists(self.p("w.jpg")))
        self.assertIn("can't re-encode", act("optimize", [src], MT_TEST_HIDE_FORMATS="tiff"))


# ---------------------------------------------------------------- video / audio

def wait_worker(timeout=120):
    lock = os.path.join(CACHE, "worker.lock")
    end = time.time() + timeout
    while time.time() < end:
        if not os.path.exists(lock) and not [f for f in os.listdir(os.path.join(CACHE, "queue")) if f.endswith(".job")]:
            return
        time.sleep(0.2)
    raise AssertionError("worker did not finish")


class QueueTests(Base):
    def job(self, op, files, **env):
        """Enqueue without running the worker and return the parsed job(s)."""
        q = os.path.join(CACHE, "queue")
        e = base_env(mt_files=json.dumps(files), **env)
        out = subprocess.run(["osascript", "-l", "JavaScript", "./media.js", "enqueue", op], cwd=SRC, env=e, capture_output=True, text=True)
        jobs = []
        for name in sorted(os.listdir(q)) if os.path.isdir(q) else []:
            if name.endswith(".job"):
                with open(os.path.join(q, name), "rb") as f:
                    jobs.append([x.decode() for x in f.read().split(b"\0")[:-1]])
                os.remove(os.path.join(q, name))
        return out.stdout.strip(), jobs

    def test_ffmpeg_commands(self):
        mov = self.p("in 'clip' ü.mov")
        open(mov, "w").close()
        env = dict(MT_TEST_FFMPEG="/opt/fake/ffmpeg", MT_TEST_ARCH="arm64")
        msg, jobs = self.job("mp4", [mov], **env)
        self.assertEqual(msg, "Queued 1 file")
        batch, idx, count, label, src, final, tmp, replace, reveal, tool, *cmd = jobs[0]
        self.assertEqual((idx, count, label, src, replace, reveal, tool), ("1", "1", "in 'clip' ü.mov", mov, "0", "0", "ffmpeg"))
        self.assertEqual(final, self.p("in 'clip' ü.mp4"))
        self.assertTrue(os.path.basename(tmp).startswith(".mt-") and tmp.endswith(".mp4"))
        self.assertEqual(cmd[0], "/opt/fake/ffmpeg")
        self.assertEqual(cmd[cmd.index("-i") + 1], "file:" + mov)
        self.assertEqual(cmd[-1], "file:" + tmp)
        self.assertIn("h264_videotoolbox", cmd)
        self.assertIn("-q:v", cmd)
        _, jobs = self.job("mp4", [mov], MT_TEST_FFMPEG="/opt/fake/ffmpeg", MT_TEST_ARCH="x86_64")
        self.assertIn("-b:v", jobs[0])
        _, jobs = self.job("hevc", [mov], **env)
        self.assertIn("hevc_videotoolbox", jobs[0])
        self.assertIn("hvc1", jobs[0])
        _, jobs = self.job("gif", [mov], gif_width=320, gif_fps=10, **env)
        vf = jobs[0][jobs[0].index("-vf") + 1]
        self.assertIn("palettegen", vf)
        self.assertIn("fps=10", vf)
        self.assertIn("min(320,iw)", vf)
        self.assertTrue(jobs[0][5].endswith(".gif"))
        _, jobs = self.job("compress:28", [mov], **env)
        self.assertEqual(jobs[0][jobs[0].index("-crf") + 1], "28")
        self.assertIn("libx264", jobs[0])
        _, jobs = self.job("webm", [mov], **env)
        self.assertIn("libvpx-vp9", jobs[0])
        _, jobs = self.job("mute", [mov], **env)
        self.assertTrue(jobs[0][5].endswith("-edited.mov"))
        self.assertIn("-0:a", jobs[0])
        _, jobs = self.job("mp3", [mov], **env)
        self.assertTrue(jobs[0][5].endswith(".mp3"))
        self.assertIn("libmp3lame", jobs[0])
        _, jobs = self.job("scale:720", [mov], **env)
        self.assertIn("720", jobs[0][jobs[0].index("-vf") + 1])
        _, jobs = self.job("trim:0_10-0_25.5", [mov], **env)
        j = jobs[0]
        self.assertEqual(j[j.index("-ss") + 1], "10")
        self.assertEqual(j[j.index("-t") + 1], "15.5")
        self.assertLess(j.index("-ss"), j.index("-i"))
        self.assertTrue(j[5].endswith("-edited.mov"))

    def test_batch_and_names(self):
        a, b = self.p("a.mov"), self.p("b.mp4")
        for f in (a, b):
            open(f, "w").close()
        open(self.p("a.mp4"), "w").close()  # existing output: numbered instead
        msg, jobs = self.job("mp4", [a, b, self.p("x.txt"), self.p("gone.mov")], MT_TEST_FFMPEG="/f/ffmpeg", mt_reveal=1)
        self.assertEqual(msg, "Queued 2 files · 2 skipped (x.txt: not a video or audio file)")
        self.assertEqual([j[1:3] for j in jobs], [["1", "2"], ["2", "2"]])
        self.assertEqual(jobs[0][0], jobs[1][0])
        self.assertEqual(jobs[0][5], self.p("a-2.mp4"))
        self.assertEqual(jobs[1][5], self.p("b-edited.mp4"))
        self.assertEqual(jobs[0][8], "1")
        # replace mode only when the format stays the same, and never for trim
        _, jobs = self.job("mp4", [b], MT_TEST_FFMPEG="/f/ffmpeg", replace_originals=1)
        self.assertEqual((jobs[0][5], jobs[0][7]), (b, "1"))
        _, jobs = self.job("trim:0_01-0_02", [b], MT_TEST_FFMPEG="/f/ffmpeg", replace_originals=1)
        self.assertEqual(jobs[0][7], "0")
        msg, jobs = self.job("webm", [a], MT_TEST_FFMPEG="none")
        self.assertEqual(msg, "Nothing queued: a.mov: needs ffmpeg")
        self.assertEqual(jobs, [])
        self.assertEqual(self.job("trim:bad", [a], MT_TEST_FFMPEG="/f/ffmpeg")[0], "Invalid trim range")

    def test_copy_install_command(self):
        e = base_env(mt_op="copy", mt_files="[]")
        old = subprocess.run(["pbpaste"], capture_output=True).stdout
        try:
            out = subprocess.run(["./action.sh", "brew install ffmpeg"], cwd=SRC, env=e, capture_output=True, text=True).stdout
            self.assertIn("Copied", out)
            self.assertEqual(subprocess.run(["pbpaste"], capture_output=True, text=True).stdout, "brew install ffmpeg")
        finally:
            subprocess.run(["pbcopy"], input=old)

    def test_cancel_and_stale_lock(self):
        e = base_env()
        out = subprocess.run(["./worker.sh", "--cancel"], cwd=SRC, env=e, capture_output=True, text=True).stdout
        self.assertEqual(out.strip(), "Nothing to cancel")
        # A lock left by a dead worker is taken over
        lock = os.path.join(CACHE, "worker.lock")
        os.makedirs(lock, exist_ok=True)
        with open(os.path.join(lock, "pid"), "w") as f:
            f.write("999999")
        wav = write_wav(self.p("tone.wav"))
        msg = act("wav", [wav], MT_TEST_FFMPEG="none", MT_TEST_WORKER_FOREGROUND=1)  # WAV→WAV isn't offered, but the action copes
        self.assertIn("Queued", msg)
        self.assertFalse(os.path.exists(lock))
        self.assertEqual(len(notifications()), 1)

    @unittest.skipUnless(os.path.exists("/usr/bin/afconvert"), "afconvert missing")
    def test_afconvert_audio(self):
        wav = write_wav(self.p("tone ü.wav"))
        msg = act("m4a", [wav], MT_TEST_FFMPEG="none", MT_TEST_WORKER_FOREGROUND=1, mt_reveal=1)
        self.assertEqual(msg, "Queued 1 file")
        self.assertEqual(notifications(), ["Converted tone ü.wav → tone ü.m4a"])
        info = subprocess.run(["afinfo", self.p("tone ü.m4a")], capture_output=True, text=True).stdout
        self.assertIn("aac", info.lower())
        with open(os.path.join(CACHE, "reveal.txt")) as f:
            self.assertEqual(f.read().strip(), self.p("tone ü.m4a"))
        for op, needle in (("flac", "flac"), ("aiff", "AIFF")):
            act(op, [wav], MT_TEST_FFMPEG="none", MT_TEST_WORKER_FOREGROUND=1)
            info = subprocess.run(["afinfo", self.p(f"tone ü.{op}")], capture_output=True, text=True).stdout
            self.assertIn(needle, info)
        self.assertFalse([f for f in os.listdir(self.d) if f.startswith(".mt-")])

    def test_background_worker(self):
        wavs = [write_wav(self.p(f"t{i}.wav"), seconds=1) for i in range(3)]
        msg = act("flac", wavs, MT_TEST_FFMPEG="none")
        self.assertEqual(msg, "Queued 3 files")
        wait_worker()
        self.assertEqual(notifications(), ["Converted 3 files"])
        for i in range(3):
            self.assertTrue(os.path.exists(self.p(f"t{i}.flac")))


@unittest.skipUnless(os.path.exists(AVTOOL) or shutil.which("swiftc"), "swiftc missing")
class AVConvertTests(Base):
    def setUp(self):
        super().setUp()
        if not os.path.exists(AVTOOL):
            self.skipTest("avtool could not be built")
        self.mov = self.p("clip ü 'x'.mov")
        r = subprocess.run([AVTOOL, "make", self.mov, "320", "240", "3"], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "ok")

    def run_op(self, op, files=None, **env):
        return act(op, files or [self.mov], MT_TEST_FFMPEG="none", MT_TEST_WORKER_FOREGROUND=1, **env)

    def test_convert_and_resize(self):
        self.run_op("mp4")
        out = self.p("clip ü 'x'.mp4")
        info = avprobe(out)
        self.assertEqual((info["width"], info["height"], info["codec"]), (320, 240, "avc1"))
        self.assertAlmostEqual(info["duration"], 3, delta=0.2)
        self.assertEqual(notifications()[-1], "Converted clip ü 'x'.mov → clip ü 'x'.mp4")
        self.run_op("hevc")
        self.assertEqual(avprobe(self.p("clip ü 'x'-2.mp4"))["codec"], "hvc1")

    def test_trim(self):
        self.run_op("trim:0_01-0_02.5")
        info = avprobe(self.p("clip ü 'x'-edited.mov"))
        self.assertAlmostEqual(info["duration"], 1.5, delta=0.2)

    def test_failure_reason_and_partial_batch(self):
        # A movie without sound can't become M4A: the notification says why; the other file still converts
        wav = write_wav(self.p("tone.wav"))
        msg = self.run_op("m4a", [self.mov, wav])
        self.assertEqual(msg, "Queued 2 files")
        n = notifications()[-1]
        self.assertTrue(n.startswith("1 converted, 1 failed · clip ü 'x'.mov: "), n)
        self.assertIn("no audio track", n)
        self.assertTrue(os.path.exists(self.p("tone.m4a")))
        self.assertFalse([f for f in os.listdir(self.d) if f.startswith(".mt-")])
        with open(os.path.join(CACHE, "conversions.log")) as f:
            log = f.read()
        self.assertIn("FAILED", log)


@unittest.skipUnless(FFMPEG, "ffmpeg is not installed (brew install ffmpeg): skipping the ffmpeg tests")
class FFmpegTests(Base):
    def setUp(self):
        super().setUp()
        self.src = self.p("test ü 'src'.mov")
        subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=4", "-f", "lavfi",
                        "-i", "sine=frequency=440:duration=4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", self.src], check=True)

    def run_op(self, op, **env):
        return act(op, [self.src], MT_TEST_FFMPEG=FFMPEG, MT_TEST_WORKER_FOREGROUND=1, **env)

    def streams(self, path):
        info = ffprobe(path)
        return {s["codec_type"]: s for s in info["streams"]}, float(info["format"]["duration"])

    def test_video_formats(self):
        for op, name, vcodec, acodec in (("mp4", "test ü 'src'.mp4", "h264", "aac"), ("hevc", "test ü 'src'-2.mp4", "hevc", "aac"),
                                         ("webm", "test ü 'src'.webm", "vp9", "opus"), ("compress:32", "test ü 'src'-3.mp4", "h264", "aac")):
            with self.subTest(op=op):
                self.run_op(op)
                s, dur = self.streams(self.p(name))
                self.assertEqual(s["video"]["codec_name"], vcodec)
                self.assertEqual(s["audio"]["codec_name"], acodec)
                self.assertAlmostEqual(dur, 4, delta=0.3)

    def test_gif_audio_trim_mute_scale(self):
        self.run_op("gif", gif_width=320)
        s, _ = self.streams(self.p("test ü 'src'.gif"))
        self.assertEqual((s["video"]["codec_name"], s["video"]["width"]), ("gif", 320))
        for op in ("mp3", "m4a", "wav", "flac"):
            self.run_op(op)
            s, _ = self.streams(self.p(f"test ü 'src'.{op}"))
            self.assertNotIn("video", s)
        self.run_op("trim:0_01-0_03")
        _, dur = self.streams(self.p("test ü 'src'-edited.mov"))
        self.assertAlmostEqual(dur, 2, delta=0.2)
        self.run_op("mute")
        s, _ = self.streams(self.p("test ü 'src'-edited-2.mov"))
        self.assertNotIn("audio", s)
        self.run_op("scale:240")
        s, _ = self.streams(self.p("test ü 'src'.mp4"))
        self.assertEqual((s["video"]["width"], s["video"]["height"]), (426, 240))


# ---------------------------------------------------------------- packaging

class PlistTests(unittest.TestCase):
    def test_build_and_plist(self):
        subprocess.run([sys.executable, "tools/build.py"], cwd=ROOT, check=True, capture_output=True)
        with open(os.path.join(SRC, "info.plist"), "rb") as f:
            p = plistlib.load(f)
        uids = [o["uid"] for o in p["objects"]]
        self.assertEqual(len(uids), len(set(uids)))
        for src, conns in p["connections"].items():
            self.assertIn(src, uids)
            for c in conns:
                self.assertIn(c["destinationuid"], uids)
        for o in p["objects"]:
            kw = o["config"].get("keyword")
            if kw:
                self.assertRegex(kw, r"^\{var:keyword_\w+\}$")
        self.assertTrue(p["readme"].startswith("## Usage"))
        ua = [o for o in p["objects"] if o["type"] == "alfred.workflow.trigger.universalaction"]
        self.assertEqual(ua[0]["config"]["acceptsfiles"], True)
        self.assertEqual(ua[0]["config"]["acceptsmulti"], 1)
        out = subprocess.run(["sips", "-g", "pixelWidth", os.path.join(SRC, "icon.png")], capture_output=True, text=True).stdout
        self.assertGreaterEqual(int(out.split()[-1]), 256)
        for f in os.listdir(SRC):
            p2 = os.path.join(SRC, f)
            if os.path.isfile(p2):
                with open(p2, "rb") as fh:
                    self.assertNotEqual(fh.read(4), b"\xcf\xfa\xed\xfe", f"compiled binary in src: {f}")
        for f in ("action.sh", "worker.sh"):
            self.assertTrue(os.stat(os.path.join(SRC, f)).st_mode & stat.S_IXUSR)


if __name__ == "__main__":
    unittest.main(verbosity=1)
