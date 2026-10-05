# Privacy · الخصوصية

[English](#english) · [العربية](#arabic)

<a id="english"></a>

## Privacy statement

*This statement applies to Sinko 3.0.*

**In one sentence:** Sinko keeps your children's names and devices, your rules and your settings on your box, and never
sends them to the project or to anyone else. What your family looks up is a different matter, and you should know it
from the start: the box answers a lookup itself when it can, and for any other site it asks an upstream DNS service
(Cloudflare for Families by default), which therefore sees **the names of those sites and your home's internet
address**. The project never receives them. The details are in [The sites your family looks up](#lookups).

Sinko sends nothing about your family to the project. The only message it sends to a server that the project runs is
optional, and it is **off until you say yes**: an anonymous counter that lets the project show how many Sinko boxes are
in use.

*Commands that start with `sudo sinko` are for people who built their own box and can log in to it. A ready-made box
has no login, and nothing in this statement needs one: everything a parent needs is on the parent page (*My box*).*

<a id="lookups"></a>

### The sites your family looks up

Every time a phone asks for a site or an app, it first asks the box "where is this?" (a *lookup*). What happens next:

* The box answers by itself when it can: for the sites and apps you blocked (those lookups go nowhere else), for
  answers it remembered a moment ago, and for the names of devices at home.
* For every other lookup, Pi-hole (the program on the box that does the filtering) asks an **upstream DNS service** and
  passes the answer back. That is how every DNS filter works.
* That service therefore sees **the name of each site that is asked for** and **your home's internet address**, and so
  when someone at home is online. With Pi-hole's default settings it does not see which device asked, because only the
  box asks it, and it does not see what you do on a site: a lookup is only a name.
* On a **new** Pi-hole that Sinko's installer sets up, the upstream is **Cloudflare for Families** (`1.1.1.3` and
  `1.0.0.3`). The installer chooses it without asking, because it also keeps out adult sites and known malware. A
  Pi-hole that was already installed keeps the upstream it had. You can change it in Pi-hole's admin page (`/admin` on
  the box's address, with the parent password; *Settings*, *DNS*).
* These lookups travel as ordinary, unencrypted DNS, as on any home network, so your internet provider can see them too.
* What Cloudflare (or the service you chose) does with them is set by its own privacy policy. Neither Sinko nor the
  project can see it or change it, and **the project never receives these lookups**.
* Pi-hole also keeps a **history of lookups on the box** (which device asked for which site, and when), so that the page
  and Pi-hole's admin page can show what happened. That history stays on the box: Sinko never sends it anywhere. On a
  Pi-hole that Sinko's installer sets up, which includes a ready-made box, it is kept for 30 days. A Pi-hole that was
  already installed keeps its own setting (Pi-hole's default is 91 days); you can change it in Pi-hole's admin page
  (*Settings*, *All settings*, *Database*, `maxDBdays`), or on your own box with
  `sudo pihole-FTL --config database.maxDBdays 30`.

### What Sinko never sends to the project

Sinko itself never sends any of these to the project, or to anyone else:

* the names of your children, and the names and addresses of their devices;
* your rules, timers, bedtime and homework settings;
* the parent password;
* the language you chose or your time zone;
* the history of lookups that Pi-hole keeps on the box. (Pi-hole, not Sinko, passes on the names of sites it cannot
  answer itself, as described above.)

There is no Sinko account, no sign-in on a server, no advertising and no tracking. The parent page does not load
anything from other websites (the fonts are on the box); it links to this project's GitHub page, and only when you
tap the link.

### The optional anonymous counter

**Off until you say yes.** You are asked in one of four places, and you can answer in any of them:

* the installer asks once when you install Sinko yourself and someone is at the keyboard (the suggested answer is no);
* the first-run checklist on the parent page, after you chose your password;
* the parent page, *My box*, *Count this box* (this is also where you change your mind);
* the command `sudo sinko telemetry on` (own install).

If the installer cannot ask (nobody is at the keyboard), nothing is decided: the page asks you later, and nothing is
sent before you answer. A box that was built without a counter address (a copy of Sinko that nobody has set up a counter
for) can never send anything, so none of the four places asks: the installer says nothing about the counter, and the
page shows neither the *Count this box* card nor the checklist question.

**What is sent.** About every 6 hours (the first time about 5 minutes after the box starts, or soon after you say yes
if that comes later), the box sends exactly three things and nothing else:

1. a random code that the box made up for itself. It is not made from anything about you, your family, your devices
   or your network;
2. the Sinko version, for example `3.0.0`;
3. the kind of box: Orange Pi Zero 3, Raspberry Pi, an ordinary PC (x86) or other.

*My box*, *Count this box* lists these three things in plain words. On a box you built yourself, `sudo sinko telemetry
payload` shows the message word for word, and `sudo sinko telemetry reset-id` makes a new code.

**What the counter adds.** The country (Cloudflare works it out from the internet address the message came from,
for example Bahrain, and the address itself is not kept) and when the box was first and last heard from.

**What reaches the counter but is not kept.** Like any message sent over the internet, it arrives with the box's
internet address and a line naming Sinko and its version. The counter does not store, log or use either of them. It
runs on Cloudflare, which handles the connection under its own terms, as any hosting company does.

**What comes back.** The number of boxes online now and the number counted in all. The page shows how many Sinko boxes
are online ("This box is one of N Sinko boxes online."), but only from two boxes up: while yours is the only box
counted, the counter has nothing to compare it with and the page says nothing.

**Where it lives.** In a database at Cloudflare (D1), in the account of whoever runs the counter your box is set up to
use (for official Sinko boxes, the project maintainer), who is the only person who can read it. What the project
publishes is totals only, on the website and in the badges on its GitHub page: boxes online, boxes counted, how many
countries, how the versions and kinds of box divide, and how many times Sinko was downloaded. Never a list of boxes and
never a box's code.

**How long.** A box's record is deleted as soon as you switch the counter off (see the next paragraph), and in any case
180 days after the last message from it; the deletion runs every day. Cloudflare may keep restore points of its
databases for a limited time after a deletion (up to about a month; a feature called Time Travel). Only the person
who runs the counter can use them, and the counter never publishes them.

**How to switch it off, and have the record deleted.** Switching the counter off stops the messages at once, and the box
then asks the counter to delete its record. No password, no command and no message to anyone is needed:

* the parent page: *My box*, *Count this box*, switch it off. This is the way on a ready-made box, and it works the
  same on any box;
* the command `sudo sinko telemetry off` on your own box (and `sudo sinko telemetry status` shows where it stands);
* when you first install, `SINKO_TELEMETRY=0` records the answer "no". That variable is only the answer given at
  install time: it does not undo a "yes" you gave later on the page, so use the page or the command for that.

What happens next: the box sends one request that says "forget this code" and holds nothing but the code, over the same
kind of connection as a message (so it arrives with the box's internet address too, which is not kept). When the counter
confirms, the box deletes its own copy of the code. If the counter cannot be reached (the box is offline, the counter is
down, or it is an old version that does not know the request), the box keeps the code, sends nothing else, and asks
again about every 6 hours until the counter confirms. The 180 days above is the backstop. A box that never sent
a message (you said no first) has nothing to delete.

### What happens when Sinko updates

**Looking for a new version.** About two minutes after the box starts, then about once a day, and whenever you tap
*Check again*, the box asks GitHub (`api.github.com`) which version is the latest. The two minutes count from every start
of the box (or of Sinko on it), so also after a power cut and after an update. If GitHub cannot be reached (the internet
is down, or GitHub is busy), the box asks again after half an hour to an hour, so a box that is offline asks more often
than once a day. A box that was pinned to one version on purpose (`sudo sinko update --ref v3.0.1`, on your own box) does
not ask which version is the latest, and one that follows a development branch asks nothing.

**Updating.** When you update (the page's *Update now*, the automatic update at night, or `sudo sinko update`), the box
asks `api.github.com` once more which version is the latest (a pinned box does not), and downloads the new version from
GitHub: the program file and its checksum, from `github.com`, which hands the file on from one of its own download
servers. Then the update runs Sinko's installer again on the box, and the installer reaches out
twice more. It refreshes the block lists: Pi-hole downloads each list from where it is published, which for Sinko's
twenty or so small lists is `raw.githubusercontent.com`, and for any list you added yourself is wherever that one lives.
And it ends with the same check as `sudo sinko doctor`, which fetches one small public file from the block-list address
(`lists/guard.txt`, on `raw.githubusercontent.com`) to see that the address can be reached. If something the installer
needs is missing on the box, it also asks the operating system's package servers for it; that is rare on a box that
already works.

**Who sees what.** GitHub sees the box's internet address, when it asked and what it asked for, as any website does. The
check and the download carry a line naming Sinko and its version. The list downloads (made by Pi-hole) and the small file
check (made by the box's Python) are ordinary requests that do not name Sinko or its version. GitHub counts downloads.
None of these requests carries anything about your family, and GitHub's own privacy policy applies to what it receives.

When you install Sinko yourself, the installer also downloads the programs it needs from the operating system's package
servers (Debian's and, on an Orange Pi, Armbian's), Pi-hole's own installer from `install.pi-hole.net` if Pi-hole is not
there yet, and Sinko from GitHub; those servers see your internet address and what was asked for, as in any download. A
ready-made box has all of this done already, and Sinko itself then makes only the requests described above (what the
operating system and Pi-hole do by themselves is under "Other things on the box that are not Sinko's"). The installer ends
every run with the same check as `sudo sinko doctor`: a ready-made box therefore makes that small request after every
update, and on your own box you can make it yourself by running the command.

The nightly refresh of the block lists works the same way: Pi-hole downloads every list it has (Sinko's, and any you
added yourself) from where each one is published, and it also refreshes them once a week on its own. Sinko's lists come
from this project's page on GitHub, so a change made to them in the project reaches every box at its next nightly
refresh, without a new release. A list can change what is blocked; it cannot run anything on the box.

### The project website

The website is hosted by GitHub Pages, so GitHub sees visits as any host does. The site itself has no analytics, sets
no cookies and shows no ads. Your browser remembers the language you chose, on your own device only. To show the
numbers of boxes online and downloads, your browser asks the counter for its public numbers when you open the page
and, when there is no counter or the counter gives no download figure, asks GitHub for its public download figures.
Those servers see that request like any other. Your browser keeps the numbers for ten minutes, on your own device, so
that reloading the page does not ask again.

### Other things on the box that are not Sinko's

Sinko is a layer on top of Pi-hole and of the operating system, and these do some things by themselves:

* **Pi-hole** passes the lookups it cannot answer itself on to an upstream DNS service, and keeps its own history of
  lookups on the box: both are described under [The sites your family looks up](#lookups). Pi-hole's own behaviour,
  its update checks and its privacy levels are Pi-hole's, and Pi-hole's documentation describes them. Pi-hole is
  updated separately from Sinko; on a ready-made box that means re-flashing a newer Sinko image (see `docs/updating.md`).
* **Debian**, the operating system, fetches its automatic security updates from Debian's servers (Sinko's installer
  turns this on, and `SINKO_OS_UPDATES=0` skips it; automatic updates that were already set up on the box are left as
  they were). The box has no clock battery, so it asks public time servers for the time (the installer sets up a time
  service when the box has none).
* **The local name** of the box (the one that ends in `.local`) is announced only inside your home network.

### If this statement changes

It changes together with the software, in the open, in this repository's history. If the counter ever needs to send
something new, this statement changes first, with the release that does it.

<a id="arabic"></a>

<div dir="rtl" lang="ar">

## بيان الخصوصية

*ينطبق هذا البيان على سينكو 3.0.*

**باختصار:** يُبقي سينكو أسماء أطفالك وأجهزتهم، وقواعدك وإعداداتك، على صندوقك، ولا يرسلها إلى المشروع ولا إلى أي جهة
أخرى. أما ما تبحث عنه عائلتك من مواقع فأمره مختلف، ومن حقك أن تعرفه من البداية: يجيب الصندوق بنفسه كلما استطاع، وفي ما
عدا ذلك يسأل خدمة DNS خارجية (هي Cloudflare for Families افتراضيًا)، فترى هذه الخدمة **أسماء تلك المواقع وعنوان إنترنت
بيتك**. ولا يصل شيء من ذلك إلى المشروع. وتجد التفاصيل في [المواقع التي تبحث عنها عائلتك](#lookups-ar).

لا يرسل سينكو إلى المشروع شيئًا عن عائلتك. والرسالة الوحيدة التي يرسلها إلى خادم يشغّله المشروع اختيارية، و**تبقى
متوقفة حتى توافق أنت**: عدّاد مجهول يساعد المشروع على معرفة عدد صناديق سينكو قيد الاستخدام.

*الأوامر التي تبدأ بـ `sudo sinko` لمن بنى صندوقه بنفسه ويستطيع الدخول إليه. أما الصندوق الجاهز فلا يُدخَل إليه، ولا
يحتاج شيء في هذا البيان إلى ذلك: كل ما يحتاجه الوالدان موجود في صفحة الوالدين («صندوقي»).*

<a id="lookups-ar"></a>

### المواقع التي تبحث عنها عائلتك

في كل مرة يطلب هاتف موقعًا أو تطبيقًا، يسأل الصندوق أولًا: «أين هذا؟» (وهذا ما نسمّيه *البحث عن عنوان*). وما يجري بعد ذلك:

* يجيب الصندوق بنفسه كلما استطاع: عن المواقع والتطبيقات التي حظرتها (ولا يذهب هذا السؤال إلى أي جهة أخرى)، وعن إجابات
  تذكّرها قبل لحظات، وعن أسماء الأجهزة داخل البيت.
* وفي كل ما عدا ذلك يسأل Pi-hole (البرنامج الذي يتولى التصفية على الصندوق) **خدمة DNS خارجية** ثم يعيد الجواب إلى
  الجهاز. هكذا يعمل أي مرشّح DNS.
* فترى هذه الخدمة **اسم كل موقع يُطلب** و**عنوان الإنترنت الذي لبيتك**، فتعرف بذلك متى يكون أحد في البيت متصلًا. وفي
  إعدادات Pi-hole الافتراضية لا ترى أي جهاز سأل، لأن الصندوق وحده هو الذي يسألها، ولا ترى ما تفعله داخل الموقع، فالسؤال
  اسم فقط.
* في Pi-hole جديد يجهّزه برنامج تثبيت سينكو، تكون الخدمة الخارجية **Cloudflare for Families** (العنوانان `1.1.1.3`
  و`1.0.0.3`). يختارها المثبّت دون أن يسألك، لأنها تحجب أيضًا المواقع الإباحية والبرمجيات الخبيثة المعروفة. أما Pi-hole
  المثبَّت مسبقًا فيُبقي الخدمة التي كان يستعملها. وتستطيع تغييرها من صفحة إدارة Pi-hole (العنوان `/admin` بعد عنوان
  الصندوق، وبكلمة مرور الوالدين: *Settings* ثم *DNS*).
* تنتقل هذه الأسئلة كأي DNS عادي دون تشفير، كما في أي شبكة منزلية، فيراها مزوّد الإنترنت أيضًا.
* وما تفعله Cloudflare (أو الخدمة التي تختارها) بها تحكمه سياسة الخصوصية الخاصة بها. ولا يستطيع سينكو ولا المشروع رؤية ذلك
  أو تغييره، و**لا تصل هذه الأسئلة إلى المشروع أبدًا**.
* ويحتفظ Pi-hole أيضًا بـ**سجل لهذه الطلبات على الصندوق** (أي جهاز سأل عن أي موقع ومتى)، لتعرض الصفحة وصفحة إدارة
  Pi-hole ما جرى. يبقى هذا السجل على الصندوق ولا يرسله سينكو إلى أي مكان. وفي Pi-hole الذي يجهّزه برنامج تثبيت سينكو،
  ومنه الصندوق الجاهز، يُحفظ السجل 30 يومًا. أما Pi-hole المثبَّت مسبقًا فيُبقي إعداده هو (والافتراضي عند Pi-hole هو 91
  يومًا)، وتستطيع تغييره من صفحة إدارة Pi-hole (*Settings* ثم *All settings* ثم *Database* ثم `maxDBdays`)، أو على صندوقك
  الذي بنيته بنفسك بالأمر `sudo pihole-FTL --config database.maxDBdays 30`.

### ما لا يرسله سينكو إلى المشروع أبدًا

لا يرسل سينكو نفسه أيًّا مما يلي إلى المشروع ولا إلى أي جهة أخرى:

* أسماء أطفالك، وأسماء أجهزتهم وعناوينها؛
* القواعد والمؤقتات وإعدادات وقت النوم ووقت الدراسة؛
* كلمة مرور الوالدين؛
* اللغة التي اخترتها أو منطقتك الزمنية؛
* سجل الطلبات الذي يحتفظ به Pi-hole على الصندوق. (والذي يمرّر أسماء المواقع التي لا يستطيع الإجابة عنها بنفسه هو
  Pi-hole لا سينكو، كما شرحنا أعلاه.)

ليس في سينكو حساب، ولا تسجيل دخول على خادم، ولا إعلانات، ولا أدوات تتبّع. وصفحة الوالدين لا تحمّل شيئًا من
مواقع أخرى (فالخطوط محفوظة على الصندوق نفسه)، وفيها رابط إلى صفحة المشروع على GitHub لا يُفتح إلا إذا ضغطت عليه.

### العدّاد المجهول (اختياري)

**متوقف حتى توافق.** يُطرح عليك السؤال في أربعة أماكن، وتستطيع أن تجيب في أيٍّ منها:

* برنامج التثبيت يسألك مرة واحدة إذا ثبّتّ سينكو بنفسك وكان أحد أمام لوحة المفاتيح (والجواب المقترح «لا»)؛
* قائمة الإعداد الأولى في صفحة الوالدين، بعد أن تختار كلمة المرور؛
* صفحة الوالدين: «صندوقي» ثم «احتساب هذا الصندوق» (وهنا أيضًا تغيّر رأيك)؛
* الأمر `sudo sinko telemetry on` (لمن بنى صندوقه بنفسه).

وإن لم يستطع برنامج التثبيت أن يسألك (لأن أحدًا لا يجلس أمام لوحة المفاتيح) فلا يُحسم شيء، وتسألك الصفحة لاحقًا، ولا يُرسَل
أي شيء قبل أن تجيب. والصندوق الذي بُني بلا عنوان للعدّاد (نسخة من سينكو لم يُعدّ لها أحد عدّادًا) لا يستطيع أن يرسل شيئًا
أصلًا، ولذلك لا يسأل أيٌّ من الأماكن الأربعة: لا يذكر برنامج التثبيت العدّاد، ولا تعرض الصفحة بطاقة «احتساب هذا الصندوق»
ولا سؤال قائمة الإعداد.

**ما يُرسَل.** كل 6 ساعات تقريبًا (وأول مرة بعد نحو 5 دقائق من تشغيل الصندوق، أو بعد قولك «نعم» بوقت قصير إن جاء
ذلك لاحقًا) يرسل الصندوق ثلاثة أشياء بالضبط، ولا شيء غيرها:

1. رمز عشوائي صنعه الصندوق لنفسه. لا يُشتق من أي شيء يخصك أو يخص عائلتك أو أجهزتك أو شبكتك؛
2. رقم إصدار سينكو، مثل `3.0.0`؛
3. نوع الصندوق: Orange Pi Zero 3، أو Raspberry Pi، أو حاسوب عادي (x86)، أو نوع آخر.

وتعرض لك «صندوقي» ثم «احتساب هذا الصندوق» هذه الأشياء الثلاثة بكلمات واضحة. وعلى الصندوق الذي بنيته بنفسك يعرض
الأمر `sudo sinko telemetry payload` الرسالة نفسها كما هي حرفيًا، ويصنع الأمر `sudo sinko telemetry reset-id` رمزًا جديدًا.

**ما يضيفه العدّاد.** البلد (تستنتجه Cloudflare من عنوان الإنترنت الذي جاءت منه الرسالة، مثل البحرين، ولا يُحفظ العنوان
نفسه)، ووقت أول رسالة من الصندوق ووقت آخر رسالة.

**ما يصل إلى العدّاد ولا يُحفظ.** كأي رسالة تنتقل عبر الإنترنت، تصل ومعها عنوان إنترنت الصندوق وسطر يذكر اسم سينكو
وإصداره. والعدّاد لا يحفظ أيًّا منهما ولا يسجّله ولا يستعمله. وهو يعمل على Cloudflare، التي تتعامل مع الاتصال وفق
شروطها هي، كما تفعل أي شركة استضافة.

**ما يعود إليك.** عدد الصناديق المتصلة الآن والعدد الكلي المحسوب. وتعرض الصفحة عدد صناديق سينكو المتصلة («هذا الصندوق واحد
من صناديق سينكو المتصلة، وعددها N»)، لكن ابتداءً من صندوقين اثنين فقط: ما دام صندوقك هو الوحيد المحسوب فليس عند العدّاد
ما يقارنه به، ولا تقول الصفحة شيئًا.

**أين يُحفظ.** في قاعدة بيانات على Cloudflare (D1)، ضمن حساب من يشغّل العدّاد الذي أُعدّ صندوقك له (وفي صناديق سينكو
الرسمية هو مشرف المشروع)، وهو الوحيد الذي يستطيع قراءتها. وما ينشره المشروع أرقام مجمّعة فقط، في موقعه وفي الشارات على
صفحته في GitHub: الصناديق المتصلة، والصناديق المحسوبة، وعدد الدول، وكيف تتوزع الإصدارات وأنواع الصناديق، وكم مرة نُزّل
سينكو. لا قائمة بالصناديق أبدًا، ولا رمز أي صندوق.

**مدة الاحتفاظ.** يُحذف سجل الصندوق فور إيقافك العدّاد (انظر الفقرة التالية)، وعلى أي حال بعد 180 يومًا من آخر رسالة
منه، ويجري الحذف كل يوم. وقد تحتفظ Cloudflare بنقاط استعادة لقواعد بياناتها مدة محدودة بعد الحذف (حتى نحو شهر، وهذه
ميزة اسمها Time Travel). ولا يستطيع استعمالها إلا من يشغّل العدّاد، والعدّاد لا ينشرها.

**كيف توقفه ويُحذف سجلك.** إيقاف العدّاد يوقف الرسائل فورًا، ثم يطلب الصندوق من العدّاد حذف سجله. ولا تحتاج إلى كلمة
مرور ولا إلى أمر ولا إلى مراسلة أحد:

* صفحة الوالدين: «صندوقي» ثم «احتساب هذا الصندوق»، وأوقف المفتاح. وهذه هي الطريقة على الصندوق الجاهز، وتصلح على أي صندوق؛
* الأمر `sudo sinko telemetry off` على صندوقك الذي بنيته بنفسك (ويبيّن لك `sudo sinko telemetry status` الحالة)؛
* عند التثبيت لأول مرة، المتغير `SINKO_TELEMETRY=0` يسجّل الجواب «لا». وهذا المتغير هو جواب وقت التثبيت فقط: لا يلغي
  «نعم» قلتها لاحقًا في الصفحة، فاستعمل الصفحة أو الأمر لذلك.

وما يحدث بعد ذلك: يرسل الصندوق طلبًا واحدًا معناه «انسَ هذا الرمز» ولا يحمل غير الرمز، عبر اتصال من النوع نفسه الذي
تُرسَل به الرسالة (فيصل أيضًا ومعه عنوان إنترنت الصندوق، ولا يُحفظ). وحين يؤكد العدّاد ذلك يحذف الصندوق نسخته من الرمز.
وإن تعذّر الوصول إلى العدّاد (الصندوق غير متصل، أو العدّاد متوقف، أو هو إصدار قديم لا يعرف هذا الطلب) فيُبقي
الصندوق الرمز، ولا يرسل شيئًا آخر، ويعيد الطلب كل 6 ساعات تقريبًا حتى يؤكد العدّاد. والـ180 يومًا المذكورة أعلاه هي
شبكة الأمان. أما الصندوق الذي لم يرسل أي رسالة قط (قلتَ «لا» من البداية) فلا شيء عنده ليُحذف.

### ماذا يحدث عند التحديث

**البحث عن إصدار جديد.** بعد دقيقتين تقريبًا من تشغيل الصندوق، ثم مرة كل يوم تقريبًا، وكلما ضغطت «افحص مجددًا»، يسأل
الصندوق GitHub (`api.github.com`) عن أحدث إصدار. والدقيقتان تبدآن من كل تشغيل للصندوق (أو لسينكو عليه)، فتبدآن من
جديد أيضًا بعد انقطاع الكهرباء وبعد كل تحديث. وإن تعذّر الوصول إلى GitHub (لانقطاع الإنترنت أو لانشغاله) أعاد الصندوق السؤال
بعد نصف ساعة إلى ساعة، فالصندوق الذي لا إنترنت عنده يسأل أكثر من مرة في اليوم. أما الصندوق الذي ثُبّت عمدًا على إصدار
واحد (`sudo sinko update --ref v3.0.1` على صندوقك الذي بنيته بنفسك) فلا يسأل عن أحدث إصدار، والذي يتبع فرعًا تطويريًا لا
يسأل عن شيء.

**التحديث.** عندما تحدّث («حدّث الآن» في الصفحة، أو التحديث التلقائي في الليل، أو الأمر `sudo sinko update`) يسأل
الصندوق `api.github.com` مرة أخرى عن أحدث إصدار (إلا الصندوق المثبَّت على إصدار بعينه)، ثم ينزّل الإصدار الجديد من
GitHub: ملف البرنامج وملف التحقق الخاص به، من `github.com`، الذي يحوّل الملف إلى أحد خوادم التنزيل التابعة له. ثم يعيد
التحديث تشغيل برنامج تثبيت سينكو على الصندوق، ويتصل هذا بالإنترنت مرتين أخريين. فهو يحدّث
قوائم الحظر: ينزّل Pi-hole كل قائمة من المكان الذي تُنشر فيه، فقوائم سينكو الصغيرة، وعددها نحو عشرين، من
`raw.githubusercontent.com`، وأي قائمة أضفتها أنت من موضعها هي. ثم يختم بالفحص نفسه الذي يجريه الأمر `sudo sinko doctor`،
فيجلب ملفًا عامًّا صغيرًا من عنوان قوائم الحظر (الملف `lists/guard.txt` على `raw.githubusercontent.com`) ليتأكد من إمكان
الوصول إلى ذلك العنوان. وإن كان ينقص الصندوق شيء يحتاجه برنامج التثبيت فإنه يطلبه أيضًا من خوادم الحزم الخاصة بنظام
التشغيل، وهذا نادر في صندوق يعمل أصلًا.

**من يرى ماذا.** يرى GitHub عنوان إنترنت الصندوق ووقت الطلب وما طُلب، كما يفعل أي موقع. ويحمل طلب الفحص وطلب التنزيل سطرًا
يذكر اسم سينكو وإصداره. أما تنزيلات القوائم (يجريها Pi-hole) وفحص الملف الصغير (تجريه لغة Python على الصندوق) فطلبات
عادية لا تذكر سينكو ولا إصداره. ويحسب GitHub عدد التنزيلات. ولا يحمل أي من هذه الطلبات شيئًا عن عائلتك، وتسري على ما يصل
إلى GitHub سياسة الخصوصية الخاصة به.

وعندما تثبّت سينكو بنفسك، ينزّل برنامج التثبيت أيضًا ما يحتاجه من البرامج من خوادم الحزم الخاصة بنظام التشغيل (خوادم
Debian، وخوادم Armbian إن كنت تستعمل Orange Pi)، ومثبّت Pi-hole نفسه من `install.pi-hole.net` إن لم يكن Pi-hole موجودًا
بعد، وسينكو من GitHub؛ وترى هذه الخوادم عنوان إنترنتك وما طلبتَه، كما في أي تنزيل. والصندوق الجاهز يكون هذا كله قد أُنجز
فيه من قبل، ولا يطلب سينكو نفسه بعد ذلك إلا ما ذُكر أعلاه (أما ما يفعله نظام التشغيل وPi-hole من تلقاء نفسيهما فمذكور في
«أشياء أخرى على الصندوق ليست من سينكو»). ويختم برنامج التثبيت كل تشغيل له بالفحص نفسه الذي يجريه الأمر `sudo sinko
doctor`: فيجري الصندوق الجاهز ذلك الطلب الصغير بعد كل تحديث، وتستطيع على صندوقك الذي بنيته بنفسك أن تجريه بنفسك بتشغيل
الأمر.

وكذلك التحديث الليلي لقوائم الحظر: ينزّل Pi-hole كل قائمة عنده (قوائم سينكو وما أضفته أنت) من المكان الذي تُنشر فيه،
وهو يحدّثها أيضًا مرة في الأسبوع من تلقاء نفسه. وقوائم سينكو تأتي من صفحة هذا المشروع على GitHub، فأي تغيير عليها في
المشروع يصل إلى كل صندوق عند تحديثه الليلي التالي دون إصدار جديد. والقائمة تغيّر ما يُحظر، ولا تستطيع تشغيل أي شيء على
الصندوق.

### موقع المشروع

يستضيف GitHub Pages موقع المشروع، فيرى GitHub الزيارات كما يراها أي مستضيف. ولا يستعمل الموقع نفسه أدوات إحصاء، ولا يضع
ملفات تعريف ارتباط، ولا يعرض إعلانات. ويتذكّر متصفحك اللغة التي اخترتها، على جهازك أنت فقط. ولعرض أرقام الصناديق المتصلة
والتنزيلات، يطلب متصفحك الأرقام العامة من العدّاد عند فتح الصفحة، وإن لم يكن هناك عدّاد أو لم يعطِ العدّاد رقمًا
للتنزيلات فيطلب أرقام التنزيل العامة من GitHub، فترى تلك الخوادم هذا الطلب كأي طلب آخر. ويحتفظ متصفحك بهذه الأرقام عشر
دقائق على جهازك أنت، حتى لا يعيد السؤال كلما أعدت تحميل الصفحة.

### أشياء أخرى على الصندوق ليست من سينكو

سينكو طبقة فوق Pi-hole وفوق نظام التشغيل، وهذان يفعلان أشياء من تلقاء نفسيهما:

* **Pi-hole** يمرّر الطلبات التي لا يستطيع الإجابة عنها بنفسه إلى خدمة DNS خارجية، ويحتفظ بسجله الخاص للطلبات على
  الصندوق: وكلاهما موصوف في [المواقع التي تبحث عنها عائلتك](#lookups-ar). وما يفعله Pi-hole، من فحص للتحديثات ومستويات
  للخصوصية، هو من شأن Pi-hole، وتشرحه وثائقه. ويُحدَّث Pi-hole على حدة بعيدًا عن سينكو؛ وفي الصندوق الجاهز يعني ذلك
  إعادة كتابة البطاقة بصورة أحدث من سينكو (انظر `docs/updating.md`).
* **Debian**، نظام التشغيل، يجلب تحديثاته الأمنية التلقائية من خوادم Debian (وبرنامج تثبيت سينكو يشغّل ذلك، والمتغير
  `SINKO_OS_UPDATES=0` يتخطّاه؛ وما كان من التحديثات التلقائية معدًّا على الصندوق من قبل يُترك كما كان). وليس في الصندوق
  بطارية للساعة، فيسأل خوادم الوقت العامة عن الوقت (ويجهّز برنامج التثبيت خدمة للوقت إن لم تكن في الصندوق خدمة).
* **الاسم المحلي** للصندوق (الذي ينتهي بـ `.local`) لا يُعلَن إلا داخل شبكة بيتك.

### إن تغيّر هذا البيان

يتغير مع البرنامج، علنًا، في تاريخ هذا المستودع. وإن احتاج العدّاد يومًا إلى إرسال شيء جديد، فإن هذا البيان يتغير
أولًا، مع الإصدار الذي يفعل ذلك.

</div>
