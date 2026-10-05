# Installing Sinko on your own hardware

You will have a box that keeps your children's devices on your rules in about 20 minutes. If you would rather not do
this yourself, a ready-made box is planned (it is not on sale yet; the [README](../README.md#two-ways-to-get-sinko) says
where things stand); everything below is for people who build their own. After that, everything is done from the parent
page; you will not need these commands again, except to look at what is happening.

## What you need

* A small computer that runs **Debian, Ubuntu or Raspberry Pi OS** (Armbian is Debian): an Orange Pi Zero 3 is what
  Sinko is tested on; a Raspberry Pi or any other small Debian computer works too. Use the **Ethernet** port, not Wi-Fi.
  More than 1 GB of free disk space for a first installation (an update needs more than 200 MB), 1 GB of RAM or more
  (comfortable), and a good (high-endurance) microSD card.
* Python 3.9 or newer (every supported system has it), `systemd`, and an internet connection while installing.
* Your router's settings page, to point your home network at the box (the last step).
* [Pi-hole](https://pi-hole.net) version 6. If it is missing, the installer installs it for you (fully unattended). If an
  older Pi-hole (v5) is there, update it first with `sudo pihole -up`; the installer stops and says so.

## Install

On the device (over SSH, or at its keyboard):

```bash
curl --proto '=https' --proto-redir '=https' -fsSL https://github.com/iret33/sinko/releases/latest/download/install.sh | sudo bash
```

The installer downloads the newest release (`sinko.tar.gz`), checks it against the published SHA-256 and refuses to
go on if they differ. Then it:

1. installs Pi-hole v6 if it is missing;
2. asks for a **parent password** (8 or more characters). If nobody can be asked, it generates one and shows it on the
   screen only; with no screen at all it saves it in `/etc/sinko/initial-password` (readable by root only: read it, then
   delete the file). The password is never written to the install log. An *update* never chooses a password. How the
   password reaches Pi-hole depends on Pi-hole: one that has no password yet (a new Pi-hole) is given it through its API,
   in the body of a request on the box itself; one that already has a password (an existing Pi-hole, or a second run with
   `SINKO_PASSWORD`) refuses to have it changed that way, so the installer uses Pi-hole's own `pihole setpassword`, which
   takes the password as an argument: for a moment it is in that program's arguments, which any user on the box can read
   in the process list. On a box that other people log in to, change the password afterwards on the page instead
   (*My box*, *Change password*, which does not go through a command line). If this copy of Sinko has a counter address, the
   installer also asks once whether to count the box in the anonymous number of Sinko boxes online (default *no*; see
   [`privacy.md`](privacy.md)); without a counter address it says nothing about it;
3. installs the parent page at `http://<box-ip>/` (the Pi-hole admin stays at `/admin/`) and, through Avahi,
   `http://<hostname>.local/` so phones can find it by name;
4. creates the Pi-hole groups, rules and block lists, and starts the scheduler and the nightly list refresh;
5. checks that the new scheduler really runs, sets up Debian's automatic security updates and, when the box has no time
   service, `systemd-timesyncd` (the boards have no clock battery), and prints the address and the router step below.

If a step fails, nothing is half-installed: fix the problem and run the same command again. **Running the same command
again updates everything and keeps your password, devices and rules.** (Updating normally happens from the page,
see [`updating.md`](updating.md).)

An existing Pi-hole v6 is used as it is: your groups, lists, clients and rules are never touched. Sinko adds its own, all
named `pb-…`.

### Options

Pass these as environment variables after `sudo`, for example
`curl … | sudo SINKO_HOSTNAME=kids.home bash`. What you set once is saved in `/etc/sinko/config` and kept by later runs.

| Variable | Default | Meaning |
|---|---|---|
| `SINKO_PASSWORD` | ask / generate | The parent password (also Pi-hole's admin password). Not on a shared computer: it stays in your shell history, and while the command runs it is in the process list (also with `sudo SINKO_PASSWORD=… bash`: those are sudo's own arguments). The installer takes it out of its own environment at once. A Pi-hole that has no password yet gets it through Pi-hole's API, in the body of a request on the box; a Pi-hole that already has one is changed with `pihole setpassword`, where the password is briefly in that program's arguments (see step 2 above) |
| `SINKO_HOSTNAME` | `family.lan` | Local name for the page (works on devices that use the box); `none` to skip |
| `SINKO_UPSTREAMS` | `1.1.1.3,1.0.0.3` | Upstream DNS for a **new** Pi-hole: Cloudflare for Families, which also keeps out adult sites and known malware |
| `SINKO_TIMEZONE` | `Asia/Bahrain` if the system is on UTC | Time zone that bedtime follows |
| `SINKO_REF` | `latest` | `latest` = newest release; `vX.Y.Z` = that release (and the box stays on it); any other value, such as `master`, is the developer path: a git checkout of that branch, with no update checks |
| `SINKO_REPO_SLUG` | `iret33/sinko` | The GitHub project (`owner/name`) releases and the lists come from; set it for a fork |
| `SINKO_REPO` | `https://github.com/<slug>.git` | Git address for the developer path |
| `SINKO_SRC` | – | A folder with an already extracted release: nothing is downloaded (offline installs, image builds, tests) |
| `SINKO_RELEASE_BASE`, `SINKO_RELEASE_API` | GitHub's | Where releases and the "latest release" answer come from; **https only** (a mirror of your own) |
| `SINKO_LISTS_BASE` | this project's `lists/` on GitHub | Where Pi-hole downloads the app block lists |
| `SINKO_TELEMETRY` | ask (default *no*) | `1` = count this box in the anonymous number of Sinko boxes online, `0` = no. The question is asked only when this copy of Sinko has a counter address; not asked and not set: the page asks later (and shows nothing when there is no counter address). What is sent: [`privacy.md`](privacy.md) |
| `SINKO_MDNS` | on | `0` = do not install Avahi (the box then has no `.local` name) |
| `SINKO_OS_UPDATES` | on | `0` = do not set up automatic Debian **security** updates (the box never restarts by itself for them). An `unattended-upgrades` that is already installed is left exactly as it is, switched on or off |
| `SINKO_NONINTERACTIVE` | – | `1` = never prompt |

The installer works whatever language the system is set to: where it reads apt's or dpkg's answers it asks for them in
English, so a box set to Arabic, German or French still gets Avahi, the security updates and a time service (Pi-hole's
installer and apt's own messages stay in your language). A time service that is already there is left alone, and so is
one that is installed but switched off: the installer then says so and how to switch it on.

## The last step: point your home at the box

Pi-hole only filters the devices that use it for DNS. In your router's settings (the page's *Setup help* shows the box's
number):

1. set the **DNS server** (LAN or DHCP settings) to the box's address, and only that address; a *number* like
   `192.168.1.50`, never a name;
2. **reserve** that address for the box (DHCP reservation, or static lease), so it never changes;
3. turn Wi-Fi off and on on each child's device, then add the device on the page.

If the router also hands out IPv6 DNS servers, turn that off (or set it to the box too), otherwise devices can skip the
filter over IPv6. The page's checklist ticks "router" by itself as soon as a device has asked the box.

## Preparing an Orange Pi Zero 3

1. Flash **Armbian minimal, Debian Trixie** for the Orange Pi Zero 3 from <https://www.armbian.com/orange-pi-zero-3/> onto a
   high-endurance microSD card (8 GB or more).
2. Connect Ethernet and power. Log in over SSH (`root` / `1234`) and finish Armbian's first-login questions: **choose a
   strong root password**, and answer **no** to the Wi-Fi question (see below).
3. Run the install command above.

Avoid the 1.5 GB RAM model (current kernels crash on it). Use the Ethernet port. On a Pi-hole that the installer sets
up, query history is kept 30 days to reduce card wear.

### Keep the box itself safe

The box is on the same network as the children's devices. A child who can log in to it as `root` can take their own device
out of the rules or switch the filter off, so:

* change the default password at once (`passwd`) to something long that only you know; never leave it at `1234`;
* better, allow SSH keys only. From your computer run `ssh-copy-id root@<box-ip>`, then **test that the key login works in
  a second window**, and only then on the box:

  ```bash
  echo 'PasswordAuthentication no' | sudo tee /etc/ssh/sshd_config.d/00-keys-only.conf
  sudo systemctl restart ssh
  ```

* the parent password is a separate secret (it is also Pi-hole's admin password); keep it from the children as well.

## Commands on the box

```bash
sudo sinko doctor        # check the installation, and that DNS really reaches the box
sudo sinko diagnose      # why is a blocked app still working? (read-only report)
sudo sinko watch         # does a device's DNS reach the box? (turn its Wi-Fi off and on, open the app)
sudo sinko use-mac       # re-register children added by IP address under their MAC address
sudo sinko status        # current rules, devices, timer, bedtime, update state (JSON)
sudo sinko update        # update now (--check only looks; --ref vX.Y.Z pins a release; --ref latest follows releases)
sudo sinko rollback      # put the previous version back (works offline)
sudo sinko selfcheck     # quick health check of the installation
sudo sinko telemetry     # the optional anonymous counter: on, off, status, payload
sudo sinko setup         # re-create the groups and lists if something was deleted
sudo sinko repair        # the page and the program are of two versions (an update was cut off): install this version again from the copy on the box
sudo pihole setpassword  # change the parent password (or use My box, Change password, in the page: no command line involved)
journalctl -u sinko      # the scheduler's log
```

Problems? [`troubleshooting.md`](troubleshooting.md).

## Removing Sinko

```bash
sudo /opt/sinko/uninstall.sh
```

Removes Sinko, its groups, lists, rules and children's devices from Pi-hole, and the page. A device that also holds a group
of your own in Pi-hole (you had registered it before Sinko, or have since given it one) is **not** deleted: it only loses
Sinko's groups and keeps your groups and its name. A device that holds nothing but Sinko's groups (and Pi-hole's Default
one) was only there for Sinko and is deleted. Pi-hole stays installed (remove it with `sudo pihole uninstall`). Then point
the router's DNS server back to its default.
