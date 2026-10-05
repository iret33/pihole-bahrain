/* pb-box.js: the "My box" sheet on the parent page.
 *
 *   updates      the update card (current version, "Update now", "Check again", progress that survives the box restarting its own
 *                services, the automatic-at-night switch)
 *   box health   filtering, uptime, temperature, memory, last device seen, the box's clock (all from the Pi-hole API)
 *   addresses    the names and numbers this page can be reached at, with copy buttons
 *   password     change the parent password
 *   backup       download and restore (the Teleporter archive; a restore only touches children's devices, apps and rules)
 *   power        restart and shut down
 *   counter      the optional anonymous counter
 *   about        version, licence, links
 *
 * The page never decides anything about updates or power: it writes a request marker into the shared state and the scheduler on
 * the box acts on it (docs/maintainers/architecture.md, "Request protocol"). Everything shown from the network is set with
 * textContent or attributes, never innerHTML. ES5 on purpose, like the rest of the page.
 *
 * The pure logic is exported as PBBox.pure and tested under node (tests/box.test.js); the strings as PBBox.strings.
 */
(function (root) {
  'use strict';

  var C = root.PBCore || (typeof require === 'function' ? require('./pb-core.js') : null);

  // ------------------------------------------------------------------ strings
  // One literal, both languages: tests/test_copy.py reads it. Plain words only: the words DNS, query, request, upstream, gravity,
  // resolver, packet, latency, cache and the name of the DNS program may appear only where the copy test lists them (boxAboutTrademark).
  var BOX_STR = {
    en: {
      boxButton: 'My box', boxTitle: 'My box',
      boxNeedsSetup: 'Sinko is not fully set up on this box yet, so updates and the counter are not available. Wait a few minutes and reload this page.',
      // updates
      boxUpdTitle: 'Updates', boxInstalled: 'Sinko {v} is installed.',
      boxUpToDate: 'Up to date. Checked {t}.', boxNeverChecked: 'Not checked yet.', boxJustNow: 'just now',
      boxAvailable: 'Sinko {v} is available', boxNotes: 'See what is new',
      boxUpdateNow: 'Update now', boxCheckAgain: 'Check again', boxChecking: 'Checking…',
      boxCheckOffline: 'The box could not reach the internet to check. Try again later.',
      boxCheckSlow: 'The box has not answered yet. Try again in a minute.',
      boxStarting: 'Starting the update…', boxRunning: 'Updating to Sinko {v}…', boxRunningNoV: 'Updating Sinko…',
      boxRunningNote: 'This takes a few minutes. Keep the box plugged in. The internet at home may pause for a minute, and this page may go quiet while the box restarts some of its parts.',
      boxLateStart: 'The box has not started yet. It will begin as soon as it can.',
      boxUpdateStuck: 'The box did not start the update. Unplug the box, plug it back in, wait two minutes and try again. If it keeps happening, ask whoever set up the box.',
      boxWaitingBox: 'The box is restarting. Waiting for it to come back…',
      boxLongWait: 'The box has not answered for a while. Check that it is plugged in and on your Wi‑Fi, then reload this page.',
      boxSignedOut: 'The box restarted and signed you out. Sign in again to see how the update went.',
      boxSignInAgain: 'Sign in',
      boxStalled: 'The update has been running for a long time and may have stopped. If the rules still work, press Try again, or come back later.',
      boxUpdated: 'Updated to Sinko {v}.', boxReloading: 'Reloading this page…', boxUpdatedToast: 'Sinko was updated to {v}',
      boxFailedTitle: 'The update did not work', boxFailedBack: 'The previous version is back and working.',
      boxFailedNotBack: 'The previous version could not be put back, so the box may not work properly. Unplug the box, plug it back in and wait two minutes. If the rules or bedtime still do not work, ask whoever set up the box.',
      boxFailedUnknown: 'Sinko could not check whether the box still works as before. If the rules or bedtime stop working, unplug the box, plug it back in and wait two minutes. If that does not help, ask whoever set up the box.',
      boxDetailsTitle: 'Details for whoever helps you', boxTryAgain: 'Try again',
      boxAuto: 'Update automatically at night', boxAutoNote: 'Between 3 and 5 in the morning by the box’s clock. The internet at home may pause for a minute.',
      boxAutoOn: 'Automatic updates are on', boxAutoOff: 'Automatic updates are off',
      // health
      boxHealthTitle: 'How the box is doing', boxHealthNone: 'The box could not be read right now.', boxNotAvailable: 'Not available',
      boxFilterLabel: 'Filtering', boxFilterOn: 'On', boxFilterOff: 'Switched off', boxFilterUnknown: 'Unknown',
      boxFilterOffNote: 'Filtering is switched off, so nothing is blocked right now. Turn it back on in Advanced settings.',
      boxUptimeLabel: 'Running for',
      boxTempLabel: 'Temperature', boxTempCool: 'Cool, {n}', boxTempWarm: 'Warm, {n}', boxTempHot: 'Hot, {n}',
      boxTempHotNote: 'Move the box somewhere cooler and open.', boxTempNone: 'Not available on this box.',
      boxMemLabel: 'Memory', boxMemOk: 'Plenty free ({p} in use)', boxMemBusy: 'Getting full ({p} in use)', boxMemFull: 'Almost full ({p} in use)',
      boxLastSeenLabel: 'Last device seen', boxLastSeenValue: '{n}, {t}', boxLastSeenNone: 'No device yet',
      boxClockLabel: 'Box clock', boxClockOk: 'Agrees with this phone.',
      boxClockOff: 'This phone and the box differ by about {t}. Bedtime and timers follow the box.',
      boxClockZone: 'Bedtime follows the box’s own time zone, which this box did not tell the page. If bedtime starts at the wrong hour, the box’s time zone is probably not yours.',
      boxClockZoneKnown: 'Bedtime and timers follow the box’s time zone: {z}.',
      boxClockZoneDiffers: 'Your phone is set to a different time zone, so its clock shows a different hour.',
      boxClockZoneUtc: 'The box is set to universal time (UTC), not to your local time. Bedtime and timers use the time the box shows above, so the hours you set follow that clock, not your own.',
      boxHealthDown: 'The box is not answering right now. These readings come back when it does.',
      // addresses
      boxNetTitle: 'Addresses', boxNetHint: 'These open this page from any phone or tablet on your Wi‑Fi.',
      boxNetIp: 'Address for your router', boxNetIpNote: 'The number to type in your router’s settings. See “Setup help” on the main page.',
      boxNetName: 'Name for this page', boxNetNameNote: 'Works on devices that already use the box.',
      boxNetLocal: 'Name for phones and computers', boxNetLocalNote: 'Works on most of them. If it does not open, use the number address.',
      boxNetNone: 'The box could not tell this page its address. Look for the box in your router’s list of connected devices.',
      boxCopy: 'Copy', boxCopyAria: 'Copy {what}', boxCopied: 'Copied', boxCopyFail: 'Could not copy. Select it and copy by hand.',
      // password
      boxPwTitle: 'Parent password', boxPwHint: 'Choose a new password for this page. You will be signed out on every phone and tablet.',
      boxPwCurrent: 'Current password', boxPwNew: 'New password', boxPwRule: 'At least 8 characters.', boxPwConfirm: 'New password again',
      boxPwCode: '6-digit code from your authenticator app', boxPwNeedCode: 'Enter the 6-digit code too.',
      boxPwChange: 'Change password', boxPwChanging: 'Changing…',
      boxPwNeedCurrent: 'Type your current password first.', boxPwWrong: 'The current password is not right.', boxPwShort: 'The new password needs at least 8 characters.',
      boxPwMismatch: 'The two new passwords are not the same.', boxPwSame: 'Choose a password that is different from the current one.',
      boxPwTooMany: 'Too many open sessions. Sign out on another device or wait 30 minutes.',
      boxPwFailed: 'The password was not changed: {e}', loginPwChanged: 'Your password was changed. Sign in with the new one.',
      // backup and restore
      boxBackupTitle: 'Backup and restore',
      boxBackupHint: 'A backup keeps your children’s devices, apps and rules.',
      boxBackupPrivate: 'The file also holds the parent password. Keep it private and never share it.',
      boxBackupDownload: 'Download backup', boxBackupBusy: 'Preparing…', boxBackupDone: 'Backup downloaded. Keep the file private.',
      boxBackupFail: 'The backup could not be made: {e}',
      boxRestoreTitle: 'Restore from a backup',
      boxRestoreHint: 'Choose a backup file you downloaded earlier. Devices, apps and rules come back as they were. The password and the box’s settings stay as they are.',
      boxRestorePick: 'Choose file', boxRestoreNone: 'No file chosen', boxRestoreButton: 'Restore', boxRestoreBusy: 'Restoring…',
      boxRestoreAsk: 'Restore from “{f}”? The devices, apps and rules on this box are replaced by the ones in the file.',
      boxRestoreGravity: 'Restored. Now the box is rebuilding its block lists. This takes a few minutes: please keep this page open.',
      boxRestoreDone: 'Restored. Devices, apps and rules are back as in the file, and filtering is ready.',
      boxRestorePartial: 'Restored, but the box could not confirm that its block lists are rebuilt. Filtering for the apps and sites you blocked may not work until the box refreshes them, usually overnight.',
      boxRestoreBad: 'That file is not a Sinko backup. Nothing was changed.',
      boxRestoreFail: 'The file could not be restored: {e}',
      // restart and shut down
      boxPowerTitle: 'Restart or shut down',
      boxPowerHint: 'The internet stops for everyone at home, including the children, while the box is off or restarting.',
      boxRestart: 'Restart', boxShutdown: 'Shut down',
      boxRestartAsk: 'Restart the box? The internet stops for the children, and for everyone at home, for about a minute.',
      boxShutdownAsk: 'Shut down the box? The internet stops for the children, and for everyone at home, until you unplug the box and plug it back in.',
      boxPowerWaitUpdate: 'An update is running. Restart and Shut down wait until it has finished, so the box is never switched off in the middle of it.',
      boxPowerDropped: 'The box was updating, so it did not restart or shut down. Try again when the update has finished.',
      boxPowerSent: 'Asking the box…', boxPowerRestarting: 'The box is restarting. This page will ask you to sign in when it is back.',
      boxPowerOff: 'The box is shutting down. You can unplug it in about half a minute. To start it again, unplug it and plug it back in.',
      boxPowerStuck: 'The box did not react. If it is not restarting, unplug it, wait ten seconds and plug it back in.',
      boxBackNote: 'The box restarted. Sign in again.',
      // counter
      boxCounterTitle: 'Count this box',
      boxCounterSwitch: 'Count this box in the anonymous Sinko counter',
      boxCounterLead: 'Optional. It lets the project show how many Sinko boxes are in use. It stays off until you switch it on.',
      boxCounterSentTitle: 'What is sent, every few hours:',
      boxCounterSent1: 'A random code made on this box. It is not made from anything about you, your family or your devices.',
      boxCounterSent2: 'The Sinko version, for example 3.0.0.',
      boxCounterSent3: 'The kind of box, for example Orange Pi Zero 3.',
      boxCounterKept: 'The counter stores those three things, the country the message comes from (for example Bahrain), and when it first and last heard from this box. It does not store the internet address.',
      boxCounterForget: 'If you switch it off, the box asks the counter service to forget this box and deletes its random code.',
      boxCounterNotTitle: 'What is never sent:',
      boxCounterNot: 'Websites, apps, names of children or devices, the addresses of your devices, rules, passwords, language or time zone.',
      // Said only from two boxes up (counterOnlineKey): a box that is the only one counted so far has nothing to compare itself with.
      boxCounterOnline: 'This box is one of {n} Sinko boxes online.', boxCounterOnlineTwo: 'This box and one other Sinko box are online.',
      boxCounterOnToast: 'The counter is on. Thank you.', boxCounterOffToast: 'The counter is off. The box will ask the counter service to forget it.',
      // about
      boxAboutTitle: 'About', boxAboutVersion: 'Sinko {v}', boxAboutPihole: 'Pi-hole {v}', boxAboutLicence: 'Free software, licensed GPL-3.0-or-later.',
      boxAboutTrademark: 'Pi-hole is a trademark of Pi-hole LLC. Sinko is independent software that works with it.',
      boxLinkHome: 'Project page', boxLinkIssues: 'Report a problem', boxLinkReleases: 'All versions', boxLinkPrivacy: 'Privacy',
      boxLinkLicense: 'Licence', boxLinkSupport: 'Get help', boxLinkBuy: 'Buy a ready-made box'
    },
    ar: {
      boxButton: 'صندوقي', boxTitle: 'صندوقي',
      boxNeedsSetup: 'إعداد سينكو لم يكتمل على هذا الصندوق بعد، لذلك لا تتوفر التحديثات والعدّاد. انتظر بضع دقائق ثم أعد تحميل الصفحة.',
      boxUpdTitle: 'التحديثات', boxInstalled: 'الإصدار المثبّت من سينكو: {v}.',
      boxUpToDate: 'الإصدار محدّث. آخر فحص {t}.', boxNeverChecked: 'لم يُفحص بعد.', boxJustNow: 'الآن',
      boxAvailable: 'الإصدار {v} من سينكو متاح', boxNotes: 'اطّلع على الجديد',
      boxUpdateNow: 'حدّث الآن', boxCheckAgain: 'افحص مجددًا', boxChecking: 'جارٍ الفحص…',
      boxCheckOffline: 'تعذّر على الصندوق الوصول إلى الإنترنت للفحص. حاول لاحقًا.',
      boxCheckSlow: 'لم يستجب الصندوق بعد. حاول مجددًا بعد دقيقة.',
      boxStarting: 'جارٍ بدء التحديث…', boxRunning: 'جارٍ التحديث إلى سينكو {v}…', boxRunningNoV: 'جارٍ تحديث سينكو…',
      boxRunningNote: 'يستغرق هذا بضع دقائق. أبقِ الصندوق موصولًا بالكهرباء. قد يتوقف الإنترنت في البيت لدقيقة، وقد تصمت هذه الصفحة قليلًا أثناء إعادة تشغيل الصندوق لبعض أجزائه.',
      boxLateStart: 'لم يبدأ الصندوق بعد. سيبدأ حال استطاعته.',
      boxUpdateStuck: 'لم يبدأ الصندوق التحديث. افصل الصندوق عن الكهرباء ثم أعد توصيله وانتظر دقيقتين وحاول مجددًا. إذا تكرر ذلك فاسأل من أعدّ الصندوق.',
      boxWaitingBox: 'الصندوق يعيد التشغيل. بانتظار عودته…',
      boxLongWait: 'لم يستجب الصندوق منذ فترة. تأكد أنه موصول بالكهرباء وبشبكة Wi‑Fi، ثم أعد تحميل الصفحة.',
      boxSignedOut: 'أعاد الصندوق التشغيل وسُجّل خروجك. سجّل الدخول مجددًا لمعرفة نتيجة التحديث.',
      boxSignInAgain: 'تسجيل الدخول',
      boxStalled: 'مضى وقت طويل على التحديث وربما توقف. إذا كانت القواعد ما زالت تعمل، فاضغط «حاول مجددًا» أو عد لاحقًا.',
      boxUpdated: 'تم التحديث إلى سينكو {v}.', boxReloading: 'جارٍ إعادة تحميل الصفحة…', boxUpdatedToast: 'تم تحديث سينكو إلى {v}',
      boxFailedTitle: 'لم ينجح التحديث', boxFailedBack: 'عاد الإصدار السابق وهو يعمل.',
      boxFailedNotBack: 'تعذّرت إعادة الإصدار السابق، لذلك قد لا يعمل الصندوق كما ينبغي. افصل الصندوق عن الكهرباء ثم أعد توصيله وانتظر دقيقتين. إذا ظلت القواعد أو وقت النوم لا تعمل، فاسأل من أعدّ الصندوق.',
      boxFailedUnknown: 'لم يستطع سينكو التحقق من أن الصندوق ما زال يعمل كما كان. إذا توقفت القواعد أو وقت النوم عن العمل، فافصل الصندوق عن الكهرباء ثم أعد توصيله وانتظر دقيقتين. وإن لم يفد ذلك فاسأل من أعدّ الصندوق.',
      boxDetailsTitle: 'تفاصيل لمن يساعدك', boxTryAgain: 'حاول مجددًا',
      boxAuto: 'التحديث التلقائي ليلًا', boxAutoNote: 'بين الساعة 3 و5 فجرًا بحسب ساعة الصندوق. قد يتوقف الإنترنت في البيت لدقيقة.',
      boxAutoOn: 'التحديث التلقائي مفعّل', boxAutoOff: 'التحديث التلقائي متوقف',
      boxHealthTitle: 'حالة الصندوق', boxHealthNone: 'تعذّرت قراءة حالة الصندوق الآن.', boxNotAvailable: 'غير متاح',
      boxFilterLabel: 'التصفية', boxFilterOn: 'تعمل', boxFilterOff: 'متوقفة', boxFilterUnknown: 'غير معروفة',
      boxFilterOffNote: 'التصفية متوقفة، لذلك لا يُحظر أي شيء الآن. شغّلها من «إعدادات متقدمة».',
      boxUptimeLabel: 'يعمل منذ',
      boxTempLabel: 'الحرارة', boxTempCool: 'معتدلة، {n}', boxTempWarm: 'دافئة، {n}', boxTempHot: 'مرتفعة، {n}',
      boxTempHotNote: 'انقل الصندوق إلى مكان أبرد ومفتوح.', boxTempNone: 'غير متاحة على هذا الصندوق.',
      boxMemLabel: 'الذاكرة', boxMemOk: 'متوفرة بكثرة (المستخدم {p})', boxMemBusy: 'تمتلئ تدريجيًا (المستخدم {p})', boxMemFull: 'شبه ممتلئة (المستخدم {p})',
      boxLastSeenLabel: 'آخر جهاز ظهر', boxLastSeenValue: '{n}، {t}', boxLastSeenNone: 'لم يظهر أي جهاز بعد',
      boxClockLabel: 'ساعة الصندوق', boxClockOk: 'متفقة مع ساعة هذا الهاتف.',
      boxClockOff: 'يوجد فرق نحو {t} بين هذا الهاتف والصندوق. وقت النوم والمؤقتات يتبعان ساعة الصندوق.',
      boxClockZone: 'يتبع وقت النوم المنطقة الزمنية الخاصة بالصندوق، ولم يخبر هذا الصندوق الصفحة بها. إذا بدأ وقت النوم في ساعة خاطئة، فالأرجح أن المنطقة الزمنية للصندوق ليست منطقتك.',
      boxClockZoneKnown: 'يتبع وقت النوم والمؤقتات المنطقة الزمنية للصندوق: {z}.',
      boxClockZoneDiffers: 'منطقة هاتفك الزمنية مختلفة، لذلك تعرض ساعته وقتًا مختلفًا.',
      boxClockZoneUtc: 'الصندوق مضبوط على التوقيت العالمي (UTC) وليس على وقتك المحلي. يستخدم وقت النوم والمؤقتات الوقت الذي يعرضه الصندوق أعلاه، فالساعات التي تحددها تتبع تلك الساعة وليس ساعتك.',
      boxHealthDown: 'الصندوق لا يستجيب الآن. تعود هذه القراءات حين يستجيب.',
      boxNetTitle: 'العناوين', boxNetHint: 'تفتح هذه العناوين هذه الصفحة من أي هاتف أو جهاز لوحي على شبكة Wi‑Fi.',
      boxNetIp: 'العنوان الرقمي للراوتر', boxNetIpNote: 'الرقم الذي تكتبه في إعدادات الراوتر. راجع «مساعدة في الإعداد» في الصفحة الرئيسية.',
      boxNetName: 'اسم لهذه الصفحة', boxNetNameNote: 'يعمل على الأجهزة التي تستخدم الصندوق بالفعل.',
      boxNetLocal: 'اسم للهواتف والحواسيب', boxNetLocalNote: 'يعمل على معظمها. إذا لم تُفتح الصفحة فاستخدم العنوان الرقمي.',
      boxNetNone: 'لم يستطع الصندوق إخبار هذه الصفحة بعنوانه. ابحث عن الصندوق في قائمة الأجهزة المتصلة في الراوتر.',
      boxCopy: 'نسخ', boxCopyAria: 'نسخ {what}', boxCopied: 'تم النسخ', boxCopyFail: 'تعذّر النسخ. حدّده وانسخه يدويًا.',
      boxPwTitle: 'كلمة مرور الوالدين', boxPwHint: 'اختر كلمة مرور جديدة لهذه الصفحة. سيتم تسجيل خروجك من كل هاتف وجهاز لوحي.',
      boxPwCurrent: 'كلمة المرور الحالية', boxPwNew: 'كلمة المرور الجديدة', boxPwRule: '8 أحرف على الأقل.', boxPwConfirm: 'أعد كتابة كلمة المرور الجديدة',
      boxPwCode: 'الرمز المكوّن من 6 أرقام من تطبيق المصادقة', boxPwNeedCode: 'أدخل الرمز المكوّن من 6 أرقام أيضًا.',
      boxPwChange: 'تغيير كلمة المرور', boxPwChanging: 'جارٍ التغيير…',
      boxPwNeedCurrent: 'اكتب كلمة المرور الحالية أولًا.', boxPwWrong: 'كلمة المرور الحالية غير صحيحة.', boxPwShort: 'يجب أن تتكوّن كلمة المرور الجديدة من 8 أحرف على الأقل.',
      boxPwMismatch: 'كلمتا المرور الجديدتان غير متطابقتين.', boxPwSame: 'اختر كلمة مرور تختلف عن الحالية.',
      boxPwTooMany: 'عدد الجلسات المفتوحة كبير. سجّل الخروج من جهاز آخر أو انتظر 30 دقيقة.',
      boxPwFailed: 'لم يتم تغيير كلمة المرور: {e}', loginPwChanged: 'تم تغيير كلمة المرور. سجّل الدخول بالكلمة الجديدة.',
      boxBackupTitle: 'النسخ الاحتياطي والاستعادة',
      boxBackupHint: 'يحفظ النسخ الاحتياطي أجهزة الأطفال والتطبيقات والقواعد.',
      boxBackupPrivate: 'يحتوي الملف أيضًا على كلمة مرور الوالدين. احتفظ به لنفسك ولا تشاركه مع أحد.',
      boxBackupDownload: 'تنزيل نسخة احتياطية', boxBackupBusy: 'جارٍ التجهيز…', boxBackupDone: 'تم تنزيل النسخة الاحتياطية. احتفظ بالملف لنفسك.',
      boxBackupFail: 'تعذّر إنشاء النسخة الاحتياطية: {e}',
      boxRestoreTitle: 'الاستعادة من نسخة احتياطية',
      boxRestoreHint: 'اختر ملف نسخة احتياطية نزّلته سابقًا. تعود الأجهزة والتطبيقات والقواعد كما كانت. وتبقى كلمة المرور وإعدادات الصندوق كما هي.',
      boxRestorePick: 'اختيار ملف', boxRestoreNone: 'لم يُختر ملف', boxRestoreButton: 'استعادة', boxRestoreBusy: 'جارٍ الاستعادة…',
      boxRestoreAsk: 'الاستعادة من «{f}»؟ سيتم استبدال الأجهزة والتطبيقات والقواعد الموجودة على هذا الصندوق بما في الملف.',
      boxRestoreGravity: 'تمت الاستعادة. يعيد الصندوق الآن بناء قوائم الحظر، ويستغرق ذلك بضع دقائق: أبقِ هذه الصفحة مفتوحة.',
      boxRestoreDone: 'تمت الاستعادة. عادت الأجهزة والتطبيقات والقواعد كما في الملف، والتصفية جاهزة.',
      boxRestorePartial: 'تمت الاستعادة، لكن الصندوق لم يستطع التأكد من إعادة بناء قوائم الحظر. قد لا تعمل التصفية للتطبيقات والمواقع التي حظرتها إلى أن يحدّث الصندوق القوائم، وغالبًا خلال الليل.',
      boxRestoreBad: 'هذا الملف ليس نسخة احتياطية من سينكو. لم يتم تغيير أي شيء.',
      boxRestoreFail: 'تعذّرت الاستعادة: {e}',
      boxPowerTitle: 'إعادة التشغيل أو الإيقاف',
      boxPowerHint: 'يتوقف الإنترنت عن الجميع في البيت، ومنهم الأطفال، أثناء إيقاف الصندوق أو إعادة تشغيله.',
      boxRestart: 'إعادة التشغيل', boxShutdown: 'إيقاف التشغيل',
      boxRestartAsk: 'إعادة تشغيل الصندوق؟ سيتوقف الإنترنت عن الأطفال، وعن الجميع في البيت، لنحو دقيقة.',
      boxShutdownAsk: 'إيقاف تشغيل الصندوق؟ سيتوقف الإنترنت عن الأطفال، وعن الجميع في البيت، إلى أن تفصل الصندوق عن الكهرباء وتعيد توصيله.',
      boxPowerWaitUpdate: 'يجري تحديث الآن. يبقى «إعادة التشغيل» و«إيقاف التشغيل» بانتظار انتهائه، حتى لا يُطفأ الصندوق في منتصفه.',
      boxPowerDropped: 'كان الصندوق يجري تحديثًا، لذلك لم تُنفَّذ إعادة التشغيل أو الإيقاف. حاول مجددًا بعد انتهاء التحديث.',
      boxPowerSent: 'جارٍ إبلاغ الصندوق…', boxPowerRestarting: 'الصندوق يعيد التشغيل. ستطلب منك هذه الصفحة تسجيل الدخول عند عودته.',
      boxPowerOff: 'الصندوق يُوقف التشغيل. يمكنك فصله عن الكهرباء بعد نصف دقيقة تقريبًا. لتشغيله مجددًا افصله ثم أعد توصيله.',
      boxPowerStuck: 'لم يستجب الصندوق. إذا لم يعد التشغيل، افصله عن الكهرباء وانتظر عشر ثوانٍ ثم أعد توصيله.',
      boxBackNote: 'أعاد الصندوق التشغيل. سجّل الدخول مجددًا.',
      boxCounterTitle: 'احتساب هذا الصندوق',
      boxCounterSwitch: 'احتساب هذا الصندوق في عدّاد سينكو دون ذكر هويتك',
      boxCounterLead: 'اختياري. يساعد المشروع على إظهار عدد صناديق سينكو قيد الاستخدام. ويبقى متوقفًا حتى تشغّله.',
      boxCounterSentTitle: 'ما يُرسل كل بضع ساعات:',
      boxCounterSent1: 'رمز عشوائي يُنشأ على هذا الصندوق. لا يُشتق من أي شيء عنك أو عن عائلتك أو أجهزتك.',
      boxCounterSent2: 'رقم إصدار سينكو، مثل 3.0.0.',
      boxCounterSent3: 'نوع الصندوق، مثل Orange Pi Zero 3.',
      boxCounterKept: 'يخزّن العدّاد هذه الأشياء الثلاثة، والدولة التي صدرت منها الرسالة (مثل البحرين)، ووقت أول رسالة وآخر رسالة من هذا الصندوق. ولا يخزّن عنوان الإنترنت.',
      boxCounterForget: 'إذا أوقفته، يطلب الصندوق من خدمة العدّاد أن تنسى هذا الصندوق، ويحذف رمزه العشوائي.',
      boxCounterNotTitle: 'ما لا يُرسل أبدًا:',
      boxCounterNot: 'المواقع، التطبيقات، أسماء الأطفال أو الأجهزة، عناوين أجهزتك، القواعد، كلمات المرور، اللغة أو المنطقة الزمنية.',
      boxCounterOnline: 'هذا الصندوق واحد من صناديق سينكو المتصلة، وعددها {n}.', boxCounterOnlineTwo: 'هذا الصندوق وصندوق آخر من سينكو متصلان.',
      boxCounterOnToast: 'تم تشغيل العدّاد. شكرًا لك.', boxCounterOffToast: 'تم إيقاف العدّاد. سيطلب الصندوق من خدمة العدّاد أن تنسى هذا الصندوق.',
      boxAboutTitle: 'عن سينكو', boxAboutVersion: 'سينكو {v}', boxAboutPihole: 'محرّك الحجب Pi-hole {v}', boxAboutLicence: 'برنامج حر، مرخّص بموجب GPL-3.0-or-later.',
      boxAboutTrademark: 'Pi-hole علامة تجارية لشركة Pi-hole LLC. سينكو برنامج مستقل يعمل معه.',
      boxLinkHome: 'صفحة المشروع', boxLinkIssues: 'الإبلاغ عن مشكلة', boxLinkReleases: 'كل الإصدارات', boxLinkPrivacy: 'الخصوصية',
      boxLinkLicense: 'الترخيص', boxLinkSupport: 'احصل على مساعدة', boxLinkBuy: 'اشترِ صندوقًا جاهزًا'
    }
  };
  // end of BOX_STR

  // ------------------------------------------------------------------ pure logic (tested under node)
  // The scheduler calls a run dead when nothing holds the update lock for 3 minutes (RUNNER_GRACE in bin/sinko) and then checks the box and
  // says how it ended. A page that still reads "running" after this long therefore means the scheduler is not judging (it is stopped,
  // or the clock is not believed), not that the update is merely slow. A live runner can take longer than 3 minutes on a bad line, so this
  // trails the scheduler's own limit by a wide margin; "Try again" while the run is really alive is simply ignored by the scheduler.
  var STALL_SEC = 15 * 60;
  var FAILED_SHOWN_SEC = 7 * 86400;      // the scheduler will not retry a failed version for a week: so the page keeps saying so
  var OK_SHOWN_SEC = 10 * 60;
  var HEARTBEAT_LATE_SEC = 20 * 60;      // box.json is rewritten at least every 10 minutes (and not during an update): 20 minutes is silence
  var MIN_PASSWORD = 8;
  var IPV4 = /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/;
  var TZ_NAME = /^[A-Za-z0-9][A-Za-z0-9_+.\/-]{0,63}$/;
  var UTC_OFFSET = /^([+-])(0\d|1[0-4]):([0-5]\d)$/;
  // Text that reads like something to type: a program that needs a keyboard, a backtick, a shell expansion, "run:", or one of Sinko's own commands.
  var COMMAND_LIKE = /\b(sudo|systemctl|journalctl|apt|apt-get)\b|`|\$\(|\brun:|\bsinko\s+(doctor|update|selfcheck|rollback|setup|configure|telemetry|remove|box-info|status|run)\b/i;
  var UTC_ZONES = ['utc', 'etc/utc', 'uct', 'etc/uct', 'universal', 'etc/universal', 'zulu', 'etc/zulu'];
  var HOSTNAME = /^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$/;
  var LABEL = /^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$/;

  // What a restore imports: ONLY the tables that hold children's devices, apps and rules. Never the configuration (that would
  // change the address, the upstream servers and the password) and never DHCP leases. Spelled out, because a missing "import"
  // means "everything" to Pi-hole.
  var RESTORE_IMPORT = { config: false, dhcp_leases: false, gravity: { group: true, adlist: true, adlist_by_group: true,
    domainlist: true, domainlist_by_group: true, client: true, client_by_group: true } };

  /**
   * Which sentence says how many Sinko boxes are online, or null for none. The counter always counts the asking box itself, so 0 and 1 are
   * "nobody else yet": a line that says "one of 1" reads like an error, so it is not shown until there are two. Exactly two has its own wording
   * ("one of 2 boxes" is not how anybody talks). Not a number (a damaged state) = none.
   */
  function counterOnlineKey(n) {
    if (typeof n !== 'number' || !isFinite(n) || n < 2) return null;
    return n === 2 ? 'boxCounterOnlineTwo' : 'boxCounterOnline';
  }

  /**
   * The release this page was installed as: the content of <meta name="sinko-version">, which the installer fills in. '' when it is not a
   * version: the placeholder is still there (development, a page copied by hand) or the tag is missing; the page then goes by the box's
   * version file, as before.
   */
  function pageStamp(content) {
    return parseSemver(content) ? String(content).trim() : '';
  }
  /**
   * The version a page should reload for, or '': the box has another version than the one this page was installed as. The browser may
   * keep the page and its scripts for an hour after the box was updated (by a phone that stayed open, another phone, the night's automatic
   * update), and a reload brings in files of one release only. Never for an unstamped page (nothing to compare), and never twice for the
   * same version: `reloadedFor` is the version this tab already reloaded for, so a box that keeps saying something else (a page that was
   * not stamped right, a proxy) cannot make a phone reload for ever.
   */
  function staleTarget(stamp, installed, reloadedFor) {
    if (!parseSemver(stamp) || !parseSemver(installed)) return '';
    var s = String(stamp).trim(), i = String(installed).trim();
    return s === i || i === reloadedFor ? '' : i;
  }

  var PIHOLE_VERSION = /^v?(\d{1,3}(?:\.\d{1,3}){1,3})$/;
  /**
   * The Pi-hole core version out of GET /api/info/version ({ version: { core: { local: { version: "v6.3" } } } }), written "v6.3" the way
   * `pihole -v` prints it; '' when the answer has no plain release number (a development build prints a branch and a hash, an older Pi-hole
   * has no such call): the About card then says nothing about it.
   */
  function piholeVersion(info) {
    var v = info && info.version && info.version.core && info.version.core.local && info.version.core.local.version;
    var m = typeof v === 'string' ? PIHOLE_VERSION.exec(v.trim()) : null;
    return m ? 'v' + m[1] : '';
  }

  function parseSemver(v) {
    var m = /^(\d{1,4})\.(\d{1,4})\.(\d{1,4})$/.exec(String(v === null || v === undefined ? '' : v).trim());
    return m ? [+m[1], +m[2], +m[3]] : null;
  }
  /** True only when both are x.y.z versions and `a` is newer than `b`. */
  function isNewer(a, b) {
    var x = parseSemver(a), y = parseSemver(b);
    if (!x || !y) return false;
    for (var i = 0; i < 3; i++) if (x[i] !== y[i]) return x[i] > y[i];
    return false;
  }

  /**
   * What the update card shows, from the shared state and the clock:
   *   starting   the page asked and the scheduler has not picked it up yet
   *   running    the scheduler is updating (or `stalled`: it says so for over 30 minutes, so the runner died)
   *   ok         it just finished
   *   failed     the last attempt failed (shown for a week, while that version is still the one on offer). `rolledBack` says whether the
   *              previous version is back: true, false (it is not working either) or null (not known); the card claims recovery only for true
   *              (a box pinned to one release has no `latest`, so it reads "up to date" even when a newer release exists: the page cannot tell)
   *   available  a newer version exists
   *   current    nothing to do
   * ctx: { now: seconds on the box's clock, current: the installed version ('' when unknown) }.
   */
  function updateView(st, ctx) {
    var u = st.update, now = ctx.now, age = u.at ? now - u.at : Infinity;
    var newer = !!(u.latest && (!ctx.current || isNewer(u.latest, ctx.current)));
    if (u.status === 'running') {
      if (age > STALL_SEC) return { phase: 'stalled', to: u.to || u.latest, from: u.from };
      return { phase: 'running', to: u.to || u.latest, from: u.from };
    }
    if (u.request !== null) return { phase: 'starting', to: u.latest };
    // A failure counts for the version on offer (or when nobody knows which version it was): not for an older one that has been superseded.
    if (u.status === 'failed' && age < FAILED_SHOWN_SEC && (!u.latest || !u.to || u.latest === u.to)) {
      return { phase: 'failed', error: u.error, rolledBack: u.rolledBack, to: u.to, from: u.from, latest: newer ? u.latest : null, notes: u.notes };
    }
    if (u.status === 'ok' && age < OK_SHOWN_SEC) return { phase: 'ok', to: u.to };
    if (newer) return { phase: 'available', latest: u.latest, notes: u.notes, checked: u.checked };
    return { phase: 'current', checked: u.checked };
  }

  /**
   * An update is running, or has been asked for and not picked up yet (or this page has just asked for one and the write is on its way).
   * The box never switches itself off in the middle of an installation: it drops a restart or shut-down asked for meanwhile, so the page
   * does not offer them and never tells a parent to unplug anything while this is true.
   */
  function updateBusy(st, ownUpdate) {
    return !!(ownUpdate || (st && st.update && (st.update.status === 'running' || st.update.request !== null)));
  }

  /** Temperature in plain words from /api/info/sensors: { c, word: cool|warm|hot } or null when the box has no sensor. */
  function temperature(sensors) {
    if (!sensors || typeof sensors.cpu_temp !== 'number' || !isFinite(sensors.cpu_temp)) return null;
    var unit = String(sensors.unit || 'C').toUpperCase();
    function toC(v) { return unit === 'F' ? (v - 32) * 5 / 9 : unit === 'K' ? v - 273.15 : v; }
    var c = toC(sensors.cpu_temp);
    // Pi-hole's own limit is 60: normal for a small board in a case, so the page only calls it hot from 70 up.
    var hot = Math.max(70, typeof sensors.hot_limit === 'number' && isFinite(sensors.hot_limit) ? toC(sensors.hot_limit) : 0);
    return { c: Math.round(c), word: c >= hot ? 'hot' : c >= hot - 15 ? 'warm' : 'cool' };
  }

  /** Memory from /api/info/system: { percent, word: ok|busy|full } or null. */
  function memory(system) {
    var ram = system && system.memory && system.memory.ram;
    if (!ram) return null;
    var p = typeof ram['%used'] === 'number' ? ram['%used'] : (ram.total > 0 ? 100 * (ram.total - (ram.available !== undefined ? ram.available : ram.free)) / ram.total : NaN);
    if (!isFinite(p)) return null;
    p = Math.max(0, Math.min(100, Math.round(p)));
    return { percent: p, word: p >= 90 ? 'full' : p >= 75 ? 'busy' : 'ok' };
  }

  /** [[3, 'day'], [4, 'hour']] for 3 days 4 hours: the two biggest units. */
  function durationParts(sec) {
    sec = Math.max(0, Math.floor(sec || 0));
    var d = Math.floor(sec / 86400), h = Math.floor(sec % 86400 / 3600), m = Math.floor(sec % 3600 / 60);
    if (d) return h ? [[d, 'day'], [h, 'hour']] : [[d, 'day']];
    if (h) return m ? [[h, 'hour'], [m, 'minute']] : [[h, 'hour']];
    return [[Math.max(1, m), 'minute']];
  }

  /** A gap between two clocks, rounded so it reads as "about 3 hours", not "2 hours 59 minutes": whole minutes, then quarter hours. */
  function roundGap(sec) {
    sec = Math.abs(sec);
    return sec < 3600 ? Math.max(60, Math.round(sec / 60) * 60) : Math.round(sec / 900) * 900;
  }

  /** The device that asked the box most recently: { name, lastQuery } or null. */
  function lastDevice(devices) {
    var best = null, i;
    for (i = 0; i < (devices || []).length; i++) {
      if (devices[i] && devices[i].lastQuery > 0 && (!best || devices[i].lastQuery > best.lastQuery)) best = devices[i];
    }
    if (!best) return null;
    var name = '';
    (best.ips || []).forEach(function (a) { if (!name && a && a.name) name = a.name; });
    return { name: name || best.macVendor || (best.ips && best.ips[0] && best.ips[0].ip) || '', lastQuery: best.lastQuery };
  }

  /**
   * The addresses this page can be reached at: [{ kind: 'ip'|'name'|'local', value }].
   * o: { hostname, port, boxIp: the address the box itself reports in /pb/box.json (or ''), hosts: Pi-hole's local records
   *    ("192.168.1.50 family.lan"), nodename: the box's own host name }.
   * The number address is the one the box reports; without that, the one the page was opened with, or the one Pi-hole's records give to
   * the name it was opened with. Only the box's own records count: a record for another device is never shown as the box.
   */
  function addressRows(o) {
    var host = String(o.hostname || '').toLowerCase().replace(/^\[|\]$/g, ''), port = String(o.port || '');
    var suffix = port && port !== '80' && port !== '443' ? ':' + port : '';
    var ip = usableIpv4(o.boxIp) || (IPV4.test(host) ? host : ''), names = [];
    (o.hosts instanceof Array ? o.hosts : []).forEach(function (line) {
      if (typeof line !== 'string') return;
      var parts = line.trim().split(/\s+/), lineIp = parts[0];
      if (!IPV4.test(lineIp)) return;
      var ours = ip ? lineIp === ip : parts.slice(1).map(function (n) { return n.toLowerCase(); }).indexOf(host) >= 0;
      if (!ours) return;
      ip = ip || lineIp;
      parts.slice(1).forEach(function (n) { n = n.toLowerCase(); if (HOSTNAME.test(n) && n !== 'localhost' && names.indexOf(n) < 0) names.push(n); });
    });
    if (host && !IPV4.test(host) && host !== 'localhost' && HOSTNAME.test(host) && names.indexOf(host) < 0) names.push(host);
    var mdns = typeof o.nodename === 'string' && LABEL.test(o.nodename) ? o.nodename.toLowerCase() + '.local' : '';
    if (mdns && names.indexOf(mdns) < 0) names.push(mdns);
    var rows = [];
    if (ip) rows.push({ kind: 'ip', value: ip });
    names.filter(function (n) { return !/\.local$/.test(n); }).forEach(function (n) { rows.push({ kind: 'name', value: n + suffix }); });
    names.filter(function (n) { return /\.local$/.test(n); }).forEach(function (n) { rows.push({ kind: 'local', value: n + suffix }); });
    return rows;
  }

  /** An address a router could be told: four numbers, not 0.x.x.x, loopback, link-local or multicast and above. '' for anything else. */
  function usableIpv4(v) {
    if (typeof v !== 'string' || !IPV4.test(v)) return '';
    var first = +v.split('.')[0], second = +v.split('.')[1];
    if (first === 0 || first === 127 || first >= 224 || (first === 169 && second === 254)) return '';
    return v;
  }

  /**
   * /pb/box.json, as the box program writes it: { "v":1, "version", "ip", "tz", "utcOffset", "counter", "mdns", "at" } (architecture.md,
   * "Amendments"). Anything that is not exactly that shape is treated as a missing file (null), and every field is checked on its own:
   * a wrong one costs only what it was for. `ip` is an IPv4 or '', `tz` a zone name or '', `utcOffset` "+03:00" or '', `counter` and `mdns`
   * are true only when the file says true, `at` is whole seconds on the box's clock or 0.
   */
  function parseBoxInfo(raw) {
    if (!raw || typeof raw !== 'object' || raw instanceof Array || raw.v !== 1) return null;
    var tz = typeof raw.tz === 'string' && TZ_NAME.test(raw.tz) && raw.tz.split('/').indexOf('..') < 0 ? raw.tz : '';
    var off = typeof raw.utcOffset === 'string' && UTC_OFFSET.test(raw.utcOffset) ? raw.utcOffset : '';
    var at = typeof raw.at === 'number' && isFinite(raw.at) && Math.floor(raw.at) === raw.at && raw.at > 0 ? raw.at : 0;
    var ver = typeof raw.version === 'string' && /^\d{1,4}\.\d{1,4}\.\d{1,4}$/.test(raw.version) ? raw.version : '';
    return { version: ver, ip: usableIpv4(raw.ip), tz: tz, utcOffset: off, counter: raw.counter === true, mdns: raw.mdns === true, at: at };
  }

  /** True when the box's own pulse (box.json's `at`) is more than 20 minutes behind the box's clock `nowSec`: the scheduler is not running. */
  function heartbeatLate(info, nowSec) {
    return !!(info && info.at && typeof nowSec === 'number' && nowSec - info.at > HEARTBEAT_LATE_SEC);
  }

  function offsetMinutes(text) {
    var m = UTC_OFFSET.exec(text || '');
    return m ? (m[1] === '-' ? -1 : 1) * (+m[2] * 60 + +m[3]) : null;
  }

  /**
   * What to tell the parent about the box's time zone, from box.json and this phone's offset (minutes east of UTC; null when unknown):
   *   unknown   no box.json (an older box) or it does not know its zone
   *   utc       the box is on universal time: it is probably not the family's local time
   *   zone      the box's zone: { label: 'Asia/Bahrain, UTC+03:00', differs: true | false | null (the phone's offset is not the box's) }
   * Only a zone that is named UTC counts as UTC: an offset of +00:00 alone is also London in winter.
   */
  function zoneInfo(info, phoneOffsetMin) {
    if (!info || (!info.tz && !info.utcOffset)) return { kind: 'unknown' };
    if (info.tz && UTC_ZONES.indexOf(info.tz.toLowerCase()) >= 0) return { kind: 'utc' };
    var off = offsetMinutes(info.utcOffset), parts = [];
    if (info.tz) parts.push(info.tz);
    if (info.utcOffset) parts.push('UTC' + info.utcOffset);
    return { kind: 'zone', label: parts.join(', '), differs: off === null || typeof phoneOffsetMin !== 'number' ? null : off !== phoneOffsetMin };
  }

  /** The time of day on the box's own wall clock, from box.json's offset and the box's epoch seconds; '' when the offset is not known. */
  function boxWallTime(info, epochSec, locale) {
    var off = info ? offsetMinutes(info.utcOffset) : null;
    if (off === null || !isFinite(epochSec)) return '';
    try { return new Date((epochSec + off * 60) * 1000).toLocaleTimeString(locale, { timeZone: 'UTC', hour: '2-digit', minute: '2-digit' }); }
    catch (e) { return ''; }
  }

  /**
   * The English technical reason of a failed update, for the collapsed "details for whoever helps you": '' when there is nothing to show or
   * when it looks like something to type (the page never shows a command to a parent; the box says it never writes one, this is the second lock).
   */
  function reasonDetails(error) {
    var s = typeof error === 'string' ? error.replace(/[\u0000-\u001f\u007f]/g, ' ').trim() : '';
    if (!s || COMMAND_LIKE.test(s)) return '';
    return s;
  }

  /**
   * Whether Pi-hole took anything from a restored file, from its answer to POST /api/teleporter: 'nothing' only when the answer lists what it
   * processed and the group table is not among it (a config-only or unrelated zip: FTL accepts it with a 200 and imports nothing). FTL's own
   * code answers { files: [...] } (its documentation says "processed"); an answer with no list is not judged, so a future FTL cannot make every
   * restore look like a failure.
   */
  function restoreVerdict(answer) {
    var l = answer && (answer.files instanceof Array ? answer.files : answer.processed instanceof Array ? answer.processed : null);
    return l && !l.some(function (s) { return /->group$/.test(String(s)); }) ? 'nothing' : 'ok';
  }

  /**
   * How a rebuild of the block lists ended, from the text FTL streamed (POST /api/action/gravity answers 200 and streams the output of the run
   * before it is over, so only the text tells): 'ok' when it shows at least one success mark and no failure line, else 'partial' (a failure
   * line, or nothing that confirms it). gravity.sh redraws a status line with OVER="\r\033[K" (carriage return, erase line) whether or not
   * anything is a terminal, so a failed list arrives as "...Target: url\r\x1b[K  [x] Status: ...": every escape sequence goes, and a carriage
   * return counts as a line break, before the lines are read.
   */
  function gravityVerdict(text) {
    var s = String(text === undefined || text === null ? '' : text).replace(/\u001b\[[0-9;?]*[ -\/]*[@-~]/g, '').replace(/\r/g, '\n');
    if (/^[ \t]*\[(✗|x|X)\]/m.test(s) || /gravity failed/i.test(s)) return 'partial';
    return /\[✓\]/.test(s) ? 'ok' : 'partial';
  }

  function charCount(s) { return String(s).replace(/[\uD800-\uDBFF][\uDC00-\uDFFF]/g, 'x').length; }
  /** The key of the first problem with a new password, or null. */
  function passwordProblem(current, next, again) {
    if (charCount(next) < MIN_PASSWORD) return 'boxPwShort';
    if (next !== again) return 'boxPwMismatch';
    if (next === current) return 'boxPwSame';
    return null;
  }

  /**
   * After a restore the stored state is the BACKUP's (the "group" table holds it): its update request, a "running" status, a pending
   * restart and an old counter answer would all come back to life. This returns the mutator that puts back what belongs to the box and
   * not to the family's rules; the timer, bedtime and whether bedtime is on right now come from the backup, because they describe the
   * rules that were just restored.
   */
  function keepBoxFields(before) {
    return function (st) {
      st.update = before.update; st.power = before.power; st.telemetry = before.telemetry;
      st.community = before.community; st.setup = before.setup;
    };
  }

  /** The Pi-hole groups Sinko cannot work without (the page's own check, kept here so a restore can verify it). */
  var REQUIRED_GROUPS = ['pb-kids', 'pb-offline', 'pb-paused', 'pb-state'];
  function hasSinkoGroups(groups) {
    var names = {};
    (groups || []).forEach(function (g) { if (g && g.name) names[g.name] = true; });
    return REQUIRED_GROUPS.every(function (n) { return names[n]; });
  }

  /** Only https links from links.json are shown; anything else is dropped. */
  function safeLinks(raw) {
    var out = {};
    ['home', 'issues', 'releases', 'privacy', 'license', 'support', 'buy'].forEach(function (k) {
      var v = raw && typeof raw[k] === 'string' ? raw[k].trim() : '';
      if (/^https:\/\/[^\s"'<>]+$/.test(v)) out[k] = v;
    });
    return out;
  }

  /** A file name for the download: the day (on this phone) in the name, nothing from the network. */
  function backupName(date) {
    var p = function (n) { return (n < 10 ? '0' : '') + n; };
    return 'sinko-backup-' + date.getFullYear() + '-' + p(date.getMonth() + 1) + '-' + p(date.getDate()) + '.zip';
  }

  var pure = {
    isNewer: isNewer, parseSemver: parseSemver, updateView: updateView, temperature: temperature, memory: memory,
    durationParts: durationParts, roundGap: roundGap, lastDevice: lastDevice, addressRows: addressRows, passwordProblem: passwordProblem,
    keepBoxFields: keepBoxFields, hasSinkoGroups: hasSinkoGroups, safeLinks: safeLinks, backupName: backupName,
    updateBusy: updateBusy, usableIpv4: usableIpv4, parseBoxInfo: parseBoxInfo, heartbeatLate: heartbeatLate, zoneInfo: zoneInfo,
    counterOnlineKey: counterOnlineKey, piholeVersion: piholeVersion, pageStamp: pageStamp, staleTarget: staleTarget,
    boxWallTime: boxWallTime, reasonDetails: reasonDetails, restoreVerdict: restoreVerdict, gravityVerdict: gravityVerdict,
    RESTORE_IMPORT: RESTORE_IMPORT, MIN_PASSWORD: MIN_PASSWORD, STALL_SEC: STALL_SEC, HEARTBEAT_LATE_SEC: HEARTBEAT_LATE_SEC
  };

  // ------------------------------------------------------------------ the sheet (browser only from here)
  // How long the page waits before it says something did not happen (ms). A variable, so the browser tests can shorten the waits.
  var timing = { checkOffline: 20000, checkSlow: 45000, powerStuck: 45000, powerStale: 60000, powerNoRestart: 180000, startLate: 90000, longDown: 600000,
    startLost: 360000,                // the scheduler waits up to 5 minutes between runs
    healthDown: 6000,                 // how long the box is silent before the health card stops showing its old readings
    heartbeatGrace: 60000,            // how long box.json must have been late (seen by this page) before the main page says the scheduler is not running
    clockStepHold: 6 * 60000 };       // after the box's clock was seen to jump: how long the scheduler gets to catch up (it refreshes box.json every 5 minutes) before it is judged
  var env = null;                    // what the page lends us, see init()
  var ui = null;                     // the elements, built on first open (and again after a language change)
  var poller = null;
  var st = null;                     // the state as last read
  var health = null;                 // { system, sensors, blocking, host } as last read (each may be null)
  var hosts = null;                  // dns.hosts, read when the sheet opens
  var links = null;                  // links.json
  var pihole = '';                   // the Pi-hole version ("v6.3"), read when the sheet opens; '' while unknown or when the box does not say
  var upd = null;                    // { kind: 'update' | 'check', since, checkedBefore }
  var updNotice = '';                // one line under the update card: "could not reach the internet", ...
  var updSig = '';
  var down = { since: 0 };           // the box stopped answering at this local time (ms), 0 while it answers
  var power = null;                  // { action, since, stage: 'asked' | 'acted' | 'dropped' | 'stuck', actedAt, sawDown, written, marker }
  var healthStale = false;           // the box stopped answering for a while: the readings in the health card are from before
  var signedOut = false;             // the box dropped our session while a flow was running
  var reloading = false;
  var restoring = false;
  var skipReload = false;            // the sheet was closed because the session ended
  var okSeenAt = 0;                  // the finish time of the update whose result was already checked
  var pending = null;                // { marker, at, withdrawing }: an update request this page has been watching, see sweepStuckRequest
  var pendingPower = null;           // the same for a restart or shut-down request, see sweepStuckPower

  function t(k, v) { return env.t(k, v); }
  function el(tag, attrs, kids) { return env.el(tag, attrs, kids); }
  function iso(s) { return env.lang() === 'ar' ? '⁦' + s + '⁩' : String(s); }      // keeps "47 °C" and "3.1.0" whole inside Arabic text
  function nowMs() { return Date.now(); }
  /** The time of a request for the box: milliseconds on the BOX's clock (from the Date header of its last answer), never this phone's, which can be
   *  anywhere. The box acts on a request only when that time is close to its own clock, so the old requests inside a restored backup do not fire.
   *  The Date header counts whole seconds, so the milliseconds are made up: without them two requests of one kind within a second (two phones)
   *  would carry the same marker, and the second would look like the first, which the box has already handled. */
  function requestTime() { return Math.round(env.serverNowSec() * 1000 + Math.random() * 1000); }
  function quiet(method, path, body, opts) { return env.call(method, path, body, Object.assign({ quiet401: true }, opts || {})); }
  function boxInfo() { return env.boxInfo ? env.boxInfo() : null; }          // /pb/box.json, parsed; null on a box that has none
  /** An update is running or on its way (this page's own, or asked for, or running): see updateBusy. */
  function busy() { return updateBusy(st, upd && upd.kind === 'update'); }
  /** A restart or shut down this page asked for that has not ended yet. */
  function powerActive() { return !!power && (power.stage === 'asked' || power.stage === 'acted'); }

  function init(e) {
    env = e;
    var d = document.getElementById('boxDialog');
    d.addEventListener('close', onClose);
    document.addEventListener('visibilitychange', function () { if (poller) poller.wake(); });
  }
  /** Everything built from strings is rebuilt the next time the sheet opens (a language switch cannot happen while it is open). */
  function relang() { ui = null; updSig = ''; }
  /** The session ended (or the page is going away): close the sheet and stop polling, silently. */
  function reset() {
    stopPolling();
    var d = document.getElementById('boxDialog');
    if (d && d.open) { skipReload = true; env.closeDialog('boxDialog'); }
    clearSecrets();
    upd = null; power = null; signedOut = false; restoring = false; st = null; health = null; hosts = null; pihole = ''; updSig = ''; pending = null;
    pendingPower = null; healthStale = false; down.since = 0;
  }

  function open() {
    if (!env) return;
    if (!ui) build();
    clearSecrets();
    updNotice = ''; signedOut = false; updSig = ''; healthStale = false; down.since = 0;
    if (power && !powerActive()) power = null;                // an old "did not react" or "was updating" message is not shown again
    st = env.model() ? env.state() : null;
    renderAll();
    env.openDialog('boxDialog');
    var h = document.getElementById('boxTitle'); if (h) h.focus();
    loadLinks();
    loadHosts();
    loadPihole();
    if (env.refreshBoxInfo) env.refreshBoxInfo().then(function () { renderAll(); });
    startPolling();
    checkInstalledVersion();
  }
  function onClose() {
    stopPolling();
    clearSecrets();
    withdrawPower();
    if (skipReload) { skipReload = false; return; }          // the session ended: there is nothing to reload
    if (!reloading) env.reload().catch(function () {});      // whatever was done in here (restore, counter, update) shows on the page
  }
  /**
   * A restart or shut down that was asked for and not taken yet must not stay behind when the sheet closes: with nobody watching, it
   * would wait in the shared state and fire whenever the scheduler next looks (a box that was unplugged and plugged back in, say).
   * Only this page's own marker is taken back: one the scheduler has already taken is gone from the state, and one from another phone is not ours.
   */
  function withdrawPower() {
    var p = power;
    if (!p || p.stage !== 'asked' || !p.written || !p.marker) return;
    power = null;
    env.writeState(function (s) { if (s.power.request === p.marker) s.power = { request: null, action: null }; }).catch(function () {});
  }
  function clearSecrets() {
    if (!ui) return;
    ['pwCur', 'pwNew', 'pwNew2', 'pwCode'].forEach(function (k) { if (ui[k]) ui[k].value = ''; });
    if (ui.pwErr) ui.pwErr.textContent = '';
    if (ui.pwCodeField) ui.pwCodeField.hidden = true;
  }

  // ---------------------------------------------------------------- building
  function card(id, titleKey, kids) {
    var h = el('h3', { id: id + 'Title', text: t(titleKey) });
    return el('section', { class: 'box-card', id: id, 'aria-labelledby': id + 'Title' }, [h].concat(kids));
  }
  function row(labelKey) {
    var value = el('span', { class: 'box-value' }), note = el('span', { class: 'box-note', hidden: true });
    var node = el('div', { class: 'box-row' }, [el('span', { class: 'box-label', text: t(labelKey) }), value, note]);
    return { node: node, value: value, note: note };
  }
  function setRow(r, text, note, tone) {
    r.value.textContent = text;
    r.value.setAttribute('data-tone', tone || '');
    r.note.hidden = !note; r.note.textContent = note || '';
  }
  function switchRow(id, textKey, noteKey) {
    var input = el('input', { type: 'checkbox', id: id });
    var kids = [el('span', { class: 'box-switch-text' }, [el('span', { text: t(textKey) })].concat(noteKey ? [el('span', { class: 'box-note', text: t(noteKey) })] : [])),
      el('span', { class: 'switch' }, [input, el('span', { class: 'switch-ui', 'aria-hidden': 'true' })])];
    return { node: el('label', { class: 'box-switch-row' }, kids), input: input };
  }
  function button(cls, textKey, handler, extra) {
    var b = el('button', Object.assign({ type: 'button', class: 'btn ' + cls }, extra || {}), [el('span', { text: t(textKey) })]);
    b.addEventListener('click', handler);
    return b;
  }
  function field(id, labelKey, attrs, noteKey) {
    var input = el('input', Object.assign({ type: 'password', id: id }, attrs || {}));
    var kids = [el('span', { text: t(labelKey) }), input];
    if (noteKey) kids.push(el('span', { class: 'box-note', text: t(noteKey) }));
    return { node: el('label', { class: 'field' }, kids), input: input };
  }

  function build() {
    ui = {};
    var body = document.getElementById('boxBody');
    body.textContent = '';
    document.getElementById('boxTitle').textContent = t('boxTitle');

    // updates
    ui.updLive = el('div', { class: 'box-live', id: 'boxUpdLive', role: 'status', 'aria-live': 'polite', tabindex: '-1' });
    ui.updMore = el('div', { class: 'box-more' });
    ui.auto = switchRow('boxAuto', 'boxAuto', 'boxAutoNote');
    ui.auto.input.addEventListener('change', onAuto);
    ui.updCard = card('boxUpdates', 'boxUpdTitle', [ui.updLive, ui.updMore, ui.auto.node]);

    // health
    ui.hFilter = row('boxFilterLabel'); ui.hUptime = row('boxUptimeLabel'); ui.hTemp = row('boxTempLabel');
    ui.hMem = row('boxMemLabel'); ui.hSeen = row('boxLastSeenLabel'); ui.hClock = row('boxClockLabel');
    ui.hNone = el('p', { class: 'box-note', text: t('boxHealthNone'), hidden: true });
    ui.hStale = el('p', { class: 'box-note is-waiting', id: 'boxHealthDown', role: 'status', text: t('boxHealthDown'), hidden: true });
    // Bedtime runs on the box's own time zone: the note under the clock row says which one (from box.json) and warns about universal time.
    ui.hZone = el('p', { class: 'box-note', id: 'boxClockZone', text: t('boxClockZone') });
    ui.hRows = el('div', { class: 'box-rows' }, [ui.hFilter.node, ui.hUptime.node, ui.hTemp.node, ui.hMem.node, ui.hSeen.node, ui.hClock.node]);
    ui.healthCard = card('boxHealth', 'boxHealthTitle', [ui.hRows, ui.hZone, ui.hStale, ui.hNone]);

    // addresses
    ui.netList = el('ul', { class: 'box-addr' });
    ui.netCard = card('boxNet', 'boxNetTitle', [el('p', { class: 'box-note', text: t('boxNetHint') }), ui.netList]);

    // password
    var fCur = field('boxPwCur', 'boxPwCurrent', { autocomplete: 'current-password' });
    var fNew = field('boxPwNew', 'boxPwNew', { autocomplete: 'new-password', minlength: '8' }, 'boxPwRule');
    var fNew2 = field('boxPwNew2', 'boxPwConfirm', { autocomplete: 'new-password' });
    ui.pwCur = fCur.input; ui.pwNew = fNew.input; ui.pwNew2 = fNew2.input;
    ui.pwCode = el('input', { type: 'text', id: 'boxPwCode', inputmode: 'numeric', pattern: '[0-9]{6}', maxlength: '6', autocomplete: 'one-time-code' });
    ui.pwCodeField = el('label', { class: 'field', hidden: true }, [el('span', { text: t('boxPwCode') }), ui.pwCode]);
    ui.pwErr = el('p', { class: 'form-error', role: 'alert' });
    ui.pwBtn = el('button', { type: 'submit', class: 'btn btn-primary', id: 'boxPwBtn' }, [el('span', { text: t('boxPwChange') })]);
    ui.pwForm = el('form', { class: 'box-form', id: 'boxPwForm', novalidate: true }, [fCur.node, fNew.node, fNew2.node, ui.pwCodeField, ui.pwErr, ui.pwBtn]);
    ui.pwForm.addEventListener('submit', onPassword);
    ui.pwCard = card('boxPw', 'boxPwTitle', [el('p', { class: 'box-note', text: t('boxPwHint') }), ui.pwForm]);

    // backup and restore
    ui.backupBtn = button('', 'boxBackupDownload', onBackup, { id: 'boxBackupBtn' });
    ui.backupMsg = el('p', { class: 'box-msg', role: 'status', 'aria-live': 'polite' });
    ui.file = el('input', { type: 'file', id: 'boxRestoreFile', accept: '.zip,application/zip,application/x-zip-compressed', class: 'sr-only', tabindex: '-1', 'aria-hidden': 'true' });   // the button below drives it: a screen reader meets one control, not two
    ui.file.addEventListener('change', onFilePicked);
    ui.fileName = el('span', { class: 'box-file-name', id: 'boxRestoreName', role: 'status', text: t('boxRestoreNone') });   // announces the chosen file
    ui.pickBtn = button('', 'boxRestorePick', function () { ui.file.click(); }, { id: 'boxRestorePick', 'aria-describedby': 'boxRestoreName' });
    ui.restoreBtn = button('btn-primary', 'boxRestoreButton', onRestore, { id: 'boxRestoreBtn', disabled: true });
    ui.restoreMsg = el('p', { class: 'box-msg', role: 'status', 'aria-live': 'polite' });
    ui.backupCard = card('boxBackup', 'boxBackupTitle', [
      el('p', { class: 'box-note', text: t('boxBackupHint') }),
      el('p', { class: 'box-warn', text: t('boxBackupPrivate') }),
      el('div', { class: 'box-actions' }, [ui.backupBtn]), ui.backupMsg,
      el('h4', { text: t('boxRestoreTitle') }),
      el('p', { class: 'box-note', text: t('boxRestoreHint') }),
      el('div', { class: 'box-file' }, [ui.pickBtn, ui.fileName, ui.file]),
      el('div', { class: 'box-actions' }, [ui.restoreBtn]), ui.restoreMsg]);

    // power
    ui.restartBtn = button('', 'boxRestart', function () { onPower('reboot'); }, { id: 'boxRestartBtn' });
    ui.shutdownBtn = button('', 'boxShutdown', function () { onPower('poweroff'); }, { id: 'boxShutdownBtn' });
    ui.powerMsg = el('p', { class: 'box-msg', id: 'boxPowerMsg', role: 'status', 'aria-live': 'polite', tabindex: '-1' });
    ui.powerCard = card('boxPower', 'boxPowerTitle', [el('p', { class: 'box-note', text: t('boxPowerHint') }),
      el('div', { class: 'box-actions' }, [ui.restartBtn, ui.shutdownBtn]), ui.powerMsg]);

    // counter
    ui.counter = switchRow('boxCounter', 'boxCounterSwitch');
    ui.counter.input.addEventListener('change', onCounter);
    ui.counterOnline = el('p', { class: 'box-online', id: 'boxCounterOnline', hidden: true });
    var li = function (k) { return el('li', { text: t(k) }); };
    ui.counterCard = card('boxCounterCard', 'boxCounterTitle', [el('p', { class: 'box-note', text: t('boxCounterLead') }), ui.counter.node, ui.counterOnline,
      el('h4', { text: t('boxCounterSentTitle') }), el('ul', { class: 'box-list' }, [li('boxCounterSent1'), li('boxCounterSent2'), li('boxCounterSent3')]),
      el('p', { class: 'box-note', text: t('boxCounterKept') }),
      el('p', { class: 'box-note', id: 'boxCounterForget', text: t('boxCounterForget') }),
      el('h4', { text: t('boxCounterNotTitle') }), el('p', { class: 'box-note', text: t('boxCounterNot') })]);

    // about
    ui.aboutLinks = el('ul', { class: 'box-links' });
    ui.aboutVersion = el('p', { class: 'box-about-version', id: 'boxAboutVersion' });
    ui.aboutPihole = el('p', { class: 'box-note', id: 'boxAboutPihole', hidden: true });
    ui.aboutCard = card('boxAbout', 'boxAboutTitle', [ui.aboutVersion, ui.aboutPihole, el('p', { class: 'box-note', text: t('boxAboutLicence') }), ui.aboutLinks,
      el('p', { class: 'box-note', text: t('boxAboutTrademark') })]);

    [ui.updCard, ui.healthCard, ui.netCard, ui.pwCard, ui.backupCard, ui.powerCard, ui.counterCard, ui.aboutCard].forEach(function (c) { body.appendChild(c); });
  }

  // ---------------------------------------------------------------- rendering
  function renderAll() {
    if (!ui) return;
    renderUpdate(); renderHealth(); renderNetwork(); renderCounter(); renderAbout(); renderPower(); renderAuto();
  }

  function agoText(sec) {
    sec = Math.max(0, sec);
    if (sec < 60) return t('boxJustNow');
    var loc = env.locale(), n, unit;
    if (sec < 3600) { n = Math.floor(sec / 60); unit = 'minute'; }
    else if (sec < 2 * 86400) { n = Math.floor(sec / 3600); unit = 'hour'; }
    else { n = Math.floor(sec / 86400); unit = 'day'; }
    try { return new Intl.RelativeTimeFormat(loc, { numeric: 'always' }).format(-n, unit); }
    catch (e) { return n + ' ' + unit.charAt(0); }
  }
  function unitText(n, unit) {
    try { return new Intl.NumberFormat(env.locale(), { style: 'unit', unit: unit, unitDisplay: 'long' }).format(n); }
    catch (e) { return n + ' ' + unit; }
  }
  function durationText(sec) {
    var parts = durationParts(sec).map(function (p) { return unitText(p[0], p[1]); });
    try { return new Intl.ListFormat(env.locale(), { style: 'narrow', type: 'unit' }).format(parts); } catch (e) { return parts.join(' '); }
  }

  function statusLine(iconId, text, tone) {
    return el('p', { class: 'box-status' + (tone ? ' is-' + tone : '') }, [env.icon(iconId), el('span', { text: text })]);
  }
  function setLive(iconId, text, tone) {
    ui.updLive.textContent = '';
    ui.updLive.appendChild(statusLine(iconId, text, tone));
  }

  function renderUpdate() {
    if (!ui) return;
    renderPower();                                                // the restart and shut-down buttons follow the update, however it started
    if (!env.model()) {                                           // Sinko's own groups are missing: nothing to read or write
      setLive('i-alert', t('boxNeedsSetup'), 'warn'); ui.updMore.textContent = ''; ui.auto.node.hidden = true; updSig = 'nosetup';
      return;
    }
    ui.auto.node.hidden = false;
    var view = updateView(st, { now: env.serverNowSec(), current: env.version() });
    var phase = view.phase;
    // Right after "Update now" the state still says what it said before: show that it started, not the old answer.
    if (upd && upd.kind === 'update' && !upd.seen && phase !== 'running' && phase !== 'starting') { phase = 'starting'; view = { phase: phase, to: view.latest || view.to }; }
    var checking = !!(upd && upd.kind === 'check');
    var longDown = down.since && nowMs() - down.since > timing.longDown;
    var lateStart = phase === 'starting' && upd && nowMs() - upd.since > timing.startLate;
    var v = view.to || view.latest || '';

    // The status line is a live region: it is only touched when its words change.
    var live = describeUpdate(phase, view, v);
    if (ui.updLive.getAttribute('data-text') !== live.text) {
      setLive(live.icon, live.text, live.tone);
      ui.updLive.setAttribute('data-text', live.text);
    }
    // The rest is rebuilt only when something it shows changed (so a button a keyboard user is on is not replaced under them).
    var sig = [phase, view.latest, view.error, view.rolledBack, view.notes, checking, !!down.since, !!longDown, !!lateStart, updNotice, signedOut,
      env.version(), env.lang(), reloading, !!upd].join('|');
    if (sig === updSig) return;
    updSig = sig;
    var more = ui.updMore; more.textContent = '';

    if (phase === 'starting' || phase === 'running') {
      more.appendChild(el('div', { class: 'box-bar', role: 'progressbar', 'aria-label': t('boxRunningNoV') }, [el('i')]));
      more.appendChild(el('p', { class: 'box-note', text: t('boxRunningNote') }));
      if (lateStart) more.appendChild(el('p', { class: 'box-note', text: t('boxLateStart') }));
      if (signedOut) more.appendChild(signedOutBlock());
      else if (longDown) more.appendChild(el('p', { class: 'box-warn', text: t('boxLongWait') }));
      else if (down.since) more.appendChild(el('p', { class: 'box-note is-waiting', text: t('boxWaitingBox') }));
    } else if (phase === 'stalled') {
      more.appendChild(el('div', { class: 'box-actions' }, [updateButton('boxTryAgain')]));
    } else if (phase === 'ok') {
      more.appendChild(el('p', { class: 'box-note', text: reloading ? t('boxReloading') : (env.version() ? t('boxInstalled', { v: iso(env.version()) }) : '') }));
    } else if (phase === 'failed') {
      // Recovery is claimed only when the box says so (rolledBack === true); anything else says what is not known and what to do.
      more.appendChild(el('p', { class: view.rolledBack === true ? 'box-note' : 'box-warn',
        text: t(view.rolledBack === true ? 'boxFailedBack' : view.rolledBack === false ? 'boxFailedNotBack' : 'boxFailedUnknown') }));
      // The technical reason is English and for whoever helps: it is never put in the parent's language, and never a command.
      var why = reasonDetails(view.error);
      if (why) more.appendChild(el('details', { class: 'box-details' }, [el('summary', { text: t('boxDetailsTitle') }),
        el('p', { class: 'box-reason', lang: 'en', dir: 'ltr', text: why })]));
      var kids = [];
      if (view.latest) kids.push(updateButton('boxTryAgain'));
      kids.push(checkButton(checking));
      more.appendChild(el('div', { class: 'box-actions' }, kids));
      appendNotice(more);
    } else if (phase === 'available') {
      if (view.notes) more.appendChild(notesLink(view.notes));
      more.appendChild(el('div', { class: 'box-actions' }, [updateButton('boxUpdateNow'), checkButton(checking)]));
      appendNotice(more);
    } else {
      if (env.version()) more.appendChild(el('p', { class: 'box-note', text: t('boxInstalled', { v: iso(env.version()) }) }));
      more.appendChild(el('div', { class: 'box-actions' }, [checkButton(checking)]));
      appendNotice(more);
    }
  }
  /** The words, icon and tone of the update card's status line. */
  function describeUpdate(phase, view, v) {
    if (phase === 'starting') return { icon: 'i-refresh', text: t('boxStarting'), tone: 'busy' };
    if (phase === 'running') return { icon: 'i-refresh', text: v ? t('boxRunning', { v: iso(v) }) : t('boxRunningNoV'), tone: 'busy' };
    if (phase === 'stalled') return { icon: 'i-alert', text: t('boxStalled'), tone: 'warn' };
    if (phase === 'ok') return { icon: 'i-check', text: t('boxUpdated', { v: iso(v) }), tone: 'ok' };
    if (phase === 'failed') return { icon: 'i-alert', text: t('boxFailedTitle'), tone: 'bad' };
    if (phase === 'available') return { icon: 'i-up', text: t('boxAvailable', { v: iso(view.latest) }), tone: 'new' };
    return view.checked ? { icon: 'i-check', text: t('boxUpToDate', { t: agoText(env.serverNowSec() - view.checked) }), tone: 'ok' }
      : { icon: 'i-check', text: t('boxNeverChecked'), tone: '' };
  }
  function appendNotice(more) { if (updNotice) more.appendChild(el('p', { class: 'box-note', role: 'status', text: updNotice })); }
  function notesLink(url) {
    return el('p', { class: 'box-note' }, [el('a', { class: 'box-link', href: url, target: '_blank', rel: 'noopener noreferrer' },
      [el('span', { text: t('boxNotes') }), env.icon('i-external')])]);
  }
  function updateButton(textKey) {
    var b = button('btn-primary', textKey, onUpdate, { id: 'boxUpdateBtn' });
    b.disabled = !!upd; return b;
  }
  function checkButton(checking) {
    var b = button('', checking ? 'boxChecking' : 'boxCheckAgain', onCheck, { id: 'boxCheckBtn' });
    b.disabled = !!upd; return b;
  }
  function signedOutBlock() {
    var b = button('btn-primary', 'boxSignInAgain', function () { env.endSession(); }, { id: 'boxSignInBtn' });
    return el('div', { class: 'box-signedout' }, [el('p', { class: 'box-warn', text: t('boxSignedOut') }), b]);
  }

  function renderAuto() {
    if (!ui || !env.model()) return;
    if (document.activeElement !== ui.auto.input) ui.auto.input.checked = !!(st && st.update.auto);
  }

  function renderHealth() {
    if (!ui) return;
    // The box stopped answering: its old readings ("Filtering: On", "last device seen just now") are not shown as if they were current.
    ui.hRows.hidden = healthStale; ui.hZone.hidden = healthStale; ui.hStale.hidden = !healthStale;
    if (healthStale) { ui.hNone.hidden = true; return; }
    renderZone();
    if (!health) {
      [ui.hFilter, ui.hUptime, ui.hTemp, ui.hMem, ui.hSeen, ui.hClock].forEach(function (r) { setRow(r, '…', ''); });
      return;
    }
    var h = health;
    var b = h.blocking && h.blocking.blocking;
    if (b === 'enabled') setRow(ui.hFilter, t('boxFilterOn'), '', 'ok');
    else if (b === 'disabled') setRow(ui.hFilter, t('boxFilterOff'), t('boxFilterOffNote'), 'bad');
    else setRow(ui.hFilter, h.blocking ? t('boxFilterUnknown') : t('boxNotAvailable'), '', '');

    var up = h.system && h.system.uptime;
    setRow(ui.hUptime, typeof up === 'number' && isFinite(up) ? durationText(up) : t('boxNotAvailable'), '', '');

    var tp = temperature(h.sensors);
    if (!tp) setRow(ui.hTemp, h.sensors ? t('boxTempNone') : t('boxNotAvailable'), '', '');
    else if (tp.word === 'hot') setRow(ui.hTemp, t('boxTempHot', { n: iso(tp.c + ' °C') }), t('boxTempHotNote'), 'bad');
    else setRow(ui.hTemp, t(tp.word === 'warm' ? 'boxTempWarm' : 'boxTempCool', { n: iso(tp.c + ' °C') }), '', tp.word === 'warm' ? 'warn' : 'ok');

    var mem = memory(h.system);
    setRow(ui.hMem, mem ? t(mem.word === 'full' ? 'boxMemFull' : mem.word === 'busy' ? 'boxMemBusy' : 'boxMemOk', { p: iso(mem.percent + '%') }) : t('boxNotAvailable'), '',
      mem ? (mem.word === 'ok' ? 'ok' : mem.word === 'busy' ? 'warn' : 'bad') : '');

    var dev = lastDevice(h.devices);
    setRow(ui.hSeen, dev ? (dev.name ? t('boxLastSeenValue', { n: dev.name, t: agoText(env.serverNowSec() - dev.lastQuery) }) : agoText(env.serverNowSec() - dev.lastQuery))
      : (h.devices ? t('boxLastSeenNone') : t('boxNotAvailable')), '', '');

    var gap = Math.round(env.clockOffsetMs() / 1000);
    var boxTime = new Date(env.serverNowSec() * 1000);
    var ok = Math.abs(gap) <= 120;
    // The box's own wall-clock time when box.json tells its offset; else this phone's idea of that moment.
    var shown = boxWallTime(boxInfo(), env.serverNowSec(), env.locale()) || boxTime.toLocaleTimeString(env.locale(), { hour: '2-digit', minute: '2-digit' });
    setRow(ui.hClock, shown, ok ? t('boxClockOk') : t('boxClockOff', { t: durationText(roundGap(gap)) }), ok ? 'ok' : 'warn');

    var anyData = h.system || h.sensors || h.blocking || h.host || h.devices;
    ui.hNone.hidden = !!anyData;
  }

  /** The note under the clock row: the box's time zone, a warning when it is universal time, and the old general wording on a box that does not say. */
  function renderZone() {
    var z = zoneInfo(boxInfo(), -new Date().getTimezoneOffset()), text, warn = false;
    if (z.kind === 'utc') { text = t('boxClockZoneUtc'); warn = true; }
    else if (z.kind === 'zone') text = t('boxClockZoneKnown', { z: iso(z.label) }) + (z.differs ? ' ' + t('boxClockZoneDiffers') : '');
    else text = t('boxClockZone');
    if (ui.hZone.textContent !== text) ui.hZone.textContent = text;
    ui.hZone.className = warn ? 'box-warn' : 'box-note';
  }

  function copyButton(what, value) {
    var b = el('button', { type: 'button', class: 'btn btn-small box-copy', 'data-copy': value, 'aria-label': t('boxCopyAria', { what: what }) },
      [env.icon('i-copy'), el('span', { text: t('boxCopy') })]);
    b.addEventListener('click', function () { copyText(value, b); });
    return b;
  }
  function renderNetwork() {
    if (!ui) return;
    var info = boxInfo();
    var rows = addressRows({ hostname: root.location.hostname, port: root.location.port, boxIp: info && info.ip, hosts: hosts, nodename: health && health.host && health.host.host && health.host.host.uname && health.host.host.uname.nodename });
    var sig = JSON.stringify(rows) + env.lang();
    if (ui.netSig === sig) return;
    ui.netSig = sig;
    ui.netList.textContent = '';
    if (!rows.length) { ui.netList.appendChild(el('li', { class: 'box-note', text: t('boxNetNone') })); return; }
    var labels = { ip: ['boxNetIp', 'boxNetIpNote'], name: ['boxNetName', 'boxNetNameNote'], local: ['boxNetLocal', 'boxNetLocalNote'] };
    rows.forEach(function (r) {
      var l = labels[r.kind];
      ui.netList.appendChild(el('li', { class: 'box-addr-row' }, [
        el('span', { class: 'box-label', text: t(l[0]) }),
        el('span', { class: 'box-addr-value', dir: 'ltr', text: r.value }),
        el('span', { class: 'box-note', text: t(l[1]) }),
        copyButton(t(l[0]), r.value)]));
    });
  }

  /** The counter is offered only when the box says a counter address is configured (box.json), and Sinko's state exists to keep the answer.
   *  No box.json (an older box) = hidden: better silent than a promise nothing keeps. */
  function counterAvailable() { var i = boxInfo(); return !!(env.model() && i && i.counter === true); }
  function renderCounter() {
    if (!ui) return;
    var avail = counterAvailable();
    ui.counterCard.hidden = !avail;
    if (!avail) { ui.counterOnline.hidden = true; return; }
    if (document.activeElement !== ui.counter.input) ui.counter.input.checked = !!(st && st.telemetry.on === true);
    var key = st && st.community ? pure.counterOnlineKey(st.community.online) : null;
    var show = !!key && st.telemetry.on !== false;
    ui.counterOnline.hidden = !show;
    if (show) ui.counterOnline.textContent = t(key, { n: env.formatCount(st.community.online) });
  }

  function renderAbout() {
    if (!ui) return;
    var v = env.version();
    ui.aboutVersion.textContent = v ? t('boxAboutVersion', { v: iso(v) }) : t('appName');
    ui.aboutPihole.hidden = !pihole;                           // a box that does not say stays quiet: no empty row, no apology
    if (pihole) ui.aboutPihole.textContent = t('boxAboutPihole', { v: iso(pihole) });
    var l = links || {};
    var items = [['home', 'boxLinkHome'], ['issues', 'boxLinkIssues'], ['releases', 'boxLinkReleases'], ['privacy', 'boxLinkPrivacy'],
      ['license', 'boxLinkLicense'], ['support', 'boxLinkSupport'], ['buy', 'boxLinkBuy']].filter(function (i) { return l[i[0]]; });
    var sig = items.map(function (i) { return l[i[0]]; }).join(',') + env.lang();
    if (ui.aboutSig === sig) return;
    ui.aboutSig = sig;
    ui.aboutLinks.textContent = '';
    items.forEach(function (i) {
      ui.aboutLinks.appendChild(el('li', null, [el('a', { class: 'box-link', href: l[i[0]], target: '_blank', rel: 'noopener noreferrer' },
        [el('span', { text: t(i[1]) }), env.icon('i-external')])]));
    });
  }

  /**
   * The buttons wait while a request of this page is being carried out and while an update runs or is on its way (the box never switches off
   * in the middle of an installation, and drops a request made then). While an update is busy the only thing said is to wait: never "unplug it".
   */
  function renderPower() {
    if (!ui) return;
    var p = power, hold = busy();
    ui.restartBtn.disabled = powerActive() || hold; ui.shutdownBtn.disabled = powerActive() || hold;
    var msg = '', bad = false;
    if (p && p.stage === 'asked') msg = t('boxPowerSent');
    else if (p && p.stage === 'acted') msg = p.action === 'poweroff' ? t('boxPowerOff') : t('boxPowerRestarting');
    else if (hold) msg = t('boxPowerWaitUpdate');
    else if (p && p.stage === 'dropped') msg = t('boxPowerDropped');
    else if (p && p.stage === 'stuck') { msg = t('boxPowerStuck'); bad = true; }
    if (ui.powerMsg.textContent !== msg) ui.powerMsg.textContent = msg;
    ui.powerMsg.className = 'box-msg' + (bad ? ' is-bad' : '');
  }

  // ---------------------------------------------------------------- loading
  function stopPolling() { if (poller) { poller.stop(); poller = null; } }
  function startPolling() {
    stopPolling();
    poller = new C.Poller({
      tasks: [{ name: 'state', every: 4000, run: pollState }, { name: 'health', every: 15000, run: loadHealth }],
      visible: function () { return document.visibilityState === 'visible'; },
      maxBackoff: 6000, stagger: 150,
      onError: function (name, err) { if (err && err.status === 0) markDown(); }
    });
    setFast(false);
    poller.start();
  }
  function setFast(fast) { if (poller) poller.tasks[0].every = fast || upd || powerActive() ? 1500 : 4000; }
  /** After a request was written: look at the state right away instead of waiting for the next round. */
  function pollSoon() { setFast(true); if (poller) pollState().catch(function (e) { if (e && e.status === 0) markDown(); }); }
  /** The health card gives up its old readings once the box has been silent for `timing.healthDown` (a single dropped answer does not blank it). */
  function goStale(started) {
    if (down.since !== started || healthStale || !ui) return;
    healthStale = true; health = null;
    renderHealth();
  }
  function markDown() {
    if (!down.since) {
      down.since = nowMs();
      var started = down.since;
      setTimeout(function () { goStale(started); }, timing.healthDown + 50);
    }
    if (power) {
      power.sawDown = true;
      // The box went quiet right after we asked it to restart or shut down: that is it acting, even if the page never saw the
      // request being picked up (the box can be gone before the next look at the state). Not during an update: then the box restarts its
      // own services all the time, and it does not act on a restart asked for meanwhile.
      if (power.written && power.stage === 'asked' && !busy()) { power.stage = 'acted'; power.actedAt = nowMs(); }
    }
    // Silence for a while: the health card stops showing readings that may no longer be true (see goStale).
    if (!healthStale && nowMs() - down.since >= timing.healthDown) { healthStale = true; health = null; }
    renderHealth(); renderPower(); renderUpdate();
  }
  function sessionLost() {
    // The box restarted (a restart asked for here) or restarted its services (an update) and forgot our session. Say so, instead of
    // bouncing a parent to the sign-in screen with no explanation while something they started is still going on.
    if (powerActive() && !busy()) { env.endSession(t('boxBackNote')); return; }
    if (busy()) {
      signedOut = true; stopPolling(); updSig = ''; renderUpdate(); return;
    }
    env.endSession();
  }

  function readState() {
    return quiet('GET', '/api/groups/pb-state').then(function (j) {
      var g = (j.groups || [])[0];
      return C.parseState(g ? g.comment : '');
    });
  }
  function pollState() {
    return readState().then(function (next) {
      var wasDown = !!down.since;
      down.since = 0;
      st = next;
      trackFlows();
      // The box went away and came back after a restart we asked for: it has forgotten our session, so sign in again.
      if (power && wasDown && power.sawDown && power.stage === 'acted') { env.endSession(t('boxBackNote')); return; }
      if (wasDown && healthStale) loadHealth().catch(function () {});          // the box is back: read it again now, not in a quarter of a minute
      renderUpdate(); renderPower(); renderAuto(); renderCounter();
      if (wasDown && !upd && !powerActive()) setFast(false);
    }, function (err) {
      if (err && err.status === 401) { sessionLost(); return; }
      throw err;
    });
  }
  /** Advances what the page is waiting for (an update, a check, a restart) from the state it just read. */
  function trackFlows() {
    var u = st.update, view = updateView(st, { now: env.serverNowSec(), current: env.version() });
    // Nothing is judged before our own write has landed: until then the state still shows an older attempt.
    if (upd && !upd.written) { /* wait for the write */ }
    else if (upd && upd.kind === 'update') {
      var active = view.phase === 'running' || view.phase === 'starting';
      if (active) upd.seen = true;
      // Over when it ended one way or the other, or when the request is gone without the scheduler ever having started (withdrawn, below).
      if (view.phase === 'ok' || view.phase === 'failed' || view.phase === 'stalled' || (!active && (upd.seen || nowMs() - upd.writtenAt > 3000))) {
        upd = null; setFast(false);
      }
    } else if (upd && upd.kind === 'check') {
      var waited = nowMs() - upd.since;
      if (u.checked > upd.checkedBefore) { upd = null; updNotice = ''; setFast(false); }
      else if (u.checkRequest === null && waited > timing.checkOffline) { upd = null; updNotice = t('boxCheckOffline'); setFast(false); }
      else if (waited > timing.checkSlow) { upd = null; updNotice = t('boxCheckSlow'); setFast(false); }
    }
    // An update that finished (watched here, or while nobody was looking: automatic at night, or started on another phone).
    if (view.phase === 'ok' && u.at !== okSeenAt) { okSeenAt = u.at; afterUpdateOk(view.to); }
    if (power && power.written) trackPower();
    sweepStuckRequest(st);
    sweepStuckPower(st);
  }

  /**
   * An update request that nobody picks up means the scheduler on the box is not running. The card must not show a progress bar for
   * ever (and the main page must not say "Sinko is updating"): after a while the request is withdrawn, so it cannot fire later by
   * surprise, and the parent is told what to do. Whoever wrote the request, and whichever screen is open (it is called from both):
   * only this page's own watching time counts, never a clock.
   */
  function sweepStuckRequest(state) {
    var u = state.update;
    if (u.request === null || u.status === 'running') { pending = null; return; }
    if (!pending || pending.marker !== u.request) { pending = { marker: u.request, at: nowMs(), withdrawing: false }; return; }
    if (pending.withdrawing || nowMs() - pending.at < timing.startLost) return;
    var mine = pending;
    mine.withdrawing = true;
    env.writeState(function (s) { if (s.update.request === mine.marker) s.update.request = null; }).then(function () {
      if (pending === mine) pending = null;
      if (upd && upd.kind === 'update') { upd = null; setFast(false); }
      updNotice = t('boxUpdateStuck');
      if (ui) { st = env.state(); updSig = ''; renderUpdate(); }
      env.toast(t('boxUpdateStuck'), true);
      env.reload().catch(function () {});                    // the banner on the main page goes back to "ready"
    }, function () { mine.withdrawing = false; });
  }
  /** Takes back this page's own power request (by its marker), so a request nobody answered cannot fire later by surprise. */
  function withdrawOwnPower(p) {
    if (!p.marker) return;
    env.writeState(function (s) { if (s.power.request === p.marker) s.power = { request: null, action: null }; }).catch(function () {});
  }
  function trackPower() {
    var p = power, waited = nowMs() - p.since, hold = busy();
    if (p.stage === 'asked') {
      if (st.power.request === null) {
        // The box took the request out of the state. While an update runs it did that to drop it, not to act on it.
        if (hold) p.stage = 'dropped'; else { p.stage = 'acted'; p.actedAt = nowMs(); }
      } else if (hold) {
        // An update started while the request waited: the box will not act on it. Take it back, and say so calmly. Never "unplug it".
        p.stage = 'dropped'; withdrawOwnPower(p);
      } else if (waited > timing.powerStuck) {
        // Nobody picked the request up: the scheduler on the box is not running. Withdraw it, so it cannot fire later by surprise.
        p.stage = 'stuck'; withdrawOwnPower(p);
      }
    } else if (p.stage === 'acted' && !p.sawDown && nowMs() - p.actedAt > timing.powerNoRestart) {
      p.stage = 'stuck';
    }
    setFast(false);
    renderPower();
  }
  /**
   * A restart or shut-down request that nobody takes means the scheduler is not running, and it would fire whenever it next looks. After a
   * minute of this page watching the same marker (whoever wrote it, whichever screen is open) it is withdrawn. A request made while an
   * update runs is dropped by the box at once, so it never gets here.
   */
  function sweepStuckPower(state) {
    var m = state.power.request;
    if (m === null) { pendingPower = null; return; }
    if (!pendingPower || pendingPower.marker !== m) { pendingPower = { marker: m, at: nowMs(), withdrawing: false }; return; }
    if (pendingPower.withdrawing || nowMs() - pendingPower.at < timing.powerStale) return;
    var mine = pendingPower;
    mine.withdrawing = true;
    env.writeState(function (s) { if (s.power.request === mine.marker) s.power = { request: null, action: null }; }).then(function () {
      if (pendingPower === mine) pendingPower = null;
    }, function () { mine.withdrawing = false; });
  }

  function checkInstalledVersion() {
    // The page itself may be older than the box (an automatic update ran at night, or another phone started it).
    return fetchVersion().then(function (v) {
      if (v && env.version() && v !== env.version() && st && st.update.status !== 'running') reloadFor(v);
    });
  }
  function fetchVersion() {
    return fetch('/pb/version.txt', { cache: 'no-store' }).then(function (r) { return r.ok ? r.text() : ''; })
      .then(function (s) { s = s.trim(); return parseSemver(s) ? s : ''; }).catch(function () { return ''; });
  }
  function afterUpdateOk(to) {
    if (reloading) return;
    fetchVersion().then(function (v) {
      if (v && v !== env.version()) reloadFor(v);
      else renderUpdate();
    });
  }
  function reloadFor(v) {
    if (reloading || (env.reloadedFor && env.reloadedFor() === v)) return;     // once per version in this tab: a reload that did not help is not repeated
    reloading = true;
    env.rememberUpdate(v);
    updSig = ''; if (ui) renderUpdate();
    setTimeout(function () { root.location.reload(); }, 1200);
  }

  function loadHealth() {
    // Each reading may fail on its own (a board with no sensor, an older Pi-hole): the rest still shows.
    var netFail = null;
    var get = function (path) {
      return quiet('GET', path).then(function (j) { return j; }, function (e) {
        if (e && e.status === 401) throw e;
        if (e && e.status === 0) netFail = e;
        return null;
      });
    };
    return Promise.all([get('/api/info/system'), get('/api/info/sensors'), get('/api/dns/blocking'), get('/api/info/host'),
      get('/api/network/devices?max_devices=200&max_addresses=1')]).then(function (r) {
      if (netFail && !r.some(Boolean)) throw netFail;               // the box is not answering at all: let the poller back off
      health = { system: r[0] && r[0].system, sensors: r[1] && r[1].sensors, blocking: r[2], host: r[3], devices: r[4] && r[4].devices };
      healthStale = false;
      renderHealth(); renderNetwork();
    }, function (err) {
      if (err && err.status === 401) { sessionLost(); return; }
      throw err;
    });
  }
  function loadHosts() {
    quiet('GET', '/api/config/dns/hosts').then(function (j) {
      var h = j && j.config && j.config.dns && j.config.dns.hosts;
      hosts = h instanceof Array ? h : [];
      renderNetwork();
    }, function () { hosts = []; renderNetwork(); });
  }
  /** Which Pi-hole this box runs, for About: it is the parent's one view of how old the Pi-hole on a sealed box is (it changes only by re-flashing).
   *  Read each time the sheet opens, not with the 15-second health round: it hardly ever changes. An answer that is not a plain version, or no
   *  answer (an older Pi-hole, a box that is busy), leaves what was known; nothing is said about the failure. */
  function loadPihole() {
    quiet('GET', '/api/info/version').then(function (j) {
      var v = piholeVersion(j);
      if (v && v !== pihole) { pihole = v; renderAbout(); }
    }, function () { /* hidden quietly: About is complete without it */ });
  }
  function loadLinks() {
    if (links) return;
    fetch('/pb/links.json', { cache: 'no-store' }).then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) { links = pure.safeLinks(j); renderAbout(); }).catch(function () { links = {}; });
  }

  // ---------------------------------------------------------------- actions
  function onUpdate() {
    if (upd) return;
    var mine = upd = { kind: 'update', since: nowMs(), seen: false, written: false };
    updNotice = ''; updSig = ''; renderUpdate();
    focusStatus();                                           // the button the parent pressed is gone now: focus moves to the status
    env.writeState(function (s) { s.update.request = requestTime(); }).then(function () { mine.written = true; mine.writtenAt = nowMs(); pollSoon(); }, function (e) {
      if (upd === mine) upd = null;
      updSig = ''; renderUpdate();
      env.toast(t('failed', { e: e && e.message ? e.message : String(e) }), true);
    });
  }
  function focusStatus() { if (ui && ui.updLive) ui.updLive.focus({ preventScroll: true }); }
  function onCheck() {
    if (upd) return;
    var mine = upd = { kind: 'check', since: nowMs(), checkedBefore: st ? st.update.checked : 0, written: false };
    updNotice = ''; updSig = ''; renderUpdate();
    env.writeState(function (s) { s.update.checkRequest = requestTime(); }).then(function () { mine.written = true; mine.since = nowMs(); pollSoon(); }, function (e) {
      if (upd === mine) upd = null;
      updSig = ''; renderUpdate();
      env.toast(t('failed', { e: e && e.message ? e.message : String(e) }), true);
    });
  }
  function onAuto() {
    var want = ui.auto.input.checked;
    env.writeState(function (s) { s.update.auto = want; }).then(function () {
      st = env.state(); env.toast(t(want ? 'boxAutoOn' : 'boxAutoOff'));
    }, function (e) {
      ui.auto.input.checked = !want;
      env.toast(t('failed', { e: e && e.message ? e.message : String(e) }), true);
    });
  }
  function onCounter() {
    var want = ui.counter.input.checked;
    env.writeState(function (s) { s.telemetry = { on: want }; }).then(function () {
      st = env.state(); renderCounter(); env.toast(t(want ? 'boxCounterOnToast' : 'boxCounterOffToast'));
    }, function (e) {
      ui.counter.input.checked = !want;
      env.toast(t('failed', { e: e && e.message ? e.message : String(e) }), true);
    });
  }

  // -- copy
  function copyText(text, btn) {
    var done = function () {
      var label = btn.querySelector('span');
      if (label) { label.textContent = t('boxCopied'); setTimeout(function () { label.textContent = t('boxCopy'); }, 1800); }
      env.toast(t('boxCopied'));
    };
    var fallback = function () {
      // Plain http on a home network is not a secure context, so the clipboard API is missing: select a hidden field and copy.
      // It lives inside the open modal dialog, because everything outside a modal dialog cannot take the selection.
      var ta = document.createElement('textarea');
      ta.value = text; ta.setAttribute('readonly', ''); ta.setAttribute('aria-hidden', 'true'); ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0';
      document.getElementById('boxDialog').appendChild(ta);
      ta.focus(); ta.select();
      var ok = false;
      try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
      ta.remove(); btn.focus();
      if (ok) done(); else env.toast(t('boxCopyFail'), true);
    };
    if (navigator.clipboard && navigator.clipboard.writeText && root.isSecureContext) navigator.clipboard.writeText(text).then(done, fallback);
    else fallback();
  }

  // -- change password
  function onPassword(ev) {
    ev.preventDefault();
    if (ui.pwBusy) return;
    var cur = ui.pwCur.value, next = ui.pwNew.value, again = ui.pwNew2.value, code = ui.pwCode.value.trim();
    ui.pwErr.textContent = '';
    if (!cur) { ui.pwErr.textContent = t('boxPwNeedCurrent'); ui.pwCur.focus(); return; }
    var problem = passwordProblem(cur, next, again);
    if (problem) { ui.pwErr.textContent = t(problem); (problem === 'boxPwMismatch' ? ui.pwNew2 : ui.pwNew).focus(); return; }
    if (!ui.pwCodeField.hidden && !/^\d{6}$/.test(code)) { ui.pwErr.textContent = t('boxPwNeedCode'); ui.pwCode.focus(); return; }
    ui.pwBusy = true; ui.pwBtn.disabled = true; ui.pwBtn.firstChild.textContent = t('boxPwChanging');
    var done = function () { ui.pwBusy = false; ui.pwBtn.disabled = false; ui.pwBtn.firstChild.textContent = t('boxPwChange'); };
    var login = { password: cur };
    if (!ui.pwCodeField.hidden) login.totp = Number(code);
    // 1. Prove the current password the way signing in does. That opens a second session, which is deleted again right away.
    env.call('POST', '/api/auth', login, { noSid: true, quiet401: true }).then(function (j) {
      if (!(j && j.session && j.session.valid)) { var bad = new Error('password'); bad.status = 401; bad.body = j; throw bad; }
      var vsid = j.session.sid;
      return (vsid ? quiet('DELETE', '/api/auth', undefined, { sid: vsid }).catch(function () {}) : Promise.resolve());
    }).then(function () {
      // 2. Set the new one. Pi-hole ends every session when the password changes, including this one.
      return env.call('PATCH', '/api/config', { config: { webserver: { api: { password: next } } } });
    }).then(function () {
      done(); clearSecrets();
      env.endSession(t('loginPwChanged'));
    }, function (e) {
      done();
      var j = e && e.body && e.body.session;
      // Two-factor sign-in: Pi-hole says a code is needed (400) or wrong (401). Ask for it, once.
      if (e && ((e.status === 400 && /totp|2fa/i.test(e.message || '')) || (e.status === 401 && j && j.totp)) && ui.pwCodeField.hidden) {
        ui.pwCodeField.hidden = false; ui.pwErr.textContent = t('boxPwNeedCode'); ui.pwCode.focus(); return;
      }
      if (e && e.status === 401 && j) {
        ui.pwErr.textContent = t(j.totp ? 'wrongTotp' : 'boxPwWrong'); (j.totp ? ui.pwCode : ui.pwCur).focus(); return;
      }
      if (e && e.status === 429) { ui.pwErr.textContent = t('boxPwTooMany'); return; }
      if (e && e.status === 401) return;                  // the page's own session ended: the sign-in screen is already showing
      ui.pwErr.textContent = t('boxPwFailed', { e: e && e.message ? e.message : String(e) });
    });
  }

  // -- backup and restore
  function onBackup() {
    if (ui.backupBusy) return;
    ui.backupBusy = true; ui.backupBtn.disabled = true; ui.backupBtn.firstChild.textContent = t('boxBackupBusy'); ui.backupMsg.textContent = '';
    ui.backupMsg.className = 'box-msg';
    env.call('GET', '/api/teleporter', undefined, { blob: true }).then(function (r) {
      var url = root.URL.createObjectURL(r.blob);
      var a = document.createElement('a');
      a.href = url; a.download = backupName(new Date()); a.style.display = 'none';
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(function () { root.URL.revokeObjectURL(url); }, 20000);
      ui.backupMsg.textContent = t('boxBackupDone');
    }).catch(function (e) {
      if (e && e.status === 401) return;
      ui.backupMsg.className = 'box-msg is-bad';
      ui.backupMsg.textContent = t('boxBackupFail', { e: e && e.message ? e.message : String(e) });
    }).then(function () { ui.backupBusy = false; ui.backupBtn.disabled = false; ui.backupBtn.firstChild.textContent = t('boxBackupDownload'); });
  }
  function onFilePicked() {
    var f = ui.file.files && ui.file.files[0];
    ui.restoreMsg.textContent = ''; ui.restoreMsg.className = 'box-msg';
    ui.fileName.textContent = f ? f.name : t('boxRestoreNone');
    ui.restoreBtn.disabled = !f || restoring;
  }
  function restoreFail(key, e) {
    ui.restoreMsg.className = 'box-msg is-bad';
    ui.restoreMsg.textContent = key === 'boxRestoreBad' ? t(key) : t(key, { e: e && e.message ? e.message : String(e) });
  }
  function postImport(blob, name) {
    var fd = new root.FormData();
    fd.append('file', blob, name);
    fd.append('import', JSON.stringify(RESTORE_IMPORT));
    return env.call('POST', '/api/teleporter', undefined, { form: fd });
  }
  function retry(fn, times, ms) {
    return fn().catch(function (e) {
      if (times <= 1 || (e && e.status === 401)) throw e;
      return new Promise(function (res) { setTimeout(res, ms); }).then(function () { return retry(fn, times - 1, ms); });
    });
  }
  /**
   * Asks the box to rebuild its block lists and waits until it has finished (a few minutes on a small board). Pi-hole answers 200 and streams
   * the text of the run first, so the verdict comes from the text. Never throws: a rebuild that fails must not turn a restore that worked into
   * "could not be restored" (the lists are rebuilt by the box overnight anyway).
   */
  function rebuildLists() {
    return env.call('POST', '/api/action/gravity', undefined, { text: true, quiet401: true }).then(function (r) { return gravityVerdict(r && r.text); },
      function () { return 'partial'; });
  }
  function onRestore() {
    var f = ui.file.files && ui.file.files[0];
    if (!f || restoring) return;
    ui.restoreMsg.textContent = ''; ui.restoreMsg.className = 'box-msg';
    if (f.size > 20 * 1024 * 1024) { restoreFail('boxRestoreBad'); return; }
    env.confirm(t('boxRestoreAsk', { f: iso(f.name) }), t('boxRestoreButton')).then(function (yes) {
      if (!yes) return;
      restoring = true; ui.restoreBtn.disabled = true; ui.restoreBtn.firstChild.textContent = t('boxRestoreBusy');
      var before = null, safety = null, bad = false;
      // The state before: what the restore must not take away from this box (see keepBoxFields). A copy of everything now: the way
      // back if the file turns out not to be a Sinko backup.
      readState().then(function (s) { before = s; })
        .then(function () { return env.call('GET', '/api/teleporter', undefined, { blob: true }).then(function (r) { safety = r.blob; }); })
        .then(function () { return postImport(f, f.name); })
        .then(function (answer) {
          // Pi-hole accepts a zip with nothing in it that it can use (a settings-only export, an unrelated file) and says so only in the list of
          // what it processed: that is "nothing was changed", not a restore.
          if (restoreVerdict(answer) === 'nothing') { var none = new Error('nothing imported'); none.status = 400; throw none; }
          return retry(function () { return quiet('GET', '/api/groups'); }, 4, 1000);
        })
        .then(function (j) {
          if (hasSinkoGroups(j.groups)) return null;
          bad = true;                                              // Sinko's groups are gone: put everything back as it was
          return postImport(safety, 'safety.zip');
        })
        .then(function () {
          if (bad) return null;
          return retry(function () { return env.writeState(keepBoxFields(before)); }, 3, 1000);
        })
        .then(function () { return env.reload().catch(function () {}); })
        .then(function () {
          if (bad) { restoreFail('boxRestoreBad'); return null; }
          st = env.state(); renderAll();
          ui.file.value = ''; ui.fileName.textContent = t('boxRestoreNone');
          // The import brings back the lists with the numbers they had in the file, and the box's own table of blocked sites is keyed by the
          // numbers it had before: until the lists are rebuilt, the apps and sites that were blocked may not be (or the wrong ones are).
          ui.restoreMsg.textContent = t('boxRestoreGravity');
          return rebuildLists().then(function (verdict) {
            ui.restoreMsg.className = 'box-msg' + (verdict === 'ok' ? '' : ' is-warn');
            ui.restoreMsg.textContent = t(verdict === 'ok' ? 'boxRestoreDone' : 'boxRestorePartial');
          });
        })
        .catch(function (e) {
          if (e && e.status === 401) return;
          // 400 = Pi-hole refused the file before changing anything.
          if (e && e.status === 400) restoreFail('boxRestoreBad'); else restoreFail('boxRestoreFail', e);
          env.reload().catch(function () {});
        })
        .then(function () { restoring = false; ui.restoreBtn.firstChild.textContent = t('boxRestoreButton'); ui.restoreBtn.disabled = !(ui.file.files && ui.file.files[0]); });
    });
  }

  // -- restart and shut down
  function onPower(action) {
    if (powerActive() || busy()) { renderPower(); return; }
    env.confirm(t(action === 'reboot' ? 'boxRestartAsk' : 'boxShutdownAsk'), t(action === 'reboot' ? 'boxRestart' : 'boxShutdown')).then(function (yes) {
      // Asked again after the question: an update may have started on another phone while it was open.
      if (!yes || powerActive() || busy()) { renderPower(); return; }
      var mine = power = { action: action, since: nowMs(), stage: 'asked', sawDown: false, written: false, marker: 0 };
      renderPower(); setFast(true);
      env.writeState(function (s) {
        // The write itself refuses, from what is stored now: the page's picture of the update may be a few seconds old.
        if (updateBusy(s)) { var busyErr = new Error('update'); busyErr.updateBusy = true; throw busyErr; }
        mine.marker = requestTime();
        s.power = { request: mine.marker, action: action };
      }).then(function () {
        mine.written = true; mine.since = nowMs();
        ui.powerMsg.focus({ preventScroll: true });
        pollSoon();
      }, function (e) {
        if (power !== mine) return;
        if (e && e.updateBusy) { mine.stage = 'dropped'; pollSoon(); }                // nothing was written: say why, calmly
        else { power = null; env.toast(t('failed', { e: e && e.message ? e.message : String(e) }), true); }
        renderPower(); setFast(false);
      });
    });
  }

  /** For the main page, which looks at the state too: it withdraws a stuck update request even when this sheet is closed. */
  function sweep(state) { if (env && env.model()) { sweepStuckRequest(state); sweepStuckPower(state); } }

  var api = { strings: BOX_STR, pure: pure, timing: timing, init: init, open: open, relang: relang, reset: reset, sweep: sweep };
  root.PBBox = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : this);
