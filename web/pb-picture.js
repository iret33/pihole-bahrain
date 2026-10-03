/* pb-picture.js: the live picture on the parent page.
 *
 * Shows the home's internet requests as little packets travelling along wires: each device asks the family box first,
 * the box says yes or no, and an allowed request goes on to the internet. The numbers (checked / stopped / share) come from
 * Pi-hole's own statistics. Everything is read-only: this file never changes a rule.
 *
 * Data (all through the page's own signed-in `call`): /api/stats/summary every 15 s, /api/queries every 3.5 s (a few rows,
 * only newer than the last poll), and, only while the detail sheet is open, /api/history and /api/stats/top_domains.
 * Honesty rules: the numbers say "whole home, last 24 hours"; example packets (the tour) are labelled "Example" and never
 * touch a counter; an unknown domain is never named, only "a website"; nothing here ever shows a raw domain name.
 *
 * Everything shown is set with textContent: names and domains come from the network.
 * Depends on pb-core.js and pb-live.js. ES5 on purpose, like the rest of the page.
 */
(function (root) {
  'use strict';

  var C = root.PBCore, L = root.PBLive;
  var doc = root.document;

  // ------------------------------------------------------------------ module state
  var env = null;              // { call, t, locale, serverNow, domains, openSheet, goDevices, addDevice }
  var m = null;                // the view-model from app.js (see app.js picModel())
  var el = {};                 // cached elements
  var stage = null, feed = null, pacer = null, poller = null, io = null;
  var started = false, inView = true, collapsedByDialog = false;
  var slots = [];              // what is drawn in the device row: [{ id, kind: 'kid'|'more'|'rest'|'add', kids: [...] }]
  var slotKey = '';
  var ipSlot = {};             // ip -> slot id ('dev-0'...)
  var kidState = {};           // kid key -> state name from deviceState
  var lastSeen = {};           // slot id -> ms of the last real query we saw from it
  var summary = null, delta = { total: 0, blocked: 0 }, shown = { total: 0, blocked: 0, share: 0 };
  var failing = 0;             // consecutive failed polls: shows "not updating"
  var clientsHidden = false;   // privacy level 2+: clients are not named in the log
  var domainsHidden = false;   // privacy level 1+
  var queriesHidden = false;   // privacy level 3
  var recent = [];             // [{ kind, label, who, at (ms), n }]
  var wireUntil = {};          // wire id -> ms until which it looks busy
  var voice = { until: 0, last: 0, timer: 0 };
  var pumpTimer = 0, recentTimer = 0, ariaTimer = 0, recentTick = 0, wireTimer = 0, captionTimer = 0, repaintTimer = 0;
  var captionHeld = false, wired = false;
  var tour = { on: false, i: 0, timer: 0 };
  var minute = [];             // [{ at, blocked }] events of the last minute, for the screen-reader summary
  var lastAria = '';
  var detailOpen = false, detail = null;

  var SLOT_MAX = 3;            // kids drawn as their own node (more are folded into "+N")

  function $(id) { return doc.getElementById(id); }
  function t(key, vars) { return env.t(key, vars); }
  function now() { return new Date().getTime(); }
  function fmt(n) { return C.formatCount(n, env.locale); }
  function fmtTile(n) {                                   // the tiles are narrow: 123456 becomes 123K
    if (n < 100000) return fmt(n);
    try { return new Intl.NumberFormat(env.locale, { notation: 'compact', maximumFractionDigits: 1 }).format(n); } catch (e) { return fmt(n); }
  }
  function node(tag, cls, text) {
    var n = doc.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }
  function icon(id) {
    var s = L.svg('svg', { 'aria-hidden': 'true' });
    s.appendChild(L.svg('use', { href: '#' + id }));
    return s;
  }
  function hasDialog() { return !!doc.querySelector('dialog[open]'); }
  function active() { return started && !doc.hidden && inView && !hasDialog(); }

  // ------------------------------------------------------------------ app names
  function appInfo(id) {
    var a = m && m.apps && m.apps[id];
    return a || null;
  }
  function labelFor(ev) {
    if (ev.hiddenDomain) return { text: t('lvWebsite'), badge: '', color: '' };
    var app = ev.app && appInfo(ev.app);
    if (app) return { text: app.name, badge: app.mono, color: app.color };
    return { text: t('lvWebsite'), badge: '', color: '' };
  }

  // ------------------------------------------------------------------ the device row
  function kidName(k) { return k.name || t('lvDevice'); }

  /** Which nodes the device row shows for this model, and which kid / address belongs to which node. */
  function buildSlots() {
    var kids = m.kids, out = [], i;
    if (!kids.length) {
      out.push({ id: 'dev-0', kind: 'add', kids: [] });
    } else if (kids.length <= SLOT_MAX) {
      for (i = 0; i < kids.length; i++) out.push({ id: 'dev-' + i, kind: 'kid', kids: [kids[i]] });
    } else {
      for (i = 0; i < SLOT_MAX - 1; i++) out.push({ id: 'dev-' + i, kind: 'kid', kids: [kids[i]] });
      out.push({ id: 'dev-' + (SLOT_MAX - 1), kind: 'more', kids: kids.slice(SLOT_MAX - 1) });
    }
    out.push({ id: 'rest', kind: 'rest', kids: [] });
    return out;
  }

  function stateOf(k) {
    var recentIp = false, i;
    for (i = 0; i < k.ips.length; i++) if (ipSlot[k.ips[i]] && lastSeen[ipSlot[k.ips[i]]] && now() - lastSeen[ipSlot[k.ips[i]]] < 300000) recentIp = true;
    return C.deviceState({ shadowed: k.shadowed, lastQuery: k.lastQuery, serverNow: m.serverNow, recent: recentIp, offline: m.offline, paused: k.paused });
  }
  var WORST = ['overridden', 'unreachable', 'offline', 'paused', 'quiet', 'active'];
  function worst(states) {
    var best = 'active', i;
    for (i = 0; i < states.length; i++) if (WORST.indexOf(states[i]) < WORST.indexOf(best)) best = states[i];
    return best;
  }
  function slotState(s) {
    if (s.kind === 'rest' || s.kind === 'add') return null;
    return worst(s.kids.map(function (k) { return kidState[k.key]; }));
  }
  var STATE_TEXT = { active: 'lvActive', quiet: 'lvQuiet', paused: 'lvPaused', offline: 'lvOffline', overridden: 'lvOverridden', unreachable: 'lvUnreachable' };

  function renderSlots() {
    var host = el.devices, key = '', i, s, b;
    ipSlot = {};
    slots = buildSlots();
    slots.forEach(function (sl) { sl.kids.forEach(function (k) { k.ips.forEach(function (ip) { ipSlot[ip] = sl.id; }); }); });
    m.kids.forEach(function (k) { kidState[k.key] = stateOf(k); });
    for (i = 0; i < slots.length; i++) {
      s = slots[i];
      key += s.id + ':' + s.kind + ':' + s.kids.map(function (k) { return k.key; }).join(',') + '|';
    }
    key += (m.dir || '') + '|' + clientsHidden;
    var rebuild = key !== slotKey;
    host.setAttribute('data-count', String(slots.length));
    if (rebuild) {
      slotKey = key;
      host.textContent = '';
      for (i = 0; i < slots.length; i++) host.appendChild(buildNode(slots[i]));
    }
    for (i = 0; i < slots.length; i++) paintNode(slots[i], host.children[i]);
    if (rebuild && stage) stage.layout();
  }

  function buildNode(s) {
    var b = node('button', 'node node-dev node-' + s.kind);
    b.type = 'button';
    b.setAttribute('data-node', s.id);
    b.setAttribute('data-act', s.kind === 'add' ? 'openAdd' : 'node');
    b.setAttribute('data-which', s.id);
    var ico = node('span', 'node-ico');
    if (s.kind === 'more') ico.appendChild(node('span', 'node-more'));
    else ico.appendChild(icon(s.kind === 'rest' ? 'i-home' : s.kind === 'add' ? 'i-plus' : 'i-device'));
    b.appendChild(ico);
    b.appendChild(node('span', 'node-name'));
    var st = node('span', 'node-state');
    st.appendChild(node('i', 'sdot'));
    st.appendChild(node('span', 'node-state-text'));
    b.appendChild(st);
    return b;
  }

  function paintNode(s, b) {
    var name, state, st = slotState(s), cls = 'node node-dev node-' + s.kind;
    if (s.kind === 'kid') { name = kidName(s.kids[0]); state = t(STATE_TEXT[st]); cls += ' st-' + st; }
    else if (s.kind === 'more') { name = t('lvMore', { n: s.kids.length }); state = t(STATE_TEXT[st]); cls += ' st-' + st; b.querySelector('.node-more').textContent = '+' + s.kids.length; }
    else if (s.kind === 'add') { name = t('lvAddDevice'); state = t('lvAddHint'); cls += ' st-none'; }
    else { name = clientsHidden ? t('lvAllHome') : t('lvRest'); state = clientsHidden ? t('lvAllHomeSt') : t('lvRestSt'); cls += ' st-rest'; }
    if (tour.on && s.id === 'dev-0') cls += ' is-example';
    b.className = cls + (b.classList.contains('is-focus') ? ' is-focus' : '');
    b.querySelector('.node-name').textContent = name;
    b.querySelector('.node-state-text').textContent = state;
    b.setAttribute('aria-label', name + '. ' + state);
    b.setAttribute('dir', 'auto');
  }

  function wireDefs() {
    var defs = [{ id: 'w-rest', from: 'rest', to: 'box' }, { id: 'w-out', from: 'box', to: 'net' }], i;
    for (i = 0; i < SLOT_MAX; i++) defs.push({ id: 'w-dev-' + i, from: 'dev-' + i, to: 'box' });
    return defs;
  }
  function wireOf(slotId) { return slotId === 'rest' ? 'w-rest' : 'w-' + slotId; }

  function paintWires() {
    var n = now();
    slots.forEach(function (s) {
      var cls = '', st = slotState(s), id = wireOf(s.id);
      if (s.kind === 'add') cls = 'is-quiet';
      else if (s.kind === 'rest') cls = 'is-rest';
      else if (st === 'overridden' || st === 'unreachable') cls = 'is-warn';
      else if (st === 'paused' || st === 'offline') cls = 'is-off';
      else cls = st === 'quiet' ? 'is-quiet' : 'is-ok';
      if (wireUntil[id] > n) cls += ' is-active';
      stage.wireState(id, cls);
    });
    stage.wireState('w-out', wireUntil['w-out'] > n ? 'is-active' : '');
    var soonest = 0, k;
    for (k in wireUntil) if (wireUntil[k] > n && (!soonest || wireUntil[k] < soonest)) soonest = wireUntil[k];
    root.clearTimeout(wireTimer);
    if (soonest) wireTimer = root.setTimeout(paintWires, Math.max(100, soonest - n + 50));
  }
  function busy(id, ms) { wireUntil[id] = now() + ms; }

  // ------------------------------------------------------------------ the verdict (headline)
  function verdict() {
    var kids = m.kids, names, bad = [], i, st, apps = m.blockedApps.length;
    if (!kids.length) return { tone: 'none', title: t('lvNoKids'), sub: t('lvNoKidsSub') };
    if (m.timer && m.timerMode === 'free') return { tone: 'good', title: t('lvFree'), sub: t('lvFreeSub') };
    for (i = 0; i < kids.length; i++) {
      st = kidState[kids[i].key];
      if (st === 'overridden' || st === 'unreachable') bad.push({ k: kids[i], st: st });
    }
    if (bad.length) {
      if (bad.length === 1) return { tone: 'warn', title: t(bad[0].st === 'overridden' ? 'lvCheckOver' : 'lvCheckAway', { n: kidName(bad[0].k) }),
        sub: t(bad[0].st === 'overridden' ? 'lvCheckOverSub' : 'lvCheckAwaySub') };
      return { tone: 'warn', title: t('lvCheckMany', { n: bad.length }), sub: t('lvCheckManySub') };
    }
    if (m.offline) return { tone: 'off', title: t('lvOff'), sub: t('lvOffSub') };
    if (kids.every(function (k) { return k.paused; })) return { tone: 'off', title: kids.length === 1 ? t('lvPausedOne', { n: kidName(kids[0]) }) : t('lvPausedAll'), sub: t('lvPausedSub') };
    return {
      tone: 'good',
      title: kids.length === 1 ? t('lvWorkingOne', { n: kidName(kids[0]) }) : t('lvWorkingAll', { n: kids.length }),
      sub: apps ? t('lvBlockedApps', { n: apps }) : t('lvNoBlockedApps')
    };
  }
  function renderHead() {
    var v = verdict();
    el.title.textContent = v.title;
    el.sub.textContent = v.sub;
    el.icon.className = 'live-icon tone-' + v.tone;
    el.icon.firstChild.firstChild.setAttribute('href', '#' + (v.tone === 'warn' ? 'i-alert' : v.tone === 'off' ? 'i-moon-sm' : v.tone === 'none' ? 'i-info' : 'i-shield'));
    var pill = 'live', text = t('lvLive');
    if (queriesHidden) { pill = 'hidden'; text = t('lvTotalsOnly'); }
    else if (tour.on) { pill = 'example'; text = t('lvExample'); }
    else if (failing >= 2) { pill = 'stale'; text = t('lvStale'); }
    else if (!active()) { pill = 'paused'; text = t('lvPausedPill'); }
    el.pill.className = 'live-pill is-' + pill;
    el.pillText.textContent = text;
  }

  // ------------------------------------------------------------------ the box's voice
  function idleVoice() {
    if (m.offline) return t('lvBoxOff');
    if (m.blockedApps.length) return t('lvBoxBlocking', { n: m.blockedApps.length });
    return t('lvBoxOpen');
  }
  function say(text, tone, force) {
    var n = now();
    if (!force && n - voice.last < 1200) return;
    voice.last = n; voice.until = n + 2600;
    el.boxSub.textContent = text;
    el.box.setAttribute('data-tone', tone || '');
    root.clearTimeout(voice.timer);
    voice.timer = root.setTimeout(function () { el.boxSub.textContent = idleVoice(); el.box.setAttribute('data-tone', ''); }, 2600);
  }

  // ------------------------------------------------------------------ the numbers
  function setStat(id, key, to, format) {
    var n = el[id];
    var from = shown[key];
    shown[key] = to;
    L.tickNumber(n, from, to, 700, format);
  }
  function renderStats() {
    var total, blocked, share;
    if (!summary) {
      el.statChecked.textContent = '–'; el.statStopped.textContent = '–'; el.statShare.textContent = '–';
      return;
    }
    total = summary.total + delta.total;
    blocked = summary.blocked + delta.blocked;
    share = C.percent(blocked, total);
    setStat('statChecked', 'total', total, fmtTile);
    setStat('statStopped', 'blocked', blocked, fmtTile);
    setStat('statShare', 'share', share, function (n) { return n.toLocaleString(env.locale) + '%'; });
  }
  function renderStaticText() {
    el.statCheckedLabel.textContent = t('lvChecked');
    el.statStoppedLabel.textContent = t('lvStopped');
    el.statShareLabel.textContent = t('lvShare');
    el.scope.textContent = t('lvScope');
    el.note.textContent = queriesHidden ? t('lvPrivacy') : t('lvNote');
    el.recentTitle.textContent = t('lvRecentTitle');
    el.tourBtn.textContent = t('lvTour');
    el.detailBtn.textContent = t('lvDetail');
    el.boxName.textContent = t('lvBox');
    el.netName.textContent = t('lvNet');
    el.nodeNet.setAttribute('aria-label', t('lvNet'));
    el.tourBack.textContent = t('lvBack');
    el.tourEnd.textContent = t('lvEndTour');
  }

  // ------------------------------------------------------------------ caption (what the picture means, or what was tapped)
  function defaultCaption() { return queriesHidden ? t('lvCaptionHidden') : t('lvCaption'); }
  function setCaption(text, hold) {
    el.caption.textContent = text;
    captionHeld = !!hold;
    root.clearTimeout(captionTimer);
    if (hold) captionTimer = root.setTimeout(function () { captionHeld = false; el.caption.textContent = tour.on ? '' : defaultCaption(); }, hold);
  }
  function explain(which) {
    var text;
    if (which === 'box') text = t('lvExBox');
    else if (which === 'net') text = t('lvExNet');
    else if (which === 'rest') text = clientsHidden ? t('lvExAllHome') : t('lvExRest');
    else {
      var s = slots.filter(function (x) { return x.id === which; })[0];
      if (!s) return;
      if (s.kind === 'more') { text = t('lvExMore', { n: s.kids.length }); if (env.goDevices) env.goDevices(); }
      else text = t('lvEx_' + slotState(s), { n: kidName(s.kids[0]) });
    }
    setCaption(text, 14000);
  }
  function explainStat(which) {
    setCaption(t(which === 'checked' ? 'lvExChecked' : which === 'stopped' ? 'lvExStopped' : 'lvExShare'), 16000);
  }

  // ------------------------------------------------------------------ recent events (text twin of the animation)
  function pushRecent(ev, label, who) {
    var kind = ev.kind === 'blocked' ? 'stop' : 'go';
    var key = kind + '|' + label + '|' + who, top = recent[0];
    if (top && top.key === key) { top.n++; top.at = now(); }
    else { recent.unshift({ key: key, kind: kind, label: label, who: who, at: now(), n: 1 }); if (recent.length > 5) recent.pop(); }
  }
  function ago(ms) {
    var s = Math.max(0, Math.round((now() - ms) / 1000));
    if (s < 10) return t('lvNow');
    if (s < 60) return t('lvSecs', { n: s });
    return t('lvMins', { n: Math.round(s / 60) });
  }
  function renderRecent() {
    var ul = el.recentList, i, r, li, line;
    ul.textContent = '';
    if (queriesHidden || domainsHidden) { ul.appendChild(node('li', 'recent-empty', t(queriesHidden ? 'lvPrivacyShort' : 'lvPrivacyNames'))); return; }
    if (!recent.length) { ul.appendChild(node('li', 'recent-empty', t('lvRecentEmpty'))); return; }
    for (i = 0; i < recent.length; i++) {
      r = recent[i];
      li = node('li', 'recent-item is-' + r.kind);
      li.appendChild(icon(r.kind === 'stop' ? 'i-stop' : 'i-play'));
      line = node('span', 'recent-text');
      line.setAttribute('dir', 'auto');
      line.textContent = t(r.kind === 'stop' ? 'lvRecStop' : 'lvRecGo', { a: r.label }) + (r.who ? ' · ' + r.who : '') + (r.n > 1 ? ' ×' + r.n : '');
      li.appendChild(line);
      li.appendChild(node('span', 'recent-when', ago(r.at)));
      ul.appendChild(li);
    }
  }
  function scheduleRepaint() {                // device states ("online now") follow the real activity, a few times a minute at most
    if (repaintTimer || !m) return;
    repaintTimer = root.setTimeout(function () { repaintTimer = 0; if (!started || !m) return; renderSlots(); renderHead(); paintWires(); }, 4000);
  }
  function scheduleRecent() {
    if (recentTimer) return;
    recentTimer = root.setTimeout(function () { recentTimer = 0; renderRecent(); }, 1500);
  }

  // ------------------------------------------------------------------ screen readers: one calm sentence, not one per packet
  function ariaSummary() {
    var cut = now() - 60000, c = 0, b = 0, i;
    minute = minute.filter(function (x) { return x.at >= cut; });
    for (i = 0; i < minute.length; i++) { c++; if (minute[i].blocked) b++; }
    var text = queriesHidden ? '' : t('lvAria', { c: fmt(c), b: fmt(b) });
    if (text !== lastAria) { lastAria = text; el.aria.textContent = text; }
  }

  // ------------------------------------------------------------------ packets
  function slotFor(ev) {
    if (ev.hiddenClient) return 'rest';
    return ipSlot[ev.ip] || 'rest';
  }
  function whoFor(slotId) {
    var s = slots.filter(function (x) { return x.id === slotId; })[0];
    if (!s || s.kind === 'rest' || s.kind === 'add') return '';
    if (s.kind === 'more') return t('lvMore', { n: s.kids.length });
    return kidName(s.kids[0]);
  }

  function animate(ev) {
    var slotId = ev.slot, w = wireOf(slotId), lab = labelFor(ev), who = whoFor(slotId), route, tone;
    if (!stage.wires[w] || !stage.wires[w].geom) return;
    var spoken = (who ? who + ' · ' : '') + lab.text;
    busy(w, 5000);
    if (ev.kind === 'blocked') {
      tone = 'is-blocked';
      route = [{ wire: w, ms: 650 }, { hold: 180 }, { wire: w, back: true, ms: 560 }];
    } else if (ev.kind === 'memory') {
      tone = 'is-memory';
      route = [{ wire: w, ms: 650 }, { hold: 160 }, { wire: w, back: true, ms: 520 }];
    } else {
      tone = 'is-allowed';
      busy('w-out', 5000);
      route = [{ wire: w, ms: 600 }, { hold: 140 }, { wire: 'w-out', ms: 620 }, { hold: 160 }, { wire: 'w-out', back: true, ms: 620 }, { wire: w, back: true, ms: 520 }];
    }
    paintWires();
    stage.send({
      label: lab.text, badge: lab.badge, color: lab.color, tone: tone, route: route,
      onStep: function (i, api) {
        if (i === 0) {                                   // arrived at the box
          stage.pulse('box', ev.kind === 'blocked' ? 'is-refusing' : 'is-checking', 650);
          say(t(ev.kind === 'blocked' ? 'lvSayNo' : 'lvSayYes', { a: spoken }), ev.kind === 'blocked' ? 'no' : 'yes');
        }
        if (ev.kind === 'allowed' && i === 2) stage.pulse('net', 'is-reached', 600);
      }
    });
  }

  /** Hands out queued events at a gentle pace. */
  function pump() {
    pumpTimer = 0;
    if (!active() || tour.on) { pacer.queue.length = 0; return; }
    var ev = pacer.next(now());
    if (ev) animate(ev);
    if (pacer.queue.length) pumpTimer = root.setTimeout(pump, Math.max(60, pacer.gap - (now() - pacer.last) + 20));
  }
  function schedulePump() {
    if (pumpTimer || !pacer.queue.length) return;
    pumpTimer = root.setTimeout(pump, Math.max(0, pacer.gap - (now() - pacer.last)));
  }

  // ------------------------------------------------------------------ data
  function pollSummary() {
    var sentAt = now();
    return env.call('GET', '/api/stats/summary').then(function (j) {
      var q = (j && j.queries) || {};
      summary = { total: q.total || 0, blocked: q.blocked || 0, cached: q.cached || 0, forwarded: q.forwarded || 0, unique: q.unique_domains || 0,
        listSize: ((j.gravity || {}).domains_being_blocked) || 0, listAt: ((j.gravity || {}).last_update) || 0, clients: ((j.clients || {}).active) || 0 };
      delta = { total: 0, blocked: 0 };                       // the real numbers include everything we counted ourselves
      failing = 0;
      renderStats(); renderHead();
      if (detailOpen) renderDetailNumbers();
    }, function (e) { failing++; renderHead(); throw e; });
  }

  function pollQueries() {
    return env.call('GET', feed.url()).then(function (j) {
      var evs = feed.ingest(j), i, ev, wasQ = queriesHidden;
      failing = 0;
      queriesHidden = feed.privacy;
      if (queriesHidden !== wasQ) { renderStaticText(); setCaption(defaultCaption()); renderRecent(); renderHead(); }
      var fresh = [];
      for (i = 0; i < evs.length; i++) {
        ev = evs[i];
        if (ev.hiddenClient && !clientsHidden) { clientsHidden = true; renderSlots(); }
        if (ev.hiddenDomain) domainsHidden = true;
        ev.app = ev.hiddenDomain ? null : C.appFor(ev.domain, env.domains);
        ev.slot = slotFor(ev);
        ev.child = ev.slot !== 'rest';
        lastSeen[ev.slot] = now();
        delta.total++; if (ev.kind === 'blocked') delta.blocked++;
        minute.push({ at: now(), blocked: ev.kind === 'blocked' });
        var lab = labelFor(ev);
        if (ev.kind === 'blocked' || ev.app) pushRecent(ev, lab.text, whoFor(ev.slot));
        fresh.push(ev);
      }
      if (fresh.length) {
        if (!tour.on && active()) { pacer.push(fresh); schedulePump(); }
        renderStats(); scheduleRecent(); scheduleRepaint();
      }
    }, function (e) { failing++; renderHead(); throw e; });
  }

  function startPolling() {
    if (poller) return;
    poller = new C.Poller({
      tasks: [
        { name: 'summary', every: 15000, run: pollSummary },
        { name: 'queries', every: 3500, run: pollQueries }
      ],
      visible: active
    });
    poller.start();
  }

  // ------------------------------------------------------------------ tour: examples that never touch a number
  var TOUR = ['lvTour1', 'lvTour2', 'lvTour3', 'lvTour4', 'lvTour5'];
  function focusNodes(ids) {
    Array.prototype.forEach.call(el.stage.querySelectorAll('.node'), function (n) {
      n.classList.toggle('is-focus', ids.indexOf(n.getAttribute('data-node')) >= 0);
    });
  }
  function example(kind) {
    var w = 'w-dev-0', route, tone, label = t('lvExampleApp');
    if (!stage.wires[w] || !stage.wires[w].geom) w = 'w-rest';
    busy(w, 5000);
    if (kind === 'blocked') { tone = 'is-blocked is-example'; route = [{ wire: w, ms: 800 }, { hold: 220 }, { wire: w, back: true, ms: 700 }]; }
    else if (kind === 'allowed') { tone = 'is-allowed is-example'; busy('w-out', 5000);
      route = [{ wire: w, ms: 750 }, { hold: 200 }, { wire: 'w-out', ms: 800 }, { hold: 200 }, { wire: 'w-out', back: true, ms: 800 }, { wire: w, back: true, ms: 650 }]; }
    else { tone = 'is-example'; route = [{ wire: w, ms: 800 }, { hold: 400 }, { wire: w, back: true, ms: 700 }]; }
    paintWires();
    stage.send({ label: label, tone: tone, route: route, onStep: function (i) {
      if (i === 0) { stage.pulse('box', kind === 'blocked' ? 'is-refusing' : 'is-checking', 700); say(t(kind === 'blocked' ? 'lvSayNoEx' : 'lvSayYesEx'), kind === 'blocked' ? 'no' : 'yes', true); }
      if (kind === 'allowed' && i === 2) stage.pulse('net', 'is-reached', 600);
    } });
  }
  function tourStep(i) {
    root.clearTimeout(tour.timer);
    tour.i = Math.max(0, Math.min(TOUR.length - 1, i));
    stage.clear();
    el.tourStep.textContent = t(TOUR[tour.i]);
    el.tourCount.textContent = t('lvStep', { i: tour.i + 1, n: TOUR.length });
    el.tourBack.hidden = tour.i === 0;
    el.tourNext.textContent = t(tour.i === TOUR.length - 1 ? 'lvDone' : 'lvNext');
    var kinds = [null, 'plain', 'allowed', 'blocked', null];
    var foci = [['dev-0', 'rest'], ['dev-0', 'box'], ['dev-0', 'box', 'net'], ['dev-0', 'box'], ['box']];
    focusNodes(foci[tour.i]);
    var run = function () { if (kinds[tour.i] && tour.on && active()) example(kinds[tour.i]); };
    run();
    if (kinds[tour.i]) tour.timer = root.setTimeout(function again() { run(); tour.timer = root.setTimeout(again, 5200); }, 5200);
  }
  function startTour() {
    if (tour.on) return;
    tour.on = true;
    pacer.queue.length = 0;
    el.live.classList.add('is-touring');
    el.tour.hidden = false;
    el.caption.textContent = '';
    renderSlots(); renderHead();
    tourStep(0);
    var first = el.tour.querySelector('#tourNext'); if (first) first.focus();
  }
  function endTour() {
    if (!tour.on) return;
    tour.on = false;
    root.clearTimeout(tour.timer);
    stage.clear();
    focusNodes([]);
    el.live.classList.remove('is-touring');
    el.tour.hidden = true;
    setCaption(defaultCaption());
    el.boxSub.textContent = idleVoice(); el.box.setAttribute('data-tone', '');
    renderSlots(); renderHead(); paintWires();
    var b = el.tourBtn; if (b) b.focus();
  }

  // ------------------------------------------------------------------ detail sheet
  function fmtAgoLong(epoch) {
    if (!epoch) return '';
    var diff = Math.round((epoch - m.serverNow) / 60);
    try {
      var rtf = new Intl.RelativeTimeFormat(env.locale, { numeric: 'auto' });
      if (diff > -60) return rtf.format(diff, 'minute');
      if (diff > -1440) return rtf.format(Math.round(diff / 60), 'hour');
      return rtf.format(Math.round(diff / 1440), 'day');
    } catch (e) { return ''; }
  }
  function section(title) {
    var s = node('section', 'sheet-sec');
    s.appendChild(node('h3', '', title));
    return s;
  }
  function openDetail() {
    var body = el.sheetBody, sec, ol, i, ul;
    detailOpen = true;
    el.sheetTitle.textContent = t('lvDetailTitle');
    body.textContent = '';

    sec = section(t('lvHowTitle'));
    ol = node('ol', 'how');
    ['lvHow1', 'lvHow2', 'lvHow3', 'lvHow4'].forEach(function (k) {
      var li = node('li', 'how-item');
      li.appendChild(node('strong', '', t(k + 'T')));
      li.appendChild(node('span', '', t(k)));
      ol.appendChild(li);
    });
    sec.appendChild(ol);
    body.appendChild(sec);

    sec = section(t('lvNumbersTitle'));
    sec.appendChild(node('p', 'muted small', t('lvScope')));
    ul = node('ul', 'facts'); ul.id = 'detailFacts';
    sec.appendChild(ul);
    var hoursTitle = node('h4', '', t('lvHours'));
    sec.appendChild(hoursTitle);
    var bars = node('div', 'hours'); bars.id = 'detailHours'; bars.setAttribute('role', 'img'); bars.setAttribute('aria-label', t('lvHours'));
    sec.appendChild(bars);
    var topTitle = node('h4', '', t('lvTopTitle'));
    sec.appendChild(topTitle);
    var top = node('ul', 'toplist'); top.id = 'detailTop';
    sec.appendChild(top);
    body.appendChild(sec);

    sec = section(t('lvCannotTitle'));
    ul = node('ul', 'cannot');
    ['lvCannot1', 'lvCannot2', 'lvCannot3', 'lvCannot4'].forEach(function (k) { ul.appendChild(node('li', '', t(k))); });
    sec.appendChild(ul);
    body.appendChild(sec);

    sec = section(t('lvWordsTitle'));
    var dl = node('dl', 'words');
    ['lvWord1', 'lvWord2', 'lvWord3', 'lvWord4', 'lvWord5'].forEach(function (k) {
      dl.appendChild(node('dt', '', t(k + 'T')));
      dl.appendChild(node('dd', '', t(k)));
    });
    sec.appendChild(dl);
    body.appendChild(sec);

    renderDetailNumbers();
    detail = { loading: true };
    Promise.all([
      env.call('GET', '/api/history').catch(function () { return null; }),
      env.call('GET', '/api/stats/top_domains?blocked=true&count=60').catch(function () { return null; })
    ]).then(function (r) {
      if (!detailOpen) return;
      detail = { hours: r[0] ? C.hourly(r[0]) : null, top: r[1] ? C.topByApp(r[1].domains || [], env.domains, 5) : null };
      renderDetailNumbers();
    });
    env.openSheet();
  }
  function closeDetail() { detailOpen = false; detail = null; }

  function renderDetailNumbers() {
    var facts = $('detailFacts'), hours = $('detailHours'), top = $('detailTop');
    if (!facts) return;
    facts.textContent = '';
    function fact(label, value) {
      var li = node('li', 'fact');
      li.appendChild(node('span', 'fact-value', value));
      li.appendChild(node('span', 'fact-label', label));
      facts.appendChild(li);
    }
    if (summary) {
      var total = summary.total || 0;
      fact(t('lvFactMemory'), total ? C.percent(summary.cached, total) + '%' : '–');
      fact(t('lvFactPlaces'), fmt(summary.unique));
      fact(t('lvFactList'), fmt(summary.listSize));
      fact(t('lvFactDevices'), fmt(summary.clients));
    } else fact(t('lvLoading'), '–');
    if (summary && summary.listAt) {
      var ago2 = fmtAgoLong(summary.listAt);
      if (ago2) { var u = node('li', 'fact fact-wide'); u.appendChild(node('span', 'fact-label', t('lvFactListAt', { t: ago2 }))); facts.appendChild(u); }
    }
    hours.textContent = ''; top.textContent = '';
    if (!detail || detail.loading) { hours.appendChild(node('span', 'muted small', t('lvLoading'))); return; }
    var hs = detail.hours, max = 1, i, b, col;
    if (hs) {
      for (i = 0; i < hs.length; i++) max = Math.max(max, hs[i].total);
      for (i = 0; i < hs.length; i++) {
        col = node('span', 'hour');
        b = node('span', 'hour-total'); b.style.height = Math.round(100 * hs[i].total / max) + '%';
        var bb = node('span', 'hour-blocked'); bb.style.height = hs[i].total ? Math.round(100 * hs[i].blocked / hs[i].total) + '%' : '0%';
        b.appendChild(bb); col.appendChild(b); hours.appendChild(col);
      }
    } else hours.appendChild(node('span', 'muted small', t('lvUnavailable')));
    if (detail.top && detail.top.length) {
      var peak = detail.top[0].count || 1;
      detail.top.forEach(function (r) {
        var a = r.app && appInfo(r.app), li = node('li', 'top-item');
        var badge = node('span', 'top-badge' + (a ? '' : ' is-other'), a ? a.mono : '·');
        if (a && a.color) badge.style.setProperty('--brand', a.color);
        li.appendChild(badge);
        var mid = node('span', 'top-mid');
        mid.appendChild(node('span', 'top-name', a ? a.name : t('lvOther')));
        var bar = node('span', 'top-bar'); var fill = node('span', 'top-fill'); fill.style.width = Math.max(4, Math.round(100 * r.count / peak)) + '%'; bar.appendChild(fill);
        mid.appendChild(bar);
        li.appendChild(mid);
        li.appendChild(node('span', 'top-count', fmt(r.count)));
        top.appendChild(li);
      });
    } else if (detail.top) top.appendChild(node('li', 'muted small', t('lvTopEmpty')));
    else top.appendChild(node('li', 'muted small', t('lvUnavailable')));
  }

  // ------------------------------------------------------------------ public
  function onClick(ev) {
    var n = ev.target.closest ? ev.target.closest('#live [data-act]') : null;
    if (!n) return false;
    var act = n.getAttribute('data-act');
    if (act === 'node') explain(n.getAttribute('data-which'));
    else if (act === 'stat') explainStat(n.getAttribute('data-which'));
    else if (act === 'tour') startTour();
    else if (act === 'tourNext') { if (tour.i >= TOUR.length - 1) endTour(); else tourStep(tour.i + 1); }
    else if (act === 'tourBack') tourStep(tour.i - 1);
    else if (act === 'tourEnd') endTour();
    else if (act === 'detail') openDetail();
    else return false;
    return true;
  }

  function cache() {
    ['live', 'liveIcon', 'liveTitle', 'liveSub', 'livePill', 'livePillText', 'stage', 'stageDevices', 'nodeBox', 'boxName', 'boxSub', 'nodeNet', 'netName',
      'liveCaption', 'liveTour', 'tourStep', 'tourCount', 'tourBack', 'tourNext', 'tourEnd', 'statChecked', 'statCheckedLabel', 'statStopped', 'statStoppedLabel',
      'statShare', 'statShareLabel', 'liveScope', 'liveNote', 'liveRecent', 'liveRecentTitle', 'liveRecentList', 'liveAria', 'tourBtn', 'detailBtn',
      'sheetTitle', 'sheetBody', 'liveSheet'].forEach(function (id) { el[id] = $(id); });
    el.devices = el.stageDevices; el.title = el.liveTitle; el.sub = el.liveSub; el.icon = el.liveIcon; el.pill = el.livePill; el.pillText = el.livePillText;
    el.box = el.nodeBox; el.caption = el.liveCaption; el.tour = el.liveTour; el.scope = el.liveScope; el.note = el.liveNote;
    el.recentTitle = el.liveRecentTitle; el.recentList = el.liveRecentList; el.aria = el.liveAria;
  }

  function wake() { if (poller) poller.wake(); }
  function onGate() {                       // page hidden, a dialog opened or closed, the card scrolled away
    if (!started) return;
    if (!active()) { stage.clear(); pacer.queue.length = 0; if (tour.on) root.clearTimeout(tour.timer); }
    else { wake(); if (tour.on) tourStep(tour.i); }
    renderHead();
  }

  /**
   * init({ call, t, locale, domains, apps, openSheet, goDevices }): `call(method, path)` is the page's signed-in API call;
   * `t(key, vars)` its translator; `apps` maps an app id to { name, mono, color }; `domains` is pb/domains.json's "domains".
   */
  function init(options) {
    if (started) return;
    env = options;
    cache();
    if (!el.live || !el.stage) return;
    feed = new C.Feed({ length: 60 });
    pacer = new C.Pacer({ gap: 380, max: 10 });
    stage = new L.Stage(el.stage, { wires: wireDefs(), poolSize: 8, onLayout: function () { paintWires(); } });
    started = true;
    el.live.hidden = false;
    if (!wired) {                              // the page may sign out and in again: listen only once
      wired = true;
      doc.addEventListener('click', function (ev) { if (started && el.live.contains(ev.target)) onClick(ev); });
      doc.addEventListener('visibilitychange', onGate);
      doc.addEventListener('close', function (ev) { if (ev.target && ev.target.id === 'liveSheet') closeDetail(); onGate(); }, true);
    }
    if (root.IntersectionObserver) {
      io = new root.IntersectionObserver(function (entries) { inView = entries[entries.length - 1].isIntersecting; onGate(); }, { threshold: 0.05 });
      io.observe(el.live);
    }
    ariaTimer = root.setInterval(function () { if (active()) ariaSummary(); }, 30000);
    recentTick = root.setInterval(function () { if (active() && el.liveRecent.open) renderRecent(); }, 10000);
    if (L.reducedMotion()) el.liveRecent.open = true;           // no movement: the list is the picture's text twin
  }

  /** The page hands over its view-model after every render (and on language change). Cheap: rebuilds nodes only when they change. */
  function update(model) {
    if (!started) return;
    m = model;
    if (model.locale) env.locale = model.locale;
    renderSlots();
    renderStaticText();
    renderHead();
    if (!tour.on) { if (voice.until <= now()) el.boxSub.textContent = idleVoice(); if (!captionHeld) el.caption.textContent = defaultCaption(); }
    renderRecent();
    renderStats();
    paintWires();
    stage.layout();
    if (tour.on) { el.tourStep.textContent = t(TOUR[tour.i]); el.tourCount.textContent = t('lvStep', { i: tour.i + 1, n: TOUR.length }); }
    startPolling();
  }
  function stop() {
    if (!started) return;
    if (poller) { poller.stop(); poller = null; }
    if (stage) stage.clear();
    root.clearTimeout(pumpTimer); pumpTimer = 0;
    if (tour.on) endTour();
    started = false;
    el.live.hidden = true;
    if (io) { io.disconnect(); io = null; }
    root.clearInterval(ariaTimer); root.clearInterval(recentTick);
    root.clearTimeout(repaintTimer); repaintTimer = 0; root.clearTimeout(recentTimer); recentTimer = 0;
    if (stage) { stage.destroy(); stage = null; }
    summary = null; recent = []; lastSeen = {}; slotKey = ''; wireUntil = {}; queriesHidden = false; clientsHidden = false; domainsHidden = false;
    shown = { total: 0, blocked: 0, share: 0 }; delta = { total: 0, blocked: 0 }; failing = 0; minute = []; lastAria = '';
  }

  root.PBPicture = { init: init, update: update, stop: stop, wake: wake };
})(window);
