# Questions and answers

*Commands that start with `sudo sinko` are for your own install, typed on the box over SSH. A ready-made box has no
login at all: use the parent page (*My box* shows health, updates, backup, restart and shut down), the router, the power
cable, or re-flash the card.*

**What does Sinko do?** It sits between your children's devices and the internet, and answers "yes" or "no" when a
device asks where an app or site is. You decide, from a page on your phone, which apps are blocked, when it is homework time
or bedtime, and which devices are paused.

**Does it read our messages, or see what we watch?** No. The box sees only the *names* of sites a device looks up, on your
own box in your own home. It never sees the content of messages, videos or pages, and Sinko sends none of it to the project.
One thing to know: for a site it does not block, Pi-hole (the filter on the box) asks an upstream DNS service to find the
address, Cloudflare for Families unless you changed it. That service therefore sees the site names and your home's
internet address. [`privacy.md`](privacy.md) explains it.

**Does anything go to the internet from the box?** Yes, a few things, and none of them is about your family's rules,
children or devices:

* the lookups Pi-hole cannot answer itself, which it passes to its upstream DNS service (Cloudflare for Families by
  default);
* the nightly refresh of the block lists, which Pi-hole downloads from where they are published (Sinko's come from this
  project's page on GitHub);
* the update check (a request to GitHub for the latest release, about two minutes after the box starts and then about
  once a day, and again within the hour when GitHub could not be reached) and, when you update, the download of the new
  version, a refresh of the block lists, and one small public file that the installer fetches from the block-list address
  to check it (`sudo sinko doctor` does the same by hand);
* Debian's automatic security updates, and the time from public time servers (the box has no clock battery);
* when you install Sinko yourself, the downloads the installer needs;
* the optional anonymous counter, which is off until you say yes, and which tells the project only a random code, the
  version and the kind of box.

[`privacy.md`](privacy.md) has the details, including what each of them can see.

**Is it free?** Yes. The software is free (GPL-3.0-or-later); you can build your own box in about 20 minutes with an
Orange Pi Zero 3 or any small Debian computer. A ready-made box (nothing to build) is not on sale yet: the project
website will say where to buy one as soon as there is a shop.

**What can children do to get around it?** Use mobile data or a VPN; use an app that connects to fixed addresses; use a
browser's own "secure DNS" or iCloud Private Relay (Sinko blocks the well-known providers, new ones appear); change the
network settings if they know the Wi-Fi password; log in to the box if its password is weak. Use Sinko together with Screen
Time (iPhone) or Family Link (Android), choose strong passwords, and read "What it cannot do" in the README.

**Why Pi-hole?** Pi-hole is a mature, well-tested DNS filter that millions of people run. Sinko is the parent-friendly layer on
top of it: the page, the timers, the lists per app, the updates, and the ready-made box. Pi-hole is a trademark of Pi-hole
LLC, and Sinko is independent software that works with it.

**Can I log in to a ready-made box?** No, on purpose: a box that nobody can log in to cannot be taken over by someone
who guesses a password. Everything a parent needs is on the page. If something is wrong, see "Ready-made box" in
[`troubleshooting.md`](troubleshooting.md): it comes down to the page, the router, the power cable, or re-flashing.

**How is Pi-hole kept up to date?** On your own install, with `sudo pihole -up` (Sinko updates only itself). A ready-made
box cannot be logged in to, so Pi-hole on it is updated by re-flashing a newer Sinko image; *My box*, *About* shows
which Pi-hole version the box runs (the line is left out when the box cannot tell). See [`updating.md`](updating.md).

**Do I need Ethernet?** Strongly recommended: a wired box is more reliable than Wi-Fi.

**Does it work with IPv6?** Only if the router tells devices to use the box for IPv6 DNS too, or has IPv6 DNS turned off.
On your own install, `sudo sinko doctor` tells you if this is the problem; with a ready-made box, look at the router's
IPv6 and DNS settings.

**It stopped working after a power cut.** It should start by itself in about two minutes. If the page does not open, see
[`troubleshooting.md`](troubleshooting.md). A card that is worn out from years of writing is the usual cause of repeated
trouble: use a high-endurance card and keep a backup (*My box → Backup*).

**How do I remove it?** Own install: `sudo /opt/sinko/uninstall.sh` removes Sinko and its rules and keeps Pi-hole (a device that
also holds a group of your own in Pi-hole stays, without Sinko's groups). Ready-made
box: there is nothing to uninstall, just unplug it. In both cases, point the router's DNS server back to its default so
that the devices at home do not keep asking a box that is gone.

**How do I help?** Report problems, improve the Arabic, add apps to the list: [`CONTRIBUTING.md`](../CONTRIBUTING.md).
