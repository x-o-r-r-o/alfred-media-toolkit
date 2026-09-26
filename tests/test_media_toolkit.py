#!/usr/bin/env python3
"""End-to-end tests: run the Script Filters and the action the way Alfred does, on generated fixtures.

Images are made with CoreGraphics (tests/fixtures.js). Movies are made with AVFoundation (tests/avtool.swift)
for the avconvert/afconvert paths, and with ffmpeg only if it is installed; the ffmpeg tests are skipped otherwise.
"""
import json, os, plistlib, unicodedata, shutil, stat, subprocess, sys, tempfile, time, unittest, wave, struct, math

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
    # MT_TEST: the safety net. Without an override the scripts never read the real Finder selection or
    # clipboard, never call Alfred, never open Finder windows and never write to the real Downloads folder
    e = dict(os.environ, MT_TEST="1", alfred_workflow_cache=CACHE, alfred_workflow_bundleid="io.github.x-o-r-r-o.media-toolkit",
             MT_TEST_NOTIFY_FILE=os.path.join(CACHE, "notify.txt"), MT_TEST_REVEAL_FILE=os.path.join(CACHE, "reveal.txt"))
    for k in ("output_suffix", "replace_originals", "image_quality", "mt_files", "mt_op", "mt_reveal", "mt_ua_files", "mt_sel",
              "ffmpeg_path", "keep_dates", "mt_copy", "MT_TEST_SELECTION", "MT_TEST_SELECTION_SCRIPT", "MT_TEST_FALLBACK_DIR", "MT_TEST_CLIPBOARD_FILE"):
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
        empty = self.p("empty folder")
        os.makedirs(empty)
        self.assertEqual(items("img", selection=[empty])[0]["title"], "Select images in Finder first")  # nothing inside

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

    def test_already_in_that_format(self):
        jpg = make(self.p("a.jpg"))
        it = items("img", "jpg", [jpg])
        self.assertEqual(it[0]["title"], "Already JPEG")

    def test_huge_selection_goes_through_cache(self):
        real = make(self.p("real.jpg"), w=40, h=40)
        many = [self.p(f"{'long name ' * 5}{i}.jpg") for i in range(600)] + [real]
        e = base_env(MT_TEST_SELECTION=json.dumps(many))
        out = subprocess.run(["osascript", "-l", "JavaScript", "./media.js", "img", ""], cwd=SRC, env=e, capture_output=True, text=True)
        self.assertLess(len(out.stdout), 60000)
        it = json.loads(out.stdout)["items"]
        ref = it[0]["variables"]["mt_files"]
        self.assertTrue(ref.startswith("@" + CACHE), ref)
        e = base_env(mt_op="resize:pct:50", mt_files=ref)
        msg = subprocess.run(["./action.sh", "resize:pct:50"], cwd=SRC, env=e, capture_output=True, text=True).stdout
        self.assertTrue(msg.startswith("Resized 1 file · 600 failed"), msg)
        self.assertTrue(os.path.exists(self.p("real-edited.jpg")))
        # only lists written by the workflow are read
        with open(self.p("list.json"), "w") as f:
            json.dump([real], f)
        self.assertEqual(act("rotate:90", []) , "No files to process")
        e = base_env(mt_op="rotate:90", mt_files="@" + self.p("list.json"))
        msg = subprocess.run(["./action.sh", "rotate:90"], cwd=SRC, env=e, capture_output=True, text=True).stdout.strip()
        self.assertEqual(msg, "No files to process")

    # ---- regressions from audit pass 3
    def test_folders_expand_to_their_media(self):
        folder = self.p("album")
        os.makedirs(os.path.join(folder, "sub"))
        b, a = make(os.path.join(folder, "b.png"), "public.png"), make(os.path.join(folder, "a.jpg"))
        make(os.path.join(folder, ".hidden.jpg"))
        make(os.path.join(folder, "sub", "deep.jpg"))
        open(os.path.join(folder, "clip.mov"), "w").close()
        open(os.path.join(folder, "notes.txt"), "w").close()
        it = items("img", "50%", [folder])
        self.assertEqual(json.loads(it[0]["variables"]["mt_files"]), [a, b])
        it = items("all", "mp4", mt_ua_files=folder, MT_TEST_FFMPEG="/f/ffmpeg")
        self.assertEqual(json.loads(it[0]["variables"]["mt_files"]), [os.path.join(folder, "clip.mov")])

    def test_selection_is_remembered_for_the_session(self):
        a, b = make(self.p("a.jpg")), make(self.p("b.jpg"))
        data = sf("img", "", [a])
        self.assertEqual(json.loads(data["variables"]["mt_sel"]), [a])
        # Alfred passes the variable back while typing: Finder isn't asked again
        it = items("img", "50%", [b], mt_sel=data["variables"]["mt_sel"])
        self.assertEqual(json.loads(it[0]["variables"]["mt_files"]), [a])
        self.assertEqual(it[0]["quicklookurl"], a)

    def test_media_keyword_without_selection(self):
        self.assertEqual(items("all", "", [])[0]["title"], "Select images, videos or audio in Finder first")

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
            fake = subprocess.Popen(["/bin/bash", "-c", "exec -a worker.sh /bin/sleep 30"])
            self.addCleanup(fake.wait)
            self.addCleanup(fake.kill)
            with open(os.path.join(lock, "pid"), "w") as f:
                f.write(str(fake.pid))
            with open(os.path.join(lock, "state"), "w") as f:
                f.write("200\nholiday ü.mov\nffmpeg\n")
            with open(os.path.join(CACHE, "progress.txt"), "w") as f:
                f.write("out_time_us=50000000\nprogress=continue\nout_time_us=100000000\nprogress=continue\n")
            data = sf("vid", "", [], MT_TEST_FFMPEG="none")
            self.assertEqual(data["items"][0]["title"], "Converting holiday ü.mov · 50%")
            self.assertEqual(data["items"][0]["arg"], "cancel")
            self.assertEqual(data.get("rerun"), 1)
            for pid in ("999999", str(os.getpid())):  # dead worker, or a reused pid (regression: showed a stale status)
                with open(os.path.join(lock, "pid"), "w") as f:
                    f.write(pid)
                self.assertNotIn("cancel", [i.get("arg") for i in items("vid", "", [], MT_TEST_FFMPEG="none")])
        finally:
            shutil.rmtree(lock, ignore_errors=True)


