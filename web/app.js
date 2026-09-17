/* Family Internet — parent page for pihole-bahrain.
 *
 * Talks only to the local Pi-hole v6 API (same origin), signed in with the
 * Pi-hole admin password. Blocking is done by enabling/disabling Pi-hole
 * groups (see bin/pihole-bahrain for the model). Timers and bedtime are stored
 * in the "pb-state" group and enforced by the pihole-bahrain service on the Pi,
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
  var DEFAULT_STATE = { v: 1, timer: null, scheduleActive: false,
    schedule: { enabled: false, start: '21:00', end: '06:00', days: [0, 1, 2, 3, 4, 5, 6] } };

  // ------------------------------------------------------------------ strings
  var STR = {
    en: {
      appName: 'Family Internet', loginHint: 'Sign in with the parent password chosen during setup.', password: 'Password',
      totp: '6-digit code from your authenticator app', signIn: 'Sign in', signOut: 'Sign out',
      wrongPassword: 'That password is not right. Try again.', wrongTotp: 'Enter the password and the 6-digit code.',
      noConnection: 'Cannot reach the Family Internet box. Check that it is switched on and connected.',
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
      helpBody: 'For the rules to work, your router must send all devices to this box for DNS. In the router settings, set the DNS server to {ip} and reserve that address for the box. Then turn Wi\u2011Fi off and on again on each child\u2019s device.', close: 'Close', cancel: 'Cancel', start: 'Start', add: 'Add',
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
      schedulerDown: 'The timer on the box is not running. On the Pi, run: sudo systemctl restart pihole-bahrain',
      notInstalled: 'Setup is not finished on this box. On the Pi, run: sudo pihole-bahrain setup',
      failed: 'That did not work: {e}', sessionEnded: 'Your session ended. Sign in again.',
      days: ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'],
      daysLong: ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'],
      cat: {}, langSwitch: 'العربية', minutesShort: '{n} min'
    },
    ar: {
      appName: 'إنترنت العائلة', loginHint: 'سجّل الدخول بكلمة مرور الوالدين التي اخترتها أثناء الإعداد.', password: 'كلمة المرور',
      totp: 'الرمز المكوّن من 6 أرقام من تطبيق المصادقة', signIn: 'تسجيل الدخول', signOut: 'تسجيل الخروج',
      wrongPassword: 'كلمة المرور غير صحيحة. حاول مرة أخرى.', wrongTotp: 'أدخل كلمة المرور والرمز المكوّن من 6 أرقام.',
      noConnection: 'تعذّر الوصول إلى جهاز إنترنت العائلة. تأكد أنه يعمل ومتصل بالشبكة.',
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
      helpBody: 'لكي تعمل القواعد، يجب أن يوجّه جهاز الراوتر كل الأجهزة إلى هذا الجهاز لخدمة DNS. في إعدادات الراوتر، اجعل خادم DNS هو {ip} واحجز هذا العنوان للجهاز. ثم أطفئ Wi‑Fi وشغّله مجددًا على جهاز كل طفل.', close: 'إغلاق', cancel: 'إلغاء', start: 'ابدأ', add: 'إضافة',
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
      schedulerDown: 'المؤقت على الجهاز لا يعمل. نفّذ على الجهاز: sudo systemctl restart pihole-bahrain',
      notInstalled: 'الإعداد غير مكتمل على هذا الجهاز. نفّذ على الجهاز: sudo pihole-bahrain setup',
      failed: 'لم تنجح العملية: {e}', sessionEnded: 'انتهت الجلسة. سجّل الدخول مجددًا.',
      days: ['أحد', 'اثنين', 'ثلاثاء', 'أربعاء', 'خميس', 'جمعة', 'سبت'],
      daysLong: ['الأحد', 'الاثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة', 'السبت'],
      cat: {}, langSwitch: 'English', minutesShort: '{n} دقيقة'
    }
  };

  // ------------------------------------------------------------------ state
  var lang = safeGet(localStorage, LANG_KEY) === 'ar' ? 'ar' : (safeGet(localStorage, LANG_KEY) ? 'en' : guessLang());
  var sid = safeGet(sessionStorage, SID_KEY) || '';
  var catalog = null;                // services.json
  var M = null;                      // model from last load
  var busy = false;
  var pollTimer = null, tickTimer = null;
  var timerMode = 'free', timerMinutes = 0, pickedDevice = null;

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
    var diff = Math.round((epoch * 1000 - Date.now()) / 60000);
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
    var n = $('toast'); n.textContent = msg; n.classList.toggle('toast-error', !!isError); n.classList.add('show');
    clearTimeout(toastTimer); toastTimer = setTimeout(function () { n.classList.remove('show'); }, isError ? 6000 : 2600);
  }

  // ------------------------------------------------------------------ API
  function ApiError(status, message) { this.status = status; this.message = message; }
  function call(method, path, body) {
    var headers = { Accept: 'application/json' };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (sid) headers.sid = sid;
    return fetch(path, { method: method, headers: headers, credentials: 'omit', cache: 'no-store',
      body: body === undefined ? undefined : JSON.stringify(body) })
      .catch(function () { throw new ApiError(0, t('noConnection')); })
      .then(function (r) {
        return r.text().then(function (txt) {
          var j = {}; try { j = txt ? JSON.parse(txt) : {}; } catch (e) { j = {}; }
          if (r.status === 401) { endSession(); throw new ApiError(401, t('sessionEnded')); }
          if (!r.ok) {
            var e = j && j.error; var m = e ? (e.message || e.key || String(e)) : ('HTTP ' + r.status);
            if (e && e.hint) m += ' (' + e.hint + ')';
            throw new ApiError(r.status, m);
          }
          return j;
        });
      });
  }
  function q(s) { return encodeURIComponent(s); }
  function putGroup(g, enabled) {
    return call('PUT', '/api/groups/' + q(g.name), { name: g.name, comment: g.comment || '', enabled: !!enabled })
      .then(function () { g.enabled = !!enabled; });
  }
  function putClient(c, groups) {
    return call('PUT', '/api/clients/' + q(c.client), { comment: c.comment || '', groups: groups });
  }

  // ------------------------------------------------------------------ auth
  function endSession() {
    sid = ''; safeSet(sessionStorage, SID_KEY, null);
    stopPolling(); showLogin();
  }
  function showLogin() { $('app').hidden = true; $('login').hidden = false; setTimeout(function () { $('pw').focus(); }, 0); }
  function showApp() { $('login').hidden = true; $('app').hidden = false; }

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
  function login(ev) {
    ev.preventDefault();
    var pw = $('pw').value, code = $('totp').value.trim();
    var btn = $('loginBtn'); $('loginErr').textContent = '';
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

  // ------------------------------------------------------------------ model
  function parseState(raw) {
    var s = JSON.parse(JSON.stringify(DEFAULT_STATE)), d = {};
    try { d = raw ? JSON.parse(raw) : {}; } catch (e) { d = {}; }
    if (!d || typeof d !== 'object') d = {};
    var tm = d.timer;
    if (tm && (tm.mode === 'free' || tm.mode === 'block') && typeof tm.until === 'number') {
      var snap = tm.snapshot || {};
      s.timer = { mode: tm.mode, until: tm.until, snapshot: { services: snap.services || {}, offline: !!snap.offline } };
    }
    if (d.schedule && typeof d.schedule === 'object') {
      var sc = d.schedule;
      s.schedule.enabled = !!sc.enabled;
      if (/^\d\d:\d\d$/.test(sc.start || '')) s.schedule.start = sc.start;
      if (/^\d\d:\d\d$/.test(sc.end || '')) s.schedule.end = sc.end;
      if (Array.isArray(sc.days)) s.schedule.days = sc.days.filter(function (x) { return x >= 0 && x <= 6; });
    }
    s.scheduleActive = !!d.scheduleActive;
    return s;
  }

  function load() {
    return Promise.all([
      call('GET', '/api/groups'),
      call('GET', '/api/clients'),
      call('GET', '/api/network/devices?max_devices=100').catch(function () { return { devices: [] }; }),
      call('GET', '/api/info/client').catch(function () { return {}; }),
      catalog ? Promise.resolve(catalog) : fetch('/pb/services.json', { cache: 'no-store' }).then(function (r) { return r.json(); })
    ]).then(function (r) {
      catalog = r[4];
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
          return { row: c, name: c.comment || deviceLabel(dev) || c.client, paused: c.groups.indexOf(pausedId) >= 0, dev: dev };
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
    return call('GET', '/api/groups/' + q(G.state)).then(function (j) {
      var g = (j.groups || [])[0] || M.groups[G.state];
      var st = parseState(g.comment);
      mutator(st);
      var comment = JSON.stringify(st);
      return call('PUT', '/api/groups/' + q(G.state), { name: G.state, comment: comment, enabled: false })
        .then(function () { M.groups[G.state].comment = comment; M.state = st; });
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
            st.timer = { mode: mode, until: Math.round(Date.now() / 1000) + minutes * 60, snapshot: snap };
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
  function deviceAddr(d) {
    var hw = d.hwaddr || '';
    if (hw.indexOf('ip-') === 0) return hw.slice(3);
    return hw.toUpperCase();
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
    var now = Date.now() / 1000;
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
    var name = $('devName').value.trim();
    var existing = M.clients.filter(function (c) { return c.client.toLowerCase() === addr.toLowerCase(); })[0];
    closeDialog('addDialog');
    run(function () {
      var need = kidGroupIds();
      if (existing) {
        var merged = existing.groups.concat(need).filter(function (v, i, a) { return a.indexOf(v) === i; });
        return call('PUT', '/api/clients/' + q(existing.client), { comment: name || existing.comment || '', groups: merged });
      }
      return call('POST', '/api/clients', { client: addr, comment: name, groups: need });
    }, t('deviceAdded', { n: name || addr }));
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

  function render() {
    if (!M) return;
    renderHero();
    renderServices();
    renderDevices();
    renderBedtime(false);
    var tm = M.state.timer;
    banner(tm && tm.until * 1000 < Date.now() - 90000 ? t('schedulerDown') : '');
    Array.prototype.forEach.call(document.querySelectorAll('.mode'), function (b) {
      var m = b.getAttribute('data-mode');
      b.classList.toggle('mode-on', !!(tm && m === tm.mode));
    });
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
      var left = tm.until - Date.now() / 1000;
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
          el('span', { class: 'device-meta', text: fmtAgo(k.dev && k.dev.lastQuery) })
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
    $('helpBody').textContent = t('helpBody', { ip: location.hostname });
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

  function start() {
    return load().then(function () { showApp(); startPolling(); })
      .catch(function (e) {
        if (e && e.status === 401) return;
        showApp(); banner(e && e.message ? e.message : t('noConnection'));
      });
  }

  function boot() {
    applyLang();
    fetch('/pb/version.txt', { cache: 'no-store' }).then(function (r) { return r.ok ? r.text() : ''; })
      .then(function (v) { $('version').textContent = v ? 'v' + v.trim() : ''; }).catch(function () {});
    document.addEventListener('click', onClick);
    $('timerChips').addEventListener('click', onChipClick);
    $('loginForm').addEventListener('submit', login);
    ['bedOn', 'bedStart', 'bedEnd', 'bedDays'].forEach(function (id) {
      $(id).addEventListener('change', function () { bedDirty = true; syncBedOn(); });
    });
    ['timerForm', 'addForm'].forEach(function (id) {
      $(id).addEventListener('submit', function (ev) {
        if (!ev.submitter || ev.submitter.value !== 'cancel') ev.preventDefault();
      });
    });
    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState === 'visible' && !$('app').hidden && !busy) load().catch(function () {});
    });
    probeAuth().then(function (valid) {
      if (valid) return start();
      if (sid) { sid = ''; safeSet(sessionStorage, SID_KEY, null); }
      showLogin();
    }).catch(function () { showLogin(); $('loginErr').textContent = t('noConnection'); });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
