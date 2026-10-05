/* The Sinko site's logic that does not touch the page: configuration checks, the numbers strip, the install command.
   A plain script that sets window.SinkoLib, so that site/test can run it in Node with a fake fetch.
   The rule for every number: it is shown only when it is a real, plausible count that came from the counter or from
   GitHub. Anything else (a failure, a surprise, a negative or fractional value) means "not shown", never a guess. */
(function (root) {
  'use strict';

  var DEFAULT_REPO = 'iret33/sinko';
  var REPO_RE = /^[A-Za-z0-9_.-]{1,100}\/[A-Za-z0-9_.-]{1,100}$/;
  var MAX_COUNT = 1e10;                  // more than this is not a count of boxes or downloads
  var TIMEOUT_MS = 6000;
  var PER_PAGE = 100;
  var MAX_PAGES = 5;
  var CONFIG_KEYS = ['repo', 'statsUrl', 'buyUrl', 'supportUrl'];

  function isRepo(value) {
    return typeof value === 'string' && REPO_RE.test(value) && value.split('/').every(function (p) { return p !== '.' && p !== '..'; });
  }

  function isCount(value) {
    return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 && value <= MAX_COUNT;
  }

  // The https address in `value`, or '' when it is empty or unacceptable. allowMailto: a support address may be mailto:.
  function safeUrl(value, allowMailto) {
    if (typeof value !== 'string') return '';
    var text = value.trim();
    if (!text) return '';
    var url;
    try { url = new URL(text); } catch (e) { return ''; }
    if (allowMailto && url.protocol === 'mailto:') return /^mailto:[^\s@]+@[^\s@]+$/.test(text) ? text : '';
    if (url.protocol !== 'https:' || !url.hostname || url.username || url.password) return '';
    return text;
  }

  // What is wrong with a configuration: a list of plain sentences, empty when it is fine. Used by the tests, and so
  // strict about things the runtime quietly puts right (a trailing slash on statsUrl).
  function configProblems(raw) {
    var problems = [];
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return ['SINKO_CONFIG is not an object'];
    Object.keys(raw).forEach(function (key) {
      if (CONFIG_KEYS.indexOf(key) === -1) problems.push('unknown setting "' + key + '"');
    });
    CONFIG_KEYS.forEach(function (key) {
      if (typeof raw[key] !== 'string') problems.push(key + ' must be a string (empty is allowed except for repo)');
    });
    if (!isRepo(raw.repo)) problems.push('repo must look like owner/name');
    if (raw.statsUrl) {
      if (!safeUrl(raw.statsUrl)) problems.push('statsUrl must be an https address');
      else if (/\/$/.test(raw.statsUrl.trim())) problems.push('statsUrl must not end with a slash');
      else if (/[?#]/.test(raw.statsUrl)) problems.push('statsUrl must not contain ? or #');
    }
    if (raw.buyUrl && !safeUrl(raw.buyUrl)) problems.push('buyUrl must be an https address');
    if (raw.supportUrl && !safeUrl(raw.supportUrl, true)) problems.push('supportUrl must be an https address or a mailto: address');
    return problems;
  }

  // The configuration the page runs with: every field checked on its own, a bad one counts as empty.
  function sanitizeConfig(raw) {
    var cfg = raw && typeof raw === 'object' ? raw : {};
    var stats = safeUrl(cfg.statsUrl);
    if (/[?#]/.test(stats)) stats = '';
    return {
      repo: isRepo(cfg.repo) ? cfg.repo : DEFAULT_REPO,
      statsUrl: stats.replace(/\/+$/, ''),
      buyUrl: safeUrl(cfg.buyUrl),
      supportUrl: safeUrl(cfg.supportUrl, true)
    };
  }

  // The one form of the install command that is written anywhere (README, docs, this site): https only, and a redirect
  // may not leave https either (a plain `curl -fsSL` would follow GitHub to an http address if it were ever sent there).
  function installCommand(repo) {
    return "curl --proto '=https' --proto-redir '=https' -fsSL https://github.com/" + repo + '/releases/latest/download/install.sh | sudo bash';
  }

  // {online, downloads}: each a real count or null. online > total would be nonsense, so it is not shown either.
  function parseStats(data) {
    var out = { online: null, downloads: null };
    if (!data || typeof data !== 'object') return out;
    if (isCount(data.online) && (data.total === undefined || !isCount(data.total) || data.online <= data.total)) out.online = data.online;
    if (isCount(data.downloads)) out.downloads = data.downloads;
    return out;
  }

  // The sum of the download counts of the published releases, or null when anything in the answer is not a count.
  function sumDownloads(releases) {
    if (!Array.isArray(releases)) return null;
    var total = 0;
    for (var i = 0; i < releases.length; i += 1) {
      var release = releases[i];
      if (!release || typeof release !== 'object') return null;
      if (release.draft === true) continue;
      var assets = release.assets === undefined ? [] : release.assets;
      if (!Array.isArray(assets)) return null;
      for (var j = 0; j < assets.length; j += 1) {
        if (!assets[j] || !isCount(assets[j].download_count)) return null;
        total += assets[j].download_count;
      }
    }
    return isCount(total) ? total : null;
  }

  function fetchJson(fetchFn, url) {
    var controller = typeof AbortController === 'function' ? new AbortController() : null;
    var timer = controller ? setTimeout(function () { controller.abort(); }, TIMEOUT_MS) : null;
    var init = { headers: { Accept: 'application/json' }, credentials: 'omit', referrerPolicy: 'no-referrer' };
    if (controller) init.signal = controller.signal;
    return fetchFn(url, init).then(function (response) {
      if (!response.ok) throw new Error('HTTP ' + response.status);
      return response.json();
    }).then(function (value) {
      clearTimeout(timer);
      return value;
    }, function (err) {
      clearTimeout(timer);
      throw err;
    });
  }

  // Downloads from GitHub's public API: the sum over the releases, or null. Never rejects.
  async function githubDownloads(fetchFn, repo) {
    if (!isRepo(repo)) return null;
    var releases = [];
    try {
      for (var page = 1; page <= MAX_PAGES; page += 1) {
        var batch = await fetchJson(fetchFn, 'https://api.github.com/repos/' + repo + '/releases?per_page=' + PER_PAGE + '&page=' + page);
        if (!Array.isArray(batch)) return null;
        releases = releases.concat(batch);
        if (batch.length < PER_PAGE) return sumDownloads(releases);
      }
    } catch (e) {
      return null;
    }
    return null;                          // more releases than are read: a partial sum would be wrong
  }

  // The numbers for the strip: {online, downloads}, each a count or null. Never rejects.
  //   o.config   a sanitized configuration
  //   o.fetch    fetch
  //   o.cache    optional {get() -> object|null, set(object)}: reloading the page must not eat GitHub's rate limit
  async function loadCounters(o) {
    var kept = o.cache ? o.cache.get() : null;
    if (kept && typeof kept === 'object') {
      var checked = { online: isCount(kept.online) ? kept.online : null, downloads: isCount(kept.downloads) ? kept.downloads : null };
      if (checked.online !== null || checked.downloads !== null) return checked;
    }
    var out = { online: null, downloads: null };
    if (o.config.statsUrl) {
      try {
        out = parseStats(await fetchJson(o.fetch, o.config.statsUrl + '/v1/stats'));
      } catch (e) {
        out = { online: null, downloads: null };
      }
    }
    if (out.downloads === null) out.downloads = await githubDownloads(o.fetch, o.config.repo);
    if (o.cache && (out.online !== null || out.downloads !== null)) {
      try { o.cache.set(out); } catch (e) { /* the cache is only a courtesy */ }
    }
    return out;
  }

  function formatCount(value, lang) {
    try {
      return new Intl.NumberFormat(lang === 'ar' ? 'ar-u-nu-latn' : 'en').format(value);   // digits stay Latin, as on the box
    } catch (e) {
      return String(value);
    }
  }

  root.SinkoLib = {
    DEFAULT_REPO: DEFAULT_REPO,
    isRepo: isRepo,
    isCount: isCount,
    safeUrl: safeUrl,
    configProblems: configProblems,
    sanitizeConfig: sanitizeConfig,
    installCommand: installCommand,
    parseStats: parseStats,
    sumDownloads: sumDownloads,
    githubDownloads: githubDownloads,
    loadCounters: loadCounters,
    formatCount: formatCount
  };
})(window);