# ---------------------------------------------------------------- image operations

class ImageTests(Base):
    # ---- regressions from audit pass 1
    def test_no_subject_found(self):
        # NSUInteger counts come back as strings from the bridge: the empty-result check never fired
        src = make(self.p("plain.png"), "public.png", 300, 200, plain=True)
        self.assertEqual(act("removebg", [src]), "Failed: plain.png: no subject found")
        self.assertEqual(self.listdir(), ["plain.png"])

    def test_panorama_downscale_is_not_too_large(self):
        # the thumbnail guard used target² instead of the real area
        src = make(self.p("pano.png"), "public.png", 2000, 10)
        msg = act("resize:max:1000", [src], MT_TEST_MAX_PIXELS=30000)
        self.assertIn("(1000×5)", msg)

    def test_strip_notes_and_gif_loop(self):
        tif = make(self.p("s.tiff"), "public.tiff", 50, 50, exif=True)
        self.assertIn("saved as JPEG", act("strip:all", [tif], MT_TEST_HIDE_FORMATS="tiff"))
        gif = make(self.p("l.gif"), "com.compuserve.gif", 40, 40, frames=3, loop=3)
        act("strip:all", [gif])
        info = probe(self.p("l-edited.gif"))
        self.assertEqual((info["count"], info["loop"]), (3, 3))
        act("resize:pct:50", [gif])
        self.assertEqual(probe(self.p("l-edited-2.gif"))["loop"], 3)

    def test_many_files_notify_early(self):
        files = [make(self.p(f"n{i}.png"), "public.png", 10, 10) for i in range(10)]
        self.assertEqual(act("rotate:90", files), "Rotated 10 files")
        self.assertEqual(notifications(), ["Processing 10 images…"])


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

    def test_optimize_keeps_orientation(self):
        src = make(self.p("o.jpg"), w=600, h=400, noise=True, quality=1.0, orientation=6)
        act("optimize", [src], image_quality=50)
        info = probe(self.p("o-edited.jpg"))
        self.assertEqual((info["width"], info["height"], info["orientation"]), (600, 400, 6))

    def test_action_reports_a_crashed_script(self):
        # If osascript dies without output, the notification still says something
        broken = os.path.join(self.dir, "src")
        shutil.copytree(SRC, broken)
        with open(os.path.join(broken, "media.js"), "w") as f:
            f.write("function run() { throw new Error('boom') }\n")
        e = base_env(mt_op="rotate:90", mt_files="[]")
        out = subprocess.run(["./action.sh", "rotate:90"], cwd=broken, env=e, capture_output=True, text=True).stdout.strip()
        self.assertTrue(out.startswith("Media Toolkit failed: see "), out)
        e = base_env(mt_op="mp4", mt_files="[]")
        out = subprocess.run(["./action.sh", "mp4"], cwd=broken, env=e, capture_output=True, text=True).stdout.strip()
        self.assertTrue(out.startswith("Media Toolkit failed: see "), out)

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

    def test_remove_background_never_replaces(self):
        src = make(self.p("keep.png"), "public.png", 400, 300, subject=True)
        act("removebg", [src], replace_originals=1)
        self.assertEqual(self.listdir(), ["keep-edited.png", "keep.png"])
        self.assertFalse(probe(src)["hasAlpha"] and probe(src, [(2, 2)])["colors"][0][3] == 0)

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
        self.assertIn("-an", jobs[0])
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

    def test_mute_and_read_only_notes(self):
        mov = self.p("m.mp4")
        open(mov, "w").close()
        _, jobs = self.job("mute", [mov], MT_TEST_FFMPEG="/f/ffmpeg")
        j = jobs[0]
        self.assertEqual(j[j.index("-map") + 1], "0:V")  # regression: -map 0 failed on data streams; V skips cover art
        self.assertIn("-an", j)
        self.assertTrue(j[j.index("-progress") + 1].startswith("file:"))
        fallback = os.path.join(self.dir, "Downloads")
        os.makedirs(fallback)
        os.chmod(self.d, 0o555)
        msg, jobs = self.job("mp4", [mov], MT_TEST_FFMPEG="/f/ffmpeg", MT_TEST_FALLBACK_DIR=fallback)
        self.assertEqual(msg, "Queued 1 file · saved to Downloads because the folder is read-only")
        self.assertEqual(jobs[0][5], os.path.join(fallback, "m-edited.mp4"))
        self.assertTrue(jobs[0][6].startswith(fallback + "/.mt-"))

    # ---- regressions from audit pass 2
    def test_stream_mapping_and_custom_ffmpeg(self):
        mkv = self.p("subs.mkv")
        open(mkv, "w").close()
        for op in ("mp4", "hevc", "mov", "webm", "compress:23", "scale:720", "trim:0_01-0_02"):
            _, jobs = self.job(op, [mkv], MT_TEST_FFMPEG="/f/ffmpeg")
            j = jobs[0]
            self.assertIn("0:V:0", j, op)  # bitmap subtitles in MKV used to fail MP4 conversions
            self.assertIn("-sn", j, op)
            self.assertLess(j.index("-i"), j.index("-map"), op)
        fake = os.path.join(self.dir, "my ffmpeg 7")
        with open(fake, "w") as f:
            f.write("#!/bin/sh\n")
        os.chmod(fake, 0o755)
        e = dict(ffmpeg_path=fake)
        _, jobs = self.job("webm", [mkv], **e)
        self.assertEqual(jobs[0][10], fake)
        self.assertIn("webm", [i.get("arg") for i in items("vid", "", [mkv], **e)])

    def test_ts_is_not_video(self):
        ts = self.p("index.ts")
        open(ts, "w").close()
        self.assertEqual(items("vid", "", [ts], MT_TEST_FFMPEG="/f/ffmpeg")[-1]["title"], "Select video or audio files in Finder first")

    def write_job(self, cmd, tool="ffmpeg", idx="1", count="1", batch="b1"):
        q = os.path.join(CACHE, "queue")
        os.makedirs(q, exist_ok=True)
        tmp = self.p(".mt-test.out")
        fields = [batch, idx, count, "fake ü.mov", self.p("src.mov"), self.p("out.mov"), tmp, "0", "0", tool] + cmd
        open(self.p("src.mov"), "w").close()
        with open(os.path.join(q, f"{batch}-{idx.zfill(5)}.job"), "wb") as f:
            f.write(b"".join(x.encode() + b"\0" for x in fields))
        return tmp

    def test_cancel_running_conversion(self):
        # A long "conversion" is killed, its temp file dropped, the queue cleared and a notification sent
        # (the child's command line names the temp file, like a real conversion: that's how cancel recognises it)
        tmp = self.write_job(["/bin/sh", "-c", 'echo partial > "$1"; exec tail -f "$1"', "sh", self.p(".mt-test.out")], count="2")
        self.write_job(["/bin/sh", "-c", "exit 0"], idx="2", count="2")
        w = subprocess.Popen(["./worker.sh"], cwd=SRC, env=base_env())
        lock = os.path.join(CACHE, "worker.lock")
        end = time.time() + 20
        while not os.path.exists(os.path.join(lock, "child")) and time.time() < end:
            time.sleep(0.1)
        with open(os.path.join(lock, "state")) as f:
            self.assertEqual(f.read().split("\n")[1], "fake ü.mov")
        out = subprocess.run(["./worker.sh", "--cancel"], cwd=SRC, env=base_env(), capture_output=True, text=True).stdout
        self.assertEqual(out.strip(), "Cancelled the conversions")
        self.assertEqual(w.wait(timeout=10), 0)
        self.assertFalse(os.path.exists(tmp))
        self.assertFalse(os.path.exists(self.p("out.mov")))
        self.assertFalse(os.path.exists(lock))
        self.assertEqual(notifications(), [])  # the cancel action shows the one notification
        self.assertEqual([f for f in os.listdir(os.path.join(CACHE, "queue")) if f.endswith(".job")], [])

    def test_trim_progress_uses_trim_length(self):
        self.write_job(["/bin/sh", "-c", 'sleep 1.5; echo x > "$0"', self.p(".mt-test.out"), "-t", "15.5"])
        w = subprocess.Popen(["./worker.sh"], cwd=SRC, env=base_env())
        state = os.path.join(CACHE, "worker.lock", "state")
        end = time.time() + 10
        while not os.path.exists(state) and time.time() < end:
            time.sleep(0.05)
        with open(state) as f:
            self.assertEqual(f.readline().strip(), "15.5")
        w.wait(timeout=20)
        self.assertEqual(notifications(), ["Converted fake ü.mov → out.mov"])

    def test_copy_install_command(self):
        # a file stands in for the clipboard: the tests never touch the real one
        clip = os.path.join(self.dir, "clipboard.txt")
        e = base_env(mt_op="copy", mt_files="[]", MT_TEST_CLIPBOARD_FILE=clip)
        out = subprocess.run(["./action.sh", "brew install ffmpeg"], cwd=SRC, env=e, capture_output=True, text=True).stdout
        self.assertIn("Copied", out)
        with open(clip) as f:
            self.assertEqual(f.read(), "brew install ffmpeg")

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
        # regression: a lock whose pid now belongs to another process blocked the queue forever
        os.makedirs(lock, exist_ok=True)
        with open(os.path.join(lock, "pid"), "w") as f:
            f.write(str(os.getpid()))
        msg = act("flac", [wav], MT_TEST_FFMPEG="none", MT_TEST_WORKER_FOREGROUND=1)
        self.assertTrue(os.path.exists(self.p("tone.flac")))
        self.assertFalse(os.path.exists(lock))

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
        self.assertEqual(r.stdout.strip(), "ok", (r.returncode, r.stderr[-800:]))

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


