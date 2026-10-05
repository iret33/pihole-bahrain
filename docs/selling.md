# Selling ready-made Sinko boxes

Sinko is free software. Anyone may build their own box, and anyone may sell help or hardware. This page is for people who
sell **flashed devices or cards**, including the project maintainer's own shop. It is practical guidance, **not legal
advice**: check the points that apply where you sell.

## 1. The name and the logo

The code is GPL, but "Sinko" and the logo are marks of the project: read [`TRADEMARK.md`](../TRADEMARK.md). Short version:
flashed units sold as "Sinko" need the maintainer's permission (the maintainer's own shop and the resellers named on the
project website have it); your own flashed units must carry your own name and say "based on Sinko". Never put "Pi-hole" in
a product name. The printed card ([`quick-start-card.md`](quick-start-card.md)) follows the same rule: its heading
says "Your Sinko box" only for units that may be sold as Sinko, and carries your own product name otherwise.

## 2. The software you are shipping, and what you owe its authors

A flashed card contains Debian (through Armbian), Pi-hole, Sinko and many other free-software packages. Handing it over
is distributing them, so for each unit:

* **Keep the licence texts on the card.** Sinko's own (`/opt/sinko/LICENSE`, `/opt/sinko/NOTICE`, and
  `/opt/sinko/lists/LICENSE` for the block lists) are put there by the installer, and the font's is at
  `/var/www/html/pb/fonts/OFL.txt`; Debian's are in `/usr/share/common-licenses` and, package by package, under
  `/usr/share/doc/*/copyright`. **Do not delete any of them when you shrink or clean the image**, and look at the card
  before you ship (`ls /opt/sinko`).
* **Offer the source.** For Sinko it is <https://github.com/iret33/sinko> at the tag that is on the card (the box's
  *My box* page and `/opt/sinko/VERSION` say which). For Pi-hole, Debian and Armbian, it is enough to give written,
  durable pointers to their source repositories **and** to offer to supply it on request for at least three years.
  Put this sentence on the leaflet or the product page, with its period (the card template already has it):
  *"This product contains free software, including Sinko (GPL-3.0-or-later), Pi-hole (EUPL-1.2), Armbian and Debian.
  Licences and source code: <your page>, or write to <your contact> and we will send it to anyone who has this product,
  for no more than the cost of copying and posting it. This offer is valid for at least three years from the day you
  received this product, and for as long as we sell or support it."*
* Do not add terms that restrict what the buyer may do with that software (for example "no copying the card").
  A warranty that ends if they change it is fine; forbidding them to change it is not.
* If you modify Sinko, your modified source must be available under the GPL as well.

## 3. What you may claim

Sinko is a **DNS filter**. Say what it does and what it cannot do ("What it cannot do" in the README): a device on mobile
data or a VPN, apps that use fixed addresses, and a child who knows the Wi-Fi password and can change network settings can
all go around it. Do not write "child-proof", "100% safe", "unbypassable", "protects your children online", or anything
that promises more than the README does. Recommend Screen Time (iPhone) or Family Link (Android) next to it.

## 4. Hardware and the shop

* Use the Orange Pi Zero 3 with **1 GB, 2 GB or 4 GB** of RAM. **Not the 1.5 GB variant** (current kernels crash on it).
  2 GB is a comfortable default.
* Sell a **complete kit**: the board, a certified 5 V power supply with the right plug for your market, a high-endurance
  microSD card (16 GB is plenty; Sinko needs a couple of gigabytes, and the space beyond the image's size is not used,
  see [`product-image.md`](product-image.md)), a case with a little cooling, an Ethernet cable.
  A bad power supply or a worn card causes most "it stopped working" calls.
* Electrical safety, radio (the board has Wi-Fi and Bluetooth, which Sinko does not use; the box works on Ethernet),
  import and consumer-warranty rules differ by country (for example CE/UKCA in Europe, GCC conformity marking and TRA
  type approval for radio equipment in Bahrain and the Gulf). The power supply and the board vendor's declarations
  cover part of this; the seller of the finished kit is responsible for the rest.
