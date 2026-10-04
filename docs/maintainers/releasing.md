# Releasing Sinko, and setting the project up for the first time

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
   *GitHub Actions*; Branches → protect `master` (require the CI checks, no force pushes); Actions → allow actions from
   GitHub and `softprops/action-gh-release`, `cloudflare/wrangler-action`.
5. **Counter service** (optional but wanted): follow `telemetry/README.md`, then put its address in
   `DEFAULT_TELEMETRY_URL` in `bin/sinko` and in `site/config.js` (`statsUrl`). Until you do, no box can ever send anything.
6. **Website**: set `buyUrl` (your shop page) and `supportUrl` in `site/config.js`; leave `buyUrl` empty to hide the button.
7. **Where the repository name is written.** If you ever move to another owner or name, change `iret33/sinko` in:
   `install.sh`, `bin/sinko`, `web/links.json`, `site/config.js`, `telemetry/wrangler.toml`, `systemd/sinko.service`,
   `.github/ISSUE_TEMPLATE/*.yml`, `README.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `docs/`. One command finds them all:
   `git grep -n "iret33/sinko"`.

## Cutting a release

1. Decide the number (`MAJOR.MINOR.PATCH`): a fix is a patch, a feature a minor, anything that changes how an installed
   box must be handled a major.
2. Put the number in `VERSION` **and** in `VERSION = "…"` in `bin/sinko` (a test fails if they differ), and write the
   section `## [X.Y.Z] - YYYY-MM-DD` in `CHANGELOG.md`. The section is the text of the GitHub release.
3. Run everything (`CONTRIBUTING.md`), merge to `master`, wait for CI to be green.
4. Tag and push: `git tag -a vX.Y.Z -m "Sinko X.Y.Z" && git push origin vX.Y.Z`. The *Release* workflow runs the
   checks again, builds `sinko.tar.gz`, `sinko.tar.gz.sha256` and `install.sh`, and publishes the release.
5. **Check the release like a family would**: on a spare box (or VM) run
   `curl -fsSL https://github.com/iret33/sinko/releases/latest/download/install.sh | sudo bash`; then on a box
   running the previous version open *My box* and press *Update now*; both must end green. Keep the old box until you have.

## A release turns out to be bad

Boxes only move to the release GitHub calls *latest*. Mark the bad release as a pre-release (or delete it) so *latest*
points to the previous good one, then publish a fixed `X.Y.Z+1`. A box that already updated can go back with
`sudo sinko rollback` (it keeps the version before the last update); a box whose update failed has already rolled back
by itself.

## Download count

Every download of a release asset is counted by GitHub. `sinko.tar.gz` is downloaded by each install and each update,
`install.sh` by each new install through the one-liner. The README badge and the website sum them; the counter service
adds them to its statistics (`downloads`). Counts include updates: they are *downloads*, not *families*; the number of
boxes online comes from the counter service and only includes families who said yes.
