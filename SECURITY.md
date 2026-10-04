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

## Supported versions

Only the latest release gets fixes. Boxes update themselves from the page (*My box* → *Update now*) or with
`sudo sinko update`.

## How updates are protected

The box downloads `sinko.tar.gz` and its SHA-256 from this repository's GitHub release, checks the checksum, refuses
archives that try to write outside their folder, keeps the previous version for rollback, and rolls back by itself if
the new version does not pass its self-check. Updates always come from the repository set in `/etc/sinko/config`; the
page can only ask the box to update, never tell it where from.
