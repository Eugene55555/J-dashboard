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
  function distinctDays(rows) { return new Set(rows.map((r) => r[0])); }
  /* Отрезки дней внутри периода, за которые в базе нет строк: та же логика, что
     в app/queries.py::missing_ranges, иначе версии показывали бы разное покрытие. */
  function missingRanges(start, end, have) {
    const out = [];
    let run = null;
    for (let d = start; d <= end; d = addDays(d, 1)) {
      if (have.has(d)) {
        if (run) { out.push([run, addDays(d, -1)]); run = null; }
      } else if (!run) run = d;
    }
    if (run) out.push([run, end]);
    return out;
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

  function pickedCampaigns(params) {
    const raw = params.get('campaigns');
    const base = raw ? new Set(raw.split(',').map((s) => s.trim()).filter(Boolean)) : null;
    const status = params.get('status');
    if (!status) return base;
    const allowed = new Set(DATA.campaigns.filter((c) => c.state === status).map((c) => c.campaign_id));
    if (!base) return allowed.size ? allowed : new Set(['__none__']);
    const picked = new Set([...base].filter((id) => allowed.has(id)));
    return picked.size ? picked : new Set(['__none__']);
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
    const picked = pickedCampaigns(params);
    const grain = params.get('grain') || 'day';
    const rows = DATA.facts.campaign_day.filter((r) =>
      inWindow(r[0], start, end) && (!picked || picked.has(r[1])));
    const have = distinctDays(DATA.facts.campaign_day);
    const coverage = {
      days: [...have].filter((d) => inWindow(d, start, end)).length,
      missing: missingRanges(start, end, have),
    };

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
      coverage,
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
    const picked = pickedCampaigns(params);
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

  // Коды авто-таргетов переводим так же, как сервер (app/queries.py).
  const AUTO_TARGET_LABELS = {
    'QUERY_HIGH_REL_MATCHES': 'авто · близкие запросы',
    'QUERY_BROAD_REL_MATCHES': 'авто · широкие запросы',
    'ASIN_SUBSTITUTE_RELATED': 'авто · замена',
    'ASIN_ACCESSORY_RELATED': 'авто · дополнение',
    'ASIN_SAME_AS': 'авто · тот же товар',
    'CLOSE-MATCH': 'авто · близкие запросы', 'LOOSE-MATCH': 'авто · широкие запросы',
    SUBSTITUTES: 'авто · замена', COMPLEMENTS: 'авто · дополнение',
  };
  function targetingLabel(value) {
    const text = String(value || '').trim();
    if (!text) return '';
    const cut = text.indexOf(':');
    const head = (cut < 0 ? text : text.slice(0, cut)).trim().toUpperCase();
    const label = AUTO_TARGET_LABELS[head];
    if (!label) return text;
    const tail = cut < 0 ? '' : text.slice(cut + 1);
    return tail ? `${label} · ${tail}` : label;
  }

  function targetsView(params) {
    const { start, end } = windowOf(params, 30);
    const picked = pickedCampaigns(params);
    const kind = params.get('kind') || 'positive';
    const q = (params.get('q') || '').toLowerCase();
    const onlyIdle = params.get('idle') === 'true';
    const groups = adGroupMap();
    const events = lastEventMap();

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
        // Ставка без собственного значения наследуется от группы — как в кабинете Amazon.
        const inherited = !t.is_negative && t.bid == null && t.effective_bid == null
          && group.default_bid != null ? group.default_bid : null;
        return ratios({
          ...t,
          targeting_raw: t.targeting,
          targeting: targetingLabel(t.targeting),
          effective_bid: t.effective_bid != null ? t.effective_bid : (inherited != null ? inherited : null),
          bid_source: t.bid_source || (inherited != null ? 'AD_GROUP_DEFAULT' : t.bid_source),
          campaign_name: meta.name || t.campaign_id,
          ad_group_name: group.name || '',
          idle: metrics.impressions ? 0 : 1,
          ...(events.get(t.target_id) || { last_field: null, last_old: null, last_new: null,
            last_date: null, last_ts: null, last_precision: null, last_source: null }),
          ...metrics,
        });
      })
      .filter((t) => !onlyIdle || t.idle)
      .sort((a, b) => (a.is_negative - b.is_negative) || (b.spend - a.spend) || String(a.targeting).localeCompare(String(b.targeting)));
  }

  /* Самое свежее событие журнала по каждому объекту — для колонки «Последнее изменение». */
  let eventIndex = null;
  function lastEventMap() {
    if (eventIndex) return eventIndex;
    eventIndex = new Map();
    DATA.changes.forEach((c) => {
      const ts = String(c.change_ts || c.change_date || '');
      const known = eventIndex.get(c.entity_id);
      if (!known || ts > known.last_ts) {
        eventIndex.set(c.entity_id, {
          last_field: c.field || null, last_old: c.old_value ?? null, last_new: c.new_value ?? null,
          last_date: c.change_date || null, last_ts: c.change_ts || null,
          last_precision: c.date_precision || null, last_source: c.source || null,
        });
      }
    });
    return eventIndex;
  }

  function termsView(params) {
    const { start, end } = windowOf(params, 30);
    const picked = pickedCampaigns(params);
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
      const meta = campaignMap().get(first[1]) || {};
      return ratios({
        search_term: first[4], campaign_id: first[1], campaign_name: meta.name || first[1],
        ad_group_id: first[2], target_id: first[3],
        targeting: first[5], match_type: first[6],
        ...sum(list, (r) => ({ impressions: r[7], clicks: r[8], spend: r[9], orders: r[10], sales: r[11], units: r[12] })),
      });
    }).filter((r) => !ordersOnly || r.orders > 0).sort((a, b) => b.spend - a.spend);
  }

  const PLACEMENT_LABELS = {
    'TOP OF SEARCH ON-AMAZON': 'Top of Search',
    'TOP OF SEARCH OFF-AMAZON': 'Top of Search',
    'REST OF SEARCH ON-AMAZON': 'Rest of Search',
    'REST OF SEARCH OFF-AMAZON': 'Rest of Search',
    'DETAIL PAGE ON-AMAZON': 'Product page',
    'DETAIL PAGE OFF-AMAZON': 'Product page',
    'PRODUCT_PAGES': 'Product page',
    'OTHER ON-AMAZON': 'Rest of Search',
    'OTHER OFF-AMAZON': 'Rest of Search',
    'TOP_OF_SEARCH': 'Top of Search',
    'REST_OF_SEARCH': 'Rest of Search',
  };

  function placementLabel(raw) {
    const value = (raw || '').trim();
    if (!value || value === '__UNKNOWN__') return 'Не определено';
    return PLACEMENT_LABELS[value.toUpperCase()] ||
      value.replace(' ON-AMAZON', '').replace(' OFF-AMAZON', '')
        .toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
  }

  function placementsView(params) {
    const { start, end } = windowOf(params, 30);
    const picked = pickedCampaigns(params);
    const grouped = new Map();
    DATA.facts.placement_day.forEach((r) => {
      if (!inWindow(r[0], start, end) || (picked && !picked.has(r[1]))) return;
      const key = r[2] + '|' + r[1];
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key).push(r);
    });
    // On-Amazon и Off-Amazon одного размещения сливаем в одну строку.
    const merged = new Map();
    [...grouped.values()].forEach((list) => {
      const first = list[0];
      const label = placementLabel(first[2]);
      const key = first[1] + '|' + label;
      if (!merged.has(key)) {
        merged.set(key, { campaign_id: first[1], placement: label, raws: new Set(), list: [] });
      }
      const slot = merged.get(key);
      slot.raws.add(first[2]);
      slot.list.push(...list);
    });
    return [...merged.values()].map((slot) => {
      const meta = campaignMap().get(slot.campaign_id) || {};
      return ratios({
        placement: slot.placement,
        placement_raws: [...slot.raws].join(', '),
        campaign_id: slot.campaign_id,
        campaign_name: meta.name || slot.campaign_id,
        ...sum(slot.list, (r) => ({ impressions: r[3], clicks: r[4], spend: r[5], orders: r[6], sales: r[7], units: r[8] })),
      });
    }).sort((a, b) => b.spend - a.spend);
  }

  function productsView(params) {
    const { start, end } = windowOf(params, 30);
    const picked = pickedCampaigns(params);
    const grouped = new Map();
    DATA.facts.product_day.forEach((r) => {
      if (!inWindow(r[0], start, end) || (picked && !picked.has(r[1]))) return;
      const key = [r[1], r[2], r[3], r[4], r[5]].join('|');
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key).push(r);
    });
    return [...grouped.entries()].map(([, list]) => {
      const first = list[0];
      const meta = campaignMap().get(first[1]) || {};
      return ratios({
        campaign_id: first[1], campaign_name: meta.name || first[1],
        ad_group_id: first[2], ad_id: first[3], asin: first[4], marketplace: first[5],
        ...sum(list, (r) => ({ impressions: r[6], clicks: r[7], spend: r[8], orders: r[9], sales: r[10], units: r[11] })),
      });
    }).sort((a, b) => b.spend - a.spend);
  }

  // Те же группы полей, что и на сервере (app/queries.py): фильтры обязаны совпадать.
  const FIELD_GROUPS = {
    status: ['STATUS'],
    bid: ['BID_AMOUNT', 'DEFAULT_BID_AMOUNT', 'BID'],
    budget: ['BUDGET_AMOUNT', 'IN_BUDGET'],
    placement: ['PLACEMENT_TOP', 'PLACEMENT_DETAIL_PAGE', 'PLACEMENT_REST_OF_SEARCH',
      'PLACEMENT_PRODUCT_PAGE'],
    name: ['NAME'],
    created: ['CREATED'],
  };

  function changesView(params) {
    const { start, end } = windowOf(params, 90);
    const picked = pickedCampaigns(params);
    const q = (params.get('q') || '').toLowerCase();
    const group = FIELD_GROUPS[params.get('group') || ''] || null;
    const entity = (params.get('entity') || '').trim();
    return DATA.changes
      .filter((c) => inWindow(c.change_date, start, end))
      .filter((c) => !picked || picked.has(c.campaign_id))
      .filter((c) => !group || group.includes(c.field))
      .filter((c) => !entity || c.entity_type === entity)
      .filter((c) => !q || [c.summary, c.field, c.entity_label, c.campaign_name, c.campaign_id]
        .join(' ').toLowerCase().includes(q))
      .map((c) => {
        const group = adGroupMap().get(c.ad_group_id) || {};
        return { ...c,
          campaign_name: c.campaign_name || (campaignMap().get(c.campaign_id) || {}).name || c.campaign_id,
          ad_group_name: c.ad_group_name || group.name || '' };
      })
      .sort((a, b) => String(b.change_ts || b.change_date).localeCompare(String(a.change_ts || a.change_date)));
  }

  function meta() {
    return {
      campaigns: DATA.campaigns.map((c) => ({
        campaign_id: c.campaign_id, name: c.name, state: c.state, budget: c.budget,
      })),
      date_min: DATA.meta.date_min,
      date_max: DATA.meta.date_max,
      coverage_days: DATA.meta.coverage_days
        || new Set(DATA.facts.campaign_day.map((r) => r[0])).size,
      changes_min: DATA.meta.changes_min || null,
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
