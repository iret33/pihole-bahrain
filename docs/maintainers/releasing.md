# Releasing Sinko, and setting the project up for the first time

## Before you publish: what works only after you act

Today the only repository that exists is `iret33/pihole-bahrain`, it has no release, and nothing is published to Pages. So
the addresses written into the tree (`iret33/sinko`) lead nowhere yet, and the installer, every box's update and the
website depend on them. Do the steps below **in this order**; each says what starts to work. The numbered steps of "One-time
setup" further down are the details of steps 1, 4 and 5 here.

| What | Where it is written | Works after step |
|---|---|---|
| The repository address `github.com/iret33/sinko`, and every link to it | README, SECURITY.md, the issue templates, `web/links.json` (*My box → About*: Project page, All versions), `site/config.js`, `install.sh`, `bin/sinko` | 1 (rename) |
| The block lists: `raw.githubusercontent.com/iret33/sinko/master/lists/` (every box downloads its lists from here every night) | the default of `SINKO_LISTS_BASE` in `install.sh` and `bin/sinko` | 2 (master holds the tree under the new name) |
| *My box → About*: Privacy and Licence (`blob/master/docs/privacy.md`, `blob/master/LICENSE`), the website's privacy link | `web/links.json`, `site/app.js` | 2 |
| README badge **CI** (`actions/workflows/ci.yml/badge.svg`) | README | 3 (it stays empty until one CI run exists on `master`) |
| README link **Website** (`https://iret33.github.io/sinko/`) and the website's own address; the website's "Full instructions on GitHub" link (README, `#install`) | README, `site/` | 4 (Pages) |
| The **boxes online** badge and the counter numbers on the website | commented out in the README; `statsUrl` in `site/config.js` | 5 (counter deployed) |
| README badges **Total downloads** (`img.shields.io/github/downloads/iret33/sinko/total`), **Downloads of the latest release** (`.../downloads/iret33/sinko/latest/total`) and **Latest release** (`img.shields.io/github/v/release/iret33/sinko`) | README | 7 (the first release with its files; before it they show an error, "no releases" or zero) |
| README badges **Stars** and **Last commit** (`img.shields.io/github/stars/iret33/sinko`, `.../last-commit/iret33/sinko`) | README | 1 (rename) |
| The install one-liner (`releases/latest/download/install.sh`), and every box's update check and download (`api.github.com/repos/iret33/sinko/releases/latest`, `releases/latest/download/sinko.tar.gz`) | README, `docs/install.md`, the website, `install.sh`, `bin/sinko` | 7 |
| The security reporting link (`security/advisories/new`) and the *Issues* links | `SECURITY.md`, the issue templates, `web/links.json` | 1, with the settings of "One-time setup" step 4 switched on |
| The **Licence** badge (and the Pi-hole, Orange Pi, language and contributions badges) | README | already (it is a static image) |

1. **Rename the repository to `sinko`**, detach it from the Pi-hole fork network, set the repository page and switch on the
   settings ("One-time setup", steps 1 to 4). Check: `https://github.com/iret33/sinko` opens, and the old
   `https://github.com/iret33/pihole-bahrain` redirects to it (2.x boxes update and fetch their lists through that
   redirect).