# ---------------------------------------------------------------- regressions from the independent audit

class AuditImageTests(Base):
    def test_rotate_keeps_16_bit_and_greyscale(self):
        # re-rendered images were always drawn into an 8-bit RGB bitmap
        deep = make(self.p("deep.png"), "public.png", 120, 80, depth16=True, p3=True)
        self.assertEqual(probe(deep)["depth"], 16)
        act("rotate:90", [deep])
        info = probe(self.p("deep-edited.png"))
        self.assertEqual((info["width"], info["height"], info["depth"], info["profile"]), (80, 120, 16, "Display P3"))
        grey = make(self.p("grey.jpg"), "public.jpeg", 120, 80, gray=True)
        act("flip:h", [grey])
        self.assertEqual(probe(self.p("grey-edited.jpg"))["model"], "Gray")
        # a huge 16-bit image falls back to 8 bits rather than doubling the memory
        big = make(self.p("big16.png"), "public.png", 100, 100, depth16=True)
        act("rotate:90", [big], MT_TEST_MAX_PIXELS=30000)  # decodes (≤ 15000 px at 16 bits), draws in 8 bits
        self.assertEqual(probe(self.p("big16-edited.png"))["depth"], 8)

    def test_remove_background_keeps_colour_space_and_depth(self):
        # the cut-out was always written as 8-bit sRGB, clipping Display P3 colours from iPhone photos
        src = make(self.p("p3.png"), "public.png", 600, 400, subject=True, p3=True, depth16=True)
        act("removebg", [src])
        info = probe(self.p("p3-edited.png"), [(5, 5), (300, 200)])
        self.assertEqual((info["profile"], info["depth"]), ("Display P3", 16))
        self.assertEqual(info["colors"][0][3], 0)
        self.assertTrue(red(info["colors"][1]), info["colors"])
        # every subject is kept, not only the first
        two = make(self.p("two.png"), "public.png", 600, 400, subject=True, two=True)
        act("removebg", [two])
        info = probe(self.p("two-edited.png"), [(80, 200), (300, 200), (165, 200)])
        self.assertEqual([c[3] for c in info["colors"]], [255, 255, 0])
        # greyscale sources still work (written as RGB with alpha)
        grey = make(self.p("g.jpg"), "public.jpeg", 600, 400, subject=True, gray=True)
        self.assertIn("transparent PNG", act("removebg", [grey]))

    def test_multi_page_tiff(self):
        # extra pages were dropped silently
        tif = make(self.p("scan.tiff"), "public.tiff", 100, 60, frames=3)
        self.assertEqual(probe(tif)["count"], 3)
        act("rotate:90", [tif])
        info = probe(self.p("scan-edited.tiff"))
        self.assertEqual((info["count"], info["width"], info["height"]), (3, 60, 100))
        act("strip:all", [tif])
        self.assertEqual(probe(self.p("scan-edited-2.tiff"))["count"], 3)
        self.assertIn("first page only", act("convert:png", [tif]))


    def test_big_batches_run_in_chunks(self):
        # JXA never freed CoreGraphics images: 200 photos grew to 17 GB. Now each file's objects are released,
        # and big batches run in child processes of CHUNK files each
        files = [make(self.p(f"c{i}.png"), "public.png", 40, 20) for i in range(7)] + [self.p("missing.png")]
        msg = act("rotate:90", files, MT_TEST_CHUNK=3, mt_reveal=1)
        self.assertEqual(msg, "Rotated 7 files · Failed: missing.png: file not found")
        self.assertEqual(len([f for f in self.listdir() if "-edited" in f]), 7)
        with open(os.path.join(CACHE, "reveal.txt")) as f:
            self.assertEqual(len(f.read().split("\n")), 7)
        self.assertEqual(notifications(), [])  # fewer than 10 files: no early notice


