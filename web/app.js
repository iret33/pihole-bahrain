/* Sinko — parent page for sinko.
 *
 * Talks only to the local Pi-hole v6 API (same origin), signed in with the
 * Pi-hole admin password. Blocking is done by enabling/disabling Pi-hole
 * groups (see bin/sinko for the model). Timers and bedtime are stored
 * in the "pb-state" group and enforced by the sinko service on the Pi,
 * so they keep working after this page is closed.
 */
'use strict';

(function () {
  // ------------------------------------------------------------------ config
  var G = { kids: 'pb-kids', guard: 'pb-guard', offline: 'pb-offline', paused: 'pb-paused', state: 'pb-state' };
  var SVC = 'pb-svc-';
  var DEFAULT_GROUP = 0;
  var POLL_MS = 30000;
  var SID_KEY = 'pb.sid';
  var LANG_KEY = 'pb.lang';

  // ------------------------------------------------------------------ strings
  var STR = {
    en: {
      appName: 'Sinko', loginHint: 'Sign in with the parent password chosen during setup.', password: 'Password',
      totp: '6-digit code from your authenticator app', signIn: 'Sign in', signOut: 'Sign out',
      wrongPassword: 'That password is not right. Try again.', wrongTotp: 'Enter the password and the 6-digit code.',
      noConnection: 'Cannot reach the family box. Check that it is switched on and connected.',
      tooMany: 'Too many open sessions. Sign out on another device or wait 30 minutes.',
      modes: 'Quick modes', homework: 'Homework', homeworkDesc: 'Games, video and social media blocked',
      freeTime: 'Free time', freeTimeDesc: 'Everything allowed for a while',
      breakTime: 'Offline break', breakTimeDesc: 'Internet off for a while',
      apps: 'Apps and sites', allowAll: 'Allow all', blockAll: 'Block all',
      svcNote: 'Tap to block or allow. Apps that are already open can take a few minutes to stop.',
      svcLocked: 'A timer is running. End it to change apps.',
      allowed: 'Allowed', blocked: 'Blocked',
      devices: "Children's devices", addDevice: 'Add device', noDevices: 'No devices yet. Add your child\u2019s phone or tablet so the rules apply to it.',
      pause: 'Pause', resume: 'Resume', remove: 'Remove', paused: 'Paused', thisDevice: 'This device',
      lastSeen: 'Last online {t}', neverSeen: 'Not seen yet',
      bedtime: 'Bedtime', bedtimeOn: 'Turn on bedtime',
      bedtimeDesc: 'Internet turns off for children\u2019s devices on these nights, and back on in the morning.',
      from: 'Off at', until: 'On at', nights: 'Nights', saveBedtime: 'Save bedtime', bedtimeSaved: 'Bedtime saved',
      pickNight: 'Pick at least one night.', sameTimes: 'Off and on times must be different.',
      advanced: 'Advanced settings', helpTitle: 'Setup help',
      helpBody: 'For the rules to work, your router must send all devices to this box for DNS. In the router settings, set the DNS server to {ip} and reserve that address for the box. Then turn Wi\u2011Fi off and on again on each child\u2019s device.',
      // When the page does not know the box's number address (no /pb/box.json, and the page was opened by a name): never a name in its place.
      helpBodyNoIp: 'For the rules to work, your router must send all devices to this box for DNS. In the router settings, set the DNS server to the box\u2019s number address (four numbers separated by dots) and reserve that address for the box. You can find the number in your router\u2019s list of connected devices: look for the box. Then turn Wi\u2011Fi off and on again on each child\u2019s device.', close: 'Close', cancel: 'Cancel', start: 'Start', add: 'Add',
      customMinutes: 'Or enter minutes', addHint: 'Pick your child\u2019s phone, tablet or console. Devices appear here after they have used the internet at home.',
      manual: 'Enter address by hand', addrLabel: 'MAC or IP address', nameLabel: 'Name',
      macTip: 'Tip: on the child\u2019s device, turn off \u201cPrivate Wi\u2011Fi address\u201d for your home network so it keeps the same address.',
      pickOrType: 'Pick a device or enter its address.', badAddr: 'Enter a MAC address like A4:83:E7:12:34:56 or an IP like 192.168.1.20.',
      noPicker: 'No new devices seen yet. Connect the device to your Wi\u2011Fi, open any website on it, then try again.',
      deviceAdded: '{n} added', deviceRemoved: '{n} removed', devicePaused: '{n} paused', deviceResumed: '{n} resumed',
      confirmRemove: 'Stop applying the rules to {n}?',
      heroOnKicker: 'Right now', heroOn: 'Children are online', heroOff: 'Children\u2019s internet is off',
      heroFree: 'Free time', heroBreak: 'Offline break', heroBedtime: 'Bedtime',
      turnOff: 'Turn internet off', turnOn: 'Turn internet on', endNow: 'End now',
      subBlocked: '{n} of {t} apps blocked.', subNone: 'No apps blocked.', subDevices: 'Rules apply to {n} devices.',
      subDevice1: 'Rules apply to 1 device.', subNoDevices: 'Add a device to start.',
      endsAt: 'Ends at {t}', bedUntil: 'Until {t}', bedEndsNote: 'Ends automatically at {t}.',
      timerFreeTitle: 'Free time', timerFreeDesc: 'All apps are allowed. When the time is up, the current rules come back.',
      timerBlockTitle: 'Offline break', timerBlockDesc: 'Internet is off for children\u2019s devices. When the time is up, it comes back on.',
      min30: '30 min', hour1: '1 hour', hours2: '2 hours', hours3: '3 hours',
      enterMinutes: 'Pick a length or enter 5 to 720 minutes.',
      homeworkOn: 'Homework mode on', freeOn: 'Free time started', breakOn: 'Offline break started', timerEnded: 'Timer ended',
      internetOff: 'Internet turned off', internetOn: 'Internet turned on',
      nowBlocked: '{n} blocked', nowAllowed: '{n} allowed', allAllowed: 'All apps allowed', allBlocked: 'All apps blocked',
      // Nothing here may need a keyboard on the box: parents cannot type commands. Unplugging and re-plugging restarts everything.
      schedulerDown: 'The timer on the box is not running. Unplug the box, plug it back in and wait two minutes. If it keeps happening, ask whoever set up the box.',
      notInstalled: 'Setup is not finished on this box. Wait a few minutes and reload this page. If it stays like this, ask whoever set up the box.',
      failed: 'That did not work: {e}', sessionEnded: 'Your session ended. Sign in again.',
      days: ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'],
      daysLong: ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'],
      shadowed: 'Pi-hole has another rule for this device\u2019s address ({r}), so these settings may not apply. Remove that rule in Pi-hole admin \u203A Clients.',
      shadowedToast: '{n} added, but Pi-hole has another rule ({r}) that overrides it. Remove that rule in Pi-hole admin.',
      ipv6Warn: 'This is an IPv6 address. Phones change their IPv6 privacy addresses often, so the rules would stop applying without warning. Use the device\u2019s MAC address instead if you can. Add it anyway?',
      cat: {}, langSwitch: 'العربية', minutesShort: '{n} min'
    },
    ar: {
      appName: 'سينكو', loginHint: 'سجّل الدخول بكلمة مرور الوالدين التي اخترتها أثناء الإعداد.', password: 'كلمة المرور',
      totp: 'الرمز المكوّن من 6 أرقام من تطبيق المصادقة', signIn: 'تسجيل الدخول', signOut: 'تسجيل الخروج',
      wrongPassword: 'كلمة المرور غير صحيحة. حاول مرة أخرى.', wrongTotp: 'أدخل كلمة المرور والرمز المكوّن من 6 أرقام.',
      noConnection: 'تعذّر الوصول إلى صندوق العائلة. تأكد أنه يعمل ومتصل بالشبكة.',
      tooMany: 'عدد الجلسات المفتوحة كبير. سجّل الخروج من جهاز آخر أو انتظر 30 دقيقة.',
      modes: 'أوضاع سريعة', homework: 'وقت الدراسة', homeworkDesc: 'حظر الألعاب والفيديو ومواقع التواصل',
      freeTime: 'وقت حر', freeTimeDesc: 'السماح بكل شيء لفترة محددة',
      breakTime: 'استراحة من الإنترنت', breakTimeDesc: 'إيقاف الإنترنت لفترة محددة',
      apps: 'التطبيقات والمواقع', allowAll: 'السماح للكل', blockAll: 'حظر الكل',
      svcNote: 'اضغط للحظر أو السماح. قد تستغرق التطبيقات المفتوحة بضع دقائق حتى تتوقف.',
      svcLocked: 'يوجد مؤقت يعمل الآن. أنهِه لتغيير التطبيقات.',
      allowed: 'مسموح', blocked: 'محظور',
      devices: 'أجهزة الأطفال', addDevice: 'إضافة جهاز', noDevices: 'لا توجد أجهزة بعد. أضف هاتف طفلك أو جهازه اللوحي لتُطبَّق عليه القواعد.',
      pause: 'إيقاف مؤقت', resume: 'استئناف', remove: 'إزالة', paused: 'متوقف مؤقتًا', thisDevice: 'هذا الجهاز',
      lastSeen: 'آخر اتصال {t}', neverSeen: 'لم يتصل بعد',
      bedtime: 'وقت النوم', bedtimeOn: 'تفعيل وقت النوم',
      bedtimeDesc: 'يتوقف الإنترنت عن أجهزة الأطفال في هذه الليالي، ويعود في الصباح.',
      from: 'الإيقاف الساعة', until: 'التشغيل الساعة', nights: 'الليالي', saveBedtime: 'حفظ وقت النوم', bedtimeSaved: 'تم حفظ وقت النوم',
      pickNight: 'اختر ليلة واحدة على الأقل.', sameTimes: 'يجب أن يختلف وقت الإيقاف عن وقت التشغيل.',
      advanced: 'إعدادات متقدمة', helpTitle: 'مساعدة في الإعداد',
      helpBody: 'لكي تعمل القواعد، يجب أن يوجّه جهاز الراوتر كل الأجهزة إلى صندوق العائلة لخدمة DNS. في إعدادات الراوتر، اجعل خادم DNS هو {ip} واحجز هذا العنوان للصندوق. ثم أطفئ Wi‑Fi وشغّله مجددًا على جهاز كل طفل.',
      helpBodyNoIp: 'لكي تعمل القواعد، يجب أن يوجّه جهاز الراوتر كل الأجهزة إلى صندوق العائلة لخدمة DNS. في إعدادات الراوتر، اجعل خادم DNS هو العنوان الرقمي للصندوق (أربعة أرقام تفصل بينها نقاط) واحجز هذا العنوان للصندوق. تجد الرقم في قائمة الأجهزة المتصلة في الراوتر: ابحث عن الصندوق. ثم أطفئ Wi‑Fi وشغّله مجددًا على جهاز كل طفل.', close: 'إغلاق', cancel: 'إلغاء', start: 'ابدأ', add: 'إضافة',
      customMinutes: 'أو أدخل عدد الدقائق', addHint: 'اختر هاتف طفلك أو جهازه اللوحي أو جهاز الألعاب. تظهر الأجهزة هنا بعد استخدامها للإنترنت في المنزل.',
      manual: 'إدخال العنوان يدويًا', addrLabel: 'عنوان MAC أو IP', nameLabel: 'الاسم',
      macTip: 'نصيحة: على جهاز الطفل، أوقف خيار «عنوان Wi‑Fi خاص» لشبكة المنزل حتى يحتفظ الجهاز بعنوان ثابت.',
      pickOrType: 'اختر جهازًا أو أدخل عنوانه.', badAddr: 'أدخل عنوان MAC مثل A4:83:E7:12:34:56 أو عنوان IP مثل 192.168.1.20.',
      noPicker: 'لم تظهر أجهزة جديدة بعد. وصّل الجهاز بشبكة Wi‑Fi وافتح أي موقع عليه، ثم حاول مجددًا.',
      deviceAdded: 'تمت إضافة {n}', deviceRemoved: 'تمت إزالة {n}', devicePaused: 'تم إيقاف {n} مؤقتًا', deviceResumed: 'تم استئناف {n}',
      confirmRemove: 'إيقاف تطبيق القواعد على {n}؟',
      heroOnKicker: 'الآن', heroOn: 'الأطفال متصلون بالإنترنت', heroOff: 'الإنترنت متوقف عن الأطفال',
      heroFree: 'وقت حر', heroBreak: 'استراحة من الإنترنت', heroBedtime: 'وقت النوم',
      turnOff: 'إيقاف الإنترنت', turnOn: 'تشغيل الإنترنت', endNow: 'إنهاء الآن',
      subBlocked: '{n} من {t} تطبيقات محظورة.', subNone: 'لا توجد تطبيقات محظورة.', subDevices: 'القواعد مطبّقة على {n} أجهزة.',
      subDevice1: 'القواعد مطبّقة على جهاز واحد.', subNoDevices: 'أضف جهازًا للبدء.',
      endsAt: 'ينتهي الساعة {t}', bedUntil: 'حتى الساعة {t}', bedEndsNote: 'ينتهي تلقائيًا الساعة {t}.',
      timerFreeTitle: 'وقت حر', timerFreeDesc: 'كل التطبيقات مسموحة. عند انتهاء الوقت تعود القواعد الحالية.',
      timerBlockTitle: 'استراحة من الإنترنت', timerBlockDesc: 'الإنترنت متوقف عن أجهزة الأطفال. عند انتهاء الوقت يعود.',
      min30: '30 دقيقة', hour1: 'ساعة', hours2: 'ساعتان', hours3: '3 ساعات',
      enterMinutes: 'اختر مدة أو أدخل من 5 إلى 720 دقيقة.',
      homeworkOn: 'تم تفعيل وقت الدراسة', freeOn: 'بدأ الوقت الحر', breakOn: 'بدأت الاستراحة', timerEnded: 'انتهى المؤقت',
      internetOff: 'تم إيقاف الإنترنت', internetOn: 'تم تشغيل الإنترنت',
      nowBlocked: 'تم حظر {n}', nowAllowed: 'تم السماح بـ {n}', allAllowed: 'تم السماح بكل التطبيقات', allBlocked: 'تم حظر كل التطبيقات',
      schedulerDown: 'المؤقت على صندوق العائلة لا يعمل. افصل الصندوق عن الكهرباء ثم أعد توصيله وانتظر دقيقتين. إذا تكرر ذلك فاسأل من أعدّ الصندوق.',
      notInstalled: 'الإعداد غير مكتمل على صندوق العائلة. انتظر بضع دقائق ثم أعد تحميل الصفحة. إذا استمر الأمر فاسأل من أعدّ الصندوق.',
      failed: 'لم تنجح العملية: {e}', sessionEnded: 'انتهت الجلسة. سجّل الدخول مجددًا.',
      days: ['أحد', 'اثنين', 'ثلاثاء', 'أربعاء', 'خميس', 'جمعة', 'سبت'],
      daysLong: ['الأحد', 'الاثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة', 'السبت'],
      shadowed: 'يوجد في Pi-hole قاعدة أخرى لعنوان هذا الجهاز ({r})، لذلك قد لا تُطبَّق هذه الإعدادات. احذف تلك القاعدة من لوحة Pi-hole ‹ العملاء.',
      shadowedToast: 'تمت إضافة {n}، لكن توجد قاعدة أخرى في Pi-hole ({r}) تتجاوزها. احذف تلك القاعدة من لوحة Pi-hole.',
      ipv6Warn: 'هذا عنوان IPv6. تغيّر الهواتف عناوين IPv6 الخاصة بها كثيرًا، فتتوقف القواعد عن العمل دون تنبيه. استخدم عنوان MAC للجهاز إن أمكن. هل تريد إضافته على أي حال؟',
      cat: {}, langSwitch: 'English', minutesShort: '{n} دقيقة'
    }
  };

  // ------------------------------------------------------------------ strings for the live picture (pb-picture.js)
  var LIVE_STR = {
    en: {
      lvWebsite: 'A website', lvDevice: 'Device', lvActive: 'Online now', lvQuiet: 'Quiet', lvEarlier: 'Earlier', lvNowShort: 'Now', lvWhyOff: 'Internet off', lvWhyPaused: 'Device paused', lvLookup: 'Looking up…', lvPaused: 'Paused', lvOffline: 'Internet off',
      lvOverridden: 'Rules may not apply', lvUnreachable: 'Not seen in 24 hours', lvUnknown: 'Rules set', lvUnwanted: 'An unwanted site', lvMore: '{n} more', lvAddDevice: 'Add a device', lvAddHint: 'Tap to start',
      lvAllHome: 'Whole home', lvAllHomeSt: 'Names are hidden', lvRest: 'Everyone else', lvRestSt: 'Basic filtering',
      lvNoKids: 'No children’s devices yet', lvNoKidsSub: 'Add a phone or tablet so the rules apply to it. Below, you can still watch the whole home.',
      lvFree: 'Free time is on', lvFreeSub: 'Apps are open until the timer ends. The safety lists stay on.',
      lvSetOne: 'The rules are set for {n}', lvSetTwo: 'The rules are set for both devices', lvSetAll: 'The rules are set for all {n} devices',
      lvWorkingTwo: 'The rules are working on both devices',
      lvBlockOff: 'Blocking is switched off', lvBlockOffSub: 'Nothing is being stopped. Turn blocking on again in Advanced settings, at the bottom of the page.',
      lvExamplePhone: 'Example phone', lvExExample: 'This is only an example phone, to show how the box works.', lvExAdd: 'Add your child\u2019s phone or tablet so the rules apply to it.',
      lvHide: 'Hide the picture', lvShow: 'Show the picture',
      lvCheckOver: '{n} may not follow the rules', lvCheckOverSub: 'Another rule on the box overrides this device. See “Children’s devices” below to fix it.',
      lvCheckAway: '{n} is not using the box', lvCheckAwaySub: 'It has not asked the box anything in 24 hours. It may be on mobile data or another Wi‑Fi.',
      lvCheckMany: '{n} devices need a look', lvCheckManySub: 'Some devices may not be following the rules. Tap one in the picture to see why.',
      lvOff: 'Internet is off for children', lvOffSub: 'Everything they ask for is stopped.',
      lvPausedOne: '{n} is paused', lvPausedAll: 'All children’s devices are paused', lvPausedSub: 'Nothing is allowed until you resume.',
      lvWorkingOne: 'The rules are working for {n}', lvWorkingAll: 'The rules are working on all {n} devices',
      lvBlockedApps: 'Apps blocked for children: {n}', lvNoBlockedApps: 'No apps are blocked right now.',
      lvLive: 'Live', lvTotalsOnly: 'Totals only', lvExample: 'Example', lvStale: 'Not updating', lvPausedPill: 'Picture paused',
      lvBox: 'Family box', lvNet: 'The internet',
      lvBoxOff: 'On duty · internet off for children', lvBoxBlocking: 'On duty · apps blocked: {n}', lvBoxOpen: 'On duty · no apps blocked', lvBoxBlockOff: 'On duty · blocking is off',
      lvSayNo: 'No · {a}', lvSayYes: 'Yes · {a}', lvSayNoEx: 'Example · No', lvSayYesEx: 'Example · Yes',
      lvChecked: 'Checked', lvStopped: 'Stopped', lvShare: 'Share stopped', lvScope: 'Whole home · last 24 hours',
      lvNote: 'Works on your home Wi‑Fi only. A phone on mobile data or a VPN skips the box. The picture shows a few of the checks, a few seconds late; every check is counted.',
      lvPrivacy: 'Live activity is hidden by a privacy setting. You still see the totals.',
      lvRecentTitle: 'What just happened', lvTour: 'Show me how it works', lvDetail: 'More detail',
      lvBack: 'Back', lvNext: 'Next', lvDone: 'Done', lvEndTour: 'Close', lvStep: 'Step {i} of {n}',
      lvCaption: 'Every device asks the box first, and the box answers yes (✓) or no (✕). For a yes, the box may look the address up on the internet. A no falls into the black hole and goes nowhere.',
      lvCaptionHidden: 'Live activity is hidden by a privacy setting, but everything is still counted.',
      lvExBox: 'The family box is a small computer at home. Every phone and tablet asks it before opening an app or website. It checks your rules and says yes or no.',
      lvExNet: 'Apps and websites live on the internet. The box only looks up addresses there. After a yes, the device goes to the app by itself, so videos and messages never pass through the box.',
      lvExRest: 'Everyone else at home: your phone, the TV, the computer. They use the box too, with basic filtering only (ads and unsafe sites).',
      lvExAllHome: 'Your privacy setting hides which device asked, so all activity is drawn together.',
      lvExMore: '{n} more devices. See them all under “Children’s devices” below.',
      lvEx_unknown: 'The rules are set for {n}. The box cannot tell when it was last online.',
      lvEx_active: '{n} used the internet in the last few minutes. The rules apply.',
      lvEx_quiet: '{n} has not asked the box anything for a while. If it is away from home Wi‑Fi, the rules cannot apply.',
      lvEx_paused: '{n} is paused. Everything it asks for is stopped until you resume it.',
      lvEx_offline: 'The internet is off for children, so {n} cannot open anything.',
      lvEx_overridden: 'Another rule on the box overrides the rules for {n}, so it may not follow the rules. See the device below to fix it.',
      lvEx_unreachable: '{n} has not asked the box anything in 24 hours. It may be on mobile data, a VPN or another Wi‑Fi, where the rules cannot work.',
      lvExChecked: 'Every time a phone, tablet, TV or computer at home asked the box where to find an app or website, over the last 24 hours. Your own devices count too.',
      lvExStopped: 'How many of those were stopped: blocked apps, bedtime, paused devices, and ads or unsafe sites from the box’s block lists.',
      lvExShare: 'Out of everything checked, how much was stopped. Ads and trackers keep this number high, even when no app is blocked.',
      lvPrivacyShort: 'Hidden by a privacy setting.', lvPrivacyNames: 'Names are hidden by a privacy setting.',
      lvRecentEmpty: 'Nothing yet. Activity shows up here as it happens.', lvRecStop: 'Stopped {a}', lvRecGo: 'Allowed {a}',
      lvNow: 'just now', lvSecs: '{n} s ago', lvMins: '{n} min ago',
      lvAria: 'In the last minute: {c} checked, {b} stopped.', lvExampleApp: 'Example',
      lvTour1: 'Every phone and tablet at home asks the family box first, before any app or website opens.',
      lvTour2: 'The box checks your rules: the apps you blocked, bedtime, and a long list of unsafe places. Here is one on its way.',
      lvTour3: 'If it is allowed, the box looks up the address and sends it back, and the app opens straight from the device. Only the address lookup goes to the internet, never your child’s videos or messages.',
      lvTour4: 'If it is blocked, the box drops it into the black hole, and the app cannot open.',
      lvTour5: 'The box only sees the names of places, never your messages, photos or videos. It works on your home Wi‑Fi only.',
      lvDetailTitle: 'How it works', lvHowTitle: 'Four steps',
      lvHow1T: '1. A device asks', lvHow1: 'Before an app or website opens, the phone asks the family box where to find it.',
      lvHow2T: '2. The box checks the rules', lvHow2: 'It looks at who is asking, the apps you blocked, bedtime, and a long list of unsafe or unwanted places.',
      lvHow3T: '3. Yes or no', lvHow3: 'Yes: the box finds the address and sends it back, and the app opens. No: it is a dead end, and the app cannot connect.',
      lvHow4T: '4. The device goes on by itself', lvHow4: 'After a yes, the device connects by itself. Videos and messages never pass through the box.',
      lvNumbersTitle: 'The numbers', lvHours: 'Busy hours (last 24 hours)', lvTopTitle: 'Stopped the most',
      lvFactMemory: 'Answered from memory', lvFactPlaces: 'Different places asked', lvFactList: 'Places on the block lists', lvFactDevices: 'Devices that asked',
      lvFactListAt: 'Block lists updated {t}',
      lvCannotTitle: 'What the box cannot do',
      lvCannot1: 'It sees only names of places, never messages, photos or calls.',
      lvCannot2: 'It works on your home Wi‑Fi. A phone on mobile data or a VPN skips it.',
      lvCannot3: 'A few apps use fixed addresses and never ask the box.',
      lvCannot4: 'An app that is already open can keep working for a few minutes.',
      lvWordsTitle: 'Words you may hear',
      lvWord1T: 'Pi-hole', lvWord1: 'The free program on your family box that does the checking.',
      lvWord2T: 'DNS', lvWord2: 'The “phone book” of the internet. It turns a name into the address a device needs.',
      lvWord3T: 'Block list (gravity)', lvWord3: 'A long list of unwanted places: ads, trackers and unsafe sites.',
      lvWord4T: 'Memory (cache)', lvWord4: 'Places the box looked up recently, so it can answer at once.',
      lvWord5T: 'Outside phone book (upstream DNS)', lvWord5: 'The big public phone book on the internet that the box asks when it does not know a place.',
      lvLoading: 'Loading…', lvUnavailable: 'Not available right now.', lvOther: 'Other places', lvTopEmpty: 'Nothing has been stopped yet.'
    },
    ar: {
      lvWebsite: 'موقع', lvDevice: 'جهاز', lvActive: 'متصل الآن', lvQuiet: 'هادئ', lvEarlier: 'أقدم', lvNowShort: 'الآن', lvWhyOff: 'الإنترنت متوقف', lvWhyPaused: 'الجهاز متوقف مؤقتًا', lvLookup: 'يبحث عن العنوان…', lvPaused: 'متوقف مؤقتًا', lvOffline: 'الإنترنت متوقف',
      lvOverridden: 'قد لا تُطبَّق القواعد', lvUnreachable: 'لم يتصل منذ 24 ساعة', lvUnknown: 'القواعد مضبوطة', lvUnwanted: 'موقع غير مرغوب', lvMore: 'أجهزة أخرى: {n}', lvAddDevice: 'إضافة جهاز', lvAddHint: 'اضغط للبدء',
      lvAllHome: 'كل البيت', lvAllHomeSt: 'الأسماء مخفية', lvRest: 'باقي البيت', lvRestSt: 'تصفية أساسية',
      lvNoKids: 'لا توجد أجهزة أطفال بعد', lvNoKidsSub: 'أضف هاتفًا أو جهازًا لوحيًا لتُطبَّق عليه القواعد. يمكنك في الأسفل متابعة كل البيت.',
      lvFree: 'الوقت الحر مفعّل', lvFreeSub: 'التطبيقات مفتوحة حتى ينتهي المؤقت، وتبقى قوائم الأمان فعّالة.',
      lvSetOne: 'القواعد مضبوطة على جهاز {n}', lvSetTwo: 'القواعد مضبوطة على الجهازين', lvSetAll: 'القواعد مضبوطة على كل الأجهزة ({n})',
      lvWorkingTwo: 'القواعد تعمل على الجهازين',
      lvBlockOff: 'الحظر متوقف', lvBlockOffSub: 'لا يتم إيقاف أي شيء. شغّل الحظر من «إعدادات متقدمة» أسفل الصفحة.',
      lvExamplePhone: 'هاتف مثال', lvExExample: 'هذا هاتف مثال فقط لشرح طريقة عمل الصندوق.', lvExAdd: 'أضف هاتف طفلك أو جهازه اللوحي لتُطبَّق عليه القواعد.',
      lvHide: 'إخفاء الرسم', lvShow: 'إظهار الرسم',
      lvCheckOver: 'قد لا يلتزم {n} بالقواعد', lvCheckOverSub: 'توجد قاعدة أخرى في الصندوق تتجاوز قواعد هذا الجهاز. راجع «أجهزة الأطفال» في الأسفل لمعرفة الحل.',
      lvCheckAway: 'لا يستخدم {n} صندوق العائلة', lvCheckAwaySub: 'لم يسأل الجهاز عن شيء منذ 24 ساعة. ربما يستخدم بيانات الجوال أو شبكة Wi‑Fi أخرى.',
      lvCheckMany: 'أجهزة تحتاج إلى نظرة: {n}', lvCheckManySub: 'قد لا تلتزم بعض الأجهزة بالقواعد. اضغط على أحدها في الرسم لمعرفة السبب.',
      lvOff: 'الإنترنت متوقف عن الأطفال', lvOffSub: 'كل ما يطلبونه يتم إيقافه.',
      lvPausedOne: '{n} متوقف مؤقتًا', lvPausedAll: 'كل أجهزة الأطفال متوقفة مؤقتًا', lvPausedSub: 'لا يُسمح بأي شيء حتى تستأنفها.',
      lvWorkingOne: 'القواعد تعمل على جهاز {n}', lvWorkingAll: 'القواعد تعمل على كل الأجهزة ({n})',
      lvBlockedApps: 'التطبيقات المحظورة على الأطفال: {n}', lvNoBlockedApps: 'لا توجد تطبيقات محظورة الآن.',
      lvLive: 'مباشر', lvTotalsOnly: 'الأرقام فقط', lvExample: 'مثال', lvStale: 'التحديث متوقف', lvPausedPill: 'الرسم متوقف',
      lvBox: 'صندوق العائلة', lvNet: 'الإنترنت',
      lvBoxOff: 'يعمل · الإنترنت متوقف عن الأطفال', lvBoxBlocking: 'يعمل · التطبيقات المحظورة: {n}', lvBoxOpen: 'يعمل · لا توجد تطبيقات محظورة', lvBoxBlockOff: 'يعمل · الحظر متوقف',
      lvSayNo: 'لا · {a}', lvSayYes: 'نعم · {a}', lvSayNoEx: 'مثال · لا', lvSayYesEx: 'مثال · نعم',
      lvChecked: 'تم فحصه', lvStopped: 'تم إيقافه', lvShare: 'نسبة المحظور', lvScope: 'كل البيت · آخر 24 ساعة',
      lvNote: 'يعمل على شبكة Wi‑Fi المنزل فقط. الهاتف الذي يستخدم بيانات الجوال أو VPN لا يمرّ عبر الصندوق. يعرض الرسم بعض عمليات الفحص بتأخر بضع ثوانٍ، وكلها تُحسب.',
      lvPrivacy: 'النشاط المباشر مخفي بسبب إعداد الخصوصية. ما زلت ترى الأرقام الإجمالية.',
      lvRecentTitle: 'ما حدث للتو', lvTour: 'اشرح لي كيف يعمل', lvDetail: 'تفاصيل أكثر',
      lvBack: 'السابق', lvNext: 'التالي', lvDone: 'تم', lvEndTour: 'إغلاق', lvStep: 'الخطوة {i} من {n}',
      lvCaption: 'كل جهاز يسأل صندوق العائلة أولًا، فيجيب بـ«نعم» (✓) أو «لا» (✕). وعند «نعم» قد يبحث الصندوق عن العنوان في الإنترنت. أما «لا» فيسقط في الثقب الأسود ولا يصل إلى أي مكان.',
      lvCaptionHidden: 'النشاط المباشر مخفي بسبب إعداد الخصوصية، لكن كل شيء ما زال يُحسب.',
      lvExBox: 'صندوق العائلة حاسوب صغير في البيت. يسأله كل هاتف وجهاز لوحي قبل فتح أي تطبيق أو موقع. يفحص قواعدك ثم يجيب بنعم أو لا.',
      lvExNet: 'التطبيقات والمواقع موجودة على الإنترنت. لا يبحث الصندوق هناك إلا عن العناوين. وبعد «نعم» يتوجه الجهاز إلى التطبيق بنفسه، فلا يمرّ الفيديو أو الرسائل عبر الصندوق.',
      lvExRest: 'باقي من في البيت: هاتفك والتلفزيون والحاسوب. يستخدمون الصندوق أيضًا، مع تصفية أساسية فقط للإعلانات والمواقع غير الآمنة.',
      lvExAllHome: 'إعداد الخصوصية يخفي أي جهاز سأل، لذلك يُرسم كل النشاط معًا.',
      lvExMore: 'أجهزة أخرى: {n}. شاهدها كلها تحت «أجهزة الأطفال» في الأسفل.',
      lvEx_unknown: 'القواعد مضبوطة على {n}. لا يستطيع الصندوق معرفة آخر وقت اتصل فيه.',
      lvEx_active: 'استخدم {n} الإنترنت خلال الدقائق الماضية. القواعد مطبّقة.',
      lvEx_quiet: '{n} لم يسأل الصندوق عن شيء منذ فترة. إذا كان بعيدًا عن شبكة Wi‑Fi المنزل فلا يمكن تطبيق القواعد عليه.',
      lvEx_paused: '{n} متوقف مؤقتًا. يُمنع كل شيء حتى تستأنفه.',
      lvEx_offline: 'الإنترنت متوقف عن الأطفال، لذلك لا يستطيع {n} فتح أي شيء.',
      lvEx_overridden: 'توجد قاعدة أخرى في الصندوق تتجاوز قواعد {n}، لذلك قد لا يلتزم بالقواعد. راجع الجهاز في الأسفل لمعرفة الحل.',
      lvEx_unreachable: 'لم يسأل {n} صندوق العائلة عن شيء منذ 24 ساعة. ربما يستخدم بيانات الجوال أو VPN أو شبكة Wi‑Fi أخرى، حيث لا تعمل القواعد.',
      lvExChecked: 'كل مرة سأل فيها هاتف أو جهاز لوحي أو تلفزيون أو حاسوب في البيت صندوق العائلة عن مكان تطبيق أو موقع، خلال آخر 24 ساعة. وتُحسب أجهزتك أيضًا.',
      lvExStopped: 'كم منها تم إيقافه: التطبيقات المحظورة ووقت النوم والأجهزة المتوقفة مؤقتًا، والإعلانات والمواقع غير الآمنة الموجودة في قوائم الحظر.',
      lvExShare: 'من كل ما تم فحصه، كم تم إيقافه. الإعلانات والمتتبعات ترفع هذه النسبة حتى لو لم يُحظر أي تطبيق.',
      lvPrivacyShort: 'مخفي بسبب إعداد الخصوصية.', lvPrivacyNames: 'الأسماء مخفية بسبب إعداد الخصوصية.',
      lvRecentEmpty: 'لا شيء بعد. يظهر النشاط هنا فور حدوثه.', lvRecStop: 'تم إيقاف {a}', lvRecGo: 'تم السماح بـ{a}',
      lvNow: 'الآن', lvSecs: 'قبل {n} ث', lvMins: 'قبل {n} د',
      lvAria: 'خلال آخر دقيقة: {c} تم فحصها، {b} تم إيقافها.', lvExampleApp: 'مثال',
      lvTour1: 'كل هاتف وجهاز لوحي في البيت يسأل صندوق العائلة أولًا، قبل أن يُفتح أي تطبيق أو موقع.',
      lvTour2: 'يفحص صندوق العائلة قواعدك: التطبيقات المحظورة ووقت النوم وقائمة طويلة بالأماكن غير الآمنة. هذا طلب في طريقه.',
      lvTour3: 'إذا كان مسموحًا، يبحث صندوق العائلة عن العنوان ويرسله إلى الهاتف، فيفتح التطبيق مباشرة من الهاتف. الذي يذهب إلى الإنترنت هو سؤال العنوان فقط، وليس فيديوهات طفلك أو رسائله.',
      lvTour4: 'إذا كان محظورًا، يُسقطه صندوق العائلة في الثقب الأسود، فلا يستطيع التطبيق أن يفتح.',
      lvTour5: 'يرى صندوق العائلة أسماء الأماكن فقط، ولا يرى رسائلك أو صورك أو فيديوهاتك. ويعمل على شبكة Wi‑Fi المنزل فقط.',
      lvDetailTitle: 'كيف يعمل', lvHowTitle: 'أربع خطوات',
      lvHow1T: '1. الجهاز يسأل', lvHow1: 'قبل أن يُفتح التطبيق أو الموقع، يسأل الهاتف صندوق العائلة: أين أجده؟',
      lvHow2T: '2. صندوق العائلة يفحص القواعد', lvHow2: 'ينظر من الذي يسأل، وما التطبيقات التي حظرتها، ووقت النوم، وقائمة طويلة بالأماكن غير الآمنة أو غير المرغوبة.',
      lvHow3T: '3. نعم أو لا', lvHow3: 'نعم: يجد الصندوق العنوان ويرسله، ويفتح التطبيق. لا: طريق مسدود ولا يستطيع التطبيق الاتصال.',
      lvHow4T: '4. الجهاز يكمل بنفسه', lvHow4: 'بعد «نعم» يتصل الجهاز بنفسه. لا يمرّ الفيديو أو الرسائل عبر صندوق العائلة أبدًا.',
      lvNumbersTitle: 'الأرقام', lvHours: 'ساعات الازدحام (آخر 24 ساعة)', lvTopTitle: 'الأكثر إيقافًا',
      lvFactMemory: 'أجوبة من الذاكرة', lvFactPlaces: 'أماكن مختلفة تم السؤال عنها', lvFactList: 'أماكن في قوائم الحظر', lvFactDevices: 'أجهزة سألت',
      lvFactListAt: 'تحديث قوائم الحظر {t}',
      lvCannotTitle: 'ما لا يستطيع صندوق العائلة فعله',
      lvCannot1: 'يرى أسماء الأماكن فقط، ولا يرى الرسائل أو الصور أو المكالمات.',
      lvCannot2: 'يعمل على شبكة Wi‑Fi المنزل. الهاتف الذي يستخدم بيانات الجوال أو VPN يتجاوزه.',
      lvCannot3: 'بعض التطبيقات تستخدم عناوين ثابتة ولا تسأل صندوق العائلة أبدًا.',
      lvCannot4: 'التطبيق المفتوح بالفعل قد يستمر في العمل بضع دقائق.',
      lvWordsTitle: 'كلمات قد تسمعها',
      lvWord1T: 'Pi-hole', lvWord1: 'البرنامج المجاني على صندوق العائلة الذي يقوم بالفحص.',
      lvWord2T: 'DNS', lvWord2: '«دليل الهاتف» للإنترنت. يحوّل الاسم إلى العنوان الذي يحتاجه الجهاز.',
      lvWord3T: 'قائمة الحظر (gravity)', lvWord3: 'قائمة طويلة بالأماكن غير المرغوبة: الإعلانات والمتتبعات والمواقع غير الآمنة.',
      lvWord4T: 'الذاكرة (cache)', lvWord4: 'أماكن بحث عنها الصندوق مؤخرًا، فيجيب عنها فورًا.',
      lvWord5T: 'دليل الهاتف الخارجي (upstream DNS)', lvWord5: 'دليل الهاتف العام الكبير على الإنترنت، يسأله الصندوق حين لا يعرف المكان.',
      lvLoading: 'جارٍ التحميل…', lvUnavailable: 'غير متاح الآن.', lvOther: 'أماكن أخرى', lvTopEmpty: 'لم يتم إيقاف شيء بعد.'
    }
  };
  Object.keys(LIVE_STR.en).forEach(function (k) { STR.en[k] = LIVE_STR.en[k]; });
  Object.keys(LIVE_STR.ar).forEach(function (k) { STR.ar[k] = LIVE_STR.ar[k]; });

  // ------------------------------------------------------------------ strings for the first run: claim screen, setup list, update banner
  // (the My box sheet's own strings live in pb-box.js). tests/test_copy.py checks both languages and the plain-words rule.
  var FIRST_STR = {
    en: {
      tagline: 'Calm internet for the family',
      claimTitle: 'Welcome to Sinko', claimLead: 'Choose a parent password for this box. You will use it to sign in on this page.',
      claimWhy: 'Until you do, anyone on your Wi‑Fi can open this page and change the rules.',
      claimPw: 'Password (at least 8 characters)', claimPw2: 'Password again', claimBtn: 'Choose password and continue', claimBusy: 'Saving…',
      claimShort: 'The password needs at least 8 characters.', claimMismatch: 'The two passwords are not the same.',
      claimFail: 'The password could not be saved: {e}', claimSignIn: 'Your password is saved. Sign in with it.',
      setupTitle: 'Get started', setupProgress: '{n} of {t} done', setupHide: 'Hide this list',
      setupPw: 'Parent password chosen',
      setupRouter: 'Router points to this box', setupRouterHow: 'No other device at home has used the box yet. See “Setup help” at the bottom of this page.', setupRouterBtn: 'Show setup help',
      setupChild: 'First child device added', setupChildHow: 'Add your child’s phone or tablet so the rules apply to it.',
      setupCounterQ: 'Help count Sinko boxes?', setupCounterDone: 'Counter question answered',
      setupCounterText: 'Optional. Every few hours the box sends a random code, the Sinko version and the kind of box. Nothing about your family. The counter adds the country and when it first and last heard from the box. Switch it off any time in My box: the box then asks the counter service to forget it.',
      setupDone: 'done', setupTodo: 'not done yet',
      setupYes: 'Yes', setupNotNow: 'Not now', setupLaterToast: 'Okay. You can change this in My box.', setupDoneToast: 'Everything is set up.',
      updateReady: 'Sinko {v} is ready: open My box', updateRunning: 'Sinko is updating: open My box to follow it',
      updateFailed: 'The last update did not work: open My box'
    },
    ar: {
      tagline: 'إنترنت هادئ للعائلة',
      claimTitle: 'مرحبًا بك في سينكو', claimLead: 'اختر كلمة مرور الوالدين لهذا الصندوق. ستستخدمها لتسجيل الدخول في هذه الصفحة.',
      claimWhy: 'إلى أن تفعل ذلك، يستطيع أي شخص على شبكة Wi‑Fi فتح هذه الصفحة وتغيير القواعد.',
      claimPw: 'كلمة المرور (8 أحرف على الأقل)', claimPw2: 'أعد كتابة كلمة المرور', claimBtn: 'اختيار كلمة المرور والمتابعة', claimBusy: 'جارٍ الحفظ…',
      claimShort: 'يجب أن تتكوّن كلمة المرور من 8 أحرف على الأقل.', claimMismatch: 'كلمتا المرور غير متطابقتين.',
      claimFail: 'تعذّر حفظ كلمة المرور: {e}', claimSignIn: 'تم حفظ كلمة المرور. سجّل الدخول بها.',
      setupTitle: 'ابدأ من هنا', setupProgress: 'تم {n} من {t}', setupHide: 'إخفاء هذه القائمة',
      setupPw: 'تم اختيار كلمة مرور الوالدين',
      setupRouter: 'الراوتر يوجّه الأجهزة إلى هذا الصندوق', setupRouterHow: 'لم يستخدم أي جهاز آخر في البيت الصندوق بعد. راجع «مساعدة في الإعداد» أسفل هذه الصفحة.', setupRouterBtn: 'عرض المساعدة',
      setupChild: 'إضافة أول جهاز لطفل', setupChildHow: 'أضف هاتف طفلك أو جهازه اللوحي لتُطبَّق عليه القواعد.',
      setupCounterQ: 'هل تساعد في عدّ صناديق سينكو؟', setupCounterDone: 'تمت الإجابة عن سؤال العدّاد',
      setupCounterText: 'اختياري. يرسل الصندوق كل بضع ساعات رمزًا عشوائيًا ورقم إصدار سينكو ونوع الصندوق. لا شيء عن عائلتك. ويضيف العدّاد الدولة ووقت أول رسالة وآخر رسالة من الصندوق. يمكنك إيقافه في أي وقت من «صندوقي»، وعندها يطلب الصندوق من خدمة العدّاد أن تنسى هذا الصندوق.',
      setupDone: 'تم', setupTodo: 'لم يتم بعد',
      setupYes: 'نعم', setupNotNow: 'ليس الآن', setupLaterToast: 'حسنًا. يمكنك تغيير ذلك من «صندوقي».', setupDoneToast: 'تم إعداد كل شيء.',
      updateReady: 'الإصدار {v} من سينكو جاهز: افتح صندوقي', updateRunning: 'يجري تحديث سينكو: افتح صندوقي للمتابعة',
      updateFailed: 'لم ينجح آخر تحديث: افتح صندوقي'
    }
  };
  Object.keys(FIRST_STR.en).forEach(function (k) { STR.en[k] = FIRST_STR.en[k]; });
  Object.keys(FIRST_STR.ar).forEach(function (k) { STR.ar[k] = FIRST_STR.ar[k]; });
  // The My box sheet's strings (pb-box.js is loaded before this file).
  if (window.PBBox) {
    Object.keys(PBBox.strings.en).forEach(function (k) { STR.en[k] = PBBox.strings.en[k]; });
    Object.keys(PBBox.strings.ar).forEach(function (k) { STR.ar[k] = PBBox.strings.ar[k]; });
  }

  // ------------------------------------------------------------------ state
  var lang = safeGet(localStorage, LANG_KEY) === 'ar' ? 'ar' : (safeGet(localStorage, LANG_KEY) ? 'en' : guessLang());
  var sid = safeGet(sessionStorage, SID_KEY) || '';
  var catalog = null;                // services.json
  var domainMap = null;              // pb/domains.json: domain -> app id, for the live picture
  var picInited = false;
  var M = null;                      // model from last load
  var busy = false;
  var pollTimer = null, tickTimer = null;
  var timerMode = 'free', timerMinutes = 0, pickedDevice = null;
  var serverOffsetMs = 0;            // box clock minus this device's clock, from the Date header of every API answer
  var boxInfo = null;                // /pb/box.json as the box program writes it (parsed and checked); null = an older box, which has none
  var heartbeatLateSince = 0;        // local time (ms) this page first saw the box's pulse late, 0 while it is not late
  var pageStamp = '';                // the release the installer stamped into index.html ('' = not stamped: development, or a page copied by hand)
  var pageVersion = '';              // the version this page is: the stamp, else pb/version.txt at boot; the box may be newer after an update
  var offsetKnown = false;           // serverOffsetMs has been read from an answer at least once
  var clockStepHoldUntil = 0;        // local time (ms) until which the box's clock is taken to be catching up after a jump, see noteBoxClock
  var staleCheckedAt = 0, staleReloading = false;
  var UPDATED_KEY = 'pb.updated';    // set just before the page reloads itself after an update, so it can say what happened
  var RELOADED_KEY = 'pb.reloadedFor';   // the version this tab last reloaded itself for: a page that is still not that version does not reload again
  var CLOCK_STEP_MS = 30000;         // the box's clock moving by more than this between two answers (beyond the Date header's whole seconds) is a jump

  function $(id) { return document.getElementById(id); }
  function safeGet(store, key) { try { return store.getItem(key); } catch (e) { return null; } }
  function safeSet(store, key, val) { try { if (val === null) store.removeItem(key); else store.setItem(key, val); } catch (e) { /* private mode */ } }
  function guessLang() { return (navigator.language || '').toLowerCase().indexOf('ar') === 0 ? 'ar' : 'en'; }
  function t(key, vars) {
    var s = STR[lang][key]; if (s === undefined) s = STR.en[key]; if (s === undefined) return key;
    if (vars) Object.keys(vars).forEach(function (k) { s = s.split('{' + k + '}').join(vars[k]); });
    return s;
  }
  function el(tag, attrs, kids) {
    var n = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      if (k === 'text') n.textContent = attrs[k];
      else if (k === 'class') n.className = attrs[k];
      else if (attrs[k] !== null && attrs[k] !== undefined && attrs[k] !== false) n.setAttribute(k, attrs[k] === true ? '' : attrs[k]);
    });
    (kids || []).forEach(function (c) { if (c) n.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); });
    return n;
  }
  function icon(id) {
    var ns = 'http://www.w3.org/2000/svg';
    var svg = document.createElementNS(ns, 'svg'); svg.setAttribute('aria-hidden', 'true');
    var use = document.createElementNS(ns, 'use'); use.setAttribute('href', '#' + id);
    svg.appendChild(use); return svg;
  }
  // Timers and "last online" are compared by the box with ITS clock, so the page must use the box's time, not the phone's.
  function serverNowSec() { return (Date.now() + serverOffsetMs) / 1000; }
  // The box has no real-time clock: after a long time without internet it can step by hours or days at once when it reaches a time server. The
  // scheduler then needs a pass (up to five minutes) to write box.json again, so for that time the file's age says nothing about the scheduler.
  function noteBoxClock(offsetMs) {
    if (offsetKnown && Math.abs(offsetMs - serverOffsetMs) > CLOCK_STEP_MS) clockStepHoldUntil = Date.now() + PBBox.timing.clockStepHold;
    serverOffsetMs = offsetMs;
    offsetKnown = true;
  }
  function locale() { return lang === 'ar' ? 'ar-BH-u-nu-latn' : 'en-GB'; }
  function fmtTime(date) { return date.toLocaleTimeString(locale(), { hour: 'numeric', minute: '2-digit' }); }
  function fmtHHMM(hhmm) { var d = new Date(); d.setHours(+hhmm.slice(0, 2), +hhmm.slice(3), 0, 0); return fmtTime(d); }
  function fmtClock(sec) {
    sec = Math.max(0, Math.round(sec));
    var h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60;
    var p = function (n) { return (n < 10 ? '0' : '') + n; };
    return (h ? h + ':' + p(m) : m) + ':' + p(s);
  }
  function fmtAgo(epoch) {
    if (!epoch) return t('neverSeen');
    var diff = Math.round((epoch - serverNowSec()) / 60);
    var rtf = window.Intl && Intl.RelativeTimeFormat ? new Intl.RelativeTimeFormat(locale(), { numeric: 'auto' }) : null;
    var txt;
    if (!rtf) txt = new Date(epoch * 1000).toLocaleString(locale());
    else if (diff > -60) txt = rtf.format(diff, 'minute');
    else if (diff > -1440) txt = rtf.format(Math.round(diff / 60), 'hour');
    else txt = rtf.format(Math.round(diff / 1440), 'day');
    return t('lastSeen', { t: txt });
  }
  function svcName(s) { return lang === 'ar' ? s.ar : s.name; }

  var toastTimer = null;
  function toast(msg, isError) {
    // Everything outside an open modal dialog is hidden behind it, so while My box is open its messages show inside it.
    var inBox = $('boxDialog').open;
    var n = inBox ? $('boxToast') : $('toast');
    $('toast').classList.remove('show'); $('boxToast').classList.remove('show');
    n.textContent = msg; n.classList.toggle('toast-error', !!isError); n.classList.add('show');
    clearTimeout(toastTimer); toastTimer = setTimeout(function () { n.classList.remove('show'); }, isError ? 6000 : 2600);
  }

  // ------------------------------------------------------------------ API
  // `body` is a JSON value. opts (all optional): blob = the answer is a file, resolves { blob }; form = a FormData to send as is (the browser
  // writes the multipart header); noSid / sid = send no session / this one; quiet401 = an answer of 401 does not sign the page out;
  // text = the answer is plain text (a streamed run), resolves { text }.
  // An ApiError carries the status (0 = the box did not answer), a message for people, and `body`, the parsed answer.
  function ApiError(status, message, body) { this.status = status; this.message = message; this.body = body; }
  function call(method, path, body, opts) {
    opts = opts || {};
    var headers = { Accept: opts.blob ? 'application/zip, */*' : 'application/json' }, payload;
    if (opts.form) payload = opts.form;
    else if (body !== undefined) { headers['Content-Type'] = 'application/json'; payload = JSON.stringify(body); }
    var useSid = opts.sid !== undefined ? opts.sid : (opts.noSid ? '' : sid);
    if (useSid) headers.sid = useSid;
    return fetch(path, { method: method, headers: headers, credentials: 'omit', cache: 'no-store', body: payload })
      .catch(function () { throw new ApiError(0, t('noConnection')); })
      .then(function (r) {
        var boxTime = Date.parse(r.headers.get('Date') || '');
        if (!isNaN(boxTime)) noteBoxClock(boxTime - Date.now());
        if (opts.blob && r.ok) return r.blob().then(function (b) { return { blob: b }; });
        return r.text().then(function (txt) {
          var j = {}; try { j = txt ? JSON.parse(txt) : {}; } catch (e) { j = {}; }
          if (r.status === 401) { if (!opts.quiet401) endSession(); throw new ApiError(401, t('sessionEnded'), j); }
          if (!r.ok) {
            var e = j && j.error; var m = e ? (e.message || e.key || String(e)) : ('HTTP ' + r.status);
            if (e && e.hint) m += ' (' + e.hint + ')';
            throw new ApiError(r.status, m, j);
          }
          if (opts.text) return { text: txt };
          return j;
        });
      });
  }
  function q(s) { return encodeURIComponent(s); }
  // Pi-hole keeps answers it learned before a block, so a rule change only reaches an app once that cache is cleared.
  // Every rule change marks the box dirty; after the action, one quick resolver restart empties the cache.
  var rulesDirty = false;
  function flushDns() {
    if (!rulesDirty) return Promise.resolve();
    rulesDirty = false;
    return call('POST', '/api/action/restartdns').catch(function () { /* the rule change itself already worked */ });
  }
  function putGroup(g, enabled) {
    rulesDirty = true;
    return call('PUT', '/api/groups/' + q(g.name), { name: g.name, comment: g.comment || '', enabled: !!enabled })
      .then(function () { g.enabled = !!enabled; });
  }
  function putClient(c, groups) {
    rulesDirty = true;
    return call('PUT', '/api/clients/' + q(c.client), { comment: c.comment || '', groups: groups });
  }

  // ------------------------------------------------------------------ what the box says about itself (/pb/box.json)
  // Written by the box program (docs/maintainers/architecture.md, "Amendments"): its number address, time zone, whether a counter address is
  // configured, and `at`, its own pulse. Read without signing in. An older box has no such file and every feature that needs it stays off:
  // nothing here may break the page. No answer at all keeps what was known; a file that is not what it should be counts as no file.
  function loadBoxInfo() {
    return fetch('/pb/box.json', { cache: 'no-store', credentials: 'omit' })
      .then(function (r) { return r.ok ? r.json() : null; }, function () { return undefined; })
      .then(function (j) { if (j !== undefined) boxInfo = PBBox.pure.parseBoxInfo(j); return boxInfo; },
        function () { boxInfo = null; return null; });
  }
  // The number to give the router: the box's own address from box.json, else the address this page was opened by when that is a number.
  // Never a name: a router takes only numbers there.
  function renderHelp() {
    var ip = (boxInfo && boxInfo.ip) || PBBox.pure.usableIpv4((location.hostname || '').toLowerCase());
    $('helpBody').textContent = ip ? t('helpBody', { ip: ip }) : t('helpBodyNoIp');
  }
  // The timer on the box is overdue, or the box's own pulse (box.json's `at`) is more than 20 minutes behind the box's clock. The pulse is
  // not written while an update runs, and it is only believed once this page has seen it late for a minute (right after the box's clock is
  // corrected the file is a few seconds behind).
  function schedulerSilent() {
    if (Date.now() < clockStepHoldUntil) { heartbeatLateSince = 0; return false; }       // the box's clock just jumped: give the scheduler a pass to catch up
    var tm = M.state.timer, now = serverNowSec();
    if (tm && tm.until < now - 90) return true;
    var late = PBBox.pure.heartbeatLate(boxInfo, now) && M.state.update.status !== 'running';
    if (!late) { heartbeatLateSince = 0; return false; }
    if (!heartbeatLateSince) heartbeatLateSince = Date.now();
    return Date.now() - heartbeatLateSince >= PBBox.timing.heartbeatGrace;
  }

  // ------------------------------------------------------------------ auth
  // `note` is one calm line for the sign-in screen: "your password was changed", "the box restarted".
  function endSession(note) {
    sid = ''; safeSet(sessionStorage, SID_KEY, null);
    stopPolling();
    if (window.PBBox) PBBox.reset();
    if (window.PBPicture) { PBPicture.stop(); picInited = false; }
    showLogin(typeof note === 'string' ? note : '');
  }
  function showLogin(note) {
    $('app').hidden = true; $('claim').hidden = true; $('login').hidden = false;
    var n = $('loginNote'); n.textContent = note || ''; n.hidden = !note;
    $('loginErr').textContent = '';
    setTimeout(function () { $('pw').focus(); }, 0);
  }
  function showApp() { $('login').hidden = true; $('claim').hidden = true; $('app').hidden = false; }

  function probeAuth() {
    return call('GET', '/api/auth').then(function (j) {
      var s = j.session || {};
      $('totpField').hidden = !s.totp;
      return !!s.valid;
    }).catch(function (e) {
      if (e.status === 401) return false;
      throw e;
    });
  }
  // ------------------------------------------------------------------ first run: choosing the parent password
  // A box with no password answers GET /api/auth with a valid session even when no session id is sent. That is asked WITHOUT the
  // stored session id: with one, "valid" would only say that the session is fine, not that no password exists.
  function noPasswordSet() {
    return fetch('/api/auth', { headers: { Accept: 'application/json' }, credentials: 'omit', cache: 'no-store' })
      .then(function (r) { return r.json().then(function (j) { return !!(r.ok && j && j.session && j.session.valid); }, function () { return false; }); });
  }
  // Only a box that has Sinko on it is claimed here: without Sinko's groups there is nothing for this page to protect yet.
  function sinkoInstalled() {
    return fetch('/api/groups', { headers: { Accept: 'application/json' }, credentials: 'omit', cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : { groups: [] }; })
      .then(function (j) { return (j.groups || []).some(function (g) { return g.name === G.state; }); })
      .catch(function () { return false; });
  }
  function showClaim() {
    $('app').hidden = true; $('login').hidden = true; $('claim').hidden = false;
    $('claimErr').textContent = '';
    setTimeout(function () { $('claimPw').focus(); }, 0);
  }
  function claim(ev) {
    ev.preventDefault();
    var pw = $('claimPw').value, again = $('claimPw2').value, err = $('claimErr'), btn = $('claimBtn');
    err.textContent = '';
    var problem = PBBox.pure.passwordProblem('', pw, again);
    if (problem === 'boxPwShort') { err.textContent = t('claimShort'); $('claimPw').focus(); return; }
    if (problem === 'boxPwMismatch') { err.textContent = t('claimMismatch'); $('claimPw2').focus(); return; }
    btn.disabled = true; btn.textContent = t('claimBusy');
    var finish = function () { btn.disabled = false; btn.textContent = t('claimBtn'); };
    var saved = false;
    call('PATCH', '/api/config', { config: { webserver: { api: { password: pw } } } }, { noSid: true, quiet401: true })
      .then(function () {
        saved = true;
        // Pi-hole may be busy applying the change for a moment: signing in is tried a few times before giving up.
        var attempt = function (left) {
          return call('POST', '/api/auth', { password: pw }, { noSid: true, quiet401: true }).catch(function (e) {
            if (left > 1 && (!e.status || e.status >= 500)) return new Promise(function (res) { setTimeout(res, 1200); }).then(function () { return attempt(left - 1); });
            throw e;
          });
        };
        return attempt(4);
      })
      .then(function (j) {
        $('claimPw').value = ''; $('claimPw2').value = '';
        sid = (j.session && j.session.sid) || ''; safeSet(sessionStorage, SID_KEY, sid);
        finish();
        return start();
      }, function (e) {
        finish();
        if (saved) { $('claimPw').value = ''; $('claimPw2').value = ''; showLogin(t('claimSignIn')); return; }   // the password exists: only signing in failed
        err.textContent = t('claimFail', { e: e && e.message ? e.message : String(e) });
      });
  }

  function login(ev) {
    ev.preventDefault();
    var pw = $('pw').value, code = $('totp').value.trim();
    var btn = $('loginBtn'); $('loginErr').textContent = ''; $('loginNote').hidden = true;
    if (!pw) { $('pw').focus(); return; }
    btn.disabled = true;
    var body = { password: pw };
    if (!$('totpField').hidden && code) body.totp = Number(code);
    fetch('/api/auth', { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      credentials: 'omit', body: JSON.stringify(body) })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { return { r: r, j: j }; }); })
      .then(function (x) {
        var s = x.j.session || {};
        if (x.r.ok && s.valid) {
          sid = s.sid || ''; safeSet(sessionStorage, SID_KEY, sid);
          $('pw').value = ''; $('totp').value = '';
          return start();
        }
        if (x.r.status === 429) { $('loginErr').textContent = t('tooMany'); return; }
        if (s.totp || (x.j.error && /totp|2fa/i.test(x.j.error.message || ''))) {
          $('totpField').hidden = false; $('loginErr').textContent = t('wrongTotp'); $('totp').focus(); return;
        }
        $('loginErr').textContent = t('wrongPassword'); $('pw').select();
      })
      .catch(function () { $('loginErr').textContent = t('noConnection'); })
      .then(function () { btn.disabled = false; });
  }
  function logout() {
    var had = sid;
    stopPolling();
    (had ? call('DELETE', '/api/auth').catch(function () {}) : Promise.resolve()).then(endSession);
  }

  // ------------------------------------------------------------------ client matching
  // Pi-hole (FTL) picks a client's groups from the client rows whose IP or subnet contains the
  // address a query comes from (longest prefix wins, highest id on a tie) and only then looks at
  // the device's MAC row. So an IP or subnet row silently overrides a child's MAC row.
  // Same logic as shadowing_rows() in bin/sinko.
  function parseIpv4(s) {
    var m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(s);
    if (!m) return null;
    var out = [];
    for (var i = 1; i <= 4; i++) { if (+m[i] > 255) return null; out.push(+m[i]); }
    return out;
  }
  function parseIpv6(s) {
    var zone = s.indexOf('%'); if (zone >= 0) s = s.slice(0, zone);
    if (s.indexOf(':') < 0 || !/^[0-9a-f:.]+$/i.test(s)) return null;
    var tail = /^(.*:)(\d+\.\d+\.\d+\.\d+)$/.exec(s);
    if (tail) {
      var v4 = parseIpv4(tail[2]); if (!v4) return null;
      s = tail[1] + ((v4[0] << 8) | v4[1]).toString(16) + ':' + ((v4[2] << 8) | v4[3]).toString(16);
    }
    var halves = s.split('::'); if (halves.length > 2) return null;
    var head = halves[0] ? halves[0].split(':') : [];
    var rest = halves.length === 2 && halves[1] ? halves[1].split(':') : [];
    var groups = head.slice();
    if (halves.length === 2) { while (groups.length + rest.length < 8) groups.push('0'); }
    groups = groups.concat(rest);
    if (groups.length !== 8) return null;
    var out = [];
    for (var i = 0; i < 8; i++) {
      if (!/^[0-9a-f]{1,4}$/i.test(groups[i])) return null;
      var v = parseInt(groups[i], 16); out.push(v >> 8, v & 255);
    }
    return out;
  }
  function parseIp(s) { s = String(s || ''); return s.indexOf(':') >= 0 ? parseIpv6(s) : parseIpv4(s); }
  function parseNet(text) {            // an address or CIDR subnet; null for MAC, host name and :interface rows
    var parts = String(text || '').split('/'); if (parts.length > 2) return null;
    var b = parseIp(parts[0]); if (!b) return null;
    var bits = b.length * 8;
    if (parts.length === 2) { if (!/^\d+$/.test(parts[1]) || +parts[1] > bits) return null; bits = +parts[1]; }
    return { bytes: b, bits: bits };
  }
  function inNet(ip, net) {
    if (ip.length !== net.bytes.length) return false;
    for (var i = 0, left = net.bits; left > 0; i++, left -= 8) {
      var mask = left >= 8 ? 255 : (255 << (8 - left)) & 255;
      if ((ip[i] & mask) !== (net.bytes[i] & mask)) return false;
    }
    return true;
  }
  function winningRow(ip, clients) {
    var best = null, bestKey = null;
    clients.forEach(function (c) {
      var net = parseNet(c.client);
      if (!net || !inNet(ip, net)) return;
      var key = net.bits * 1e9 + (c.id || 0);
      if (bestKey === null || key > bestKey) { best = c; bestKey = key; }
    });
    return best;
  }
  function deviceIps(d) {
    var ips = (d.ips || []).map(function (i) { return i.ip; }).filter(Boolean);
    var hw = d.hwaddr || '';
    if (hw.indexOf('ip-') === 0 && ips.indexOf(hw.slice(3)) < 0) ips.push(hw.slice(3));
    return ips;
  }
  function neededGroupIds(byName, paused) {   // the pb-* groups a child's row must have (not Default)
    var ids = [];
    [G.kids, G.guard, G.offline].concat(paused ? [G.paused] : []).forEach(function (n) { if (byName[n]) ids.push(byName[n].id); });
    catalog.services.forEach(function (s) { var g = byName[SVC + s.id]; if (g) ids.push(g.id); });
    return ids;
  }
  // Client rows FTL would use instead of this MAC-registered child's own row, if they lack its pb groups.
  function shadowRowsFor(addr, devices, clients, byName, paused) {
    if (!MAC_RE.test(addr || '')) return [];
    var dev = devices.filter(function (d) { return (d.hwaddr || '').toLowerCase() === addr.toLowerCase(); })[0];
    if (!dev) return [];
    var needed = neededGroupIds(byName, paused), found = {}, out = [];
    deviceIps(dev).forEach(function (ipText) {
      var ip = parseIp(ipText); if (!ip) return;
      var row = winningRow(ip, clients);
      if (!row || found[row.client]) return;
      var have = row.groups || [];
      if (needed.some(function (id) { return have.indexOf(id) < 0; })) { found[row.client] = true; out.push(row); }
    });
    return out;
  }

  // ------------------------------------------------------------------ model
  // The shared state is normalised by PBCore.parseState (pb-core.js), with the same rules as the scheduler.
  var parseState = PBCore.parseState;

  function load() {
    return Promise.all([
      call('GET', '/api/groups'),
      call('GET', '/api/clients'),
      call('GET', '/api/network/devices?max_devices=200&max_addresses=50').catch(function () { return { devices: [] }; }),
      call('GET', '/api/info/client').catch(function () { return {}; }),
      catalog ? Promise.resolve(catalog) : fetch('/pb/services.json', { cache: 'no-store' }).then(function (r) { return r.json(); }),
      // A missing file (an older install) is cached as "no names"; a failed fetch is retried on the next load.
      domainMap ? Promise.resolve(domainMap) : fetch('/pb/domains.json', { cache: 'no-store' }).then(function (r) { return r.status === 404 ? { domains: {} } : r.ok ? r.json() : null; })
        .then(function (j) { return j && j.domains && typeof j.domains === 'object' ? j.domains : null; }).catch(function () { return null; }),
      loadBoxInfo()
    ]).then(function (r) {
      catalog = r[4]; domainMap = r[5] || domainMap;
      var byName = {};
      (r[0].groups || []).forEach(function (g) { byName[g.name] = g; });
      var ok = [G.kids, G.offline, G.paused, G.state].every(function (n) { return byName[n]; });
      if (!ok) { banner(t('notInstalled')); M = null; return; }
      var kidId = byName[G.kids].id, pausedId = byName[G.paused].id;
      var devicesByMac = {}, devicesByIp = {};
      (r[2].devices || []).forEach(function (d) {
        devicesByMac[(d.hwaddr || '').toLowerCase()] = d;
        (d.ips || []).forEach(function (ip) { devicesByIp[ip.ip] = d; });
      });
      var kids = (r[1].clients || []).filter(function (c) { return (c.groups || []).indexOf(kidId) >= 0; })
        .map(function (c) {
          var key = c.client.toLowerCase();
          var dev = devicesByMac[key] || devicesByIp[c.client] || null;
          var paused = c.groups.indexOf(pausedId) >= 0;
          return { row: c, name: c.comment || deviceLabel(dev) || c.client, paused: paused, dev: dev,
            shadow: shadowRowsFor(c.client, r[2].devices || [], r[1].clients || [], byName, paused) };
        });
      var services = catalog.services.map(function (s) {
        var g = byName[SVC + s.id];
        return { meta: s, group: g, blocked: !!(g && g.enabled) };
      }).filter(function (s) { return s.group; });
      M = {
        groups: byName, services: services, kids: kids, clients: r[1].clients || [],
        devices: r[2].devices || [], myIp: r[3].remote_addr || '', byIp: devicesByIp,
        offline: !!byName[G.offline].enabled, state: parseState(byName[G.state].comment)
      };
      render();
    });
  }
  function deviceLabel(d) {
    if (!d) return '';
    var name = (d.ips || []).map(function (i) { return i.name; }).filter(Boolean)[0];
    return name || '';
  }
  function kidGroupIds() {
    var ids = [DEFAULT_GROUP];
    [G.kids, G.guard, G.offline].forEach(function (n) { if (M.groups[n]) ids.push(M.groups[n].id); });
    M.services.forEach(function (s) { ids.push(s.group.id); });
    return ids;
  }
  function snapshot() {
    var services = {};
    M.services.forEach(function (s) { services[s.meta.id] = s.blocked; });
    return { services: services, offline: M.offline };
  }
  function writeState(mutator) {
    // Re-read first so we never overwrite a change the Pi service just made.
    // PBCore.editState keeps every field the mutator does not touch, including the ones only the scheduler writes.
    return call('GET', '/api/groups/' + q(G.state)).then(function (j) {
      var g = (j.groups || [])[0] || M.groups[G.state];
      var next = PBCore.editState(g.comment, mutator);
      return call('PUT', '/api/groups/' + q(G.state), { name: G.state, comment: next.json, enabled: false })
        .then(function () { M.groups[G.state].comment = next.json; M.state = next.state; });
    });
  }
  // Apply a full rule set. Only groups that actually change are written.
  function applyRules(blockedMap, offline) {
    var jobs = [];
    M.services.forEach(function (s) {
      var want = !!blockedMap[s.meta.id];
      if (want !== s.blocked) jobs.push(function () { return putGroup(s.group, want).then(function () { s.blocked = want; }); });
    });
    if (offline !== undefined && !!offline !== M.offline) {
      jobs.push(function () { return putGroup(M.groups[G.offline], offline).then(function () { M.offline = !!offline; }); });
    }
    return jobs.reduce(function (p, job) { return p.then(job); }, Promise.resolve());
  }

  // ------------------------------------------------------------------ actions
  function run(fn, okMsg) {
    if (busy) return Promise.resolve();
    busy = true; document.body.classList.add('is-busy');
    return Promise.resolve().then(fn)
      .then(function () { if (okMsg) toast(typeof okMsg === 'function' ? okMsg() : okMsg); })
      .catch(function (e) { if (e && e.status !== 401) toast(t('failed', { e: e.message || String(e) }), true); })
      .then(flushDns)
      .then(function () { busy = false; document.body.classList.remove('is-busy'); return load().catch(function () {}); });
  }
  function timerActive() { return !!(M && M.state.timer); }

  function toggleService(id) {
    var s = M.services.filter(function (x) { return x.meta.id === id; })[0];
    if (!s || timerActive()) return;
    var want = !s.blocked;
    run(function () { return putGroup(s.group, want).then(function () { s.blocked = want; }); },
      t(want ? 'nowBlocked' : 'nowAllowed', { n: svcName(s.meta) }));
  }
  function setAll(blocked) {
    if (timerActive()) return;
    var map = {}; M.services.forEach(function (s) { map[s.meta.id] = blocked; });
    run(function () { return applyRules(map); }, t(blocked ? 'allBlocked' : 'allAllowed'));
  }
  function homework() {
    var map = {}; M.services.forEach(function (s) { map[s.meta.id] = !!s.meta.homework; });
    run(function () {
      return endTimerIfAny().then(function () { return applyRules(map, M.state.scheduleActive ? undefined : false); });
    }, t('homeworkOn'));
  }
  function heroAction() {
    if (timerActive()) return endTimer();
    if (!M.kids.length && !M.offline) return openAdd();
    var want = !M.offline;
    run(function () { return applyRules(currentMap(), want); }, t(want ? 'internetOff' : 'internetOn'));
  }
  function currentMap() { var m = {}; M.services.forEach(function (s) { m[s.meta.id] = s.blocked; }); return m; }

  function openTimer(mode) {
    timerMode = mode; timerMinutes = 0;
    $('timerTitle').textContent = t(mode === 'free' ? 'timerFreeTitle' : 'timerBlockTitle');
    $('timerDesc').textContent = t(mode === 'free' ? 'timerFreeDesc' : 'timerBlockDesc');
    var labels = { 30: t('min30'), 60: t('hour1'), 120: t('hours2'), 180: t('hours3') };
    Array.prototype.forEach.call(document.querySelectorAll('#timerChips .chip'), function (c) {
      c.textContent = labels[c.getAttribute('data-minutes')]; c.setAttribute('aria-pressed', 'false');
    });
    $('timerMinutes').value = '';
    openDialog('timerDialog');
  }
  function startTimer() {
    var typed = parseInt($('timerMinutes').value, 10);
    var minutes = typed > 0 ? typed : timerMinutes;
    if (!(minutes >= 5 && minutes <= 720)) { toast(t('enterMinutes'), true); return; }
    closeDialog('timerDialog');
    var mode = timerMode;
    run(function () {
      return endTimerIfAny().then(function () {
        var snap = snapshot();
        var rules = mode === 'free' ? applyRules({}, false) : applyRules(snap.services, true);
        return rules.then(function () {
          return writeState(function (st) {
            st.timer = { mode: mode, until: Math.round(serverNowSec()) + minutes * 60, snapshot: snap };
          });
        });
      });
    }, t(mode === 'free' ? 'freeOn' : 'breakOn'));
  }
  // Restore the rules saved when the timer started, then clear it.
  function restoreTimer() {
    var tm = M.state.timer;
    if (!tm) return Promise.resolve();
    var offline = tm.snapshot.offline || M.state.scheduleActive;
    return applyRules(tm.snapshot.services, offline).then(function () {
      return writeState(function (st) { st.timer = null; });
    });
  }
  function endTimerIfAny() { return timerActive() ? restoreTimer() : Promise.resolve(); }
  function endTimer() { run(restoreTimer, t('timerEnded')); }

  function togglePause(client) {
    var k = M.kids.filter(function (x) { return x.row.client === client; })[0]; if (!k) return;
    var pid = M.groups[G.paused].id;
    var groups = k.row.groups.filter(function (g) { return g !== pid; });
    if (!k.paused) groups.push(pid);
    run(function () { return putClient(k.row, groups); }, t(k.paused ? 'deviceResumed' : 'devicePaused', { n: k.name }));
  }
  function removeKid(client) {
    var k = M.kids.filter(function (x) { return x.row.client === client; })[0]; if (!k) return;
    confirmBox(t('confirmRemove', { n: k.name }), t('remove')).then(function (yes) {
      if (!yes) return;
      var ours = {}; Object.keys(M.groups).forEach(function (n) { if (n.indexOf('pb-') === 0) ours[M.groups[n].id] = true; });
      var rest = k.row.groups.filter(function (g) { return !ours[g]; });
      run(function () {
        if (rest.length === 0 || (rest.length === 1 && rest[0] === DEFAULT_GROUP)) {
          return call('DELETE', '/api/clients/' + q(k.row.client));
        }
        return putClient(k.row, rest);
      }, t('deviceRemoved', { n: k.name }));
    });
  }

  var MAC_RE = /^[0-9a-f]{2}([:-][0-9a-f]{2}){5}$/i;
  var IPV4_RE = /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/;
  function normalizeAddr(v) {
    v = (v || '').trim();
    if (MAC_RE.test(v)) return v.replace(/-/g, ':').toUpperCase();
    if (IPV4_RE.test(v)) return v;
    if (v.indexOf(':') >= 0 && /^[0-9a-f:]+$/i.test(v) && v.length >= 3) return v.toLowerCase();  // IPv6
    return null;
  }
  // The address to register a network-table entry by. A real MAC is stable. An "ip-<addr>" entry means
  // FTL has no MAC for it: prefer its IPv4 address, and offer nothing for an IPv6-only entry (phones
  // rotate their IPv6 privacy addresses daily, which would silently end the filtering).
  function deviceAddr(d) {
    var hw = d.hwaddr || '';
    if (hw.indexOf('ip-') !== 0) return hw.toUpperCase();
    if (parseIpv4(hw.slice(3))) return hw.slice(3);
    var v4 = (d.ips || []).map(function (i) { return i.ip; }).filter(function (a) { return parseIpv4(a); })[0];
    return v4 || '';
  }
  function openAdd() {
    pickedDevice = null;
    $('manualAddr').value = ''; $('devName').value = ''; $('addErr').textContent = '';
    var found = renderPicker();
    document.querySelector('#addForm .manual').open = !found;
    openDialog('addDialog');
  }
  function renderPicker() {
    var list = $('picker'); list.textContent = '';
    var taken = {};
    M.clients.forEach(function (c) {
      if (c.groups.indexOf(M.groups[G.kids].id) >= 0) taken[c.client.toLowerCase()] = true;
    });
    var now = serverNowSec();
    var devices = M.devices.filter(function (d) {
      var addr = deviceAddr(d).toLowerCase();
      if (!addr || taken[addr]) return false;
      if ((d.ips || []).some(function (i) { return taken[i.ip]; })) return false;
      return d.lastQuery && now - d.lastQuery < 30 * 86400;
    }).sort(function (a, b) { return b.lastQuery - a.lastQuery; }).slice(0, 25);
    if (!devices.length) { list.appendChild(el('li', { class: 'picker-empty', text: t('noPicker') })); return 0; }
    devices.forEach(function (d) {
      var addr = deviceAddr(d);
      var ips = (d.ips || []).map(function (i) { return i.ip; });
      var mine = M.myIp && ips.indexOf(M.myIp) >= 0;
      var label = deviceLabel(d) || d.macVendor || addr;
      var btn = el('button', { type: 'button', class: 'pick', 'data-act': 'pick', 'data-addr': addr,
        'data-name': deviceLabel(d), 'aria-pressed': 'false' }, [
        icon('i-device'),
        el('span', { class: 'pick-text' }, [
          el('span', { class: 'pick-name', text: label }),
          el('span', { class: 'pick-meta', text: [mine ? t('thisDevice') : '', ips[0] || '', d.macVendor || ''].filter(Boolean).join('  ') }),
          el('span', { class: 'pick-meta', text: fmtAgo(d.lastQuery) })
        ])
      ]);
      list.appendChild(el('li', null, [btn]));
    });
    return devices.length;
  }
  function pick(btn) {
    Array.prototype.forEach.call(document.querySelectorAll('#picker .pick'), function (b) { b.setAttribute('aria-pressed', 'false'); });
    btn.setAttribute('aria-pressed', 'true');
    pickedDevice = btn.getAttribute('data-addr');
    if (!$('devName').value) $('devName').value = btn.getAttribute('data-name') || '';
    $('addErr').textContent = '';
  }
  function addDevice() {
    var typed = $('manualAddr').value.trim();
    var addr = typed ? normalizeAddr(typed) : pickedDevice;
    if (typed && !addr) { $('addErr').textContent = t('badAddr'); $('manualAddr').focus(); return; }
    if (!addr) { $('addErr').textContent = t('pickOrType'); return; }
    if (typed && addr.indexOf(':') >= 0 && !MAC_RE.test(addr)) {       // an IPv6 address typed by hand
      confirmBox(t('ipv6Warn'), t('add')).then(function (yes) { if (yes) submitDevice(addr); });
      return;
    }
    submitDevice(addr);
  }
  function submitDevice(addr) {
    var name = $('devName').value.trim();
    var existing = M.clients.filter(function (c) { return c.client.toLowerCase() === addr.toLowerCase(); })[0];
    var shadow = shadowRowsFor(addr, M.devices, M.clients, M.groups, false);
    closeDialog('addDialog');
    run(function () {
      var need = kidGroupIds();
      if (existing) {
        var merged = existing.groups.concat(need).filter(function (v, i, a) { return a.indexOf(v) === i; });
        return call('PUT', '/api/clients/' + q(existing.client), { comment: name || existing.comment || '', groups: merged });
      }
      return call('POST', '/api/clients', { client: addr, comment: name, groups: need });
    }, t('deviceAdded', { n: name || addr })).then(function () {
      if (shadow.length) toast(t('shadowedToast', { n: name || addr, r: shadow.map(function (x) { return x.client; }).join(', ') }), true);
    });
  }

  function saveBedtime() {
    var start = $('bedStart').value, end = $('bedEnd').value;
    var days = Array.prototype.filter.call(document.querySelectorAll('#bedDays input'), function (i) { return i.checked; })
      .map(function (i) { return +i.value; });
    var enabled = $('bedOn').checked;
    if (!/^\d\d:\d\d$/.test(start) || !/^\d\d:\d\d$/.test(end)) return;
    if (enabled && start === end) { toast(t('sameTimes'), true); return; }
    if (enabled && !days.length) { toast(t('pickNight'), true); return; }
    run(function () {
      return writeState(function (st) { st.schedule = { enabled: enabled, start: start, end: end, days: days }; });
    }, t('bedtimeSaved'));
  }

  // ------------------------------------------------------------------ render
  function banner(msg) { var b = $('banner'); b.hidden = !msg; b.textContent = msg || ''; }

  // The banner for a newer Sinko (or an update that is running or failed): opens My box, where the update is done.
  function renderUpdateBanner() {
    var b = $('updateBanner');
    PBBox.sweep(M.state);                       // an update request nobody picks up is withdrawn after a while (see pb-box.js)
    var view = PBBox.pure.updateView(M.state, { now: serverNowSec(), current: pageVersion });
    var text = '';
    if (view.phase === 'available') text = t('updateReady', { v: view.latest });
    else if (view.phase === 'starting' || view.phase === 'running') text = t('updateRunning');
    else if (view.phase === 'failed' && view.latest) text = t('updateFailed');
    b.hidden = !text; b.textContent = text;
    b.setAttribute('data-phase', view.phase);
  }

  // ---- the setup list: shown until it is dismissed or every step is done
  function routerPointsHere() {
    // A device other than the parent's own phone (and other than the box itself) has asked the box something: the router sends devices here.
    var now = serverNowSec();
    return M.devices.some(function (d) {
      if (!d.lastQuery || now - d.lastQuery > 7 * 86400) return false;
      var ips = (d.ips || []).map(function (i) { return i.ip; });
      if (M.myIp && ips.indexOf(M.myIp) >= 0) return false;
      return !ips.some(function (ip) { return ip === '127.0.0.1' || ip === '::1' || ip === location.hostname; });
    });
  }
  var setupSig = '', setupSaving = false, setupWasShown = false;
  function renderSetup() {
    var card = $('setup'), s = M.state;
    var items = [
      { id: 'pw', done: true, title: t('setupPw') },
      { id: 'router', done: routerPointsHere(), title: t('setupRouter'), how: t('setupRouterHow'), btn: t('setupRouterBtn'), act: 'setupHelp' },
      { id: 'child', done: M.kids.length > 0, title: t('setupChild'), how: t('setupChildHow'), btn: t('addDevice'), act: 'openAdd' }
    ];
    // The counter question is asked only when the box says a counter address is configured (box.json): a promise nothing keeps is worse than
    // silence. A box with no box.json (an older one) does not ask.
    if (boxInfo && boxInfo.counter === true) {
      items.push({ id: 'counter', done: s.telemetry.on !== null, title: t('setupCounterQ'), doneTitle: t('setupCounterDone'), how: t('setupCounterText') });
    }
    var doneCount = items.filter(function (i) { return i.done; }).length;
    var all = doneCount === items.length;
    if (all && !s.setup.done && !setupSaving) {
      // Everything is ticked: remember it, so the list does not come back if the router changes later.
      setupSaving = true;
      writeState(function (st) { st.setup.done = true; }).then(function () { if (setupWasShown) toast(t('setupDoneToast')); }, function () {})
        .then(function () { setupSaving = false; });
    }
    var hide = s.setup.done || all;
    card.hidden = hide;
    if (hide) { setupSig = ''; setupWasShown = false; return; }
    setupWasShown = true;
    var sig = lang + '|' + items.map(function (i) { return i.id + (i.done ? '1' : '0'); }).join('');
    if (sig === setupSig) return;                     // the list is only rebuilt when it changed, so a button being pressed is not replaced
    setupSig = sig;
    $('setupProgress').textContent = t('setupProgress', { n: doneCount, t: items.length });
    var ul = $('setupList'); ul.textContent = '';
    items.forEach(function (i) {
      var kids = [el('span', { class: 'setup-check', 'aria-hidden': 'true' }, [icon(i.done ? 'i-check' : 'i-dot')])];
      var text = [el('span', { class: 'setup-title', text: i.done && i.doneTitle ? i.doneTitle : i.title }),
        el('span', { class: 'sr-only', text: ' (' + t(i.done ? 'setupDone' : 'setupTodo') + ')' })];
      var li = el('li', { class: 'setup-item' + (i.done ? ' is-done' : ''), 'data-item': i.id });
      if (!i.done && i.how) text.push(el('span', { class: 'setup-how', text: i.how }));
      var body = el('div', { class: 'setup-text' }, text);
      if (!i.done && i.act) body.appendChild(el('div', { class: 'setup-actions' }, [el('button', { type: 'button', class: 'btn btn-small', 'data-act': i.act, text: i.btn })]));
      if (!i.done && i.id === 'counter') {
        body.appendChild(el('div', { class: 'setup-actions' }, [
          el('button', { type: 'button', class: 'btn btn-small btn-primary', 'data-act': 'counterYes', text: t('setupYes') }),
          el('button', { type: 'button', class: 'btn btn-small', 'data-act': 'counterNo', text: t('setupNotNow') })]));
      }
      li.appendChild(kids[0]); li.appendChild(body);
      ul.appendChild(li);
    });
  }
  function answerCounter(yes) {
    run(function () { return writeState(function (st) { st.telemetry = { on: yes }; }); }, t(yes ? 'boxCounterOnToast' : 'setupLaterToast'));
  }
  function hideSetup() { run(function () { return writeState(function (st) { st.setup.done = true; }); }); }
  function showSetupHelp() {
    var h = $('help'); h.open = true;
    if (h.scrollIntoView) h.scrollIntoView({ behavior: window.PBLive && PBLive.reducedMotion() ? 'auto' : 'smooth', block: 'center' });
    $('help').querySelector('summary').focus({ preventScroll: true });
  }

  function render() {
    if (!M) return;
    renderHero();
    renderUpdateBanner();
    renderSetup();
    renderServices();
    renderDevices();
    renderBedtime(false);
    var tm = M.state.timer;
    banner(schedulerSilent() ? t('schedulerDown') : '');
    renderHelp();
    Array.prototype.forEach.call(document.querySelectorAll('.mode'), function (b) {
      var m = b.getAttribute('data-mode');
      b.classList.toggle('mode-on', !!(tm && m === tm.mode));
    });
    renderPicture();
  }

  // The live picture (pb-picture.js) gets a plain description of the family: it never touches M or the API rules.
  function picModel() {
    var apps = {};
    catalog.services.forEach(function (s) { apps[s.id] = { name: svcName(s), mono: monogram(s.name), color: s.color || '#667' }; });
    return {
      lang: lang, dir: lang === 'ar' ? 'rtl' : 'ltr', locale: locale(), serverNow: serverNowSec(),
      // Without any device activity (privacy level, failed list) "not seen in 24 hours" would be a guess: say "rules set" instead.
      activityKnown: M.devices.some(function (d) { return d.lastQuery > 0; }),
      offline: M.offline, timer: !!M.state.timer, timerMode: M.state.timer ? M.state.timer.mode : '',
      kids: M.kids.map(function (k) {
        var ips = k.dev ? deviceIps(k.dev) : [];
        if (parseIp(k.row.client) && ips.indexOf(k.row.client) < 0) ips.push(k.row.client);
        return { key: k.row.client, name: k.name, ips: ips, lastQuery: k.dev ? k.dev.lastQuery : 0, shadowed: k.shadow.length > 0, paused: k.paused };
      }),
      blockedApps: M.services.filter(function (s) { return s.blocked; }).map(function (s) { return s.meta.id; }),
      apps: apps
    };
  }
  function renderPicture() {
    if (!window.PBPicture || !window.PBCore || !window.PBLive) return;
    if (!picInited) {
      picInited = true;
      var picEnv = {
        call: call, t: t, locale: locale(), serverNow: serverNowSec,
        openSheet: function () { openDialog('liveSheet'); },
        goDevices: function () {
          var d = $('devices');
          if (d && d.scrollIntoView) d.scrollIntoView({ behavior: window.PBLive.reducedMotion() ? 'auto' : 'smooth', block: 'center' });
        }
      };
      Object.defineProperty(picEnv, 'domains', { get: function () { return domainMap || {}; } });      // always the current map
      PBPicture.init(picEnv);
    }
    PBPicture.update(picModel());
  }

  function renderHero() {
    var hero = $('hero'), tm = M.state.timer;
    var blocked = M.services.filter(function (s) { return s.blocked; }).length;
    var total = M.services.length;
    var n = M.kids.length;
    var devicesLine = n === 0 ? t('subNoDevices') : (n === 1 ? t('subDevice1') : t('subDevices', { n: n }));
    var appsLine = blocked ? t('subBlocked', { n: blocked, t: total }) : t('subNone');
    hero.classList.remove('hero-off', 'hero-free', 'hero-block');
    var kicker, title, sub, btn;
    if (tm) {
      var ends = new Date(tm.until * 1000);
      hero.classList.add(tm.mode === 'free' ? 'hero-free' : 'hero-block');
      kicker = t(tm.mode === 'free' ? 'heroFree' : 'heroBreak');
      title = t('endsAt', { t: fmtTime(ends) });
      sub = devicesLine;
      btn = t('endNow');
    } else if (M.offline) {
      hero.classList.add('hero-off');
      kicker = M.state.scheduleActive ? t('heroBedtime') : t('heroOnKicker');
      title = t('heroOff');
      sub = M.state.scheduleActive ? t('bedEndsNote', { t: fmtHHMM(M.state.schedule.end) }) + ' ' + devicesLine : devicesLine;
      btn = t('turnOn');
    } else {
      kicker = t('heroOnKicker');
      title = t('heroOn');
      sub = appsLine + ' ' + devicesLine;
      btn = n ? t('turnOff') : t('addDevice');
    }
    $('heroKicker').textContent = kicker;
    $('heroTitle').textContent = title;
    $('heroSub').textContent = sub;
    $('heroBtn').textContent = btn;
    $('heroClock').hidden = !tm;
    tickClock();
  }
  function tickClock() {
    clearInterval(tickTimer);
    var tm = M && M.state.timer;
    if (!tm) return;
    var upd = function () {
      var left = tm.until - serverNowSec();
      $('heroClock').textContent = fmtClock(left);
      if (left <= 0) { clearInterval(tickTimer); setTimeout(function () { load().catch(function () {}); }, 20000); }
    };
    upd(); tickTimer = setInterval(upd, 1000);
  }

  function renderServices() {
    var root = $('services'); root.textContent = '';
    var locked = timerActive();
    $('svcNote').textContent = locked ? t('svcLocked') : t('svcNote');
    Array.prototype.forEach.call(document.querySelectorAll('[data-act=allowAll],[data-act=blockAll]'), function (b) { b.disabled = locked; });
    catalog.categories.forEach(function (cat) {
      var items = M.services.filter(function (s) { return s.meta.category === cat.id; });
      if (!items.length) return;
      var grid = el('div', { class: 'tiles' });
      items.forEach(function (s) {
        var name = svcName(s.meta);
        var badge = el('span', { class: 'tile-badge', 'aria-hidden': 'true', text: monogram(s.meta.name) });
        badge.style.setProperty('--brand', s.meta.color || '#667');
        grid.appendChild(el('button', {
          type: 'button', class: 'tile' + (s.blocked ? ' tile-blocked' : ''), 'data-act': 'svc', 'data-id': s.meta.id,
          'aria-pressed': s.blocked ? 'true' : 'false', disabled: locked,
          'aria-label': name + ', ' + t(s.blocked ? 'blocked' : 'allowed')
        }, [badge, el('span', { class: 'tile-name', text: name }),
          el('span', { class: 'tile-state', text: t(s.blocked ? 'blocked' : 'allowed') })]));
      });
      root.appendChild(el('div', { class: 'cat' }, [el('h3', { text: cat[lang] || cat.en }), grid]));
    });
  }
  function monogram(name) {
    var words = name.replace(/[^A-Za-z0-9 ]/g, ' ').trim().split(/\s+/);
    return (words.length > 1 ? words[0][0] + words[1][0] : name.slice(0, 2)).toUpperCase().slice(0, 2);
  }

  function renderDevices() {
    var ul = $('devices'); ul.textContent = '';
    if (!M.kids.length) { ul.appendChild(el('li', { class: 'empty', text: t('noDevices') })); return; }
    M.kids.forEach(function (k) {
      var ips = k.dev ? (k.dev.ips || []).map(function (i) { return i.ip; }) : [];
      var mine = M.myIp && (k.row.client === M.myIp || ips.indexOf(M.myIp) >= 0);
      var meta = [mine ? t('thisDevice') : '', k.row.client].filter(Boolean).join('  ');
      ul.appendChild(el('li', { class: 'device' + (k.paused ? ' device-paused' : '') }, [
        el('span', { class: 'device-icon' }, [icon('i-device')]),
        el('span', { class: 'device-text' }, [
          el('span', { class: 'device-name' }, [k.name, k.paused ? el('span', { class: 'tag', text: t('paused') }) : null]),
          el('span', { class: 'device-meta', text: meta }),
          el('span', { class: 'device-meta', text: fmtAgo(k.dev && k.dev.lastQuery) }),
          k.shadow.length ? el('span', { class: 'device-warn', role: 'alert',
            text: t('shadowed', { r: k.shadow.map(function (x) { return x.client; }).join(', ') }) }) : null
        ]),
        el('span', { class: 'device-actions' }, [
          el('button', { type: 'button', class: 'btn btn-small', 'data-act': 'pause', 'data-client': k.row.client },
            [icon(k.paused ? 'i-play' : 'i-pause'), el('span', { text: t(k.paused ? 'resume' : 'pause') })]),
          el('button', { type: 'button', class: 'icon-btn', 'data-act': 'remove', 'data-client': k.row.client,
            'aria-label': t('remove') + ' ' + k.name }, [icon('i-close')])
        ])
      ]));
    });
  }

  var bedDirty = false;
  function renderBedtime(force) {
    if (bedDirty && !force) return;          // don't clobber what the parent is editing
    var sc = M.state.schedule;
    $('bedOn').checked = sc.enabled;
    $('bedStart').value = sc.start;
    $('bedEnd').value = sc.end;
    var fs = $('bedDays');
    Array.prototype.slice.call(fs.querySelectorAll('label')).forEach(function (l) { l.remove(); });
    for (var d = 0; d < 7; d++) {
      var input = el('input', { type: 'checkbox', value: String(d) });
      input.checked = sc.days.indexOf(d) >= 0;
      fs.appendChild(el('label', { class: 'day', title: t('daysLong')[d] }, [input, el('span', { text: t('days')[d] })]));
    }
    bedDirty = false;
    syncBedOn();
  }
  function syncBedOn() { $('bedFields').classList.toggle('is-off', !$('bedOn').checked); }

  function applyLang() {
    var html = document.documentElement;
    html.lang = lang; html.dir = lang === 'ar' ? 'rtl' : 'ltr';
    document.title = t('appName');
    Array.prototype.forEach.call(document.querySelectorAll('[data-i18n]'), function (n) {
      n.textContent = t(n.getAttribute('data-i18n'));
    });
    Array.prototype.forEach.call(document.querySelectorAll('.lang-toggle'), function (n) {
      n.textContent = t('langSwitch'); n.setAttribute('lang', lang === 'ar' ? 'en' : 'ar');
    });
    renderHelp();
    if (window.PBBox) PBBox.relang();
    if (M) { render(); renderBedtime(true); }
  }

  // ------------------------------------------------------------------ dialogs
  function openDialog(id) { var d = $(id); if (d.showModal) d.showModal(); else d.setAttribute('open', ''); }
  function closeDialog(id) { var d = $(id); if (d.close) d.close(); else d.removeAttribute('open'); }
  function confirmBox(text, yesLabel) {
    return new Promise(function (resolve) {
      var d = $('confirmDialog');
      $('confirmText').textContent = text; $('confirmYes').textContent = yesLabel;
      var done = function () { d.removeEventListener('close', done); resolve(d.returnValue === 'yes'); };
      d.returnValue = '';
      d.addEventListener('close', done);
      openDialog('confirmDialog');
    });
  }

  // ------------------------------------------------------------------ wiring
  function onClick(ev) {
    var n = ev.target.closest ? ev.target.closest('[data-act]') : null;
    if (!n || n.disabled) return;
    var act = n.getAttribute('data-act');
    switch (act) {
      case 'lang': lang = lang === 'ar' ? 'en' : 'ar'; safeSet(localStorage, LANG_KEY, lang); applyLang(); break;
      case 'logout': logout(); break;
      case 'hero': heroAction(); break;
      case 'homework': homework(); break;
      case 'timer': openTimer(n.getAttribute('data-mode')); break;
      case 'startTimer': startTimer(); break;
      case 'svc': toggleService(n.getAttribute('data-id')); break;
      case 'allowAll': setAll(false); break;
      case 'blockAll': setAll(true); break;
      case 'openAdd': openAdd(); break;
      case 'pick': pick(n); break;
      case 'addDevice': addDevice(); break;
      case 'pause': togglePause(n.getAttribute('data-client')); break;
      case 'remove': removeKid(n.getAttribute('data-client')); break;
      case 'saveBed': saveBedtime(); break;
      case 'box': PBBox.open(); break;
      case 'boxClose': closeDialog('boxDialog'); break;
      case 'setupHelp': showSetupHelp(); break;
      case 'counterYes': answerCounter(true); break;
      case 'counterNo': answerCounter(false); break;
      case 'setupHide': hideSetup(); break;
    }
  }
  function onChipClick(ev) {
    var c = ev.target.closest('.chip'); if (!c) return;
    Array.prototype.forEach.call(document.querySelectorAll('#timerChips .chip'), function (x) { x.setAttribute('aria-pressed', 'false'); });
    c.setAttribute('aria-pressed', 'true');
    timerMinutes = +c.getAttribute('data-minutes');
    $('timerMinutes').value = '';
  }

  function startPolling() {
    stopPolling();
    pollTimer = setInterval(function () {
      if (document.visibilityState === 'visible' && !busy && !document.querySelector('dialog[open]')) load().catch(function () {});
    }, POLL_MS);
  }
  function stopPolling() { clearInterval(pollTimer); clearInterval(tickTimer); }

  // pb/version.txt is the version the box has installed now ('' when it cannot be read or is not a version).
  function fetchInstalled() {
    return fetch('/pb/version.txt', { cache: 'no-store' }).then(function (r) { return r.ok ? r.text() : ''; })
      .then(function (v) { v = (v || '').trim(); return PBBox.pure.parseSemver(v) ? v : ''; }, function () { return ''; });   // a 404 page or garbage is not a version
  }
  // Pi-hole's web server lets a browser keep a page and its scripts for an hour, so a phone can be running a release that is gone from the box:
  // one that stayed open through an update, or another phone's, or the night's automatic update. A page that was stamped by the installer knows
  // what it is; when the box has another version it reloads itself (a reload fetches the page afresh, and every script and style is addressed by the
  // new version). Not while an update runs (the files are being replaced), not twice for one version, and only where a tab can remember it.
  function checkStale() {
    if (!pageStamp || staleReloading) return Promise.resolve(false);
    if (M && M.state.update.status === 'running') return Promise.resolve(false);
    return fetchInstalled().then(function (v) {
      var target = PBBox.pure.staleTarget(pageStamp, v, safeGet(sessionStorage, RELOADED_KEY));
      if (!target || staleReloading) return false;
      safeSet(sessionStorage, UPDATED_KEY, target);
      if (safeGet(sessionStorage, UPDATED_KEY) !== target) return false;                   // no memory in this tab (private mode): never risk a loop
      staleReloading = true;
      location.reload();
      return true;
    });
  }
  // Coming back to the page: look at the version, but not while something is open or being typed, and not more than once a minute.
  function checkStaleLater() {
    var now = Date.now();
    if (now - staleCheckedAt < PBBox.timing.staleEvery || $('app').hidden || document.querySelector('dialog[open]') || bedDirty || busy) return;
    staleCheckedAt = now;
    checkStale().catch(function () {});
  }

  function start() {
    return load().then(function () { showApp(); startPolling(); staleCheckedAt = Date.now(); checkStale(); })
      .catch(function (e) {
        if (e && e.status === 401) return;
        showApp(); banner(e && e.message ? e.message : t('noConnection'));
      });
  }

  function boot() {
    applyLang();
    var meta = document.querySelector('meta[name="sinko-version"]');
    pageStamp = PBBox.pure.pageStamp(meta && meta.getAttribute('content'));
    if (pageStamp) {
      pageVersion = pageStamp;                                  // the page says what it is: the box's version file may already be newer
      $('version').textContent = 'v' + pageVersion;
    } else {
      fetchInstalled().then(function (v) {                      // development or a copy that was not stamped: the box's own version is the best there is
        pageVersion = v;
        $('version').textContent = pageVersion ? 'v' + pageVersion : '';
        if (M) renderUpdateBanner();
      }).catch(function () {});
    }
    PBBox.init({
      t: t, el: el, icon: icon, lang: function () { return lang; }, locale: locale, fmtTime: fmtTime,
      formatCount: function (n) { return PBCore.formatCount(n, locale()); },
      call: call, model: function () { return M; }, state: function () { return M ? M.state : null; },
      writeState: writeState, reload: load, openDialog: openDialog, closeDialog: closeDialog, confirm: confirmBox, toast: toast,
      endSession: endSession, serverNowSec: serverNowSec, clockOffsetMs: function () { return serverOffsetMs; },
      version: function () { return pageVersion; },
      boxInfo: function () { return boxInfo; }, refreshBoxInfo: loadBoxInfo,
      rememberUpdate: function (v) { safeSet(sessionStorage, UPDATED_KEY, v); },
      reloadedFor: function () { return safeGet(sessionStorage, RELOADED_KEY) || ''; }
    });
    var updated = safeGet(sessionStorage, UPDATED_KEY);        // the page just reloaded itself after an update
    if (updated) {
      safeSet(sessionStorage, UPDATED_KEY, null);
      safeSet(sessionStorage, RELOADED_KEY, updated);            // remembered for this tab: see checkStale
      // Said only when the page really is that version now (an unstamped page cannot tell): a reload that brought the same old page back says nothing.
      if (!pageStamp || pageStamp === updated) setTimeout(function () { toast(t('boxUpdatedToast', { v: updated })); }, 700);
    }
    document.addEventListener('click', onClick);
    $('timerChips').addEventListener('click', onChipClick);
    $('loginForm').addEventListener('submit', login);
    $('claimForm').addEventListener('submit', claim);
    ['bedOn', 'bedStart', 'bedEnd', 'bedDays'].forEach(function (id) {
      $(id).addEventListener('change', function () { bedDirty = true; syncBedOn(); });
    });
    ['timerForm', 'addForm'].forEach(function (id) {
      $(id).addEventListener('submit', function (ev) {
        if (!ev.submitter || ev.submitter.value !== 'cancel') ev.preventDefault();
      });
    });
    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState === 'visible' && !$('app').hidden && !busy) load().catch(function () {}).then(checkStaleLater);
    });
    noPasswordSet().catch(function () { return false; }).then(function (open) {
      if (open) {
        // No password: offer to choose one when Sinko is on the box; otherwise carry on as before (the page explains what is missing).
        return sinkoInstalled().then(function (installed) { return installed ? showClaim() : start(); });
      }
      return probeAuth().then(function (valid) {
        if (valid) return start();
        if (sid) { sid = ''; safeSet(sessionStorage, SID_KEY, null); }
        showLogin();
      });
    }).catch(function () { showLogin(); $('loginErr').textContent = t('noConnection'); });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