* Decide and publish your warranty and returns policy, and how a customer gets help (a person who reads
  [`docs/troubleshooting.md`](troubleshooting.md) first, then you). **A ready-made box has no login, for you as well as
  for the customer: you cannot connect to it.** Support works with what the parent can show you: a screenshot of
  *My box*, the router's list of connected devices, and, as the last step, a re-flash (the parent's backup from *My box →
  Backup* brings devices and rules back). Plan your support around that, and say so on the product page. A box whose
  update was cut short by a power failure usually puts itself right within a quarter of an hour (it installs the version
  again from the copy it keeps; [`updating.md`](updating.md), "If the power fails during an update"): tell the customer to
  leave it plugged in and wait before they write to you. The printed card says so.

## 5. Privacy promise to buyers

This is the section sellers copy onto a product page, so every sentence has to stay true. Say this:

*"Sinko keeps your family's names, devices, rules and history on the box and never sends them to us or to anyone else.
Like any DNS filter, the box asks an upstream DNS service (Cloudflare for Families, unless you change it) to find the
address of the sites it does not block, and that service sees those site names and your home's internet address. The only
optional message to us is an anonymous counter (a random code, the version and the kind of box), which is off until you
say yes, and switching it off deletes the box's record."*

In Arabic: *«يحتفظ سينكو بأسماء أفراد العائلة وأجهزتها وقواعدها وسجلّها على الصندوق، ولا يرسلها إلينا ولا إلى أي جهة أخرى.
ومثل أي مرشّح DNS، يسأل الصندوق خدمة DNS خارجية (Cloudflare for Families ما لم تغيّرها) عن عنوان المواقع التي لا يحظرها،
فترى هذه الخدمة أسماء تلك المواقع وعنوان إنترنت بيتكم. والرسالة الوحيدة الاختيارية إلينا هي عدّاد مجهول (رمز عشوائي ورقم
الإصدار ونوع الصندوق)، وهو متوقف حتى توافقوا، وإيقافه يحذف سجل الصندوق.»*

Do **not** write "nothing leaves the box", "no data is sent", or "your children's browsing is private": the box does send
site names to its upstream DNS service, as any DNS filter does. If the unit you ship uses a different upstream (the seller
can set one when building the golden unit), or if you fork Sinko and add anything that sends data, change the text and tell
buyers. The other things the box talks to are listed in [`docs/privacy.md`](privacy.md) (updates and block lists from
GitHub, Debian's security updates, public time servers); do not promise less than that page does.

## 6. Making the units

Follow [`docs/product-image.md`](product-image.md) (golden unit, sealing, imaging, flashing, quality checks) and use
[`docs/quick-start-card.md`](quick-start-card.md) as the printed card in the box.

## 7. Keeping the boxes you sold safe

* **Sinko** updates itself from the page (*My box → Update now*, or automatically at night, if the parent switched that
  on): you do not need to do anything for it, and you do not need to log in.
* **Debian's security updates** are installed automatically. The box never restarts by itself for them, so some only
  take effect when a parent restarts it from *My box*.
* **Pi-hole** is **not** updated on the box. A ready-made box cannot be logged in to and Sinko's updater replaces only
  Sinko, so Pi-hole on a shipped unit stays at the version of the golden unit until the card is re-flashed with a newer
  image. The honest policy is therefore: **rebuild the image for every Sinko release, and whenever Pi-hole publishes a
  security fix**, and offer your customers a re-flash (a newer card, or instructions to re-flash the same one) at a
  period you choose and publish, for example once a year. *My box*, *About* shows which Pi-hole version a box runs, so
  you and the parent can see how old it is. Say on the product page that Pi-hole is updated by re-flashing.