class AuditQueueTests(QueueTests):
    def test_videotoolbox_args(self):
        mov = self.p("v.mov")
        open(mov, "w").close()
        env = dict(MT_TEST_FFMPEG="/opt/fake/ffmpeg", MT_TEST_ARCH="arm64")
        _, jobs = self.job("mp4", [mov], **env)
        j = jobs[0]
        self.assertEqual(j[j.index("-allow_sw") + 1], "1")  # no hardware encoder → software, not a failure
        self.assertEqual(j[j.index("-pix_fmt") + 1], "yuv420p")
        # an Intel ffmpeg on Apple Silicon has no -q:v for VideoToolbox: it must get a bitrate
        _, jobs = self.job("mp4", [mov], MT_TEST_FFMPEG_ARCHS="x86_64", **env)
        self.assertNotIn("-q:v", jobs[0])
        self.assertIn("-b:v", jobs[0])
        _, jobs = self.job("mp4", [mov], MT_TEST_FFMPEG_ARCHS="x86_64,arm64", **env)
        self.assertIn("-q:v", jobs[0])
        # HEVC keeps 10-bit sources in 10 bits (format negotiation) instead of forcing 8-bit yuv420p
        _, jobs = self.job("hevc", [mov], **env)
        j = jobs[0]
        self.assertNotIn("-pix_fmt", j)
        self.assertIn("format=yuv420p|p010le", j[j.index("-vf") + 1])
        # WebM: 8-bit 4:2:0 for browsers, stereo because libopus rejects 5.1(side)
        _, jobs = self.job("webm", [mov], **env)
        j = jobs[0]
        self.assertEqual((j[j.index("-pix_fmt") + 1], j[j.index("-ac") + 1]), ("yuv420p", "2"))

    def test_ffmpeg_architecture_from_the_binary(self):
        mov = self.p("v.mov")
        open(mov, "w").close()
        intel = os.path.join(self.dir, "ffmpeg-intel")
        with open(intel, "wb") as f:
            f.write(bytes.fromhex("cffaedfe07000001") + b"\0" * 64)  # thin x86_64 Mach-O header
        arm = os.path.join(self.dir, "ffmpeg-arm")
        with open(arm, "wb") as f:
            f.write(bytes.fromhex("cafebabe00000002" + "01000007" + "00" * 16 + "0100000c" + "00" * 16))  # universal
        for path, expect in ((intel, "-b:v"), (arm, "-q:v")):
            os.chmod(path, 0o755)
            _, jobs = self.job("mp4", [mov], ffmpeg_path=path, MT_TEST_ARCH="arm64")
            self.assertIn(expect, jobs[0], path)

    def test_gif_uses_two_passes(self):
        mov = self.p("v.mov")
        open(mov, "w").close()
        _, jobs = self.job("gif", [mov], MT_TEST_FFMPEG="/opt/fake/ffmpeg")
        j = jobs[0]
        tmp, cmd = j[6], j[10:]
        k = cmd.index("::then::")
        first, second = cmd[:k], cmd[k + 1:]
        palette = first[-1]
        self.assertEqual(palette, "file:" + tmp + "-aux-palette.png")
        self.assertIn("palettegen", first[first.index("-vf") + 1])
        self.assertNotIn("-progress", first)
        self.assertEqual(second[0], "/opt/fake/ffmpeg")
        self.assertIn(palette, second)
        self.assertIn("paletteuse", second[second.index("-filter_complex") + 1])
        self.assertEqual(second[-1], "file:" + tmp)

    def test_unknown_operations_are_refused(self):
        mov = self.p("v.mov")
        open(mov, "w").close()
        for op in ("compress:99", "compress:28;x", "scale:abc", "bogus"):
            msg, jobs = self.job(op, [mov], MT_TEST_FFMPEG="/f/ffmpeg")
            self.assertEqual((msg, jobs), (f"Unknown operation: {op}", []))

    def run_worker(self, **env):
        return subprocess.run(["./worker.sh"], cwd=SRC, env=base_env(**env), timeout=60)

    def test_worker_runs_every_command_of_a_job(self):
        tmp = self.p(".mt-test.out")
        order = os.path.join(self.dir, "order.txt")
        self.write_job(["/bin/sh", "-c", 'echo one >> "$0"; echo p > "$1-aux-palette.png"', order, tmp, "::then::",
                        "/bin/sh", "-c", 'echo two >> "$0"; test -e "$1-aux-palette.png" && echo gif > "$1"', order, tmp])
        self.run_worker()
        with open(order) as f:
            self.assertEqual(f.read().split(), ["one", "two"])
        self.assertTrue(os.path.exists(self.p("out.mov")))
        self.assertFalse([f for f in os.listdir(self.d) if f.startswith(".mt-")])  # palette removed
        # a failing first command stops the job
        self.write_job(["/bin/sh", "-c", "echo 'Stream map '\\''0:a:0'\\'' matches no streams.' >&2; exit 1", "::then::",
                        "/bin/sh", "-c", 'echo x > "$0"', tmp])
        self.run_worker()
        self.assertEqual(notifications()[-1], "Failed: fake ü.mov: no audio track")
        self.assertFalse(os.path.exists(self.p("out-2.mov")))

    def test_jobs_queued_after_a_cancel_still_run(self):
        # regression: a job queued while a cancelled conversion was still stopping was deleted
        tmp = self.p(".mt-test.out")
        self.write_job(["/bin/sh", "-c", 'trap "" TERM; echo a > "$1"; sleep 2', "sh", tmp], batch="a")
        w = subprocess.Popen(["./worker.sh"], cwd=SRC, env=base_env())
        lock = os.path.join(CACHE, "worker.lock")
        end = time.time() + 20
        while not os.path.exists(os.path.join(lock, "child")) and time.time() < end:
            time.sleep(0.05)
        subprocess.run(["./worker.sh", "--cancel"], cwd=SRC, env=base_env(), capture_output=True)
        self.write_job(["/bin/sh", "-c", 'echo b > "$0"', self.p(".mt-test.out")], batch="b")
        self.assertEqual(w.wait(timeout=20), 0)
        self.assertEqual(notifications(), ["Converted fake ü.mov → out.mov"])
        with open(self.p("out.mov")) as f:
            self.assertEqual(f.read().strip(), "b")  # the cancelled one was never saved

    def test_stopped_worker_stops_its_conversion(self):
        tmp = self.write_job(["/bin/sh", "-c", 'echo partial > "$1"; exec tail -f "$1"', "sh", self.p(".mt-test.out")])
        # C locale, as under Alfred: ps escapes the ü of the folder name (regression: the child wasn't recognised)
        e = base_env()
        for k in ("LANG", "LC_ALL", "LC_CTYPE"):
            e.pop(k, None)
        w = subprocess.Popen(["./worker.sh"], cwd=SRC, env=e)
        lock = os.path.join(CACHE, "worker.lock")
        end = time.time() + 20
        while not os.path.exists(os.path.join(lock, "child")) and time.time() < end:
            time.sleep(0.05)
        with open(os.path.join(lock, "child")) as f:
            child = int(f.read())
        w.terminate()
        w.wait(timeout=10)
        time.sleep(0.3)
        self.assertRaises(ProcessLookupError, os.kill, child, 0)
        self.assertFalse(os.path.exists(tmp))
        self.assertFalse(os.path.exists(lock))

    def test_stale_lock_orphan_is_stopped(self):
        # a worker killed hard leaves its conversion running and a partial hidden file next to the video
        tmp = self.p(".mt-orphan.mp4")
        with open(tmp, "w") as f:
            f.write("partial")
        orphan = subprocess.Popen(["/usr/bin/tail", "-f", tmp], stdout=subprocess.DEVNULL)
        self.addCleanup(orphan.wait)
        self.addCleanup(orphan.kill)
        lock = os.path.join(CACHE, "worker.lock")
        os.makedirs(lock)
        for name, value in (("pid", "999999"), ("child", str(orphan.pid)), ("tmp", tmp)):
            with open(os.path.join(lock, name), "w") as f:
                f.write(value)
        self.run_worker()
        self.assertIsNotNone(orphan.wait(timeout=5))
        self.assertFalse(os.path.exists(tmp))
        self.assertFalse(os.path.exists(lock))
        # a live process that isn't our conversion (reused pid) is left alone
        other = subprocess.Popen(["/bin/sleep", "30"])
        self.addCleanup(other.wait)
        self.addCleanup(other.kill)
        os.makedirs(lock)
        for name, value in (("pid", "999999"), ("child", str(other.pid)), ("tmp", tmp)):
            with open(os.path.join(lock, name), "w") as f:
                f.write(value)
        self.run_worker()
        self.assertIsNone(other.poll())

    def test_progress_of_a_long_conversion_is_fast(self):
        # the status regex scanned the whole progress file again for every match: minutes for a long movie
        lock = os.path.join(CACHE, "worker.lock")
        os.makedirs(lock, exist_ok=True)
        fake = subprocess.Popen(["/bin/bash", "-c", "exec -a worker.sh /bin/sleep 30"])
        self.addCleanup(fake.wait)
        self.addCleanup(fake.kill)
        try:
            with open(os.path.join(lock, "pid"), "w") as f:
                f.write(str(fake.pid))
            with open(os.path.join(lock, "state"), "w") as f:
                f.write("7200\nlong.mov\nffmpeg\n")
            with open(os.path.join(CACHE, "progress.txt"), "w") as f:
                for i in range(40000):
                    f.write(f"frame={i}\nfps=30\nout_time_us={i * 90000}\nout_time_ms={i * 90000}\nspeed=1x\nprogress=continue\n")
            start = time.time()
            data = sf("vid", "", [], MT_TEST_FFMPEG="none")
            self.assertLess(time.time() - start, 3)
            self.assertEqual(data["items"][0]["title"], "Converting long.mov · 50%")
        finally:
            shutil.rmtree(lock, ignore_errors=True)
            os.remove(os.path.join(CACHE, "progress.txt"))