2. **Merge this tree to `master`.** Check: `curl -sI https://raw.githubusercontent.com/iret33/sinko/master/lists/guard.txt`
   answers `200` (the doctor's check and the nightly list refresh both read there), and `blob/master/docs/privacy.md` opens.
3. **Let CI run on `master` once** (the merge does it; or Actions, *CI*, if it did not). Check: the CI badge in the README
   says *passing*.
4. **Switch Pages on:** Settings → Pages → Build and deployment → Source = *GitHub Actions*, then run *Project site* once
   (Actions → *Project site* → Run workflow; it only runs by itself when something under `site/` or `docs/img/` changes on
   `master`). Check: the Website link opens the site, its install command is the hardened one, and "Full instructions on
   GitHub" lands on the README's *Install* section.
5. **Deploy the counter** with `/v1/forget` (`telemetry/README.md`), then put its address into `TELEMETRY_URL` in `bin/sinko`
   and `statsUrl` in `site/config.js`, and add the **boxes online** badge to the README (the commented block under the
   other badges) with the same address. Do it **before the tag**: the address is part of the release. From then on the
   installer asks the counter question and the page shows the *Count this box* card; until then neither does.
6. **If there is a shop:** set `buyUrl` in `site/config.js` and change the sentence "Ready-made boxes are not on sale yet" in
   the README, `docs/install.md` and `docs/faq.md` (a test fails while those words and an empty `buyUrl` disagree).
7. **Release:** replace `Unreleased` in the `CHANGELOG.md` heading with the date, make `VERSION` and `bin/sinko` agree, tag
   `v3.0.0` and push it ("Cutting a release" below). Check: the download badges and the Latest release badge show the release, the
   one-liner installs on a clean box, and a box on 2.x or on an older build updates from the page.
8. **Walk every link in the table** once, with a phone and a computer, then do section G of
   [`../hardware-test-checklist.md`](../hardware-test-checklist.md). Upload the social preview by hand (Settings → General →
   Social preview: `docs/img/social-preview.png`; nothing in the repository does it).

## One-time setup (do this before the first public release)

1. **Rename the repository to `sinko`** (GitHub → Settings → General → Repository name). GitHub then redirects the old
   `iret33/pihole-bahrain` address (web, git, `raw.githubusercontent.com`) to the new one. **Never create a new
   repository called `pihole-bahrain` afterwards**: that would break the redirect, and every box still running 2.x
   updates itself through it.
2. **Detach it from the Pi-hole fork network.** The repository began as a fork of Pi-hole's web interface, but no Pi-hole
   code is in the tree any more. Ask GitHub Support to "detach the fork", or push `master` to a brand-new repository named
   `sinko` (not a fork). Then the repository is no longer labelled as a Pi-hole fork and the Pi-hole history stays out of it.
   Do the second only if you accept that old links and stars do not follow.
3. **Repository page**: description (for example "Calm internet for the family: parental controls on your own box,
   Arabic and English"), website (the GitHub Pages address), topics (`parental-controls`, `pi-hole`, `orange-pi`,
   `arabic`, `dns`, `family`), social preview image `docs/img/social-preview.png`. Remove the old description that says
   "Pi-hole web interface fork".
4. **Settings**: enable *Issues* and *Discussions*; Security → *Private vulnerability reporting* on; Pages → source
   *GitHub Actions*; Actions → allow actions from GitHub and `cloudflare/wrangler-action` (the counter's deploy workflow
   only; the release workflow uses no third-party action). **Protect everything that decides what every box installs**,
   because a box runs the installer from a release as root (see `SECURITY.md`, "Who you are trusting"):
   * turn on two-factor authentication or passkeys on the owner account, and add a second owner so that one stolen
     account is not enough;
   * Branches → protect `master` (require the CI checks, no force pushes, and code-owner review, so that a change to
     `lists/` is reviewed like code: add `lists/` to `.github/CODEOWNERS`);
   * Rules → a tag ruleset for `v*` that lets only the owner create, move or delete tags;
   * keep every action in the workflows that can write either GitHub-owned or pinned to a commit (Dependabot, configured in
     `.github/dependabot.yml`, keeps pins up to date; it does not create them).
5. **Counter service** (optional but wanted): follow `telemetry/README.md` (deploy it **with `/v1/forget`** before boxes of
   this version reach families: an older Worker answers 404, and a box whose parent switched the counter off would keep
   asking), then put its address in `TELEMETRY_URL` near the top of `bin/sinko` and in `site/config.js` (`statsUrl`).
   Until you do, no box can ever send anything. **Do this before you tag**: the address is part of the release.
   If you want a per-address rate limit in front of the counter, it needs a custom domain (see the README, "Abuse").
6. **Website**: set `buyUrl` (your shop page) and `supportUrl` in `site/config.js`; leave `buyUrl` empty to hide the button
   (the card then says "Not on sale yet", and so do the README, `docs/install.md` and `docs/faq.md`: change those three
   together with `buyUrl`).
7. **Where the repository name is written.** If you ever move to another owner or name, change `iret33/sinko` in:
   `install.sh`, `bin/sinko`, `web/links.json`, `site/config.js`, `telemetry/wrangler.toml`, `systemd/sinko.service`,
   `.github/ISSUE_TEMPLATE/*.yml`, `README.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `docs/`. One command finds them all:
   `git grep -n "iret33/sinko"`.

## Cutting a release

1. Decide the number (`MAJOR.MINOR.PATCH`): a fix is a patch, a feature a minor, anything that changes how an installed
   box must be handled a major.
2. Put the number in `VERSION` **and** in `VERSION = "…"` in `bin/sinko` (a test fails if they differ), and write the
   section `## [X.Y.Z] - YYYY-MM-DD` in `CHANGELOG.md`, with the real date of the release. The section is the text of the
   GitHub release, and the Release workflow **refuses** a heading that still says `Unreleased` or has no real date
   (`tools/changelog-section.py --require-date`), so replace the word with the date before you tag.
3. Run everything (`CONTRIBUTING.md`), merge to `master`, wait for CI to be green.
4. Tag and push: `git tag -a vX.Y.Z -m "Sinko X.Y.Z" && git push origin vX.Y.Z`. The *Release* workflow runs the
   checks again, builds `sinko.tar.gz`, `sinko.tar.gz.sha256` and `install.sh` (in a job that can only read), and a
   second job that can write publishes the release with the GitHub CLI.
5. **Check the release like a family would**: on a spare box (or VM) run
   `curl --proto '=https' --proto-redir '=https' -fsSL https://github.com/iret33/sinko/releases/latest/download/install.sh | sudo bash`; then on a box
   running the previous version open *My box* and press *Update now*; both must end green. Keep the old box until you have.

## Ready-made images follow releases

Pi-hole on a ready-made box is not updated by Sinko (nobody can log in to the box), so the only way a shipped box gets a
newer Pi-hole is a newer image. Rebuild the golden unit for every Sinko release and whenever Pi-hole publishes a security
fix ([`../product-image.md`](../product-image.md)), and say in your shop how a customer re-flashes
([`../selling.md`](../selling.md)).

## A release turns out to be bad

Boxes only move to the release GitHub calls *latest*. Mark the bad release as a pre-release (or delete it) so *latest*
points to the previous good one, then publish a fixed `X.Y.Z+1`. A box that already updated can go back with
`sudo sinko rollback` (it keeps the version before the last update); a box whose update failed has already rolled back
by itself.

## A list change turns out to be bad

Lists are not part of a release. Pi-hole downloads `lists/*.txt` from `master` every night (03:30 plus up to two hours)
with no checksum and no rollback, and marking a release as a pre-release does nothing for them. Revert the commit
(`git revert <sha>`), merge it through a pull request with green CI, and boxes recover at their next nightly refresh, or at
once with `sudo pihole -g`. No release is needed. A list can change what is blocked; it cannot run anything on a box. The
tests refuse entries that would block shared infrastructure, but an over-broad entry such as a whole public suffix is not
caught by them, so review list changes by hand.

## Download count

Every download of a release asset is counted by GitHub. `sinko.tar.gz` is downloaded by each install and each update,
`install.sh` by each new install through the one-liner. The README badge and the website sum them; the counter service
adds them to its statistics (`downloads`). Counts include updates: they are *downloads*, not *families*; the number of
boxes online comes from the counter service and only includes families who said yes.

## Notes on the policy texts

* [`TRADEMARK.md`](../../TRADEMARK.md) is a plain-language draft written for the project maintainer. **Have a lawyer review
  it, and register the name where you sell, before you rely on it.** (This note lives here and not in the published
  policy.)
* The privacy statement ([`../privacy.md`](../privacy.md)) is part of the release: if a release changes what is sent, what
  the counter stores, or for how long, change the statement in the same change, and keep the site's text
  (`site/strings.js`) and the counter's root page in step with it.
