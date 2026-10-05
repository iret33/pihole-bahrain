# The Sinko project site

A one-page, bilingual (English and Arabic, right to left) site for GitHub Pages. Static files, no build step, no
framework, nothing to install. It is published by `.github/workflows/pages.yml`.

| File | What it is |
|---|---|
| `index.html` | the page. The English text is written here, so it reads without JavaScript |
| `strings.js` | every text in both languages (the Arabic is shown by `app.js`) |
| `config.js` | the four settings: `repo`, `statsUrl`, `buyUrl`, `supportUrl`, each explained inside |
| `lib.js` | logic without the page: settings checks, the numbers strip, the install command |
| `app.js` | the page's behaviour: language, numbers, copy button, missing pictures |
| `style.css` | one stylesheet for both directions (logical properties only), light and dark, reduced motion |
| `assemble.sh` | builds the folder that is published |
| `test/` | the tests |

## Settings (`config.js`)

* `repo`: `owner/name` on GitHub. Gives the install command and every link to GitHub.
* `statsUrl`: the address of the anonymous counter (`telemetry/`), `https`, no trailing slash. With it the site shows
  how many boxes are online and the counter's download total. Without it only the download count is shown, taken
  from GitHub's public API. If the counter or GitHub cannot be reached, the numbers are not shown, and the strip
  stays hidden: the site never shows a number it did not receive.
* `buyUrl`: where to buy a ready-made box. Empty hides the button (the card then says it is not on sale yet).
* `supportUrl`: a help page or `mailto:`. Empty hides the footer link.

## What is copied in at deploy time

`site/assemble.sh` copies, so that each file exists once in the repository: the pictures in `docs/img/` (`.png`, `.jpg`,
`.webp`, `.svg`; the README that lives there is not copied) to `img/` (the page uses `panel-en.png`, `panel-ar.png`,
`live-en.png`, `box-en.png`, and `social-preview.png` for link previews), `web/fonts/*` to `fonts/` (IBM Plex Sans
Arabic, SIL OFL) and `web/icon.svg` to `icon.svg`. It also puts the site's address into the three places that must be
absolute (canonical link, `og:url`, `og:image`), because link previews do not run JavaScript.

**The screenshots arrive later than the site.** `tools/make-screenshots.py` makes them (it needs a browser, so the Pages
workflow does not run it) and they are committed to `docs/img/`. Until then the site is published without them, and that
is meant to work: `assemble.sh` prints one `::warning::` line for each missing picture and carries on, and the page
asks once (a cheap `HEAD` request) which pictures exist, hides the ones that do not, and keeps the whole "See it"
section hidden until at least one is there. Nothing to change in the site when they are added: commit them to
`docs/img/` and the next deploy shows them.

## Preview it

```bash
bash site/assemble.sh /tmp/sinko-site            # pass the real address as a second argument to test the previews
python3 -m http.server -d /tmp/sinko-site 8080   # then open http://localhost:8080/ and try /?lang=ar
```

GitHub serves a project site from a sub-folder (`https://<owner>.github.io/sinko/`), so every address in the site is
relative and a test fails if one starts with a slash.

## Tests

```bash
node --test site/test/*.test.mjs                 # Node 20 or newer
```

`site.test.mjs`: both languages have exactly the same keys, the same placeholders, and no Arabic is English by
mistake; the privacy answer says that the box asks an upstream DNS service and never says that what a family looks up
"stays on the box"; the Arabic tagline is one; the English written in `index.html` is the English in `strings.js`;
`config.js` is valid; every file the page names exists; text colours pass WCAG AA in light and dark; the stylesheet has no left/right properties; the
language is picked correctly before the first paint; `assemble.sh` builds the right folder and refuses an unsafe
address. `lib.test.mjs`: what the numbers strip shows and leaves out, with a fake `fetch`.

## Adding or changing text

Change the key in `strings.js` for both languages, and write the same English in `index.html`. The test says which
side is out of step. A line that needs a link is two elements, because the strings are text only.
