# Contributing to Sinko

Thank you for helping families get a calmer internet. Sinko is used by parents who are not technical, so the bar is:
*it must keep working, it must be honest about what it can do, and it must read well in Arabic and English.*

By taking part you agree to our [Code of Conduct](CODE_OF_CONDUCT.md). Security problems go to the private channel in
[SECURITY.md](SECURITY.md), never to a public issue.

## Ways to help

* **Report a problem** with the bug template. On an own install, run `sudo sinko doctor` first and paste its output
  (it has no passwords, but it can contain your children's names and their devices' addresses: the issue is public,
  so replace them first). A ready-made box has no login: describe what *My box* shows instead.
* **Ask for an app to be blocked** with the *New app* template, or add it yourself (see below).
* **Improve the Arabic.** Native speakers: strings live in `web/app.js` and `web/pb-box.js`; read them in the page
  (`python3 tests/mock_pihole.py --web web --setup --live`, then open <http://127.0.0.1:8080>, password `test`).
* **Test on your hardware** and tell us what you saw (board, router, phone).
* **Fix a bug or build a feature.** For anything bigger than a bug fix, open an issue first so we agree on the shape.

## Development setup

Needs Python 3.9+, Node 20+, bash and, for browser tests, Playwright with Chromium:

```bash
pip install playwright && python -m playwright install chromium     # once
python3 -m unittest discover -s tests -p "test_*.py"                # box program, lists, doctor, diagnose, copy
node --test tests/*.test.js                                          # page logic
sudo bash tests/test_install.sh                                      # installer end to end, on a stubbed system
shellcheck install.sh uninstall.sh tools/*.sh tests/*.sh            # shell lint
python3 tests/ui_smoke.py --shots /tmp/shots                         # the page in a real browser, English and Arabic
python3 tests/mock_pihole.py --web web --setup --live               # the page at http://127.0.0.1:8080 (password "test")
```

`tests/mock_pihole.py` imitates the parts of the Pi-hole v6 API Sinko uses, so no device is needed. CI runs all of
the above on every pull request. How the parts fit together, and the contracts between them (state schema, update
flow, release assets), is in [`docs/maintainers/architecture.md`](docs/maintainers/architecture.md).

## Adding or changing an app's block list

1. Create `lists/<id>.txt` (Adblock style, one `||domain^` per line) and add an entry to `lists/services.json`
   (id, English and Arabic name, category, colour, whether Homework mode blocks it).
2. Run the unit tests. They refuse lists that would block shared infrastructure (`google.com`, `apple.com`,
   `akamaihd.net`, ...): block the app's own domains, not the platform underneath it.
3. Say in the pull request how you checked it (which domains the app really uses, and that the app stops working).

Everything in `lists/` (the block lists and `services.json`) is public domain (CC0); contributions to it are too.

## Pull requests

* One change per pull request, with tests. Behaviour changes need a test that fails without the change.
* Keep to the style around you. Comments explain *why*. No new runtime dependencies: the box has only Debian's Python 3
  standard library, bash and curl, and the page is dependency-free JavaScript with no build step.
* Every user-facing string exists in English and Arabic with the same `{placeholders}`; the copy test enforces it and
  bans technical words (DNS, query, cache, ...) outside the "how it works" sheet.
* Update the README and `CHANGELOG.md` when users would notice. If a change alters what the box sends or stores, change
  `docs/privacy.md` (both languages), `site/strings.js` and the counter's root page in the same pull request.
* Sign your commits off (`git commit -s`): it says you wrote the change or have the right to submit it under this
  project's licence (the [Developer Certificate of Origin](https://developercertificate.org)).

## Licence

Contributions are accepted under the project's licences: GPL-3.0-or-later for code, CC0 for everything in `lists/`
(see [COPYING.md](COPYING.md)). The Sinko name and logo are not covered by those licences
([TRADEMARK.md](TRADEMARK.md)).
