"""The brand assets are consistent (tools/make-brand.py --check) and the checker itself catches what it promises.

Standard library only: no Playwright, no fonts. The PNGs the negative tests need are written here by hand.
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tools", "make-brand.py")
TEAL = (0x0F, 0x76, 0x6E, 255)
WHITE = (255, 255, 255, 255)
CLEAR = (0, 0, 0, 0)


def png(width, height, pixel, ctype=6, filt=0):
    """A tiny PNG encoder. `pixel(x, y)` gives an (r, g, b, a) tuple; `filt` is the PNG filter used on every row."""
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[ctype]
    stride = width * channels
    raw, prev = bytearray(), bytearray(stride)
    for y in range(height):
        cur = bytearray()
        for x in range(width):
            r, g, b, a = pixel(x, y)
            cur += bytes({0: (r,), 2: (r, g, b), 4: (r, a), 6: (r, g, b, a)}[ctype])
        out = bytearray(cur) if filt == 0 else bytearray()
        for i in range(stride if filt else 0):
            a = cur[i - channels] if i >= channels else 0
            b = prev[i]
            c = prev[i - channels] if i >= channels else 0
            if filt == 1:
                pred = a
            elif filt == 2:
                pred = b
            elif filt == 3:
                pred = (a + b) >> 1
            else:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
            out.append((cur[i] - pred) & 255)
        raw += bytes([filt]) + out
        prev = cur

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, ctype, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b""))


def tile(size, content_radius=None, corners=TEAL):
    """A teal square with an optional white disc of the given radius in the middle."""
    def pixel(x, y):
        if content_radius is not None and ((x + .5 - size / 2.0) ** 2 + (y + .5 - size / 2.0) ** 2) ** .5 <= content_radius:
            return WHITE
        return corners
    return pixel


def load(tool):
    loader = importlib.machinery.SourceFileLoader("make_brand_under_test", tool)
    spec = importlib.util.spec_from_loader("make_brand_under_test", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class CommittedAssets(unittest.TestCase):
    def test_the_committed_assets_pass_the_check(self):
        proc = subprocess.run([sys.executable, TOOL, "--check"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              universal_newlines=True)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("consistent", proc.stdout)

    def test_the_manifest_matches_what_the_page_links(self):
        with open(os.path.join(ROOT, "web", "manifest.json"), encoding="utf-8") as fh:
            man = json.load(fh)
        for icon in man["icons"]:
            self.assertTrue(os.path.isfile(os.path.join(ROOT, "web", icon["src"][len("/pb/"):])), icon["src"])
        self.assertEqual([i["purpose"] for i in man["icons"] if i["sizes"] != "any"], ["any", "any", "maskable"])


class Decoder(unittest.TestCase):
    """read_png has to cope with every filter and colour type a browser or an optimiser may write."""

    def setUp(self):
        self.mod = load(TOOL)
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def pixel_fn(self, x, y):
        v = (x * 37 + y * 91) & 255
        return (v, (v * 3) & 255, (v * 7) & 255, (255 - v) if (x + y) % 3 else 255)

    def decode(self, data):
        path = os.path.join(self.tmp, "t.png")
        with open(path, "wb") as fh:
            fh.write(data)
        return self.mod.read_png(path)

    def test_every_filter(self):
        for filt in range(5):
            w, h, pixel = self.decode(png(23, 17, self.pixel_fn, filt=filt))
            self.assertEqual((w, h), (23, 17))
            for y in range(h):
                for x in range(w):
                    self.assertEqual(pixel(x, y), self.pixel_fn(x, y), "filter %d at %d,%d" % (filt, x, y))

    def test_rgb_grey_and_grey_alpha(self):
        w, h, pixel = self.decode(png(5, 4, self.pixel_fn, ctype=2, filt=4))
        self.assertEqual(pixel(3, 2)[:3], self.pixel_fn(3, 2)[:3])
        self.assertEqual(pixel(3, 2)[3], 255)
        w, h, pixel = self.decode(png(5, 4, self.pixel_fn, ctype=0, filt=1))
        self.assertEqual(pixel(3, 2), (self.pixel_fn(3, 2)[0],) * 3 + (255,))
        w, h, pixel = self.decode(png(5, 4, self.pixel_fn, ctype=4, filt=2))
        self.assertEqual(pixel(3, 2)[3], self.pixel_fn(3, 2)[3])

    def test_garbage_is_an_error_not_a_crash(self):
        for data in (b"", b"not a png at all", b"\x89PNG\r\n\x1a\n", png(4, 4, self.pixel_fn)[:40]):
            with self.assertRaises((ValueError, zlib.error, struct.error)):
                self.decode(data)


class Checker(unittest.TestCase):
    """Break a copy of the assets in one way at a time and see that the check says so."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        os.makedirs(os.path.join(self.tmp, "tools"))
        shutil.copy(TOOL, os.path.join(self.tmp, "tools", "make-brand.py"))
        os.makedirs(os.path.join(self.tmp, "web"))
        for name in os.listdir(os.path.join(ROOT, "web")):
            if name.endswith((".png", ".svg", ".json")):
                shutil.copy(os.path.join(ROOT, "web", name), os.path.join(self.tmp, "web", name))
        shutil.copytree(os.path.join(ROOT, "docs", "img"), os.path.join(self.tmp, "docs", "img"))
        # Loaded from the copy, so its ROOT, WEB and IMG are the copy: nothing here can touch the real assets.
        self.mod = load(os.path.join(self.tmp, "tools", "make-brand.py"))

    def path(self, rel):
        return os.path.join(self.tmp, *rel.split("/"))

    def put(self, rel, data, mode="wb"):
        with open(self.path(rel), mode) as fh:
            fh.write(data)

    def png_problems(self, name, size, transparent):
        return self.mod.check_png(self.path("web/" + name), size, transparent)

    def edit(self, rel, old, new):
        with open(self.path(rel), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn(old, text)
        with open(self.path(rel), "w", encoding="utf-8") as fh:
            fh.write(text.replace(old, new))

    def assertProblem(self, found, fragment):
        self.assertTrue(any(fragment in line for line in found), "%r not in %r" % (fragment, found))

    def test_a_clean_copy_passes(self):
        self.assertEqual(self.mod.check(), [])

    def test_a_missing_file(self):
        os.remove(self.path("web/apple-touch-icon.png"))
        self.assertProblem(self.mod.check(), "apple-touch-icon.png is missing")

    def test_a_png_of_the_wrong_size(self):
        shutil.copy(self.path("web/icon-192.png"), self.path("web/apple-touch-icon.png"))
        self.assertProblem(self.png_problems("apple-touch-icon.png", (180, 180), False), "is 192x192, expected 180x180")

    def test_apple_touch_icon_must_be_opaque_and_teal(self):
        self.put("web/apple-touch-icon.png", png(180, 180, lambda x, y: CLEAR if x < 20 and y < 20 else TEAL))
        self.assertProblem(self.png_problems("apple-touch-icon.png", (180, 180), False), "fully opaque")
        self.put("web/apple-touch-icon.png", png(180, 180, tile(180, corners=(200, 30, 30, 255))))
        self.assertProblem(self.png_problems("apple-touch-icon.png", (180, 180), False), "brand teal")

    def test_any_purpose_icons_keep_transparent_corners(self):
        self.put("web/icon-192.png", png(192, 192, tile(192, 40)))
        self.assertProblem(self.png_problems("icon-192.png", (192, 192), True), "corners must be transparent")

    def test_maskable_content_must_stay_inside_the_safe_circle(self):
        self.put("web/icon-maskable-512.png", png(512, 512, tile(512, 0.4 * 512 - 3)))
        self.assertEqual(self.png_problems("icon-maskable-512.png", (512, 512), False), [])
        self.put("web/icon-maskable-512.png", png(512, 512, tile(512, 0.4 * 512 + 12)))
        self.assertProblem(self.png_problems("icon-maskable-512.png", (512, 512), False), "safe zone")

    def test_svgs_must_not_need_fonts_or_the_network(self):
        rel = "docs/img/wordmark-en.svg"
        original = os.path.join(ROOT, "docs", "img", "wordmark-en.svg")
        for bad, fragment in (
            ('<text x="0" y="9">Sinko</text></svg>', "draw outlines"),
            ('<style>text{font-family:Arial}</style></svg>', "no fonts"),
            ('<image href="https://example.com/a.png"/></svg>', "no external links"),
            ('<path style="fill:url(https://example.com/a.svg#x)"/></svg>', "no external references"),
            ('<script>1</script></svg>', "no scripts"),
        ):
            shutil.copy(original, self.path(rel))
            self.edit(rel, "</svg>", bad)
            self.assertProblem(self.mod.check_svg(self.path(rel), "Sinko"), fragment)

    def test_data_uris_are_allowed_in_svgs(self):
        rel = "docs/img/wordmark-en.svg"
        self.edit(rel, "</svg>", '<image href="data:image/png;base64,AAAA"/></svg>')
        self.assertEqual(self.mod.check_svg(self.path(rel), "Sinko"), [])

    def test_the_svg_title_follows_the_product_name(self):
        self.edit("docs/img/wordmark-en.svg", '<title id="t">Sinko</title>', '<title id="t">Nay</title>')
        self.assertProblem(self.mod.check_svg(self.path("docs/img/wordmark-en.svg"), "Sinko"), "<title> is ['Nay']")
        self.edit("web/icon.svg", ' role="img"', "")
        self.assertProblem(self.mod.check_svg(self.path("web/icon.svg"), "Sinko"), 'needs role="img"')

    def test_manifest_rules(self):
        path = self.path("web/manifest.json")
        with open(path, encoding="utf-8") as fh:
            good = json.load(fh)

        def problems(mutate):
            man = json.loads(json.dumps(good))
            mutate(man)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(man, fh)
            return self.mod.check_manifest(path)

        self.assertProblem(problems(lambda m: m.update(short_name="Family")), "short_name is 'Family'")
        self.assertProblem(problems(lambda m: m["icons"][-1].update(purpose="any maskable")), "never both in one entry")
        self.assertProblem(problems(lambda m: m["icons"][1].update(src="/icon-192.png")), "must start with /pb/")
        self.assertProblem(problems(lambda m: m["icons"][1].update(sizes="256x256")), "do not match the file")
        self.assertProblem(problems(lambda m: m.pop("description")), "description is missing")
        self.assertProblem(problems(lambda m: m.update(icons=[i for i in m["icons"] if i["purpose"] != "maskable"])),
                           'one "any" and one "maskable"')
        self.assertProblem(problems(lambda m: m["icons"][1].update(src="/pb/nope.png")), "does not exist")
        self.put("web/manifest.json", "{not json", mode="w")
        self.assertProblem(self.mod.check_manifest(path), "manifest.json")


if __name__ == "__main__":
    unittest.main()