class AuditEncoderTests(Base):
    def test_missing_encoders(self):
        # a minimal ffmpeg build (no libvpx, x264 or LAME) failed every such conversion with "Unknown encoder"
        mov, wav = self.p("clip.mov"), write_wav(self.p("t.wav"))
        open(mov, "w").close()
        env = dict(MT_TEST_FFMPEG="/opt/fake/ffmpeg", MT_TEST_FFMPEG_ENCODERS="h264_videotoolbox,hevc_videotoolbox,aac,flac,pcm_s16le,pcm_s16be")
        it = items("vid", "", [mov], **env)
        args = [i.get("arg") for i in it]
        for a in ("mp4", "hevc", "m4a", "flac"):
            self.assertIn(a, args)
        for a in ("webm", "compress:28", "mp3"):
            self.assertNotIn(a, args)
        titles = [i["title"] for i in it]
        self.assertIn("Convert to WebM (VP9): this ffmpeg can't encode it", titles)
        self.assertIn("libvpx-vp9 or libopus", next(i for i in it if "WebM" in i["title"])["subtitle"])
        self.assertNotIn("Install ffmpeg with Homebrew", titles)
        # the macOS tools step in where they can: a movie to M4A via ffmpeg, MP3 isn't possible
        msg, = [QueueTests.job(self, "mp3", [mov], **env)[0]]
        self.assertEqual(msg, "Nothing queued: clip.mov: this ffmpeg has no libmp3lame encoder")
        # trimming an MP3 needs LAME too: avconvert does it instead (as M4A)
        mp3 = self.p("song.mp3")
        open(mp3, "w").close()
        _, jobs = QueueTests.job(self, "trim:0_01-0_02", [mp3], **env)
        self.assertEqual((jobs[0][9], jobs[0][5]), ("avconvert", self.p("song-edited.m4a")))

    def test_encoder_list_is_read_and_cached(self):
        fake = os.path.join(self.dir, "ffmpeg")
        count = os.path.join(self.dir, "runs")
        with open(fake, "w") as f:
            f.write(f"""#!/bin/sh
echo run >> '{count}'
cat <<'EOF'
Encoders:
 V..... = Video
 ------
 V....D h264_videotoolbox    VideoToolbox H.264 Encoder
 V....D hevc_videotoolbox    VideoToolbox H.265 Encoder
 A....D aac                  AAC (Advanced Audio Coding)
EOF
""")
        os.chmod(fake, 0o755)
        mov = self.p("clip.mov")
        open(mov, "w").close()
        for _ in range(2):
            args = [i.get("arg") for i in items("vid", "", [mov], ffmpeg_path=fake)]
            self.assertIn("mp4", args)
            self.assertNotIn("webm", args)
        with open(count) as f:
            self.assertEqual(len(f.read().split()), 1)  # asked once, then cached
        os.remove(os.path.join(CACHE, "ffmpeg-encoders.txt"))


