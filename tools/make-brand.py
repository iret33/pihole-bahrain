#!/usr/bin/env python3
"""Sinko's brand assets: regenerate the PNGs from the SVGs, redraw the wordmarks, or check everything.

    python3 tools/make-brand.py              redraw web/icon-*.png, web/apple-touch-icon.png and
                                             docs/img/social-preview.png from the SVGs
                                             (needs Playwright + Chromium: pip install playwright &&
                                             playwright install chromium; works offline)
    python3 tools/make-brand.py --wordmarks  redraw docs/img/wordmark-en.svg and wordmark-ar.svg as outlines of
                                             IBM Plex Sans Arabic (needs: pip install fonttools brotli uharfbuzz)
    python3 tools/make-brand.py --check      verify the committed assets, standard library only (what CI runs)

web/icon.svg and docs/img/logo.svg (the mark) are drawn by hand and are the source of truth for every PNG.
The product name lives in the constants below, in the <title> of each SVG and in web/manifest.json; --check fails
when they disagree, so a rename is: change the constants, run --wordmarks and the default mode, fix the mark's
<title>s and the manifest, run --check.
Development tool only: it is not part of the release archive and nothing on a box runs it.
"""
import argparse
import base64
import itertools
import json
import math
import os
import re
import string
import struct
import sys
import tempfile
import xml.etree.ElementTree as ET
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")
IMG = os.path.join(ROOT, "docs", "img")

NAME_EN = "Sinko"
NAME_AR = "سينكو"
TAGLINE_EN = "Calm internet for the family"
TAGLINE_AR = "إنترنت هادئ للعائلة"

TEAL = "#0F766E"        # the page's --lagoon
TEAL_ON_DARK = "#2BA597"  # the page's --lagoon in dark mode: readable on a dark background
PEARL = "#F1F4F2"       # the page's --pearl (manifest background_color)

# Wordmark outlines: Plex Sans Arabic SemiBold, corners softened by a round stroke of the same colour.
# Plex Arabic is drawn smaller than its Latin at the same size, so the Arabic is enlarged.
WORDMARK_WEIGHT = 600
WORDMARK_STROKE = 28
WORDMARK_PAD = 48
ARABIC_SCALE = 1.12

PNGS = {   # file -> (size w, h, whether the corners are transparent)
    os.path.join(WEB, "icon-192.png"): (192, 192, True),
    os.path.join(WEB, "icon-512.png"): (512, 512, True),
    os.path.join(WEB, "icon-maskable-512.png"): (512, 512, False),
    os.path.join(WEB, "apple-touch-icon.png"): (180, 180, False),
    os.path.join(IMG, "social-preview.png"): (1280, 640, False),
}
SVGS = [
    os.path.join(WEB, "icon.svg"),
    os.path.join(IMG, "logo.svg"),
    os.path.join(IMG, "wordmark-en.svg"),
    os.path.join(IMG, "wordmark-ar.svg"),
]
MASKABLE_SAFE = 0.4   # every visible pixel of a maskable icon must lie inside a circle of this radius * size


# ---------------------------------------------------------------------------------------------------------------
# --check (standard library only; runs on Python 3.9+)
# ---------------------------------------------------------------------------------------------------------------

