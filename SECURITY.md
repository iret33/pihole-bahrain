# Security policy

Sinko runs on a box inside a family's home network and holds the parent password, so we take reports seriously.

## Reporting a vulnerability

**Please do not open a public issue.** Use GitHub's private reporting instead:
<https://github.com/iret33/sinko/security/advisories/new>

Include what you found, the Sinko version (`sudo sinko --version`, or *My box* in the page), the steps to reproduce, and
what an attacker on the home network (or on the internet) could do with it. You will get an answer within a few days.
We will agree a fix and a disclosure date with you, and credit you in the release notes unless you prefer not to be named.

## What is in scope

* The parent page (`web/`), the scheduler and CLI (`bin/sinko`), the installer, updater and image tools
  (`install.sh`, `tools/`), the release assets, and the optional counter service (`telemetry/`).
* Anything that lets a **child's device** (a non-admin on the home network) switch the rules off, read the parent
  password, run code on the box, or change what an update installs.
* Anything that lets a stranger **on the internet** reach the box, or lets a release or update be tampered with.

## What is not a vulnerability

Sinko filters by DNS, which is a strong everyday filter and not a lock. These are documented limits, not bugs:
a device on mobile data or a VPN does not use the box; apps that connect to fixed IP addresses can keep working; a
child who knows the Wi-Fi password and can change network settings can go around it; whoever has the parent password,
physical access to the box, or `root` on it can change anything. Weaknesses in Pi-hole itself belong to
[Pi-hole's own security policy](https://github.com/pi-hole/pi-hole/security/policy).

A **ready-made box has no password until the parent chooses one** on its welcome screen. Until then Pi-hole accepts any
device on the home network, so the first device to choose a password owns the box. That is an accepted limit of a box
that has no screen, and the mitigation is the parent's: choose the password as soon as the box has started. A parent
who is asked for a password the first time they open the page knows that someone else chose it, and re-flashes the card.

A ready-made box also has **no login of any kind** (root is locked, SSH passwords are off, there is no key and no
support account), so there is no remote support and no back door; the seller and the project cannot connect to it.

When the installer has to give Pi-hole a password and Pi-hole already has one (an existing Pi-hole, or a second run with
`SINKO_PASSWORD`), it uses Pi-hole's own `pihole setpassword`, which takes the password as an argument: for a moment it is
in that program's arguments, and any user who can log in to the box can read it there. A Pi-hole that has no password yet
is given it through its API instead, in the body of a request on the box, never in a program's arguments. Pi-hole refuses a
password change from the installer's command-line session, so the first route cannot be used for the second case. On a box
that only its owner logs in to, and on a ready-made box (nobody logs in), this is accepted; on a shared computer, choose the
password on the parent page (*My box*, *Change password*). [`docs/install.md`](docs/install.md) says the same to whoever
installs.

## Supported versions

Only the latest release gets fixes. Boxes update themselves from the page (*My box* → *Update now*) or with
`sudo sinko update`. Pi-hole is a separate program with its own releases: on a box you built yourself it is updated with
`sudo pihole -up`, and on a ready-made box only by re-flashing a newer image (see [`docs/updating.md`](docs/updating.md)).

## How updates are protected

The box downloads `sinko.tar.gz` and its SHA-256 from this repository's GitHub release over HTTPS, checks the checksum,
refuses archives that try to write outside their folder or contain links, keeps the previous version for rollback, and tries
to put it back by itself if the new version does not pass its self-check. Updates always come from the repository set in
`/etc/sinko/config`; the page can only ask the box to update, never tell it where from.

### Who you are trusting

This is what the protection above does **not** do, and it is better said plainly:

* **The checksum proves integrity, not origin.** It is downloaded from the same place as the archive. It catches a damaged,
  truncated or mismatched download. It does not catch a release that was published on purpose, because whoever publishes
  the archive also publishes its checksum.
* **Whoever controls this GitHub repository controls every box.** That means whoever can push a release tag, replace or
  add files to a release, change the workflow that builds the release, or take over the maintainer's GitHub account (or
  one of the third-party actions that workflow uses). A box runs the installer from the release as `root`, immediately
  when a parent presses *Update now*, and without anyone present between 03:00 and 05:00 on boxes whose parent switched on
  *Update automatically at night* (it is off until switched on). Nothing on the box checks a signature with a key that is
  not stored on GitHub: **Sinko 3.0 has no signed releases**, and we do not pretend it has. The first install through
  `curl … | sudo bash` trusts TLS and GitHub in the same way. This is the trust model of any updater that downloads from a
  code host, and Pi-hole's own installer works like it.
* **Block lists are outside this chain.** Pi-hole downloads Sinko's lists from the `master` branch of this repository every
  night, without a release, a checksum or a rollback. A bad change to `lists/` reaches every box within a night (and is
  repaired the same way). Lists are plain domain names: they can change what is blocked, they cannot run anything on the box.
  Changes to `lists/` are reviewed like code.

What is meant to protect the repository, and what the maintainer's checklist
([`docs/maintainers/releasing.md`](docs/maintainers/releasing.md)) asks to be switched on: two-factor authentication or
passkeys on the owner account and a second owner; a tag ruleset that lets only the owner create, move or delete `v*` tags;
a protected `master` (required checks, no force pushes, code-owner review for `lists/`); third-party actions pinned to a
commit; and publishing only from the release workflow. These reduce the chance of an accident or a stolen password.
They do not remove the trust: someone with write access can still change the workflow in the commit a tag points to. The
real fix is a signature made with an offline key and checked by the box against a public key it already has, so that the
box that is already installed authenticates the next release. Sinko does not do this yet.

**What a seller can do on top.** A seller who ships boxes and does not want their customers to depend on this repository
can: keep *Update automatically at night* off (the default) and tell customers that the update button is theirs to press;
publish **their own releases** from a fork they control and review (set `SINKO_REPO_SLUG` in `/etc/sinko/config` of the
golden unit to the fork, so that boxes follow the seller's releases), applying the same protections (two-factor
authentication, a tag ruleset, a second owner, pinned actions) to that repository; build every image from a tagged release
the seller has read, and rebuild images for every release (see [`docs/selling.md`](docs/selling.md)); and, if they run a
mirror of the release files, point `SINKO_RELEASE_BASE` and `SINKO_RELEASE_API` at it (https only). A seller who does this
becomes the trust root for their customers in place of this project, and should say so.
