/* Статический режим: те же ответы, что у API, но посчитанные в браузере. */
'use strict';

window.LocalAPI = (() => {
  let DATA = null;
  const UNLOCK_KEY = 'jd_password';

  function b64ToBytes(value) {
    const binary = atob(value);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return bytes;
  }

  async function decryptWith(password, cfg) {
    const encoder = new TextEncoder();
    const baseKey = await crypto.subtle.importKey(
      'raw', encoder.encode((cfg.login || '') + '\u0000' + password), 'PBKDF2', false, ['deriveKey']);
    const key = await crypto.subtle.deriveKey(
      { name: 'PBKDF2', salt: b64ToBytes(cfg.salt), iterations: cfg.iter, hash: cfg.hash || 'SHA-256' },
      baseKey, { name: 'AES-GCM', length: 256 }, false, ['decrypt']);
    const plain = await crypto.subtle.decrypt(
      { name: 'AES-GCM', iv: b64ToBytes(cfg.nonce) }, key, b64ToBytes(cfg.ct));
    return JSON.parse(new TextDecoder().decode(plain));
  }

  async function unlock() {
    const cfg = await (await fetch(window.J_STATIC.dataUrl, { cache: 'no-store' })).json();
    const saved = sessionStorage.getItem(UNLOCK_KEY);
    if (saved) {
      try {
        const data = await decryptWith(saved, cfg);
        const ready = document.getElementById('gate');
        if (ready) ready.classList.add('hidden');
        document.body.classList.remove('locked');
        return data;
      } catch (err) { sessionStorage.removeItem(UNLOCK_KEY); }
    }
    const gate = document.getElementById('gate');
    const form = document.getElementById('gate-form');
    const error = document.getElementById('gate-error');
    const loginInput = document.getElementById('gate-login');
    const passwordInput = document.getElementById('gate-password');
    if (!gate || !form) throw new Error('нет формы входа');
    loginInput.value = cfg.login || '';
    gate.classList.remove('hidden');
    document.body.classList.add('locked');
    passwordInput.focus();
    return new Promise((resolve) => {
      form.onsubmit = async (event) => {
        event.preventDefault();
        error.textContent = '';
        const login = loginInput.value.trim();
        if (login !== cfg.login) { error.textContent = 'Неверный логин или пароль'; return; }
        const button = document.getElementById('gate-btn');
        button.disabled = true;
        try {
          const data = await decryptWith(passwordInput.value, cfg);
          sessionStorage.setItem(UNLOCK_KEY, passwordInput.value);
          gate.classList.add('hidden');
          document.body.classList.remove('locked');
          resolve(data);
        } catch (err) {
          error.textContent = 'Неверный логин или пароль';
          passwordInput.select();
        } finally {
          button.disabled = false;
        }
      };
    });
  }

  async function load() {
    if (!DATA) {
      if (window.J_STATIC && window.J_STATIC.encrypted) {
        DATA = await unlock();
      } else {
        const url = (window.J_STATIC && window.J_STATIC.dataUrl) || 'data.json';
        DATA = await (await fetch(url, { cache: 'no-store' })).json();
      }
      DATA.index = {
        campaign: byKey(DATA.facts.campaign_day, 1),
        target: byKey(DATA.facts.target_day, 1),
      };
    }
    return DATA;
  }

  function byKey(rows, col) {
    const map = new Map();
    rows.forEach((r) => {
      const k = r[col];
      if (!map.has(k)) map.set(k, []);
      map.get(k).push(r);
    });
    return map;
  }

  const METRICS = ['impressions', 'clicks', 'spend', 'orders', 'sales', 'units'];

  function ratios(row) {
    const { impressions = 0, clicks = 0, spend = 0, sales = 0, orders = 0 } = row;
    row.ctr = impressions ? +(clicks / impressions * 100).toFixed(3) : null;
    row.cpc = clicks ? +(spend / clicks).toFixed(4) : null;
    row.cpa = orders ? +(spend / orders).toFixed(2) : null;
    row.acos = sales ? +(spend / sales * 100).toFixed(2) : null;
    row.roas = spend ? +(sales / spend).toFixed(3) : null;
    row.cvr = clicks ? +(orders / clicks * 100).toFixed(2) : null;
    row.aov = orders ? +(sales / orders).toFixed(2) : null;
    return row;
  }

  function parseDate(iso) { return new Date(iso + 'T00:00:00Z'); }
  function iso(d) { return d.toISOString().slice(0, 10); }
  function addDays(isoDate, days) {
    const d = parseDate(isoDate);
    d.setUTCDate(d.getUTCDate() + days);
    return iso(d);
  }
  function daysBetween(start, end) {
    return Math.round((parseDate(end) - parseDate(start)) / 86400000) + 1;
  }
  function mondayOf(isoDate) {
    const d = parseDate(isoDate);
    const shift = (d.getUTCDay() + 6) % 7;
    d.setUTCDate(d.getUTCDate() - shift);
    return iso(d);
  }
  function bucketOf(isoDate, grain) {
    if (grain === 'week') return mondayOf(isoDate);
    if (grain === 'month') return isoDate.slice(0, 7) + '-01';
    return isoDate;
  }

  function sum(rows, map) {
    const total = { impressions: 0, clicks: 0, spend: 0, orders: 0, sales: 0, units: 0 };
    for (const row of rows) {
      const values = map(row);
      for (const metric of METRICS) total[metric] += values[metric] || 0;
    }
    return total;
  }

  function inWindow(date, start, end) { return date >= start && date <= end; }

  function campaignsParam(params) {
    const raw = params.get('campaigns');
    return raw ? new Set(raw.split(',').map((s) => s.trim()).filter(Boolean)) : null;
  }

  function windowOf(params, fallbackDays = 7) {
    const end = params.get('end') || DATA.meta.date_max;
    const start = params.get('start') || addDays(end, -(fallbackDays - 1));
    return { start, end };
  }

  function factCampaignRow(r) {
    return { impressions: r[2], clicks: r[3], spend: r[4], orders: r[5], sales: r[6], units: r[7] };
  }

  function overview(params) {
    const { start, end } = windowOf(params);
    const picked = campaignsParam(params);
    const grain = params.get('grain') || 'day';
    const rows = DATA.facts.campaign_day.filter((r) =>
      inWindow(r[0], start, end) && (!picked || picked.has(r[1])));

    const current = ratios(sum(rows, factCampaignRow));
    const length = daysBetween(start, end);
    const prevEnd = addDays(start, -1);
    const prevStart = addDays(prevEnd, -(length - 1));
    const prevRows = DATA.facts.campaign_day.filter((r) =>
      inWindow(r[0], prevStart, prevEnd) && (!picked || picked.has(r[1])));
    const previous = ratios(sum(prevRows, factCampaignRow));

    const buckets = new Map();
    rows.forEach((r) => {
      const key = bucketOf(r[0], grain);
      if (!buckets.has(key)) buckets.set(key, []);
      buckets.get(key).push(r);
    });
    const series = [...buckets.entries()]
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([bucket, list]) => ratios({ bucket, ...sum(list, factCampaignRow) }));

    const byCampaign = new Map();
    rows.forEach((r) => {
      if (!byCampaign.has(r[1])) byCampaign.set(r[1], []);
      byCampaign.get(r[1]).push(r);
    });
    const settings = campaignMap();
    const campaigns = [...byCampaign.entries()].map(([id, list]) => {
      const meta = settings.get(id) || {};
      return ratios({
        campaign_id: id,
        name: meta.name || id,
        state: meta.state || '',
        budget: meta.budget ?? null,
        bid_strategy: meta.bid_strategy || '',
        ...sum(list, factCampaignRow),
      });
    }).sort((a, b) => b.spend - a.spend);

    return {
      period: { start, end, days: length, previous: { start: prevStart, end: prevEnd } },
      current, previous, series, campaigns,
      data_range: { min: DATA.meta.date_min, max: DATA.meta.date_max,
        days: daysBetween(DATA.meta.date_min, DATA.meta.date_max) },
    };
  }

  let campaignIndex = null;
  function campaignMap() {
    if (!campaignIndex) {
      campaignIndex = new Map(DATA.campaigns.map((c) => [c.campaign_id, c]));
    }
    return campaignIndex;
  }

  let adGroupIndex = null;
  function adGroupMap() {
    if (!adGroupIndex) {
      adGroupIndex = new Map(DATA.adgroups.map((g) => [g.ad_group_id, g]));
    }
    return adGroupIndex;
  }

  function campaignsView(params) {
    const { start, end } = windowOf(params);
    const picked = campaignsParam(params);
    const totals = new Map();
    DATA.facts.campaign_day.forEach((r) => {
      if (!inWindow(r[0], start, end) || (picked && !picked.has(r[1]))) return;
      if (!totals.has(r[1])) totals.set(r[1], []);
      totals.get(r[1]).push(r);
    });
    return DATA.campaigns
      .filter((c) => !picked || picked.has(c.campaign_id))
      .map((c) => ratios({ ...c, ...sum(totals.get(c.campaign_id) || [], factCampaignRow) }))
      .sort((a, b) => (b.spend || 0) - (a.spend || 0));
  }

  function targetsView(params) {
    const { start, end } = windowOf(params, 30);
    const picked = campaignsParam(params);
    const kind = params.get('kind') || 'positive';
    const q = (params.get('q') || '').toLowerCase();
    const groups = adGroupMap();

    const totals = new Map();
    DATA.facts.target_day.forEach((r) => {
      if (!inWindow(r[0], start, end) || (picked && !picked.has(r[1]))) return;
      const key = r[1] + '|' + r[2] + '|' + r[3];
      if (!totals.has(key)) totals.set(key, []);
      totals.get(key).push(r);
    });

    const empty = { impressions: 0, clicks: 0, spend: 0, orders: 0, sales: 0, units: 0 };
    return DATA.targets
      .filter((t) => (kind === 'all' ? true : kind === 'negative' ? t.is_negative : !t.is_negative))
      .filter((t) => !picked || picked.has(t.campaign_id))
      .filter((t) => !q || (t.targeting || '').toLowerCase().includes(q) || (t.match_type || '').toLowerCase().includes(q))
      .map((t) => {
        const list = totals.get(t.campaign_id + '|' + t.ad_group_id + '|' + t.target_id) || [];
        const metrics = list.length ? sum(list, (r) => ({ impressions: r[4], clicks: r[5], spend: r[6], orders: r[7], sales: r[8], units: r[9] })) : empty;
        const meta = campaignMap().get(t.campaign_id) || {};
        const group = groups.get(t.ad_group_id) || {};
        return ratios({
          ...t,
          campaign_name: meta.name || t.campaign_id,
          ad_group_name: group.name || '',
          ...metrics,
        });
      })
      .sort((a, b) => (a.is_negative - b.is_negative) || (b.spend - a.spend) || String(a.targeting).localeCompare(String(b.targeting)));
  }

  function termsView(params) {
    const { start, end } = windowOf(params, 30);
    const picked = campaignsParam(params);
    const q = (params.get('q') || '').toLowerCase();
    const ordersOnly = params.get('orders_only') === 'true';
    const grouped = new Map();
    DATA.facts.searchterm_day.forEach((r) => {
      if (!inWindow(r[0], start, end) || (picked && !picked.has(r[1]))) return;
      if (q && !(r[4] || '').toLowerCase().includes(q)) return;
      const key = r[4] + '|' + r[1];
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key).push(r);
    });
    return [...grouped.entries()].map(([, list]) => {
      const first = list[0];
      return ratios({
        search_term: first[4], campaign_id: first[1], ad_group_id: first[2], target_id: first[3],
        targeting: first[5], match_type: first[6],
        ...sum(list, (r) => ({ impressions: r[7], clicks: r[8], spend: r[9], orders: r[10], sales: r[11], units: r[12] })),
      });
    }).filter((r) => !ordersOnly || r.orders > 0).sort((a, b) => b.spend - a.spend).slice(0, 500);
  }

  function placementsView(params) {
    const { start, end } = windowOf(params, 30);
    const picked = campaignsParam(params);
    const grouped = new Map();
    DATA.facts.placement_day.forEach((r) => {
      if (!inWindow(r[0], start, end) || (picked && !picked.has(r[1]))) return;
      const key = r[2] + '|' + r[1];
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key).push(r);
    });
    return [...grouped.entries()].map(([, list]) => {
      const first = list[0];
      const meta = campaignMap().get(first[1]) || {};
      return ratios({
        placement: first[2], campaign_id: first[1], campaign_name: meta.name || first[1],
        ...sum(list, (r) => ({ impressions: r[3], clicks: r[4], spend: r[5], orders: r[6], sales: r[7], units: r[8] })),
      });
    }).sort((a, b) => b.spend - a.spend);
  }

  function productsView(params) {
    const { start, end } = windowOf(params, 30);
    const picked = campaignsParam(params);
    const grouped = new Map();
    DATA.facts.product_day.forEach((r) => {
      if (!inWindow(r[0], start, end) || (picked && !picked.has(r[1]))) return;
      const key = [r[1], r[2], r[3], r[4], r[5]].join('|');
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key).push(r);
    });
    return [...grouped.entries()].map(([, list]) => {
      const first = list[0];
      return ratios({
        campaign_id: first[1], ad_group_id: first[2], ad_id: first[3], asin: first[4], marketplace: first[5],
        ...sum(list, (r) => ({ impressions: r[6], clicks: r[7], spend: r[8], orders: r[9], sales: r[10], units: r[11] })),
      });
    }).sort((a, b) => b.spend - a.spend);
  }

  function changesView(params) {
    const { start, end } = windowOf(params, 90);
    const picked = campaignsParam(params);
    const q = (params.get('q') || '').toLowerCase();
    return DATA.changes
      .filter((c) => inWindow(c.change_date, start, end))
      .filter((c) => !picked || picked.has(c.campaign_id))
      .filter((c) => !q || [c.summary, c.field, c.entity_label].join(' ').toLowerCase().includes(q))
      .sort((a, b) => String(b.change_ts || b.change_date).localeCompare(String(a.change_ts || a.change_date)))
      .slice(0, 2000);
  }

  function meta() {
    return {
      campaigns: DATA.campaigns.map((c) => ({
        campaign_id: c.campaign_id, name: c.name, state: c.state, budget: c.budget,
      })),
      date_min: DATA.meta.date_min,
      date_max: DATA.meta.date_max,
      last_run: DATA.runs[0] || null,
      counts: DATA.meta.counts,
      targets: DATA.meta.targets,
      negatives: DATA.meta.negatives,
      account: DATA.meta.account,
      accounts: [DATA.meta.account],
    };
  }

  async function handle(path) {
    await load();
    const url = new URL(path, 'https://local.invalid');
    const params = url.searchParams;
    switch (url.pathname) {
      case '/api/meta': return meta();
      case '/api/accounts': return { default: DATA.meta.account.id, accounts: [DATA.meta.account] };
      case '/api/overview': return overview(params);
      case '/api/campaigns': return campaignsView(params);
      case '/api/targets': return targetsView(params);
      case '/api/terms': return termsView(params);
      case '/api/placements': return placementsView(params);
      case '/api/products': return productsView(params);
      case '/api/changes': return changesView(params);
      case '/api/coverage': return { coverage: DATA.coverage, runs: DATA.runs };
      default: throw new Error('Неизвестный запрос: ' + url.pathname);
    }
  }

  return { handle, load };
})();
