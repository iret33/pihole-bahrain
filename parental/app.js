'use strict';
/* Parental Controls for Pi-hole — client-side, authenticates with the admin
 * password (same password as the dashboard) and drives the local /api. */
(function () {
  var DEFAULT_GROUP = 0, KIDS_GROUP = 1;
  var LOCKED_GROUP_NAME = 'Paused', LOCK_REGEX = '.+', KILL_REGEX = '.*';
  var SID_KEY = 'pihole_bahrain_sid';

  var SERVICE_META = {
    'YouTube': ['\u25B6\uFE0F', 'YouTube'], 'TikTok': ['\uD83C\uDFB5', 'TikTok'],
    'Instagram': ['\uD83D\uDCF8', 'Instagram'], 'Snapchat': ['\uD83D\uDC7B', 'Snapchat'],
    'Facebook': ['\uD83D\uDCD8', 'Facebook'], 'X (Twitter)': ['\uD83D\uDC26', 'X / Twitter'],
    'Netflix': ['\uD83C\uDFAC', 'Netflix'], 'Roblox': ['\uD83E\uDDF1', 'Roblox'],
    'Steam': ['\uD83C\uDFAE', 'Steam'], 'PlayStation': ['\uD83D\uDD79\uFE0F', 'PlayStation'],
    'Xbox': ['\u274E', 'Xbox'], 'WhatsApp': ['\uD83D\uDFE2', 'WhatsApp'],
    'Telegram': ['\u2708\uFE0F', 'Telegram'], 'Discord': ['\uD83D\uDDE3\uFE0F', 'Discord'],
    'Microsoft Teams': ['\uD83D\uDCAC', 'Teams'], 'ChatGPT': ['\uD83E\uDD16', 'ChatGPT']
  };
  var CATEGORIES = [
    ['Social', ['Instagram', 'Snapchat', 'Facebook', 'X (Twitter)', 'TikTok']],
    ['Video', ['YouTube', 'Netflix']],
    ['Games', ['Roblox', 'Steam', 'PlayStation', 'Xbox']],
    ['Messaging', ['WhatsApp', 'Telegram', 'Discord', 'Microsoft Teams']],
    ['AI', ['ChatGPT']]
  ];
  var HOMEWORK_BLOCK = ['Instagram', 'Snapchat', 'Facebook', 'X (Twitter)', 'TikTok',
    'YouTube', 'Netflix', 'Roblox', 'Steam', 'PlayStation', 'Xbox', 'Discord'];

  var SID = sessionStorage.getItem(SID_KEY) || '';
  var S = null, BUSY = false;

  function $(id) { return document.getElementById(id); }
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function toast(m) { var t = $('toast'); t.textContent = m; t.classList.add('show');
    clearTimeout(t._h); t._h = setTimeout(function () { t.classList.remove('show'); }, 2800); }
  function enc(s) { return encodeURIComponent(s); }

  function headers(body) {
    var h = {};
    if (body !== undefined) h['Content-Type'] = 'application/json';
    if (SID) h['sid'] = SID;
    return h;
  }
  function errmsg(j) {
    if (!j) return 'unknown error';
    if (j.error) { var e = j.error; return typeof e === 'string' ? e : (e.message || e.hint || JSON.stringify(e)); }
    return JSON.stringify(j).slice(0, 120);
  }
  function rawFetch(path, opts) {
    return fetch(path, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) { return { r: r, j: j }; });
    });
  }
  function api(method, path, body) {
    return rawFetch(path, { method: method, headers: headers(body), body: body !== undefined ? JSON.stringify(body) : undefined })
      .then(function (x) {
        if (x.r.status === 401 || x.r.status === 403) { SID = ''; sessionStorage.removeItem(SID_KEY); showLogin(); throw new Error('Please sign in'); }
        if (!x.r.ok) throw new Error(errmsg(x.j));
        return x.j;
      });
  }
  function tryLogin(pw) {
    return rawFetch('/api/auth', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password: pw }) })
      .then(function (x) {
        if (x.r.ok && x.j.session && x.j.session.sid) { SID = x.j.session.sid; sessionStorage.setItem(SID_KEY, SID); return true; }
        return false;
      });
  }
  function showLogin() { $('login').classList.remove('hidden'); $('app').classList.add('hidden'); }
  function showApp() { $('login').classList.add('hidden'); $('app').classList.remove('hidden'); }

  function restart() {
    return api('POST', '/api/action/restartdns').catch(function () { /* FTL may drop mid-restart */ })
      .then(function () {
        var i = 0;
        function wait() {
          return new Promise(function (res) { setTimeout(res, 600); }).then(function () {
            return rawFetch('/api/info/version', { headers: headers() }).then(function (x) {
              if (x.r.ok) return true; if (++i < 50) return wait(); return true;
            }).catch(function () { if (++i < 50) return wait(); return true; });
          });
        }
        return wait();
      });
  }

  // ---- state ----
  function loadState() {
    return Promise.all([
      api('GET', '/api/lists'), api('GET', '/api/clients'), api('GET', '/api/groups'),
      api('GET', '/api/domains?length=10000'),
      api('GET', '/api/info/version').catch(function () { return {}; })
    ]).then(function (r) {
      var lists = r[0], clients = r[1], groups = r[2], domains = r[3], version = r[4];
      var svcs = [];
      (lists.lists || []).forEach(function (l) {
        if ((l.groups || []).indexOf(KIDS_GROUP) >= 0) {
          svcs.push({ comment: l.comment || l.address, address: l.address, enabled: !!l.enabled, number: l.number || 0 });
        }
      });
      var order = Object.keys(SERVICE_META);
      svcs.sort(function (a, b) {
        var oa = order.indexOf(a.comment), ob = order.indexOf(b.comment);
        return (oa < 0 ? 999 : oa) - (ob < 0 ? 999 : ob);
      });
      var lockedGid = null;
      (groups.groups || []).forEach(function (g) { if (g.name === LOCKED_GROUP_NAME) lockedGid = g.id; });
      var kids = [];
      (clients.clients || []).forEach(function (c) {
        if ((c.groups || []).indexOf(KIDS_GROUP) >= 0) {
          kids.push({ client: c.client, comment: c.comment || '', locked: lockedGid != null && (c.groups || []).indexOf(lockedGid) >= 0 });
        }
      });
      var kill = { exists: false, enabled: false, groups: [DEFAULT_GROUP] };
      (domains.domains || []).forEach(function (d) {
        if (d.type === 'deny' && d.kind === 'regex' && d.domain === KILL_REGEX) {
          kill = { exists: true, enabled: !!d.enabled, groups: d.groups || [DEFAULT_GROUP] };
        }
      });
      var core = ((version.version || {}).core || {}).local || {};
      S = { services: svcs, kids: kids, kill: kill, cats: CATEGORIES, meta: SERVICE_META, version: core.version || '' };
      render();
    });
  }

  // ---- mutations ----
  function setList(comment, enabled) {
    var s = null;
    S.services.forEach(function (x) { if (x.comment === comment) s = x; });
    if (!s) return Promise.reject(new Error('service not found: ' + comment));
    return api('PUT', '/api/lists/' + enc(s.address) + '?type=block',
      { address: s.address, type: 'block', comment: comment, groups: [KIDS_GROUP], enabled: !!enabled });
  }
  function setKill(enabled) {
    if (S.kill.exists) {
      return api('PUT', '/api/domains/deny/regex/' + enc(KILL_REGEX) + '?type=deny&kind=regex',
        { domain: KILL_REGEX, type: 'deny', kind: 'regex', groups: S.kill.groups, enabled: !!enabled });
    }
    return api('POST', '/api/domains/deny/regex',
      { domain: KILL_REGEX, kind: 'regex', groups: [DEFAULT_GROUP], enabled: !!enabled });
  }
  function apply(changes, killVal) {
    var p = Promise.resolve();
    changes.forEach(function (c) { p = p.then(function () { return setList(c[0], c[1]); }); });
    if (killVal !== undefined) p = p.then(function () { return setKill(killVal); });
    return p.then(function () { return restart(); });
  }
  function ensureLockGroup() {
    return api('GET', '/api/groups').then(function (g) {
      var found = null; (g.groups || []).forEach(function (x) { if (x.name === LOCKED_GROUP_NAME) found = x; });
      if (found) return found.id;
      return api('POST', '/api/groups', { name: LOCKED_GROUP_NAME, comment: 'Per-device pause', enabled: true })
        .then(function (j) {
          var nf = null; (j.groups || []).forEach(function (x) { if (x.name === LOCKED_GROUP_NAME) nf = x; });
          return nf ? nf.id : api('GET', '/api/groups').then(function (g2) {
            var f2 = null; (g2.groups || []).forEach(function (x) { if (x.name === LOCKED_GROUP_NAME) f2 = x; });
            return f2.id;
          });
        });
    }).then(function (gid) {
      return api('GET', '/api/domains?length=10000').then(function (d) {
        var lock = null; (d.domains || []).forEach(function (x) { if (x.kind === 'regex' && x.domain === LOCK_REGEX) lock = x; });
        if (!lock) return api('POST', '/api/domains/deny/regex', { domain: LOCK_REGEX, kind: 'regex', groups: [gid], enabled: true });
        return api('PUT', '/api/domains/deny/regex/' + enc(LOCK_REGEX) + '?type=deny&kind=regex',
          { domain: LOCK_REGEX, type: 'deny', kind: 'regex', groups: [gid], enabled: true });
      }).then(function () { return gid; });
    });
  }
  function setLock(client, locked) {
    return api('GET', '/api/clients').then(function (d) {
      var row = null; (d.clients || []).forEach(function (c) { if (c.client === client) row = c; });
      if (!row) throw new Error('device not found');
      var groups = (row.groups || []).slice();
      var p = Promise.resolve();
      if (locked) p = ensureLockGroup().then(function (gid) { if (groups.indexOf(gid) < 0) groups.push(gid); });
      else {
        p = api('GET', '/api/groups').then(function (g) {
          var gid = null; (g.groups || []).forEach(function (x) { if (x.name === LOCKED_GROUP_NAME) gid = x.id; });
          if (gid != null) groups = groups.filter(function (x) { return x !== gid; });
        });
      }
      return p.then(function () {
        if (!groups.length) groups = [DEFAULT_GROUP];
        return api('PUT', '/api/clients/' + enc(client), { client: client, comment: row.comment || '', groups: groups });
      }).then(function () { return restart(); });
    });
  }
  function addKid(client, comment) {
    client = (client || '').trim();
    if (!/^[0-9a-fA-F:.]+$/.test(client)) throw new Error('not a valid IP address');
    return api('GET', '/api/clients').then(function (d) {
      var row = null; (d.clients || []).forEach(function (c) { if (c.client === client) row = c; });
      var groups = [DEFAULT_GROUP, KIDS_GROUP].concat(row ? (row.groups || []) : []);
      groups = groups.filter(function (v, i, a) { return a.indexOf(v) === i; });
      var body = { client: client, comment: (comment || '').trim() || (row && row.comment) || 'Kid device', groups: groups };
      if (row) return api('PUT', '/api/clients/' + enc(client), body);
      return api('POST', '/api/clients', body);
    }).then(function () { return restart(); });
  }
  function removeKid(client) {
    return api('GET', '/api/clients').then(function (d) {
      var row = null; (d.clients || []).forEach(function (c) { if (c.client === client) row = c; });
      if (!row) throw new Error('device not found');
      var groups = (row.groups || []).filter(function (g) { return g !== KIDS_GROUP; });
      if (!groups.length) groups = [DEFAULT_GROUP];
      return api('PUT', '/api/clients/' + enc(client), { client: client, comment: row.comment || '', groups: groups });
    }).then(function () { return restart(); });
  }
  function preset(name) {
    if (name === 'homework') {
      var ch = Object.keys(SERVICE_META).map(function (c) { return [c, HOMEWORK_BLOCK.indexOf(c) >= 0]; });
      return apply(ch, false);
    }
    if (name === 'free') return apply(Object.keys(SERVICE_META).map(function (c) { return [c, false]; }), false);
    if (name === 'bedtime') return setKill(true).then(function () { return restart(); });
    return Promise.reject(new Error('unknown preset'));
  }

  // ---- render ----
  function render() {
    var k = $('kids');
    k.innerHTML = S.kids.length ? S.kids.map(function (d) {
      var nm = esc(d.comment || d.client);
      return '<div class="kid"><div><div class="name">' + nm + (d.locked ? ' <span class="tag paused">paused</span>' : '') +
        '</div><div class="meta">' + esc(d.client) + '</div></div><div style="display:flex;gap:8px">' +
        '<button onclick="window.PC.lock(\'' + esc(d.client) + '\',' + (!d.locked) + ')">' + (d.locked ? '\u25B6\uFE0F Resume' : '\u23F8\uFE0F Pause') + '</button>' +
        '<button class="danger" onclick="window.PC.rm(\'' + esc(d.client) + '\')">Remove</button></div></div>';
    }).join('') : '<div style="color:var(--dim);padding:4px 0">No devices yet &mdash; nothing is filtered.</div>';

    var by = {}; S.services.forEach(function (s) { by[s.comment] = s; });
    var html = '';
    S.cats.forEach(function (cat) {
      var names = cat[1].filter(function (n) { return by[n]; });
      if (!names.length) return;
      html += '<div class="sec">' + esc(cat[0]) + '</div><div class="grid">';
      names.forEach(function (n) {
        var s = by[n], m = S.meta[n] || ['\u2022', n];
        html += '<div class="svc"><span>' + m[0] + ' ' + esc(m[1]) + '</span>' +
          '<button class="sw ' + (s.enabled ? 'on' : '') + '" title="' + (s.enabled ? 'Blocked' : 'Allowed') + '" ' +
          'onclick="window.PC.toggle(\'' + esc(s.comment) + '\',' + (!s.enabled) + ')"></button></div>';
      });
      html += '</div>';
    });
    $('svcs').innerHTML = html;
    var blocked = S.services.filter(function (s) { return s.enabled; }).length;
    $('svcCount').textContent = blocked + ' of ' + S.services.length + ' blocked';

    var on = S.kill.enabled;
    $('bigwrap').className = 'big' + (on ? ' on' : '');
    $('killtitle').textContent = on ? '\u2705 Internet is blocked' : '\u23F8\uFE0F Block the entire internet';
    $('kill').className = 'bigbtn' + (on ? ' armed' : '');
    $('kill').textContent = on ? 'Unblock all' : 'Block all';

    var paused = S.kids.filter(function (d) { return d.locked; }).length;
    $('status').textContent = on ? 'Internet BLOCKED' : (paused ? paused + ' device paused' : 'Internet allowed');
    $('foot').textContent = 'Pi-hole ' + (S.version || '') + ' \u00B7 ' + S.kids.length + ' kid device(s) \u00B7 ' + blocked + '/' + S.services.length + ' services blocked';

    loadDevices();
  }
  function loadDevices() {
    return api('GET', '/api/network/devices?max_devices=500').then(function (d) {
      var sel = $('devpick'), known = {};
      S.kids.forEach(function (k) { known[k.client] = 1; });
      var o = [];
      (d.devices || []).forEach(function (dev) {
        (dev.ips || []).forEach(function (i) {
          if (!i.ip) return;
          o.push('<option value="' + esc(i.ip) + '">' + esc((i.name || dev.macVendor || i.ip) + ' \u00B7 ' + i.ip) + (known[i.ip] ? ' (kid)' : '') + '</option>');
        });
      });
      sel.innerHTML = o.length ? '<option value="">\u2014 pick a device seen on your network \u2014</option>' + o.join('')
        : '<option value="">No devices seen yet \u2014 type the IP below</option>';
    }).catch(function () {});
  }
  function pick(ip) { if (ip) $('devip').value = ip; }

  function withBusy(fn, okmsg) {
    if (BUSY) return; BUSY = true;
    document.querySelectorAll('.sw,.preset,.bigbtn,button').forEach(function (b) { b.classList.add('busy'); });
    fn().then(function () { toast(okmsg); }).catch(function (e) { toast('Failed: ' + e.message); })
      .then(function () { BUSY = false; document.querySelectorAll('.busy').forEach(function (b) { b.classList.remove('busy'); }); return loadState(); });
  }

  // ---- wiring ----
  window.PC = {
    toggle: function (n, w) { withBusy(function () { return setList(n, w); }, (w ? 'Blocked ' : 'Allowed ') + n); },
    lock: function (c, l) { withBusy(function () { return setLock(c, l); }, (l ? 'Paused ' : 'Resumed ') + c); },
    rm: function (c) { if (!confirm('Stop kid filtering for ' + c + '?')) return; withBusy(function () { return removeKid(c); }, 'Removed'); },
    add: function () {
      var c = $('devip').value.trim(), n = $('devname').value.trim();
      if (!c) { toast('Enter the device IP'); return; }
      withBusy(function () { return addKid(c, n); }, 'Device added').then(function () { $('devip').value = ''; $('devname').value = ''; });
    },
    preset: function (n) { withBusy(function () { return preset(n); }, 'Applied: ' + n); },
    kill: function () {
      var want = !S.kill.enabled;
      if (want && !confirm('Block the internet for EVERYONE using this Pi-hole?')) return;
      withBusy(function () { return setKill(want).then(function () { return restart(); }); }, want ? 'Internet blocked' : 'Internet unblocked');
    }
  };

  document.querySelectorAll('.preset').forEach(function (b) {
    b.addEventListener('click', function () { window.PC.preset(b.getAttribute('data-p')); });
  });
  $('addBtn').addEventListener('click', function () { window.PC.add(); });
  $('kill').addEventListener('click', function () { window.PC.kill(); });
  $('devip').addEventListener('keydown', function (e) { if (e.key === 'Enter') window.PC.add(); });

  $('loginBtn').addEventListener('click', function () { doLogin(); });
  $('pw').addEventListener('keydown', function (e) { if (e.key === 'Enter') doLogin(); });
  function doLogin() {
    var pw = $('pw').value;
    $('loginErr').textContent = '';
    $('loginBtn').disabled = true;
    tryLogin(pw).then(function (ok) {
      if (ok) { $('pw').value = ''; showApp(); return loadState(); }
      $('loginErr').textContent = 'Wrong password.';
    }).catch(function (e) { $('loginErr').textContent = 'Error: ' + e.message; })
      .then(function () { $('loginBtn').disabled = false; });
  }

  // boot
  if (SID) {
    api('GET', '/api/info/version').then(function () { showApp(); return loadState(); })
      .catch(function () { showLogin(); });
  } else {
    showLogin();
  }
})();