class AuditMiscTests(Base):
    def test_finder_selection_in_one_apple_event(self):
        a, b = make(self.p("a b.jpg")), make(self.p("ü.png"), "public.png")
        quote = lambda x: '"' + x.replace("\\", "\\\\").replace('"', '\\"') + '"'
        script = "return {" + ", ".join(f"POSIX file {quote(x)} as alias" for x in (a, b)) + "}"
        e = base_env(MT_TEST_SELECTION_SCRIPT=script)
        e.pop("MT_TEST_SELECTION", None)
        out = subprocess.run(["osascript", "-l", "JavaScript", "./media.js", "img", ""], cwd=SRC, env=e, capture_output=True, text=True)
        data = json.loads(out.stdout)
        nfc = lambda x: unicodedata.normalize("NFC", x)  # file URLs come back decomposed; both open the same file
        self.assertEqual([nfc(x) for x in json.loads(data["variables"]["mt_sel"])], [nfc(os.path.realpath(a)), nfc(os.path.realpath(b))])

    # ---- regressions from the final review
    def test_prototype_keys_in_queries(self):
        jpg = make(self.p("a.jpg"))
        for q in ("constructor", "__proto__", "to constructor", "convert to __proto__", "hasownproperty"):
            it = items("img", q, [jpg])
            self.assertNotEqual(it[0]["title"], "Media Toolkit error", (q, it))
        self.assertIn("unknown format", act("convert:constructor", [jpg]))
        odd = self.p("x.constructor")
        open(odd, "w").close()
        self.assertEqual(items("img", "", [odd])[0]["title"], "Select images in Finder first")

    def test_control_and_bidi_characters_in_titles(self):
        name = "a\u202egpj.exe\nb\tc.jpg"
        jpg = make(self.p(name))
        for it in items("img", "", [jpg]):
            for field in (it["title"], it["subtitle"]):
                self.assertFalse(any(unicodedata.category(ch) in ("Cc",) or ch in "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069" for ch in field), field)
            self.assertEqual(json.loads(it["variables"]["mt_files"]), [jpg])  # the real path is untouched
        mov = self.p("m\u202eov.mov")
        open(mov, "w").close()
        self.assertEqual(items("vid", "trim 1-2", [mov], MT_TEST_FFMPEG="/f/ffmpeg")[0]["title"], "Trim mov.mov from 0:01 to 0:02")

    def test_test_mode_never_touches_real_state(self):
        # no selection override: Finder is not asked
        self.assertEqual(items("img", "")[0]["title"], "Select images in Finder first")
        self.assertEqual(items("vid", "", MT_TEST_FFMPEG="none")[-1]["title"], "Select video or audio files in Finder first")
        self.assertEqual(items("all", "")[0]["title"], "Select images, videos or audio in Finder first")
        # no clipboard override: pbcopy is not run (the message still comes back)
        e = base_env(mt_op="copy", mt_files="[]")
        out = subprocess.run(["./action.sh", "brew install ffmpeg"], cwd=SRC, env=e, capture_output=True, text=True).stdout
        self.assertIn("Copied", out)
        # read-only folder without a fallback override: the result goes to the cache, not ~/Downloads
        ro = os.path.join(self.dir, "ro")
        os.makedirs(ro)
        src = make(os.path.join(ro, "a.jpg"))
        os.chmod(ro, 0o555)
        e = base_env(mt_op="resize:pct:50", mt_files=json.dumps([src]))
        e.pop("MT_TEST_FALLBACK_DIR", None)
        subprocess.run(["./action.sh", "resize:pct:50"], cwd=SRC, env=e, capture_output=True, text=True)
        self.assertTrue(os.path.exists(os.path.join(CACHE, "a-edited.jpg")))
        os.remove(os.path.join(CACHE, "a-edited.jpg"))

    def test_log_action_reveals_in_test_file(self):
        log = os.path.join(CACHE, "conversions.log")
        open(log, "a").close()
        act("log", [])
        with open(os.path.join(CACHE, "reveal.txt")) as f:
            self.assertEqual(f.read().strip(), log)

    def test_progress_without_selection_shows_only_the_progress_row(self):
        lock = os.path.join(CACHE, "worker.lock")
        os.makedirs(lock, exist_ok=True)
        fake = subprocess.Popen(["/bin/bash", "-c", "exec -a worker.sh /bin/sleep 30"])
        self.addCleanup(fake.wait)
        self.addCleanup(fake.kill)
        try:
            with open(os.path.join(lock, "pid"), "w") as f:
                f.write(str(fake.pid))
            with open(os.path.join(lock, "state"), "w") as f:
                f.write("0\nclip.mov\navconvert\n")
            titles = [i["title"] for i in items("vid", "", [], MT_TEST_FFMPEG="none")]
            self.assertEqual(titles, ["Converting clip.mov…", "Select video or audio files in Finder first"])
        finally:
            shutil.rmtree(lock, ignore_errors=True)

    def test_errors_log_is_trimmed(self):
        log = os.path.join(CACHE, "errors.log")
        with open(log, "w") as f:
            f.write("x" * 1500000)
        act("copy", [], MT_TEST_CLIPBOARD_FILE=os.path.join(self.dir, "clip"))
        self.assertLessEqual(os.path.getsize(log), 200000)
        os.remove(log)

    def test_avconvert_scale_subtitle(self):
        mov = self.p("clip.mov")
        open(mov, "w").close()
        it = items("vid", "480", [mov], MT_TEST_FFMPEG="none")
        self.assertIn("Fits within 640x480", it[0]["subtitle"])


