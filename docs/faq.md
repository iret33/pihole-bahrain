# Questions and answers

**What does Sinko do?** It sits between your children's devices and the internet, and answers "yes" or "no" when a
device asks where an app or site is. You decide, from a page on your phone, which apps are blocked, when it is homework time
or bedtime, and which devices are paused.

**Does it read our messages, or see what we watch?** No. It sees only the *names* of sites a device looks up, on your own
box in your own home. It never sees the content of messages, videos or pages, and nothing leaves the box.

**Does anything go to the internet from the box?** Looking up sites (that is its job), Pi-hole's block-list downloads from this
project's GitHub, and the update check (a request to GitHub for the latest release). The optional anonymous counter is off
until you say yes. See [`privacy.md`](privacy.md).

**Is it free?** Yes. The software is free (GPL-3.0-or-later); you can build your own box in about 20 minutes with an
Orange Pi Zero 3 or any small Debian computer. You can also buy it ready-made.

**What can children do to get around it?** Use mobile data or a VPN; use an app that connects to fixed addresses; use a
browser's own "secure DNS" or iCloud Private Relay (Sinko blocks the well-known providers, new ones appear); change the
network settings if they know the Wi-Fi password; log in to the box if its password is weak. Use Sinko together with Screen
Time (iPhone) or Family Link (Android), choose strong passwords, and read "What it cannot do" in the README.

**Why Pi-hole?** Pi-hole is a mature, well-tested DNS filter that millions of people run. Sinko is the parent-friendly layer on
top of it: the page, the timers, the lists per app, the updates, and the ready-made box. Pi-hole is a trademark of Pi-hole
LLC, and Sinko is independent software that works with it.

**Do I need Ethernet?** Strongly recommended: a wired box is more reliable than Wi-Fi.

**Does it work with IPv6?** Only if the router tells devices to use the box for IPv6 DNS too, or has IPv6 DNS turned off.
`sudo sinko doctor` tells you if this is the problem.

**It stopped working after a power cut.** It should start by itself in about two minutes. If the page does not open, see
[`troubleshooting.md`](troubleshooting.md). A card that is worn out from years of writing is the usual cause of repeated
trouble: use a high-endurance card and keep a backup (*My box → Backup*).

**How do I remove it?** `sudo /opt/sinko/uninstall.sh` removes Sinko and its rules and keeps Pi-hole. To go back to the
way things were, point the router's DNS server back to its default.

**How do I help?** Report problems, improve the Arabic, add apps to the list: [`CONTRIBUTING.md`](../CONTRIBUTING.md).
