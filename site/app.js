/* The Sinko site's behaviour: language, the numbers strip, the copy button, screenshots that may be missing.
   The page reads fine without any of it (English text is in index.html); this only adds the Arabic text, the live
   numbers and conveniences. The logic that can be tested without a browser is in lib.js. */
(function () {
  'use strict';

  var Lib = window.SinkoLib;
  var STR = window.SINKO_STRINGS;
  if (!Lib || !STR) return;

  var config = Lib.sanitizeConfig(window.SINKO_CONFIG);
  var root = document.documentElement;
  var lang = root.getAttribute('lang') === 'ar' ? 'ar' : 'en';     // set before the first paint by the script in index.html
  var counters = { online: null, downloads: null };
  var animations = {};

  function $(selector) { return document.querySelector(selector); }
  function all(selector) { return Array.prototype.slice.call(document.querySelectorAll(selector)); }
  function t(key) { return (STR[lang] && STR[lang][key]) || STR.en[key] || ''; }
  function motionAllowed() {
    return !(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  }

  // ------------------------------------------------------------------ configuration
  function applyConfig() {
    var base = 'https://github.com/' + config.repo;
    all('[data-repo-link]').forEach(function (a) { a.href = base; });
    all('[data-privacy-link]').forEach(function (a) { a.href = base + '/blob/master/docs/privacy.md'; });
    all('[data-readme-link]').forEach(function (a) { a.href = base + '#install'; });
    $('#installCmd').textContent = Lib.installCommand(config.repo);

    var buy = $('#buyBtn');
    var soon = $('#readySoon');
    if (config.buyUrl) {
      buy.href = config.buyUrl;
      buy.hidden = false;
      soon.hidden = true;
    } else {
      buy.hidden = true;
      soon.hidden = false;
    }
    var support = $('#supportLink');
    if (config.supportUrl) {
      support.href = config.supportUrl;
      support.hidden = false;
    }
  }

  // ------------------------------------------------------------------ language
  function applyLanguage(next) {
    lang = next === 'ar' ? 'ar' : 'en';
    root.lang = lang;
    root.dir = lang === 'ar' ? 'rtl' : 'ltr';
    all('[data-i18n]').forEach(function (el) { el.textContent = t(el.getAttribute('data-i18n')); });
    // data-i18n-attr="alt:key;aria-label:key": text that lives in an attribute
    all('[data-i18n-attr]').forEach(function (el) {
      el.getAttribute('data-i18n-attr').split(';').forEach(function (pair) {
        var at = pair.indexOf(':');
        if (at > 0) el.setAttribute(pair.slice(0, at).trim(), t(pair.slice(at + 1).trim()));
      });
    });
    document.title = t('docTitle');
    $('#langText').lang = lang === 'ar' ? 'en' : 'ar';              // the button names the other language, in that language
    all('[data-shot]').forEach(function (img) {
      var src = 'img/' + img.getAttribute('data-shot') + '-' + lang + '.png';
      if (img.getAttribute('src') !== src) img.setAttribute('src', src);
    });
    renderCounters(false);
  }

  $('#langBtn').addEventListener('click', function () {
    var next = lang === 'ar' ? 'en' : 'ar';
    try { localStorage.setItem('sinko-lang', next); } catch (e) { /* private mode: the choice lasts until the page closes */ }
    applyLanguage(next);
  });

  // ------------------------------------------------------------------ the numbers strip
  // Hidden until real numbers arrive. A count of 0 is also left out: it is true, but "0 boxes online" says nothing
  // useful on the day the counter starts.
  function setNumber(el, value, animate) {
    if (animations[el.id]) { cancelAnimationFrame(animations[el.id]); animations[el.id] = 0; }
    if (!animate || !motionAllowed() || value < 2) { el.textContent = Lib.formatCount(value, lang); return; }
    var start = null;
    function frame(now) {
      if (start === null) start = now;
      var p = Math.min(1, (now - start) / 700);
      el.textContent = Lib.formatCount(Math.round(value * (1 - Math.pow(1 - p, 3))), lang);
      animations[el.id] = p < 1 ? requestAnimationFrame(frame) : 0;
    }
    animations[el.id] = requestAnimationFrame(frame);
  }

  function renderCounters(animate) {
    var online = Lib.isCount(counters.online) && counters.online > 0;
    var downloads = Lib.isCount(counters.downloads) && counters.downloads > 0;
    $('#statOnline').hidden = !online;
    $('#statDownloads').hidden = !downloads;
    $('#countsNote').hidden = !online;                              // the note is about the box counter
    $('#counts').hidden = !(online || downloads);
    if (online) setNumber($('#numOnline'), counters.online, animate);
    if (downloads) setNumber($('#numDownloads'), counters.downloads, animate);
  }

  var cache = {
    get: function () {
      try {
        var saved = JSON.parse(sessionStorage.getItem('sinko-counters'));
        return saved && Date.now() - saved.at < 10 * 60 * 1000 ? saved.v : null;
      } catch (e) { return null; }
    },
    set: function (value) {
      try { sessionStorage.setItem('sinko-counters', JSON.stringify({ at: Date.now(), v: value })); } catch (e) { /* optional */ }
    }
  };

  function loadCounters() {
    if (typeof window.fetch !== 'function') return;
    Lib.loadCounters({ config: config, fetch: window.fetch.bind(window), cache: cache }).then(function (result) {
      counters = result;
      renderCounters(true);
    }, function () { /* hidden, quietly */ });
  }

  // ------------------------------------------------------------------ the install command
  var copyTimer = 0;
  function selectCommand() {
    var range = document.createRange();
    range.selectNodeContents($('#installCmd'));
    var selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  }

  function legacyCopy(text) {
    var area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', '');
    area.style.position = 'fixed';
    area.style.insetBlockStart = '-1000px';
    document.body.appendChild(area);
    area.select();
    var ok = false;
    try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
    document.body.removeChild(area);
    return ok;
  }

  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text).then(function () { return true; }, function () { return legacyCopy(text); });
    }
    return Promise.resolve(legacyCopy(text));
  }

  $('#copyBtn').addEventListener('click', function () {
    copyText($('#installCmd').textContent).then(function (ok) {
      var button = $('#copyBtn');
      var message = ok ? t('ownCopied') : t('ownCopyFail');
      button.textContent = message;
      $('#copyStatus').textContent = message;
      if (!ok) selectCommand();                                     // so that Ctrl+C or a long press still works
      clearTimeout(copyTimer);
      copyTimer = setTimeout(function () {
        button.textContent = t('ownCopy');
        $('#copyStatus').textContent = '';
      }, 2400);
    });
  });
  $('#installCmd').addEventListener('click', selectCommand);

  // ------------------------------------------------------------------ screenshots
  // The pictures are copied in at deploy time and may be missing: a missing one takes its frame with it, and the
  // page closes up around the gap (no broken-image icon, no empty box).
  function watchImage(img, setBroken) {
    img.addEventListener('error', function () { setBroken(true); });
    img.addEventListener('load', function () { setBroken(false); });
    if (img.complete && img.naturalWidth === 0 && img.getAttribute('src')) setBroken(true);   // it failed before this script ran
  }

  function syncGallery() {
    var visible = all('#shots figure').filter(function (f) { return !f.hidden; });
    $('#shots').hidden = visible.length === 0;
  }

  // The screenshots may be added to the repository after the site first goes live. A lazy image far down the page would
  // only fail when a visitor scrolls to it, leaving empty frames until then, so ask once, cheaply, which ones exist
  // (a HEAD request moves no picture) and hide the others at once. The section stays hidden until at least one exists.
  function probeGallery() {
    var checks = all('#shots figure').map(function (figure) {
      var img = figure.querySelector('img');
      if (!img || typeof window.fetch !== 'function') return Promise.resolve();
      return window.fetch(img.getAttribute('src'), { method: 'HEAD', credentials: 'omit' }).then(function (response) {
        if (!response.ok) figure.hidden = true;
      }, function () { /* offline or blocked: the image itself decides when it is shown */ });
    });
    return Promise.all(checks).then(syncGallery, syncGallery);
  }

  function watchImages() {
    var hero = $('.hero');
    all('.hero-shot img').forEach(function (img) {
      watchImage(img, function (broken) { hero.classList.toggle('no-shot', broken); });
    });
    all('#shots img').forEach(function (img) {
      var figure = img.closest('figure');
      watchImage(img, function (broken) { figure.hidden = broken; syncGallery(); });
    });
  }

  // ------------------------------------------------------------------ start
  applyConfig();
  applyLanguage(lang);
  watchImages();
  probeGallery();
  loadCounters();
})();