def read_png(path):
    """Decode an 8-bit, non-interlaced PNG into (width, height, pixel(x, y) -> (r, g, b, a))."""
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, plte, trns, ihdr = 8, [], b"", b"", None
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            plte = body
        elif kind == b"tRNS":
            trns = body
        elif kind == b"IDAT":
            idat.append(body)
        pos += 12 + length
    if ihdr is None or not idat:
        raise ValueError("truncated PNG")
    width, height, depth, ctype, _comp, _flt, interlace = ihdr
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(ctype)
    if depth != 8 or interlace or channels is None:
        raise ValueError("unsupported PNG layout (need 8-bit, not interlaced)")
    raw = zlib.decompress(b"".join(idat))
    stride = width * channels
    if len(raw) != height * (stride + 1):
        raise ValueError("PNG data has the wrong length")
    rows, prev = [], bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        ft, cur = raw[start], bytearray(raw[start + 1:start + 1 + stride])
        if ft == 1:
            for i in range(channels, stride):
                cur[i] = (cur[i] + cur[i - channels]) & 255
        elif ft == 2:
            for i in range(stride):
                cur[i] = (cur[i] + prev[i]) & 255
        elif ft == 3:
            for i in range(stride):
                left = cur[i - channels] if i >= channels else 0
                cur[i] = (cur[i] + ((left + prev[i]) >> 1)) & 255
        elif ft == 4:
            for i in range(stride):
                a = cur[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                cur[i] = (cur[i] + pred) & 255
        elif ft != 0:
            raise ValueError("unknown PNG filter %d" % ft)
        rows.append(cur)
        prev = cur

    def pixel(x, y):
        row = rows[y]
        if ctype == 6:
            return tuple(row[x * 4:x * 4 + 4])
        if ctype == 2:
            return (row[x * 3], row[x * 3 + 1], row[x * 3 + 2], 255)
        if ctype == 0:
            return (row[x], row[x], row[x], 255)
        if ctype == 4:
            return (row[x * 2], row[x * 2], row[x * 2], row[x * 2 + 1])
        i = row[x]
        return (plte[i * 3], plte[i * 3 + 1], plte[i * 3 + 2], trns[i] if i < len(trns) else 255)

    return width, height, pixel


def check_png(path, size, transparent_corners):
    """Return a list of problems with one PNG."""
    name = os.path.relpath(path, ROOT)
    try:
        width, height, pixel = read_png(path)
    except (OSError, ValueError, zlib.error, struct.error) as exc:
        return ["%s: %s" % (name, exc)]
    bad = []
    if (width, height) != size:
        return ["%s is %dx%d, expected %dx%d" % (name, width, height, size[0], size[1])]
    corners = [pixel(0, 0), pixel(width - 1, 0), pixel(0, height - 1), pixel(width - 1, height - 1)]
    if transparent_corners:
        if any(c[3] > 8 for c in corners):
            bad.append("%s: its corners must be transparent (the rounded tile)" % name)
        if pixel(width // 2, height // 2)[3] != 255:
            bad.append("%s: its middle must be opaque" % name)
    elif any(c[3] != 255 for c in corners):
        # iOS paints transparent pixels black and a maskable icon is cut by the system, so both must be solid.
        bad.append("%s: it must be fully opaque, corner to corner" % name)
    if os.path.basename(path) in ("icon-maskable-512.png", "apple-touch-icon.png"):
        want = tuple(int(TEAL[i:i + 2], 16) for i in (1, 3, 5))
        for c in corners:
            if max(abs(c[i] - want[i]) for i in range(3)) > 2:
                bad.append("%s: corners must be the full-bleed brand teal %s" % (name, TEAL))
                break
    if os.path.basename(path) == "icon-maskable-512.png":
        limit, cx, cy = MASKABLE_SAFE * width + 0.75, width / 2.0, height / 2.0
        worst = 0.0
        for y in range(height):
            # Only the pixels outside the circle can break the rule, so skip the ones inside it.
            room = limit * limit - (y + 0.5 - cy) ** 2
            if room > 0:
                half = math.sqrt(room)
                outside = itertools.chain(range(0, int(math.ceil(cx - half - 0.5))),
                                          range(int(math.floor(cx + half - 0.5)) + 1, width))
            else:
                outside = range(width)
            for x in outside:
                r, g, b, a = pixel(x, y)
                if a > 128 and min(r, g, b) > 200:   # white content; the background is teal
                    worst = max(worst, math.hypot(x + 0.5 - cx, y + 0.5 - cy))
        if worst > limit:
            bad.append("%s: white content reaches %.1f px from the centre, the safe zone ends at %.1f"
                       % (name, worst, MASKABLE_SAFE * width))
    return bad


def check_svg(path, title):
    """Return problems with one SVG: it must be self-contained and carry its accessible name."""
    name = os.path.relpath(path, ROOT)
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        root = ET.fromstring(text)
    except (OSError, ET.ParseError, UnicodeDecodeError) as exc:
        return ["%s: %s" % (name, exc)]
    bad = []
    if not root.tag.endswith("}svg") or "viewBox" not in root.attrib:
        bad.append("%s: not an <svg> with a viewBox" % name)
    for pattern, why in (
        (r"<text\b", "text needs a font; draw outlines instead"),
        (r"font-family|@font-face|@import", "no fonts, no imports"),
        (r"<script|<foreignObject|\son\w+\s*=", "no scripts"),
        (r"""(?:xlink:)?href\s*=\s*["'](?!data:|#)""", "no external links"),
        (r"""url\(\s*["']?(?!#|data:)""", "no external references"),
    ):
        if re.search(pattern, text):
            bad.append("%s: %s" % (name, why))
    if root.attrib.get("role") != "img":
        bad.append("%s: needs role=\"img\"" % name)
    titles = [el.text for el in root.iter() if el.tag.endswith("}title")]
    if titles != [title]:
        bad.append("%s: <title> is %r, expected %r" % (name, titles, title))
    return bad


def check_manifest(path):
    name = os.path.relpath(path, ROOT)
    try:
        with open(path, encoding="utf-8") as fh:
            man = json.load(fh)
    except (OSError, ValueError) as exc:
        return ["%s: %s" % (name, exc)]
    bad = []
    want = {"name": NAME_EN, "short_name": NAME_EN, "lang": "en", "start_url": "/", "scope": "/",
            "display": "standalone", "background_color": PEARL, "theme_color": TEAL}
    for key, value in want.items():
        if man.get(key) != value:
            bad.append("%s: %s is %r, expected %r" % (name, key, man.get(key), value))
    if not isinstance(man.get("description"), str) or not man["description"].strip():
        bad.append("%s: description is missing" % name)
    icons = man.get("icons")
    if not isinstance(icons, list) or not icons:
        return bad + ["%s: icons is missing" % name]
    seen = set()
    for icon in icons:
        src, sizes, kind, purpose = (icon.get(k) for k in ("src", "sizes", "type", "purpose"))
        if not isinstance(src, str) or not src.startswith("/pb/"):
            bad.append("%s: icon src %r must start with /pb/ (the page's folder)" % (name, src))
            continue
        local = os.path.join(WEB, src[len("/pb/"):])
        if not os.path.isfile(local):
            bad.append("%s: %s does not exist in web/" % (name, src))
            continue
        if purpose not in ("any", "maskable"):
            bad.append("%s: %s needs purpose \"any\" or \"maskable\", never both in one entry" % (name, src))
        seen.add(purpose)
        if src.endswith(".svg"):
            if sizes != "any" or kind != "image/svg+xml":
                bad.append("%s: %s must be sizes \"any\", type image/svg+xml" % (name, src))
        else:
            expected = PNGS.get(local)
            if expected is None or kind != "image/png" or sizes != "%dx%d" % expected[:2]:
                bad.append("%s: %s has sizes %r / type %r that do not match the file" % (name, src, sizes, kind))
            if (purpose == "maskable") != os.path.basename(local).startswith("icon-maskable"):
                bad.append("%s: %s has the wrong purpose %r" % (name, src, purpose))
    if seen != {"any", "maskable"}:
        bad.append("%s: needs at least one \"any\" and one \"maskable\" icon" % name)
    for need in (192, 512):
        if not any(i.get("sizes") == "%dx%d" % (need, need) and i.get("purpose") == "any" for i in icons):
            bad.append("%s: needs a %d px icon with purpose \"any\"" % (name, need))
    return bad


def check():
    """Verify the committed brand assets. Returns the list of problems (empty = fine)."""
    bad = []
    have_docs = os.path.isdir(IMG)
    if not have_docs:
        print("make-brand: docs/img is not here (a release tree?); only web/ is checked")
    for svg in SVGS:
        if not have_docs and svg.startswith(IMG):
            continue
        title = NAME_AR if svg.endswith("wordmark-ar.svg") else NAME_EN
        bad += check_svg(svg, title) if os.path.isfile(svg) else ["%s is missing" % os.path.relpath(svg, ROOT)]
    for png, (w, h, transparent) in sorted(PNGS.items()):
        if not have_docs and png.startswith(IMG):
            continue
        bad += check_png(png, (w, h), transparent) if os.path.isfile(png) else ["%s is missing" % os.path.relpath(png, ROOT)]
    manifest = os.path.join(WEB, "manifest.json")
    bad += check_manifest(manifest) if os.path.isfile(manifest) else ["web/manifest.json is missing"]
    return bad


# ---------------------------------------------------------------------------------------------------------------
# Regenerate the PNGs (Playwright, imported only here so --check and the box never need it)
# ---------------------------------------------------------------------------------------------------------------

def read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def svg_document(svg, width, height):
    """The mark's SVG sized to exact pixels on a transparent page."""
    sized = svg.replace("<svg ", '<svg width="%d" height="%d" ' % (width, height), 1)
    return ("<!doctype html><meta charset=utf-8><style>html,body{margin:0;background:transparent}"
            "svg{display:block}</style>" + sized)


def full_bleed(svg):
    """The mark with a square tile: iOS and Android cut the corners themselves."""
    out, n = re.subn(r'(<rect [^>]*?) rx="\d+"', r"\1", svg, count=1)
    if n != 1:
        raise SystemExit("make-brand: web/icon.svg has no <rect ... rx=...> tile to square off")
    return out


def wordmark_parts(path):
    """(viewBox width, height, path data) of a wordmark SVG."""
    text = read_text(path)
    box = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', text)
    d = re.search(r'\sd="([^"]+)"', text)
    if not box or not d:
        raise SystemExit("make-brand: %s is not a wordmark this tool wrote (run --wordmarks)" % path)
    return float(box.group(1)), float(box.group(2)), d.group(1)


def font_face(file, weight, ranges):
    with open(os.path.join(WEB, "fonts", file), "rb") as fh:
        blob = base64.b64encode(fh.read()).decode("ascii")
    return ('@font-face{font-family:"Plex Arabic";font-weight:%d;src:url(data:font/woff2;base64,%s) format("woff2");'
            'unicode-range:%s}' % (weight, blob, ranges))


ARABIC_RANGE = "U+0600-06FF,U+0750-077F,U+0870-088E,U+0890-0891,U+0897-08E1,U+08E3-08FF,U+200C-200E,U+FB50-FDFF,U+FE70-FEFC"
LATIN_RANGE = "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+2000-206F,U+20AC,U+2122,U+FEFF"

SOCIAL = string.Template("""<!doctype html><meta charset="utf-8">
<style>
$fonts
html,body{margin:0;width:1280px;height:640px;overflow:hidden}
body{position:relative;background:$teal;font-family:"Plex Arabic",sans-serif;color:#fff}
.ripples{position:absolute;inset:0}
.stack{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center}
.lockup{display:flex;align-items:flex-end;gap:56px}
.lockup svg{display:block}
.tag{margin:0;font-weight:600;color:rgba(255,255,255,.94)}
.en{margin-top:44px;font-size:46px;line-height:1.2;letter-spacing:.005em}
.ar{margin-top:10px;font-size:50px;line-height:1.4;direction:rtl}
</style>
<svg class="ripples" viewBox="0 0 1280 640" aria-hidden="true">$ripples</svg>
<div class="stack">
  <div class="lockup">
    <svg width="$mark_w" height="$mark_h" viewBox="$mark_box">$mark</svg>
    <svg width="$word_w" height="$word_h" viewBox="0 0 $word_vw $word_vh" style="margin-bottom:${lift}px"><path d="$word_d" fill="#fff" stroke="#fff" stroke-width="$stroke" stroke-linejoin="round"/></svg>
  </div>
  <p class="tag en" lang="en">$tag_en</p>
  <p class="tag ar" lang="ar">$tag_ar</p>
</div>""")


def social_html():
    """The social preview page. The mark and the wordmark come from the committed SVGs, the taglines from Plex."""
    icon = read_text(os.path.join(WEB, "icon.svg"))
    # The mark without its tile: on the flat teal background the glyph alone is the same picture (the door is drawn in
    # the tile's colour, so the background must stay flat).
    glyph = re.sub(r"<rect [^>]*/>", "", icon.split("</title>", 1)[1].rsplit("</svg>", 1)[0], count=1)
    mark_h = 200.0
    box = (130, 72, 252, 362)    # the glyph's extent in the mark's 512 grid, with a little air
    mark_w = mark_h * box[2] / box[3]
    vw, vh, d = wordmark_parts(os.path.join(IMG, "wordmark-en.svg"))
    scale = 150.0 / (vh - 2 * WORDMARK_PAD)   # px per outline unit: the letters (without the margin) are 150 px tall
    word_h = vh * scale
    # Sit the wordmark's baseline on the house's floor instead of centring the two: the margin under the outlines is
    # the pad plus the stroke's half plus the round letters' overshoot below the baseline (12 units in Plex).
    mark_air = (box[1] + box[3] - 427) * mark_h / box[3]         # the house's floor is at y=427 in the mark's grid
    word_air = (WORDMARK_PAD + WORDMARK_STROKE / 2.0 + 12) * scale
    ripples = "".join(
        '<circle cx="640" cy="%d" r="%d" fill="none" stroke="#fff" stroke-opacity="%.3f" stroke-width="2"/>'
        % (330, r, op) for r, op in ((420, .05), (560, .04), (700, .03), (840, .02)))
    return SOCIAL.substitute(
        fonts="".join(font_face(f, w, r) for f, w, r in (
            ("plex-arabic-latin-600.woff2", 600, LATIN_RANGE), ("plex-arabic-arabic-600.woff2", 600, ARABIC_RANGE))),
        teal=TEAL, ripples=ripples, mark=glyph, mark_box="%d %d %d %d" % box, mark_w="%.1f" % mark_w,
        mark_h="%.1f" % mark_h, word_w="%.1f" % (word_h * vw / vh), word_h="%.1f" % word_h, word_vw=vw, word_vh=vh,
        word_d=d, stroke=WORDMARK_STROKE, lift="%.1f" % (mark_air - word_air), tag_en=TAGLINE_EN, tag_ar=TAGLINE_AR)


def render():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("make-brand: Playwright is needed to draw the PNGs: pip install playwright && "
                         "playwright install chromium")
    icon = read_text(os.path.join(WEB, "icon.svg"))
    jobs = (   # (output, markup, size, transparent)
        (os.path.join(WEB, "icon-192.png"), svg_document(icon, 192, 192), (192, 192), True),
        (os.path.join(WEB, "icon-512.png"), svg_document(icon, 512, 512), (512, 512), True),
        (os.path.join(WEB, "icon-maskable-512.png"), svg_document(full_bleed(icon), 512, 512), (512, 512), False),
        (os.path.join(WEB, "apple-touch-icon.png"), svg_document(full_bleed(icon), 180, 180), (180, 180), False),
    )
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            for out, markup, size, transparent in jobs:
                # An exact viewport at scale 1, so the PNG has exactly the size the manifest promises.
                page = browser.new_page(viewport={"width": size[0], "height": size[1]}, device_scale_factor=1,
                                        color_scheme="light")
                page.set_content(markup)
                page.screenshot(path=out, omit_background=transparent)
                page.close()
            # The social preview has text, so it goes through a real file: fonts are inlined, nothing is fetched.
            page = browser.new_page(viewport={"width": 1280, "height": 640}, device_scale_factor=1,
                                    color_scheme="light")
            with tempfile.TemporaryDirectory() as tmp:
                html = os.path.join(tmp, "social.html")
                with open(html, "w", encoding="utf-8") as fh:
                    fh.write(social_html())
                page.goto("file://" + html)
                page.evaluate("document.fonts.ready.then(() => 1)")
                # A missing glyph would silently fall back to some other Arabic font that also joins, so prove that
                # Plex is the face in use for both taglines.
                faces = page.evaluate("Array.from(document.fonts).map(f => f.status)")
                if not faces or any(s != "loaded" for s in faces):
                    raise SystemExit("make-brand: the Plex fonts did not load (%s)" % faces)
                widths = page.evaluate("""() => {
                    const c = document.createElement('canvas').getContext('2d');
                    const w = (font, text) => { c.font = font; return c.measureText(text).width; };
                    return ['%s', '%s'].map((t, i) => [w('600 50px "Plex Arabic", monospace', t), w('600 50px monospace', t)]);
                }""" % (TAGLINE_EN, TAGLINE_AR))
                if any(abs(a - b) < 1 for a, b in widths):
                    raise SystemExit("make-brand: a tagline fell back to another font (%s)" % widths)
                page.screenshot(path=os.path.join(IMG, "social-preview.png"))
            page.close()
        finally:
            browser.close()
    for out, _m, size, _t in jobs:
        print("make-brand: wrote %s (%dx%d)" % (os.path.relpath(out, ROOT), size[0], size[1]))
    print("make-brand: wrote docs/img/social-preview.png (1280x640)")


# ---------------------------------------------------------------------------------------------------------------
# Redraw the wordmarks as outlines (fontTools + HarfBuzz, imported only here)
# ---------------------------------------------------------------------------------------------------------------

def outline(font_file, text, rtl, script, lang, scale):
    """Shape `text` with HarfBuzz (joining, dots, kerning) and return its outline in font units, y down."""
    try:
        import io
        import uharfbuzz as hb
        from fontTools.pens.boundsPen import BoundsPen
        from fontTools.pens.svgPathPen import SVGPathPen
        from fontTools.pens.transformPen import TransformPen
        from fontTools.ttLib import TTFont
    except ImportError:
        raise SystemExit("make-brand: --wordmarks needs: pip install fonttools brotli uharfbuzz")
    tt = TTFont(os.path.join(WEB, "fonts", font_file))
    tt.flavor = None   # HarfBuzz reads plain sfnt, the woff2 wrapper is for browsers
    sfnt = io.BytesIO()
    tt.save(sfnt)
    font = hb.Font(hb.Face(hb.Blob(sfnt.getvalue())))
    buf = hb.Buffer()
    buf.add_str(text)
    buf.direction, buf.script, buf.language = ("rtl" if rtl else "ltr"), script, lang
    hb.shape(font, buf, {})
    glyphs, order = tt.getGlyphSet(), tt.getGlyphOrder()
    runs, x = [], 0
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
        runs.append((order[info.codepoint], x + pos.x_offset, pos.y_offset))
        x += pos.x_advance

    def draw(pen, dx, dy):
        for name, gx, gy in runs:
            glyphs[name].draw(TransformPen(pen, (scale, 0, 0, -scale, dx + gx * scale, dy - gy * scale)))

    bounds = BoundsPen(glyphs)
    draw(bounds, 0, 0)
    x0, y0, x1, y1 = bounds.bounds
    half = WORDMARK_STROKE / 2.0   # the round stroke grows the shape by half its width on every side
    dx, dy = WORDMARK_PAD + half - x0, WORDMARK_PAD + half - y0
    pen = SVGPathPen(glyphs, ntos=lambda v: ("%.1f" % v).rstrip("0").rstrip("."))
    draw(pen, dx, dy)
    width = (x1 - x0) + WORDMARK_STROKE + 2 * WORDMARK_PAD
    height = (y1 - y0) + WORDMARK_STROKE + 2 * WORDMARK_PAD
    return pen.getCommands(), width, height


def wordmark_svg(title, d, width, height):
    # currentColor + a dark-mode rule inside the file: an <img> of it is readable on a light and on a dark page.
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %s %s" role="img" aria-labelledby="t">'
            '<title id="t">%s</title>'
            '<style>svg{color:%s}@media (prefers-color-scheme:dark){svg{color:%s}}</style>'
            '<path fill="currentColor" stroke="currentColor" stroke-width="%d" stroke-linejoin="round" d="%s"/>'
            '</svg>\n' % (("%.1f" % width).rstrip("0").rstrip("."), ("%.1f" % height).rstrip("0").rstrip("."), title,
                          TEAL, TEAL_ON_DARK, WORDMARK_STROKE, d))


def wordmarks():
    jobs = (
        ("wordmark-en.svg", NAME_EN, "plex-arabic-latin-%d.woff2" % WORDMARK_WEIGHT, False, "Latn", "en", 1.0),
        ("wordmark-ar.svg", NAME_AR, "plex-arabic-arabic-%d.woff2" % WORDMARK_WEIGHT, True, "Arab", "ar",
         ARABIC_SCALE),
    )
    for out, text, font_file, rtl, script, lang, scale in jobs:
        d, width, height = outline(font_file, text, rtl, script, lang, scale)
        with open(os.path.join(IMG, out), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(wordmark_svg(text, d, width, height))
        print("make-brand: wrote docs/img/%s" % out)


def main():
    ap = argparse.ArgumentParser(description="Sinko brand assets (see the top of this file).")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="verify the committed assets (standard library only)")
    mode.add_argument("--wordmarks", action="store_true", help="redraw the two wordmark SVGs from the Plex fonts")
    args = ap.parse_args()
    if args.check:
        problems = check()
        for line in problems:
            print("FAIL  " + line)
        if problems:
            return 1
        print("make-brand: brand assets are consistent")
        return 0
    if args.wordmarks:
        wordmarks()
    else:
        render()
    return 0


if __name__ == "__main__":
    sys.exit(main())
