# Brand images

What each file is, how it was made, and how to make it again. The product mark is a house with two sound arcs
rising from its roof, in white on the page's teal (`#0F766E`). Nothing here needs a font or the network.

| File | What it is |
|---|---|
| `logo.svg` | The mark (teal rounded tile, white house, two arcs). The same drawing as `web/icon.svg`. |
| `wordmark-en.svg` | "Sinko" as outlines, no tile. |
| `wordmark-ar.svg` | سينكو as outlines, no tile. |
| `social-preview.png` | 1280 x 640 card for the GitHub repository and link previews. |

Files in `web/` (the installer copies the whole folder to the page's `/pb/`):

| File | What it is |
|---|---|
| `icon.svg` | The mark. Hand drawn on a 512 grid; the source of every PNG. Used as the favicon and the manifest's `any` icon. |
| `icon-192.png`, `icon-512.png` | Rounded tile with transparent corners, purpose `any`. |
| `icon-maskable-512.png` | Square, full-bleed teal, purpose `maskable`. The white content stays inside the 40 % safe circle (it reaches 202 of 205 px at 512), so any launcher mask (circle, squircle, teardrop) keeps all of it. |
| `apple-touch-icon.png` | 180 x 180, square, fully opaque. iOS paints transparent pixels black and applies its own rounding, so this one has neither transparency nor its own corners. |
| `manifest.json` | Name, colours, `start_url`/`scope` `/`, and the four icons above, each with its own `purpose`. |

## The mark

* A solid white house with soft corners (a 30 unit stroke with round joins), a rounded doorway cut out of its floor line,
  and two white arcs (26 unit stroke, round caps) centred on the roof peak, at radius 62 and 116, spanning 88 and 76
  degrees. The arcs read as sound; they also say "the home's internet".
* Solid shapes, so at 16 to 32 px it is still a house with something above it. No text, no gradient, no
  transparency inside the tile: it is the same in a light or a dark browser tab.
* It replaces the earlier white house outline and keeps its tile, its teal and its sense of home.

### One-colour glyph for the page's icon sprite

For places that colour the mark with `currentColor` (the page's top bar and login card draw their own tile, so they
only need the glyph). A 24 grid symbol in the style of the page's other sprite icons; the doorway is cut into the
house's single outline, so it needs no second colour. It was rendered over `icon.svg`'s glyph and no pixel differs
visibly.

```html
<symbol id="i-sinko" viewBox="0 0 24 24"><path d="M11.41 8.8A0.86 0.86 0 0 1 12.59 8.8L18.58 14.33A0.86 0.86 0 0 1 18.86 14.97L18.86 21.19A0.86 0.86 0 0 1 17.99 22.06L13.5 22.06V19.81a1.5 1.5 0 0 0 -3 0V22.06L6.01 22.06A0.86 0.86 0 0 1 5.14 21.19L5.14 14.97A0.86 0.86 0 0 1 5.42 14.33z" fill="currentColor"/><path d="M9.52 6.86A3.57 3.57 0 0 1 14.48 6.86M7.88 4.17A6.69 6.69 0 0 1 16.12 4.17" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></symbol>
```

## The wordmarks

* Outlines of IBM Plex Sans Arabic SemiBold (SIL Open Font License 1.1, `web/fonts/OFL.txt`), the same family the page
  uses, so the page and the logo match. The corners are softened with a 28 unit round stroke in the fill colour.
  This is a typeset wordmark, not a bespoke drawing, and no font file is embedded: the files contain only paths.
* The Arabic is shaped by HarfBuzz (so the letters join, the dots sit where they belong, and س is the rightmost
  letter) and drawn as one path. It was overlaid on Chromium's own rendering of the same text in the same font: same
  size, same joins, same dots, only edge anti-aliasing differs. Plex Arabic looks smaller than its Latin at the same size, so the Arabic is drawn 12 % larger.
* Each file is cropped to its letters plus a margin, in 1000 units to the em. They carry `currentColor` and a
  `prefers-color-scheme: dark` rule inside the file (`#0F766E` on light, `#2BA597` on dark), so an `<img>` of either
  one is readable on a light or a dark page. In a GitHub README:

  ```html
  <img src="docs/img/wordmark-en.svg" alt="Sinko" height="48">
  ```

  The rule follows the reader's system colour scheme. If someone sets GitHub's theme differently from their system,
  the wordmark is still readable, just dimmer (about 3:1 against the opposite background).

## The social preview

Flat teal, the mark and "Sinko" on one baseline, the English tagline and the Arabic one under them, and very faint
rings echoing the arcs. Text is set in Plex (inlined as data into a temporary page, so nothing is fetched). The script
refuses to write the PNG if the fonts do not load, or if either tagline fell back to another font.
The Arabic tagline renders joined and right to left. Keep the important content inside the middle 1200 x 600: some
sites crop the card a little.

Uploading it is a manual step in the GitHub repository settings (Settings, General, Social preview); nothing in the
repository does it.

## Making them again

```bash
python3 tools/make-brand.py              # the PNGs, from the SVGs (pip install playwright && playwright install chromium)
python3 tools/make-brand.py --wordmarks  # the two wordmark SVGs (pip install fonttools brotli uharfbuzz)
python3 tools/make-brand.py --check      # standard library only; also runs in the test suite
```

On the machine that made them, running the first two again gives the same bytes. `--check` verifies the PNG sizes, that
the `any` icons have transparent corners and the maskable and apple-touch icons are opaque brand teal, that the
maskable content is inside the safe circle, that the manifest and the files agree, that no SVG contains text, a font
reference or an external link, and that every SVG's `<title>` is the product name.

The product name is a constant at the top of `tools/make-brand.py`, the `<title>` of each SVG and `web/manifest.json`.
`--check` fails when they disagree, so renaming the product is: change those, run `--wordmarks` and the default
mode, run `--check`.

## Licence

The mark, the wordmarks and the preview are the project's logo and product mark: they are not covered by the GPL
(see `COPYING.md`). The letter shapes inside the wordmarks come from IBM Plex Sans Arabic under the OFL.
