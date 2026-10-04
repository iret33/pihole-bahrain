# Privacy · الخصوصية

[English](#english) · [العربية](#arabic)

<a id="english"></a>

## Privacy statement

*This statement applies to Sinko 3.0.*

**In one sentence:** Sinko works inside your home. The sites your family visits, your children's names and devices,
and your rules stay on your box, and nobody at the project ever receives them.

There is one optional exception, and it is **off until you say yes**: an anonymous counter that lets the project
show how many Sinko boxes are in use.

### What never leaves your box

Sinko never sends any of these to the project, or to anyone else:

* the sites and apps your family's devices ask for;
* the names of your children, and the names and addresses of their devices;
* your rules, timers, bedtime and homework settings;
* the parent password;
* the language you chose or your time zone.

There is no Sinko account, no sign-in on a server, no advertising and no tracking. The parent page does not load
anything from other websites (the fonts are on the box); it links to this project's GitHub page, and only when you
tap the link.

### The optional anonymous counter

**Off until you say yes.** You are asked in one of three places, and you can answer in any of them:

* the installer asks once when you install (the suggested answer is no);
* the parent page, *My box*, *Count this box*;
* the command `sudo sinko telemetry on`.

If the installer cannot ask (nobody is at the keyboard), nothing is decided: the page asks you later, and nothing is
sent before you answer. A box that was built without a counter address never sends anything, even when the answer is yes.

**What is sent.** About every 6 hours (the first time about 5 minutes after the box starts), the box sends exactly
three things and nothing else:

1. a random code that the box made up for itself. It is not made from anything about you, your family, your devices
   or your network, and `sudo sinko telemetry reset-id` makes a new one;
2. the Sinko version, for example `3.0.0`;
3. the kind of box: Orange Pi Zero 3, Raspberry Pi, an ordinary PC (x86) or other.

To see the message word for word, run `sudo sinko telemetry payload`.

**What the counter adds.** The country (Cloudflare works it out from the internet address the message came from,
for example Bahrain, and the address itself is not kept) and when the box was first and last heard from.

**What reaches the counter but is not kept.** Like any message sent over the internet, it arrives with the box's
internet address and a line naming Sinko and its version. The counter does not store, log or use either of them. It
runs on Cloudflare, which handles the connection under its own terms, as any hosting company does.

**What comes back.** The number of boxes online now and the number counted in all. The page shows it as "You are
one of N Sinko boxes online."

**Where it lives.** In a database at Cloudflare (D1), in the account of whoever runs the counter your box is set up to
use (for official Sinko boxes, the project maintainer), who is the only person who can read it. What the project publishes is totals only, on the website and in the badges on its GitHub page:
boxes online, boxes counted, how many countries, how the versions and kinds of box divide, and how many times Sinko was
downloaded. Never a list of boxes and never a box's code.

**How long.** A box's record is deleted 180 days after the last message from it; the deletion runs every day. Switching
the counter off stops the messages at once, and the old record disappears within 180 days. To have it deleted sooner,
see "Asking for your record to be deleted" below.

**How to switch it off**, any one of these:

* the parent page: *My box*, *Count this box*, switch it off;
* the command `sudo sinko telemetry off` (and `sudo sinko telemetry status` shows where it stands);
* when you first install, `SINKO_TELEMETRY=0` records the answer "no". That variable is only the answer given at
  install time: it does not undo a "yes" you gave later on the page, so use the page or the command for that.

### What happens when Sinko updates

Once a day, and whenever you tap *Check again*, the box asks GitHub (`api.github.com`) which version is the latest.
When you update, the box downloads the new version from GitHub in the same way. GitHub sees the box's internet
address and a line naming Sinko and its version, as it does for any download, and it counts downloads. Sinko sends
GitHub nothing else, and GitHub's own privacy policy applies to what it receives. The nightly refresh of the block
lists works the same way: Pi-hole downloads them from this project's page on GitHub.

### The project website

The website is hosted by GitHub Pages, so GitHub sees visits as any host does. The site itself has no analytics, sets
no cookies and shows no ads. Your browser remembers the language you chose, on your own device only. To show the
numbers of boxes online and downloads, your browser asks for the counter's public numbers when you open the page (or,
when no counter is set up, for GitHub's public download figures), and those servers see that request like any other.

### Other things on the box that are not Sinko's

Sinko is a layer on top of Pi-hole and of the operating system, and these do some things by themselves:

* **Pi-hole** passes your family's lookups on to an upstream DNS service so that the answer can be found. Sinko's
  installer suggests Cloudflare for Families (`1.1.1.3`) for a new Pi-hole; you can change it in Pi-hole's own admin
  page. Pi-hole also keeps its own history of lookups on the box (Sinko's installer keeps 30 days). Pi-hole's own
  behaviour, its update checks and its privacy levels are Pi-hole's, and Pi-hole's documentation describes them.
* **Debian**, the operating system, fetches its automatic security updates from Debian's servers (Sinko's installer
  turns this on, and `SINKO_OS_UPDATES=0` skips it). The box has no clock battery, so it asks public time servers for
  the time.
* **The local name** of the box (the one that ends in `.local`) is announced only inside your home network.

### Asking for your record to be deleted

Run `sudo sinko telemetry payload` and send the `id` it shows to the project maintainer **privately**, never in a public
issue. GitHub's private reporting form works for this today:
<https://github.com/iret33/sinko/security/advisories/new>. Switch the counter off first, or the box will make a new
record with its next message.

### If this statement changes

It changes together with the software, in the open, in this repository's history. If the counter ever needs to send
something new, this statement changes first, with the release that does it.

<a id="arabic"></a>

<div dir="rtl" lang="ar">

## بيان الخصوصية

*ينطبق هذا البيان على سينكو 3.0.*

**باختصار:** سينكو يعمل داخل بيتك. المواقع التي تزورها عائلتك، وأسماء أطفالك وأجهزتهم، والقواعد التي وضعتها،
كلها تبقى على صندوقك، ولا يصل شيء منها إلى أي شخص في المشروع.

هناك استثناء واحد اختياري، و**يبقى متوقفًا حتى توافق أنت**: عدّاد مجهول يساعد المشروع على معرفة عدد صناديق
سينكو قيد الاستخدام.

### ما لا يغادر صندوقك أبدًا

لا يرسل سينكو أيًّا مما يلي إلى المشروع ولا إلى أي جهة أخرى:

* المواقع والتطبيقات التي تطلبها أجهزة العائلة؛
* أسماء أطفالك، وأسماء أجهزتهم وعناوينها؛
* القواعد والمؤقتات وإعدادات وقت النوم ووقت الدراسة؛
* كلمة مرور الوالدين؛
* اللغة التي اخترتها أو منطقتك الزمنية.

ليس في سينكو حساب، ولا تسجيل دخول على خادم، ولا إعلانات، ولا أدوات تتبّع. وصفحة الوالدين لا تحمّل شيئًا من
مواقع أخرى (فالخطوط محفوظة على الصندوق نفسه)، وفيها رابط إلى صفحة المشروع على GitHub لا يُفتح إلا إذا ضغطت عليه.

### العدّاد المجهول (اختياري)

**متوقف حتى توافق.** يُطرح عليك السؤال في ثلاثة أماكن، وتستطيع أن تجيب في أيٍّ منها:

* برنامج التثبيت يسألك مرة واحدة عند التثبيت (والجواب المقترح «لا»)؛
* صفحة الوالدين: «صندوقي» ثم «احتساب هذا الصندوق»؛
* الأمر `sudo sinko telemetry on`.

وإن لم يستطع برنامج التثبيت أن يسألك (لأن أحدًا لا يجلس أمام لوحة المفاتيح) فلا يُحسم شيء، وتسألك الصفحة لاحقًا، ولا يُرسَل
أي شيء قبل أن تجيب. والصندوق الذي بُني بلا عنوان للعدّاد لا يرسل شيئًا حتى لو كان جوابك «نعم».

**ما يُرسَل.** كل 6 ساعات تقريبًا (وأول مرة بعد نحو 5 دقائق من تشغيل الصندوق) يرسل الصندوق ثلاثة أشياء بالضبط،
ولا شيء غيرها:

1. رمز عشوائي صنعه الصندوق لنفسه. لا يُشتق من أي شيء يخصك أو يخص عائلتك أو أجهزتك أو شبكتك، ويمكنك تجديده بالأمر
   `sudo sinko telemetry reset-id`؛
2. رقم إصدار سينكو، مثل `3.0.0`؛
3. نوع الصندوق: Orange Pi Zero 3، أو Raspberry Pi، أو حاسوب عادي (x86)، أو نوع آخر.

وإن أردت أن ترى الرسالة نفسها كما هي حرفيًا فاكتب `sudo sinko telemetry payload`.

**ما يضيفه العدّاد.** البلد (تستنتجه Cloudflare من عنوان الإنترنت الذي جاءت منه الرسالة، مثل البحرين، ولا يُحفظ العنوان
نفسه)، ووقت أول رسالة من الصندوق ووقت آخر رسالة.

**ما يصل إلى العدّاد ولا يُحفظ.** كأي رسالة تنتقل عبر الإنترنت، تصل ومعها عنوان إنترنت الصندوق وسطر يذكر اسم سينكو
وإصداره. والعدّاد لا يحفظ أيًّا منهما ولا يسجّله ولا يستعمله. وهو يعمل على Cloudflare، التي تتعامل مع الاتصال وفق
شروطها هي، كما تفعل أي شركة استضافة.

**ما يعود إليك.** عدد الصناديق المتصلة الآن والعدد الكلي المحسوب. وتعرضه الصفحة هكذا: «أنت ضمن N من صناديق سينكو
المتصلة».

**أين يُحفظ.** في قاعدة بيانات على Cloudflare (D1)، ضمن حساب من يشغّل العدّاد الذي أُعدّ صندوقك له (وفي صناديق سينكو
الرسمية هو مشرف المشروع)، وهو الوحيد الذي يستطيع قراءتها. وما ينشره
المشروع أرقام مجمّعة فقط، في موقعه وفي الشارات على صفحته في GitHub: الصناديق المتصلة، والصناديق المحسوبة، وعدد الدول،
وكيف تتوزع الإصدارات وأنواع الصناديق، وكم مرة نُزّل سينكو. لا قائمة بالصناديق أبدًا، ولا رمز أي صندوق.

**كم تُحفظ.** يُحذف سجل الصندوق بعد 180 يومًا من آخر رسالة منه، ويجري الحذف كل يوم. وإيقاف العدّاد يوقف الرسائل فورًا،
ويختفي السجل القديم خلال 180 يومًا. وإن أردت حذفه قبل ذلك فانظر «طلب حذف سجلك» في الأسفل.

**كيف توقفه.** بأي طريقة من هذه:

* صفحة الوالدين: «صندوقي» ثم «احتساب هذا الصندوق»، وأوقف المفتاح؛
* الأمر `sudo sinko telemetry off` (ويبيّن لك `sudo sinko telemetry status` الحالة)؛
* عند التثبيت لأول مرة، المتغير `SINKO_TELEMETRY=0` يسجّل الجواب «لا». وهذا المتغير هو جواب وقت التثبيت فقط: لا يلغي
  «نعم» قلتها لاحقًا في الصفحة، فاستعمل الصفحة أو الأمر لذلك.

### ماذا يحدث عند التحديث

مرة كل يوم، وكلما ضغطت «افحص مجددًا»، يسأل الصندوق GitHub (`api.github.com`) عن أحدث إصدار. وعندما تحدّث، ينزّل
الصندوق الإصدار الجديد من GitHub بالطريقة نفسها. يرى GitHub عنوان إنترنت الصندوق وسطرًا يذكر اسم سينكو وإصداره، كما
يحدث مع أي تنزيل، ويحسب عدد التنزيلات. ولا يرسل سينكو إلى GitHub شيئًا غير ذلك، وتسري على ما يصل إلى GitHub سياسة
الخصوصية الخاصة به. وكذلك التحديث الليلي لقوائم الحظر: ينزّلها Pi-hole من صفحة هذا المشروع على GitHub.

### موقع المشروع

يستضيف GitHub Pages موقع المشروع، فيرى GitHub الزيارات كما يراها أي مستضيف. ولا يستعمل الموقع نفسه أدوات إحصاء، ولا يضع
ملفات تعريف ارتباط، ولا يعرض إعلانات. ويتذكّر متصفحك اللغة التي اخترتها، على جهازك أنت فقط. ولعرض أرقام الصناديق المتصلة
والتنزيلات، يطلب متصفحك الأرقام العامة من العدّاد عند فتح الصفحة (أو، إن لم يكن هناك عدّاد، أرقام التنزيل العامة من
GitHub)، فترى تلك الخوادم هذا الطلب كأي طلب آخر.

### أشياء أخرى على الصندوق ليست من سينكو

سينكو طبقة فوق Pi-hole وفوق نظام التشغيل، وهذان يفعلان أشياء من تلقاء نفسيهما:

* **Pi-hole** يمرّر طلبات عائلتك إلى خدمة DNS خارجية ليجد العنوان المطلوب. وبرنامج تثبيت سينكو يقترح Cloudflare for Families
  (`1.1.1.3`) لأي Pi-hole جديد، ويمكنك تغييرها من صفحة إدارة Pi-hole نفسه. كما يحتفظ Pi-hole بسجله الخاص للطلبات على
  الصندوق (وبرنامج تثبيت سينكو يُبقيه 30 يومًا). وما يفعله Pi-hole، من فحص للتحديثات ومستويات للخصوصية، هو من شأن Pi-hole،
  وتشرحه وثائقه.
* **Debian**، نظام التشغيل، يجلب تحديثاته الأمنية التلقائية من خوادم Debian (وبرنامج تثبيت سينكو يشغّل ذلك، والمتغير
  `SINKO_OS_UPDATES=0` يتخطّاه). وليس في الصندوق بطارية للساعة، فيسأل خوادم الوقت العامة عن الوقت.
* **الاسم المحلي** للصندوق (الذي ينتهي بـ `.local`) لا يُعلَن إلا داخل شبكة بيتك.

### طلب حذف سجلك

اكتب `sudo sinko telemetry payload` وأرسل الرمز (`id`) الذي يظهر إلى مشرف المشروع **بشكل خاص**، وليس في بلاغ علني على
GitHub. ونموذج البلاغ الخاص على GitHub يصلح لهذا الغرض حاليًا: <https://github.com/iret33/sinko/security/advisories/new>.
أوقف العدّاد أولًا، وإلا أنشأ الصندوق سجلًا جديدًا مع رسالته التالية.

### إن تغيّر هذا البيان

يتغير مع البرنامج، علنًا، في تاريخ هذا المستودع. وإن احتاج العدّاد يومًا إلى إرسال شيء جديد، فإن هذا البيان يتغير
أولًا، مع الإصدار الذي يفعل ذلك.

</div>