# ---------------------------------------------------------------- round 4: Alfred's runtime, v1.1 features

OLD = time.mktime((2019, 5, 6, 12, 0, 0, 0, 0, -1))


def birthtime(path):
    return os.stat(path).st_birthtime


class Round4Tests(Base):
    job = QueueTests.job

    def fake_ffmpeg(self, body):
        path = os.path.join(self.dir, "ffmpeg")
        with open(path, "w") as f:
            f.write("#!/bin/bash\n" + body + "\n")
        os.chmod(path, 0o755)
        return path

    def test_failure_reason_is_short_valid_utf8(self):
        # Alfred runs scripts in the C locale, where cut counts bytes: a reason cut inside "ü" made osascript
        # receive undefined, and no notification arrived. The source's folder path is dropped too.
        d = os.path.join(self.d, "Ordner " + "ü" * 90)
        os.makedirs(d)
        wav = write_wav(os.path.join(d, "tön.wav"), seconds=0.2)
        ff = self.fake_ffmpeg('for a in "$@"; do case "$a" in file:*.wav) s="${a#file:}";; esac; done\n'
                              'echo "x${s}: Invalid data found when processing input" >&2; exit 1')
        env = dict(PATH="/usr/bin:/bin:/usr/sbin:/sbin", LANG="", LC_ALL="", LC_CTYPE="")
        act("mp3", [wav], MT_TEST_FFMPEG=ff, MT_TEST_FFMPEG_ENCODERS="libmp3lame", MT_TEST_WORKER_FOREGROUND=1, **env)
        with open(os.path.join(CACHE, "notify.txt"), "rb") as f:
            n = f.read().decode("utf-8").strip()  # raises when not valid UTF-8
        self.assertEqual(n, "Failed: tön.wav: xtön.wav: Invalid data found when processing input")
        for k in range(1, 4):  # every cut position inside a two-byte character
            self.assertEqual(subprocess.run(["bash", "-c", 'printf "%s" "$1" | cut -c1-' + str(2 * k + 1) + ' | iconv -c -f UTF-8 -t UTF-8 2>/dev/null', "_", "ü" * 9],
                                            capture_output=True, env={"PATH": "/usr/bin:/bin"}).stdout.decode().rstrip("\n"), "ü" * k)

    def test_checkbox_values(self):
        jpg = make(self.p("a.jpg"), w=100, h=80)
        act("resize:pct:50", [jpg], replace_originals="true")
        self.assertEqual(self.listdir(), ["a.jpg"])
        self.assertEqual(sips(jpg)[:2], (50, 40))
        act("resize:pct:50", [jpg], replace_originals="0")
        self.assertEqual(self.listdir(), ["a-edited.jpg", "a.jpg"])

    def test_fallback_subtitle_has_no_leading_separator(self):
        wav = write_wav(self.p("t.wav"), seconds=0.2)
        subs = [i["subtitle"] for i in items("vid", "m4a", [wav], MT_TEST_FFMPEG="none")]
        self.assertEqual(subs[0], "via afconvert · t.wav")

    def test_keep_dates_images(self):
        jpg = make(self.p("a.jpg"), w=100, h=80)
        os.utime(jpg, (OLD, OLD))
        act("resize:pct:50", [jpg])
        self.assertGreater(os.path.getmtime(self.p("a-edited.jpg")), OLD + 86400)  # off by default
        act("convert:png", [jpg], keep_dates="1")
        self.assertEqual(os.path.getmtime(self.p("a.png")), OLD)
        self.assertEqual(birthtime(self.p("a.png")), birthtime(jpg))
        act("rotate:90", [jpg], keep_dates="1", replace_originals="1")
        self.assertEqual(os.path.getmtime(jpg), OLD)
        self.assertEqual(sips(jpg)[:2], (80, 100))

    def test_copy_results_images(self):
        jpg = make(self.p("a b.jpg"), w=100, h=80)
        clip = os.path.join(self.dir, "clip.txt")
        it = items("img", "50%", [jpg])[0]
        self.assertEqual(it["mods"]["alt"]["variables"]["mt_copy"], "1")
        self.assertEqual(it["mods"]["alt"]["arg"], it["arg"])
        self.assertEqual(it["variables"]["mt_copy"], "0")
        msg = act("resize:pct:50", [jpg], mt_copy="1", MT_TEST_CLIPBOARD_FILE=clip)
        self.assertTrue(msg.endswith(" · copied to the clipboard"), msg)
        with open(clip) as f:
            self.assertEqual(f.read(), self.p("a b-edited.jpg"))
        os.remove(clip)
        act("resize:pct:50", [jpg], MT_TEST_CLIPBOARD_FILE=clip)
        self.assertFalse(os.path.exists(clip))  # only with ⌥↩

    def test_worker_copy_and_dates(self):
        wavs = [write_wav(self.p(f"t{i}.wav"), seconds=0.3) for i in range(2)]
        for w in wavs:
            os.utime(w, (OLD, OLD))
        clip = os.path.join(self.dir, "clip.txt")
        _, jobs = self.job("flac", wavs, MT_TEST_FFMPEG="none", mt_copy="1")
        self.assertEqual([j[8] for j in jobs], ["2", "2"])
        act("flac", wavs, MT_TEST_FFMPEG="none", MT_TEST_WORKER_FOREGROUND=1, mt_copy="1", keep_dates="1", MT_TEST_CLIPBOARD_FILE=clip)
        self.assertEqual(notifications()[-1], "Converted 2 files · copied to the clipboard")
        with open(clip) as f:
            self.assertEqual(f.read().split("\n")[:2], [self.p("t0.flac"), self.p("t1.flac")])
        self.assertEqual(os.path.getmtime(self.p("t0.flac")), OLD)
        # a v1.0.0 job (reveal field 0/1) still runs
        act("aiff", wavs[:1], MT_TEST_FFMPEG="none", MT_TEST_WORKER_FOREGROUND=1, mt_reveal="1")
        with open(os.path.join(CACHE, "reveal.txt")) as f:
            self.assertEqual(f.read().strip(), self.p("t0.aiff"))
        self.assertGreater(os.path.getmtime(self.p("t0.aiff")), OLD + 86400)

    def test_gif_of_a_range(self):
        mov = self.p("v ü.mov")
        open(mov, "w").close()
        ff = dict(MT_TEST_FFMPEG="/opt/fake/ffmpeg")
        it = items("vid", "gif 0:10-0:15", [mov], **ff)
        self.assertEqual([i["arg"] for i in it], ["gif:0_10-0_15"])
        self.assertIn("from 0:10 to 0:15", it[0]["title"])
        self.assertEqual(items("vid", "gif 1:00-", [mov], **ff)[0]["arg"], "gif:1_00-")
        self.assertEqual(items("vid", "gif 5-2", [mov], **ff)[0]["title"], "Invalid range")
        self.assertEqual(items("vid", "gif 0:10-0:15", [mov], MT_TEST_FFMPEG="none")[0]["title"], "Install ffmpeg with Homebrew")
        self.assertIn("gif", [i["arg"] for i in items("vid", "gif", [mov], **ff)])
        _, jobs = self.job("gif:0_10-0_15", [mov], **ff)
        cmd = jobs[0][10:]
        k = cmd.index("::then::")
        for part in (cmd[:k], cmd[k + 1:]):
            self.assertEqual(part[part.index("-ss") + 1], "10")
            self.assertEqual(part[part.index("-t") + 1], "5")
            self.assertLess(part.index("-ss"), part.index("-i"))
        self.assertTrue(jobs[0][5].endswith("v ü.gif"))
        msg, jobs = self.job("gif:0_20-0_10", [mov], **ff)
        self.assertEqual((msg, jobs), ("Invalid gif range", []))

    def test_action_output_is_never_blank(self):
        # The action feeds a Notification set to "only show if populated": it prints either nothing at all or a
        # real message (each operation's result is the notification), never a bare newline
        def raw(op, files, **env):
            e = base_env(mt_op=op, mt_files=json.dumps(files), **env)
            return subprocess.run(["./action.sh", op], cwd=SRC, env=e, capture_output=True, text=True, timeout=120).stdout
        log = os.path.join(CACHE, "conversions.log")
        open(log, "a").close()
        self.assertEqual(raw("log", []), "")
        jpg = make(self.p("a.jpg"), w=60, h=40)
        wav = write_wav(self.p("t.wav"), seconds=0.2)
        for op, files, env in (("resize:pct:50", [jpg], {}), ("flac", [wav], dict(MT_TEST_FFMPEG="none", MT_TEST_WORKER_FOREGROUND="1")),
                               ("cancel", [], {}), ("copy", [], dict(MT_TEST_CLIPBOARD_FILE=os.path.join(self.dir, "c.txt"))),
                               ("resize:pct:50", [], {}), ("bogus", [], {})):
            out = raw(op, files, **env)
            self.assertTrue(out.strip(), (op, out))

    def test_minimal_alfred_environment(self):
        # Alfred's PATH has no Homebrew; ffmpeg is still found in a Homebrew-style folder (symlink into a Cellar)
        # through ffmpeg_path, and the Script Filter output is the same without LANG/LC_*
        cellar = os.path.join(self.dir, "brew", "Cellar", "ffmpeg", "7.1", "bin")
        os.makedirs(cellar)
        real = os.path.join(cellar, "ffmpeg")
        with open(real, "w") as f:
            f.write("#!/bin/bash\nprintf ' A....D libmp3lame x\\n'\n")
        os.chmod(real, 0o755)
        os.makedirs(os.path.join(self.dir, "brew", "bin"))
        link = os.path.join(self.dir, "brew", "bin", "ffmpeg")
        os.symlink("../Cellar/ffmpeg/7.1/bin/ffmpeg", link)
        wav = write_wav(self.p("tön.wav"), seconds=0.2)
        e = {"HOME": os.environ["HOME"], "USER": os.environ.get("USER", ""), "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
             "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "MT_TEST": "1", "alfred_workflow_cache": os.path.join(self.dir, "fresh cache", "x"),
             "alfred_workflow_data": os.path.join(self.dir, "fresh data"), "alfred_version": "5.6", "alfred_debug": "1",
             "MT_TEST_SELECTION": json.dumps([wav])}
        for d in (os.path.dirname(link), cellar):
            out = subprocess.run(["osascript", "-l", "JavaScript", "./media.js", "vid", "mp3"], cwd=SRC, env=dict(e, ffmpeg_path=" " + d + " "),
                                 capture_output=True, text=True)
            it = json.loads(out.stdout)["items"]
            self.assertEqual([i["arg"] for i in it], ["mp3"], it)
        out = subprocess.run(["osascript", "-l", "JavaScript", "./media.js", "vid", "mp3"], cwd=SRC, env=dict(e, ffmpeg_path=link),
                             capture_output=True, text=True)
        self.assertEqual(json.loads(out.stdout)["items"][0]["arg"], "mp3")
        self.assertTrue(os.path.exists(os.path.join(e["alfred_workflow_cache"], "ffmpeg-encoders.txt")))  # fresh cache folder made


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
