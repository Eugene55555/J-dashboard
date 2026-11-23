/* J-dashboard — интерфейс. Без сборки и внешних библиотек. */
'use strict';

const state = {
  view: 'overview',
  account: localStorage.getItem('jd_account') || null,
  preset: '7',
  start: null,
  end: null,
  all: true,
  selected: new Set(),
  meta: null,
  sort: {},
  chart: 'money',
  token: 0,
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

/* ---------- форматирование ---------- */
const RU = new Intl.NumberFormat('ru-RU');
const money0 = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });
const money2 = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmtInt = (v) => (v === null || v === undefined) ? '—' : RU.format(Math.round(v));
const fmtMoney = (v) => (v === null || v === undefined) ? '—' : money2.format(v);
const fmtMoney0 = (v) => (v === null || v === undefined) ? '—' : money0.format(v);
const fmtPct = (v, d = 2) => (v === null || v === undefined) ? '—' : Number(v).toFixed(d).replace('.', ',') + '%';
const fmtNum = (v, d = 2) => (v === null || v === undefined) ? '—' : Number(v).toFixed(d).replace('.', ',');
const fmtRoas = (v) => (v === null || v === undefined) ? '—' : Number(v).toFixed(2).replace('.', ',') + '×';

function plural(n, forms) {
  const abs = Math.abs(n) % 100;
  const last = abs % 10;
  if (abs > 10 && abs < 20) return forms[2];
  if (last === 1) return forms[0];
  if (last >= 2 && last <= 4) return forms[1];
  return forms[2];
}
const todayISO = () => new Date().toISOString().slice(0, 10);

function isoAdd(iso, days) {
  const d = new Date(iso + 'T00:00:00Z');
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

function fmtDate(iso) {
  if (!iso) return '—';
  const [y, m, d] = iso.split('-');
  return `${d}.${m}.${y}`;
}

function monthStart(iso) { return iso.slice(0, 7) + '-01'; }
function prevMonthRange(iso) {
  // Date.UTC(y, m, 0) — последний день предыдущего месяца.
  // Сдвигать обе даты через setUTCMonth нельзя: 31 октября минус месяц
  // превращается в 1 октября, и «Прошлый месяц» схлопывается в один день.
  const d = new Date(iso + 'T00:00:00Z');
  const year = d.getUTCFullYear();
  const month = d.getUTCMonth();
  const first = new Date(Date.UTC(year, month - 1, 1));
  const last = new Date(Date.UTC(year, month, 0));
  return [first.toISOString().slice(0, 10), last.toISOString().slice(0, 10)];
}

/* ---------- API ---------- */
async function api(path, opts = {}) {
  if (window.J_STATIC && window.LocalAPI) return window.LocalAPI.handle(path);
  let url = path;
  if (state.account && path.startsWith('/api/') && !path.startsWith('/api/accounts') && !path.startsWith('/api/login')) {
    url += (path.includes('?') ? '&' : '?') + 'account=' + encodeURIComponent(state.account);
  }
  const res = await fetch(url, { credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, ...opts });
  if (res.status === 401) { location.href = '/login'; throw new Error('auth'); }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) { /* ignore */ }
    throw new Error(detail);
  }
  return res.json();
}

function query() {
  const p = new URLSearchParams();
  if (state.start) p.set('start', state.start);
  if (state.end) p.set('end', state.end);
  if (!state.all && state.selected.size) p.set('campaigns', [...state.selected].join(','));
  return p;
}

/* ---------- каркас ---------- */
function setPeriod(preset) {
  state.preset = preset;
  const today = state.today || todayISO();
  if (preset === 'month') { state.start = monthStart(today); state.end = today; }
  else if (preset === 'prev_month') { [state.start, state.end] = prevMonthRange(today); }
  else if (preset === 'all') {
    // Весь загруженный период: от первого дня в базе до последнего.
    const meta = state.meta || {};
    state.start = meta.date_min || isoAdd(today, -365);
    state.end = meta.date_max || today;
  }
  else if (preset === 'custom') { /* даты из полей */ }
  else { state.end = today; state.start = isoAdd(today, -(Number(preset) - 1)); }
  if (preset !== 'custom') {
    $('#date-start').value = state.start;
    $('#date-end').value = state.end;
  }
  $$('#presets button').forEach((b) => b.classList.toggle('active', b.dataset.preset === preset));
  updatePill();
}

function updatePill() {
  const days = state.start && state.end
    ? Math.round((Date.parse(state.end) - Date.parse(state.start)) / 86400000) + 1 : 0;
  $('#pill-days').textContent = days > 0
    ? `${days} дн. · ${fmtDate(state.start)} — ${fmtDate(state.end)}` : '—';
}

const TITLES = {
  overview: ['Обзор', 'Ключевые показатели и динамика по дням'],
  campaigns: ['Кампании', 'Бюджеты, стратегии и результаты по каждой кампании'],
  targets: ['Ключи и цели', 'Ставки, статусы и эффективность таргетов'],
  terms: ['Поисковые термины', 'Реальные запросы покупателей'],
  placements: ['Размещения', 'Top of Search, Rest of Search, Product Pages'],
  products: ['Товары', 'Эффективность по ASIN'],
  changes: ['Изменения', 'Что и когда менялось в аккаунте'],
  data: ['Данные', 'Покрытие, запуски и загрузка отчётов'],
};

function setView(view) {
  state.view = view;
  $('.filters').classList.toggle('hidden', view === 'data');
  $$('#nav button').forEach((b) => b.classList.toggle('active', b.dataset.view === view));
  $('#view-title').textContent = TITLES[view][0];
  $('#view-sub').textContent = TITLES[view][1];
  render();
}

/* ---------- skeleton ---------- */
function skeleton() {
  return `<div class="kpis">${Array.from({ length: 6 }).map(() => `
    <div class="kpi"><div class="skeleton" style="width:55%"></div>
    <div class="skeleton" style="width:70%;height:22px;margin-top:12px"></div>
    <div class="skeleton" style="width:40%;margin-top:10px"></div></div>`).join('')}</div>
  <div class="card"><div class="card-body">${Array.from({ length: 7 }).map(() => `
    <div class="skeleton" style="height:16px;margin:10px 0"></div>`).join('')}</div></div>`;
}

function notice(text) { return `<div class="notice" style="margin-top:14px">${text}</div>`; }

/* ---------- KPI ---------- */
function delta(cur, prev, { inverse = false, pp = false, suffix = '%' } = {}) {
  if (cur === null || cur === undefined || prev === null || prev === undefined || prev === 0) {
    return '';
  }
  let value, text;
  if (pp) { value = cur - prev; text = `${value > 0 ? '+' : ''}${fmtNum(value, 2)} п.п.`; }
  else { value = (cur - prev) / Math.abs(prev) * 100; text = `${value > 0 ? '+' : ''}${fmtNum(value, 1)}${suffix}`; }
  const good = inverse ? value < 0 : value > 0;
  const cls = Math.abs(value) < 0.05 ? 'flat' : (good ? 'up' : 'down');
  const arrow = Math.abs(value) < 0.05 ? '→' : (value > 0 ? '▲' : '▼');
  return `<span class="${cls}">${arrow} ${text}</span><span style="color:var(--dim)">к прошлому периоду</span>`;
}

function kpiCard({ label, value, sub, deltaHtml, metric }) {
  return `<div class="kpi">
    <div class="label">${label}</div>
    <div class="value">${value}</div>
    <div class="delta">${deltaHtml || ''}</div>
    <div class="sub">${sub || ''}</div>
    <canvas data-spark="${metric || ''}"></canvas>
  </div>`;
}

/* ---------- charts ---------- */
function prepare(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(10, rect.width * dpr);
  canvas.height = Math.max(10, rect.height * dpr);
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w: rect.width, h: rect.height };
}

function sparkline(canvas, values, color = '#ff9900') {
  const { ctx, w, h } = prepare(canvas);
  if (!values.length) return;
  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  ctx.clearRect(0, 0, w, h);
  ctx.beginPath();
  values.forEach((v, i) => {
    const x = (i / Math.max(values.length - 1, 1)) * (w - 2) + 1;
    const y = h - 2 - ((v - min) / Math.max(max - min, 1)) * (h - 5);
    i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  });
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.6;
  ctx.stroke();
  ctx.lineTo(w - 1, h);
  ctx.lineTo(1, h);
  ctx.closePath();
  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, color + '55');
  grad.addColorStop(1, color + '00');
  ctx.fillStyle = grad;
  ctx.fill();
}

function comboChart(canvas, series, mode) {
  const { ctx, w, h } = prepare(canvas);
  const padL = 54, padR = 16, padT = 16, padB = 26;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;
  ctx.clearRect(0, 0, w, h);
  if (!series.length) return;

  const defs = {
    money: [
      { key: 'spend', color: '#ff9900', type: 'bar', label: 'Расход', fmt: fmtMoney },
      { key: 'sales', color: '#4aa8ff', type: 'line', label: 'Продажи', fmt: fmtMoney },
    ],
    volume: [
      { key: 'clicks', color: '#ff9900', type: 'bar', label: 'Клики', fmt: fmtInt },
      { key: 'impressions', color: '#4aa8ff', type: 'line', label: 'Показы', fmt: fmtInt, scale: 0.35 },
    ],
    efficiency: [
      { key: 'acos', color: '#ff9900', type: 'line', label: 'ACOS, %', fmt: (v) => fmtPct(v, 1) },
      { key: 'cpc', color: '#4aa8ff', type: 'line', label: 'CPC', fmt: (v) => '$' + fmtNum(v, 2) },
    ],
  };
  const items = defs[mode] || defs.money;
  const maxBy = {};
  items.forEach((it) => {
    // 12% запаса сверху, чтобы максимальный столбец не упирался в рамку
    maxBy[it.key] = Math.max(...series.map((r) => r[it.key] || 0), 0) * (it.scale || 1) * 1.12 || 1;
  });

  // сетка
  ctx.strokeStyle = 'rgba(30,44,68,.75)';
  ctx.fillStyle = '#61758f';
  ctx.font = '10.5px Inter, sans-serif';
  ctx.textAlign = 'right';
  for (let i = 0; i <= 4; i++) {
    const y = padT + (plotH / 4) * i;
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(w - padR, y); ctx.stroke();
    const top = maxBy[items[0].key] || 0;
    const val = top - (top / 4) * i;
    ctx.fillText(mode === 'efficiency' ? fmtNum(val, 1) : (mode === 'money' ? '$' + fmtMoney0(val) : fmtInt(val)), padL - 8, y + 3.5);
  }

  const step = series.length > 1 ? plotW / series.length : plotW;
  const barW = Math.max(3, Math.min(26, step * 0.55));

  ctx.textAlign = 'center';
  const labelEvery = Math.ceil(series.length / 9);
  series.forEach((row, i) => {
    const x = padL + step * i + step / 2;
    if (i % labelEvery === 0 || i === series.length - 1) {
      const label = row.bucket.length === 7 ? row.bucket.slice(5) + '.' + row.bucket.slice(0, 4)
        : row.bucket.slice(8) + '.' + row.bucket.slice(5, 7);
      ctx.fillStyle = '#61758f';
      ctx.fillText(label, x, h - 8);
    }
    items.forEach((it) => {
      const v = (row[it.key] || 0) * (it.scale || 1);
      const max = maxBy[it.key] || 1;
      const y = padT + plotH - (v / max) * plotH;
      if (it.type === 'bar') {
        const grad = ctx.createLinearGradient(0, y, 0, padT + plotH);
        grad.addColorStop(0, it.color);
        grad.addColorStop(1, it.color + '33');
        ctx.fillStyle = grad;
        const hgt = Math.max(0, padT + plotH - y);
        roundRect(ctx, x - barW / 2, y, barW, hgt, Math.min(4, barW / 2));
        ctx.fill();
      }
    });
  });

  items.filter((it) => it.type === 'line').forEach((it) => {
    ctx.beginPath();
    series.forEach((row, i) => {
      const x = padL + step * i + step / 2;
      const max = maxBy[it.key] || 1;
      const y = padT + plotH - ((row[it.key] || 0) * (it.scale || 1) / max) * plotH;
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.strokeStyle = it.color;
    ctx.lineWidth = 2;
    ctx.lineJoin = 'round';
    ctx.stroke();
    series.forEach((row, i) => {
      const x = padL + step * i + step / 2;
      const max = maxBy[it.key] || 1;
      const y = padT + plotH - ((row[it.key] || 0) * (it.scale || 1) / max) * plotH;
      ctx.beginPath(); ctx.arc(x, y, 2.4, 0, Math.PI * 2); ctx.fillStyle = it.color; ctx.fill();
    });
  });

  canvas._meta = { series, items, padL, step, plotW, padT, plotH };
}

function roundRect(ctx, x, y, w, h, r) {
  const rr = Math.min(r, w / 2, Math.abs(h) / 2);
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.lineTo(x + w - rr, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + rr);
  ctx.lineTo(x + w, y + h);
  ctx.lineTo(x, y + h);
  ctx.lineTo(x, y + rr);
  ctx.quadraticCurveTo(x, y, x + rr, y);
  ctx.closePath();
}

function bindChartHover(canvas) {
  const tip = $('#tooltip');
  canvas.onmousemove = (ev) => {
    const meta = canvas._meta;
    if (!meta) return;
    const rect = canvas.getBoundingClientRect();
    const x = ev.clientX - rect.left;
    const i = Math.floor((x - meta.padL) / meta.step);
    if (i < 0 || i >= meta.series.length) { tip.style.display = 'none'; return; }
    const row = meta.series[i];
    tip.innerHTML = `<div class="tt-date">${esc(row.bucket)}</div>` + meta.items.map((it) =>
      `<div class="tt-row"><span>${it.label}</span><b>${it.fmt(row[it.key])}</b></div>`).join('') +
      `<div class="tt-row"><span>ACOS</span><b>${fmtPct(row.acos)}</b></div>`;
    tip.style.display = 'block';
    tip.style.left = Math.min(ev.clientX + 14, window.innerWidth - 190) + 'px';
    tip.style.top = (ev.clientY + 14) + 'px';
  };
  canvas.onmouseleave = () => { tip.style.display = 'none'; };
}

/* ---------- tables ---------- */
function sortRows(rows, key, dir) {
  if (!key) return rows;
  return [...rows].sort((a, b) => {
    const va = a[key], vb = b[key];
    if (va === null || va === undefined) return 1;
    if (vb === null || vb === undefined) return -1;
    if (typeof va === 'number' && typeof vb === 'number') return dir === 'asc' ? va - vb : vb - va;
    return dir === 'asc' ? String(va).localeCompare(String(vb), 'ru') : String(vb).localeCompare(String(va), 'ru');
  });
}

function tableHtml(id, columns, rows, opts = {}) {
  const sort = state.sort[id] || { key: opts.defaultSort, dir: opts.defaultDir || 'desc' };
  const data = sortRows(rows, sort.key, sort.dir);
  const head = columns.map((c) => {
    const arrow = sort.key === c.key ? `<span class="arrow">${sort.dir === 'asc' ? '▲' : '▼'}</span>` : '';
    return `<th class="${c.left ? 'left' : ''}" data-sort-table="${id}" data-key="${c.key}" style="${c.width ? `width:${c.width}` : ''}">${c.label}${arrow}</th>`;
  }).join('');
  const body = data.map((row) => `<tr>${columns.map((c) => {
    const cls = c.left ? 'left' : '';
    return `<td class="${cls}">${c.render ? c.render(row) : esc(row[c.key])}</td>`;
  }).join('')}</tr>`).join('');
  return `<div class="table-wrap"><table>
    <thead><tr>${head}</tr></thead>
    <tbody>${body || `<tr><td colspan="${columns.length}"><div class="empty"><b>Нет данных за период</b>Измените даты или кампанию</div></td></tr>`}</tbody>
  </table></div>`;
}

function totalsRow(columns, rows) { return null; }

function bindSorts(root, redraw) {
  $$('[data-sort-table]', root).forEach((th) => {
    th.onclick = () => {
      const id = th.dataset.sortTable;
      const key = th.dataset.key;
      const cur = state.sort[id] || {};
      state.sort[id] = { key, dir: cur.key === key && cur.dir === 'desc' ? 'asc' : 'desc' };
      redraw();
    };
  });
}

function nameCell(name, stateVal, sub) {
  return `<div class="cell-name"><span class="dot ${esc(stateVal || '')}"></span>
    <div><div class="nm">${esc(name || '—')}</div>${sub ? `<div class="mono">${esc(sub)}</div>` : ''}</div></div>`;
}

function barCell(value, max, text) {
  const pct = max ? Math.min(100, (value / max) * 100) : 0;
  return `<div class="bar-cell"><div class="bar" style="width:${pct}%"></div><span class="money">${text}</span></div>`;
}

function acosCell(row) {
  if (row.acos === null || row.acos === undefined) return '<span style="color:var(--dim)">—</span>';
  const cls = row.acos > 40 ? 'acos-bad' : (row.acos < 20 ? 'acos-good' : '');
  return `<span class="${cls}">${fmtPct(row.acos, 1)}</span>`;
}

/* ---------- views ---------- */
const views = {};

views.overview = async () => {
  const p = query();
  const grain = (state.end && state.start && (Date.parse(state.end) - Date.parse(state.start)) > 62 * 86400000) ? 'week' : 'day';
  p.set('grain', grain);
  const data = await api('/api/overview?' + p);
  const cur = data.current, prev = data.previous;
  const s = data.series;

  const cards = [
    kpiCard({ label: 'Расход', value: '$' + fmtMoney(cur.spend), metric: 'spend',
      deltaHtml: delta(cur.spend, prev.spend, { inverse: true }), sub: `CPC $${fmtNum(cur.cpc)}` }),
    kpiCard({ label: 'Продажи', value: '$' + fmtMoney(cur.sales), metric: 'sales',
      deltaHtml: delta(cur.sales, prev.sales), sub: `${fmtInt(cur.units)} шт.` }),
    kpiCard({ label: 'ACOS', value: fmtPct(cur.acos, 1), metric: 'acos',
      deltaHtml: delta(cur.acos, prev.acos, { inverse: true, pp: true }), sub: `ROAS ${fmtRoas(cur.roas)}` }),
    kpiCard({ label: 'Заказы', value: fmtInt(cur.orders), metric: 'orders',
      deltaHtml: delta(cur.orders, prev.orders), sub: `CVR ${fmtPct(cur.cvr, 1)}` }),
    kpiCard({ label: 'Клики', value: fmtInt(cur.clicks), metric: 'clicks',
      deltaHtml: delta(cur.clicks, prev.clicks), sub: `CTR ${fmtPct(cur.ctr)}` }),
    kpiCard({ label: 'Показы', value: fmtInt(cur.impressions), metric: 'impressions',
      deltaHtml: delta(cur.impressions, prev.impressions), sub: `CPM $${fmtNum(cur.impressions ? cur.spend / cur.impressions * 1000 : null)}` }),
  ].join('');

  const spending = data.campaigns.filter((c) => c.spend > 0);
  const tops = spending.slice(0, 8);
  const maxSpend = Math.max(...tops.map((c) => c.spend), 1);
  const totalSpend = spending.reduce((a, c) => a + c.spend, 0) || 1;

  return `
    <div class="kpis">${cards}</div>
    ${data.data_range.min && data.data_range.min > data.period.start ? notice(`<b>Данные начинаются с ${fmtDate(data.data_range.min)}.</b> За более ранние дни отчётов в базе нет — они не показаны как нули.`) : ''}
    ${cur.orders === 0 ? notice('<b>В выбранном периоде нет заказов.</b> Это может быть нормально для узкого окна; проверьте даты и кампании.') : ''}
    <div class="grid-2">
      <div class="card">
        <div class="card-head">
          <div><div class="card-title">Динамика</div><div class="card-sub">${grain === 'week' ? 'по неделям' : 'по дням'}</div></div>
          <div class="seg" id="chart-modes">
            <button data-mode="money" class="${state.chart === 'money' ? 'active' : ''}">Деньги</button>
            <button data-mode="volume" class="${state.chart === 'volume' ? 'active' : ''}">Объём</button>
            <button data-mode="efficiency" class="${state.chart === 'efficiency' ? 'active' : ''}">Эффективность</button>
          </div>
        </div>
        <div class="card-body">
          <div class="chart-wrap"><canvas id="main-chart"></canvas></div>
          <div class="legend" style="margin-top:8px">${state.chart === 'money'
            ? '<span><i style="background:#ff9900"></i>Расход</span><span><i class="line" style="background:#4aa8ff"></i>Продажи</span>'
            : state.chart === 'volume'
              ? '<span><i style="background:#ff9900"></i>Клики</span><span><i class="line" style="background:#4aa8ff"></i>Показы (×0,35)</span>'
              : '<span><i class="line" style="background:#ff9900"></i>ACOS, %</span><span><i class="line" style="background:#4aa8ff"></i>CPC</span>'}</div>
        </div>
      </div>
      <div class="card">
        <div class="card-head"><div><div class="card-title">Куда уходит бюджет</div><div class="card-sub">топ кампаний по расходу</div></div></div>
        <div class="card-body" style="padding-top:6px">
          ${tops.map((c) => `
            <div style="display:flex;justify-content:space-between;gap:10px;padding:8px 0;border-bottom:1px solid var(--line-soft)">
              <div style="min-width:0"><div style="font-size:12.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(c.name || c.campaign_id)}</div>
                <div class="mono">${fmtInt(c.clicks)} ${plural(c.clicks, ['клик', 'клика', 'кликов'])} · ${fmtInt(c.orders)} ${plural(c.orders, ['заказ', 'заказа', 'заказов'])} · ACOS ${fmtPct(c.acos, 0)}</div></div>
              <div style="text-align:right;white-space:nowrap"><div class="money">$${fmtMoney(c.spend)}</div>
                <div style="font-size:11px;color:var(--dim)">${fmtPct(c.spend / totalSpend * 100, 0)} расхода</div></div>
            </div>`).join('') || '<div class="empty">За период расходов не было</div>'}
        </div>
      </div>
    </div>
    <div class="card">
      <div class="card-head"><div><div class="card-title">Кампании</div><div class="card-sub">сортировка по расходу</div></div></div>
      ${tableHtml('ov-campaigns', [
        { key: 'name', label: 'Кампания', left: true, render: (r) => nameCell(r.name, r.state, r.campaign_id) },
        { key: 'budget', label: 'Бюджет', render: (r) => r.budget ? '$' + fmtMoney0(r.budget) : '—' },
        { key: 'impressions', label: 'Показы', render: (r) => fmtInt(r.impressions) },
        { key: 'clicks', label: 'Клики', render: (r) => fmtInt(r.clicks) },
        { key: 'ctr', label: 'CTR', render: (r) => fmtPct(r.ctr) },
        { key: 'spend', label: 'Расход', render: (r) => '$' + fmtMoney(r.spend) },
        { key: 'orders', label: 'Заказы', render: (r) => fmtInt(r.orders) },
        { key: 'sales', label: 'Продажи', render: (r) => '$' + fmtMoney(r.sales) },
        { key: 'acos', label: 'ACOS', render: acosCell },
        { key: 'roas', label: 'ROAS', render: (r) => fmtRoas(r.roas) },
      ], data.campaigns, { defaultSort: 'spend' })}
    </div>`;
};

views.campaigns = async () => {
  const rows = await api('/api/campaigns?' + query());
  const maxSpend = Math.max(...rows.map((r) => r.spend || 0), 1);
  const total = rows.reduce((a, r) => ({
    spend: a.spend + (r.spend || 0), sales: a.sales + (r.sales || 0),
    clicks: a.clicks + (r.clicks || 0), orders: a.orders + (r.orders || 0),
    impressions: a.impressions + (r.impressions || 0),
  }), { spend: 0, sales: 0, clicks: 0, orders: 0, impressions: 0 });

  return `
    <div class="card" style="margin-top:16px">
      <div class="table-tools">
        <input class="search" id="cmp-search" placeholder="Поиск по названию или ID…">
        <span class="pill">${rows.length} кампаний</span>
        <span class="pill">Расход $${fmtMoney(total.spend)}</span>
        <span class="pill">Продажи $${fmtMoney(total.sales)}</span>
        <span class="pill">ACOS ${fmtPct(total.sales ? total.spend / total.sales * 100 : null, 1)}</span>
      </div>
      <div id="cmp-table">${tableHtml('campaigns', [
        { key: 'name', label: 'Кампания', left: true, render: (r) => nameCell(r.name, r.state, r.campaign_id) },
        { key: 'state', label: 'Статус', left: true, render: (r) => `<span class="tag ${r.state === 'ENABLED' ? 'ok' : ''}">${esc(r.state)}</span>` },
        { key: 'budget', label: 'Бюджет', render: (r) => r.budget ? '$' + fmtMoney0(r.budget) : '—' },
        { key: 'bid_strategy', label: 'Стратегия', left: true, render: (r) => `<span class="mono">${esc(r.bid_strategy || '—')}</span>${r.adj_top ? `<div class="mono">TOS +${fmtPct(r.adj_top, 0)}</div>` : ''}` },
        { key: 'impressions', label: 'Показы', render: (r) => fmtInt(r.impressions) },
        { key: 'clicks', label: 'Клики', render: (r) => fmtInt(r.clicks) },
        { key: 'ctr', label: 'CTR', render: (r) => fmtPct(r.ctr) },
        { key: 'spend', label: 'Расход', render: (r) => barCell(r.spend, maxSpend, '$' + fmtMoney(r.spend)) },
        { key: 'cpc', label: 'CPC', render: (r) => '$' + fmtNum(r.cpc) },
        { key: 'orders', label: 'Заказы', render: (r) => fmtInt(r.orders) },
        { key: 'sales', label: 'Продажи', render: (r) => '$' + fmtMoney(r.sales) },
        { key: 'acos', label: 'ACOS', render: acosCell },
        { key: 'roas', label: 'ROAS', render: (r) => fmtRoas(r.roas) },
      ], rows, { defaultSort: 'spend' })}</div>
    </div>`;
};

views.targets = async () => {
  const kind = state.targetKind || 'positive';
  const p = query();
  p.set('kind', kind);
  if (state.targetSearch) p.set('q', state.targetSearch);
  const rows = await api('/api/targets?' + p);
  const maxSpend = Math.max(...rows.map((r) => r.spend || 0), 1);

  return `
    <div class="card" style="margin-top:16px">
      <div class="table-tools">
        <div class="seg" id="target-kind">
          <button data-kind="positive" class="${kind === 'positive' ? 'active' : ''}">Положительные</button>
          <button data-kind="negative" class="${kind === 'negative' ? 'active' : ''}">Негативы</button>
          <button data-kind="all" class="${kind === 'all' ? 'active' : ''}">Все</button>
        </div>
        <input class="search" id="tgt-search" placeholder="Таргет, ключ, тип совпадения…" value="${esc(state.targetSearch || '')}">
        <span class="pill">${rows.length} объектов</span>
      </div>
      <div id="tgt-table">${tableHtml('targets', [
        { key: 'targeting', label: 'Таргет / ключ', left: true, render: (r) => `<div style="max-width:300px"><div style="overflow:hidden;text-overflow:ellipsis">${esc(r.targeting || '—')}</div><span class="tag${r.is_negative ? ' neg' : ''}">${esc(r.match_type || r.kind || '—')}</span></div>` },
        { key: 'campaign_name', label: 'Кампания', left: true, render: (r) => `<span style="color:var(--muted);display:inline-block;max-width:190px;overflow:hidden;text-overflow:ellipsis;vertical-align:bottom">${esc(r.campaign_name || r.campaign_id)}</span>` },
        { key: 'state', label: 'Статус', left: true, render: (r) => `<span class="dot ${esc(r.state)}" style="display:inline-block;margin-right:5px"></span>${esc(r.state)}` },
        { key: 'bid', label: 'Ставка', render: (r) => {
            if (r.is_negative) return '<span style="color:var(--dim)">—</span>';
            if (r.bid !== null && r.bid !== undefined) return '$' + fmtNum(r.bid, 3);
            if (r.effective_bid !== null && r.effective_bid !== undefined) return `<span style="color:var(--dim)">гр. $${fmtNum(r.effective_bid, 3)}</span>`;
            return '<span style="color:var(--dim)">не задана</span>';
          } },
        { key: 'impressions', label: 'Показы', render: (r) => fmtInt(r.impressions) },
        { key: 'clicks', label: 'Клики', render: (r) => fmtInt(r.clicks) },
        { key: 'spend', label: 'Расход', render: (r) => barCell(r.spend, maxSpend, '$' + fmtMoney(r.spend)) },
        { key: 'orders', label: 'Заказы', render: (r) => fmtInt(r.orders) },
        { key: 'sales', label: 'Продажи', render: (r) => '$' + fmtMoney(r.sales) },
        { key: 'acos', label: 'ACOS', render: acosCell },
      ], rows, { defaultSort: 'spend' })}</div>
      ${kind === 'negative' ? `<div class="card-body" style="padding-top:0"><div class="card-sub">Негативы не показывают рекламу, поэтому метрик у них нет. Ставка для негатива не применяется; колонка «Эффект.» пустая.</div></div>` : ''}
    </div>`;
};

views.terms = async () => {
  const p = query();
  if (state.termSearch) p.set('q', state.termSearch);
  if (state.termOrders) p.set('orders_only', 'true');
  const rows = await api('/api/terms?' + p);
  const maxSpend = Math.max(...rows.map((r) => r.spend || 0), 1);

  return `
    <div class="card" style="margin-top:16px">
      <div class="table-tools">
        <input class="search" id="term-search" placeholder="Поиск по термину…" value="${esc(state.termSearch || '')}">
        <label class="check"><input type="checkbox" id="term-orders" ${state.termOrders ? 'checked' : ''}> только с заказами</label>
        <span class="pill">${rows.length} терминов</span>
      </div>
      <div id="term-table">${tableHtml('terms', [
        { key: 'search_term', label: 'Поисковый термин', left: true, render: (r) => `<div style="max-width:330px;overflow:hidden;text-overflow:ellipsis">${esc(r.search_term || '—')}</div>` },
        { key: 'match_type', label: 'Совпадение', left: true, render: (r) => `<span class="tag">${esc(r.match_type || '—')}</span>` },
        { key: 'targeting', label: 'Таргет', left: true, render: (r) => `<span class="mono" style="max-width:220px;display:inline-block;overflow:hidden;text-overflow:ellipsis">${esc(r.targeting || '—')}</span>` },
        { key: 'impressions', label: 'Показы', render: (r) => fmtInt(r.impressions) },
        { key: 'clicks', label: 'Клики', render: (r) => fmtInt(r.clicks) },
        { key: 'ctr', label: 'CTR', render: (r) => fmtPct(r.ctr) },
        { key: 'spend', label: 'Расход', render: (r) => barCell(r.spend, maxSpend, '$' + fmtMoney(r.spend)) },
        { key: 'orders', label: 'Заказы', render: (r) => fmtInt(r.orders) },
        { key: 'sales', label: 'Продажи', render: (r) => '$' + fmtMoney(r.sales) },
        { key: 'acos', label: 'ACOS', render: acosCell },
        { key: 'cvr', label: 'CVR', render: (r) => fmtPct(r.cvr, 1) },
      ], rows, { defaultSort: 'spend' })}</div>
    </div>`;
};

views.placements = async () => {
  const rows = await api('/api/placements?' + query());
  const byType = {};
  rows.forEach((r) => {
    const t = byType[r.placement] ||= { placement: r.placement, impressions: 0, clicks: 0, spend: 0, orders: 0, sales: 0, units: 0 };
    ['impressions', 'clicks', 'spend', 'orders', 'sales', 'units'].forEach((k) => { t[k] += r[k] || 0; });
  });
  const totals = Object.values(byType).map((t) => ({
    ...t, ctr: t.impressions ? t.clicks / t.impressions * 100 : null,
    cpc: t.clicks ? t.spend / t.clicks : null,
    acos: t.sales ? t.spend / t.sales * 100 : null,
    roas: t.spend ? t.sales / t.spend : null,
  })).sort((a, b) => b.spend - a.spend);
  const maxSpend = Math.max(...rows.map((r) => r.spend || 0), 1);

  return `
    <div class="kpis" style="grid-template-columns:repeat(3,minmax(0,1fr))">
      ${totals.map((t) => `<div class="kpi">
        <div class="label">${esc(t.placement)}</div>
        <div class="value">$${fmtMoney(t.spend)}</div>
        <div class="delta"><span style="color:var(--muted)">${fmtInt(t.clicks)} ${plural(t.clicks, ['клик','клика','кликов'])} · ${fmtInt(t.orders)} ${plural(t.orders, ['заказ','заказа','заказов'])}</span></div>
        <div class="sub">ACOS ${fmtPct(t.acos, 1)} · ROAS ${fmtRoas(t.roas)}</div>
      </div>`).join('') || '<div class="empty">Нет данных о размещениях</div>'}
    </div>
    <div class="card">
      <div class="card-head"><div><div class="card-title">По кампаниям и размещениям</div></div></div>
      ${tableHtml('placements', [
        { key: 'campaign_name', label: 'Кампания', left: true, render: (r) => nameCell(r.campaign_name || r.campaign_id, '', r.campaign_id) },
        { key: 'placement', label: 'Размещение', left: true, render: (r) => `<span class="tag">${esc(r.placement)}</span>` },
        { key: 'impressions', label: 'Показы', render: (r) => fmtInt(r.impressions) },
        { key: 'clicks', label: 'Клики', render: (r) => fmtInt(r.clicks) },
        { key: 'ctr', label: 'CTR', render: (r) => fmtPct(r.ctr) },
        { key: 'spend', label: 'Расход', render: (r) => barCell(r.spend, maxSpend, '$' + fmtMoney(r.spend)) },
        { key: 'cpc', label: 'CPC', render: (r) => '$' + fmtNum(r.cpc) },
        { key: 'orders', label: 'Заказы', render: (r) => fmtInt(r.orders) },
        { key: 'sales', label: 'Продажи', render: (r) => '$' + fmtMoney(r.sales) },
        { key: 'acos', label: 'ACOS', render: acosCell },
      ], rows, { defaultSort: 'spend' })}
    </div>`;
};

views.products = async () => {
  const rows = await api('/api/products?' + query());
  const maxSpend = Math.max(...rows.map((r) => r.spend || 0), 1);
  return `
    <div class="card" style="margin-top:16px">
      <div class="card-head"><div><div class="card-title">ASIN</div><div class="card-sub">товары, которые рекламировались</div></div></div>
      ${tableHtml('products', [
        { key: 'asin', label: 'ASIN', left: true, render: (r) => `<span class="mono" style="color:#cfe3ff">${esc(r.asin)}</span>` },
        { key: 'campaign_id', label: 'Кампания', left: true, render: (r) => `<span style="color:var(--muted)">${esc(r.campaign_id)}</span>` },
        { key: 'impressions', label: 'Показы', render: (r) => fmtInt(r.impressions) },
        { key: 'clicks', label: 'Клики', render: (r) => fmtInt(r.clicks) },
        { key: 'ctr', label: 'CTR', render: (r) => fmtPct(r.ctr) },
        { key: 'spend', label: 'Расход', render: (r) => barCell(r.spend, maxSpend, '$' + fmtMoney(r.spend)) },
        { key: 'orders', label: 'Заказы', render: (r) => fmtInt(r.orders) },
        { key: 'sales', label: 'Продажи', render: (r) => '$' + fmtMoney(r.sales) },
        { key: 'units', label: 'Шт.', render: (r) => fmtInt(r.units) },
        { key: 'acos', label: 'ACOS', render: acosCell },
      ], rows, { defaultSort: 'spend' })}
    </div>`;
};

views.changes = async () => {
  const p = query();
  if (state.changeSearch) p.set('q', state.changeSearch);
  const rows = await api('/api/changes?' + p);
  const srcLabel = {
    AMAZON_HISTORY: 'Amazon', SNAPSHOT_DIFF: 'снимок', AGENT_WRITE: 'агент', IMPORT: 'импорт',
  };

  const groups = {};
  rows.forEach((r) => { (groups[r.change_date] ||= []).push(r); });

  const html = Object.entries(groups).map(([day, items]) => `
    <div style="margin-top:18px">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
        <span style="font-weight:650">${esc(day)}</span><span class="pill">${items.length}</span>
        <span style="flex:1;height:1px;background:var(--line-soft)"></span>
      </div>
      <div class="timeline">${items.map((r) => `
        <div class="chg">
          <div class="when"><b>${esc((r.change_ts || '').slice(11, 19) || '—')}</b>${r.date_precision === 'EXACT_TS' ? 'UTC' : 'снимок'}</div>
          <div><span class="tag">${esc(r.entity_type || '—')}</span></div>
          <div class="what">
            <span class="obj">${esc(r.entity_label || r.entity_id || '—')}</span>
            <span style="color:var(--dim)"> · </span><span>${esc(r.field)}</span>
            <div class="vals"><span class="old">${esc(r.old_value || '—')}</span><span class="arr">→</span><span class="new">${esc(r.new_value || '—')}</span></div>
          </div>
          <div class="src">${esc(srcLabel[r.source] || r.source || '')}${r.actor && r.actor !== 'UNKNOWN' ? ' · ' + esc(r.actor) : ''}</div>
        </div>`).join('')}</div>
    </div>`).join('');

  return `
    <div class="card" style="margin-top:16px">
      <div class="table-tools">
        <input class="search" id="chg-search" placeholder="Поиск: поле, объект, кампания…" value="${esc(state.changeSearch || '')}">
        <span class="pill">${rows.length} событий</span>
      </div>
      <div class="card-body">
        ${rows.length ? html : `<div class="empty"><b>За выбранный период изменений нет</b>
          Журнал пополняется при каждой синхронизации: история Amazon (90 дней) и сравнение снимков настроек.<br>
          Если период попадает на дни до первого запуска — записей за них не будет.</div>`}
      </div>
    </div>
    <div class="card"><div class="card-body"><div class="card-sub">
      <b style="color:#cfdcf0">Что здесь есть:</b> бюджет, статус, стратегия, надбавки размещений, ставки и ключи — по событиям Amazon и по диффу снимков.<br>
      <b style="color:#cfdcf0">Чего может не быть:</b> точное время правки ставки внутри дня, если Amazon не отдал событие; такие записи помечены как «снимок».
    </div></div></div>`;
};

views.data = async () => {
  const [meta, cov] = await Promise.all([api('/api/meta'), api('/api/coverage')]);
  const byDay = {};
  cov.coverage.forEach((r) => {
    const cur = byDay[r.date] ||= { ok: 0, partial: 0, total: 0 };
    cur.total += 1;
    if (r.status === 'OK') cur.ok += 1; else cur.partial += 1;
  });
  // Показываем весь выбранный период: дни без загрузки — красные, а не выпадают из сетки.
  const days = [];
  let cursor = state.start;
  while (cursor && state.end && cursor <= state.end) {
    const s = byDay[cursor] || { ok: 0, partial: 0, total: 0 };
    days.push([cursor, s]);
    cursor = isoAdd(cursor, 1);
  }
  days.reverse().splice(120);
  const lastRun = cov.runs[0];

  return `
    <div class="kpis" style="grid-template-columns:repeat(4,minmax(0,1fr));margin-top:16px">
      <div class="kpi"><div class="label">Период данных</div><div class="value" style="font-size:17px;white-space:nowrap">${fmtDate(meta.date_min)} → ${fmtDate(meta.date_max)}</div><div class="sub">дней в базе: ${fmtInt(meta.coverage_days || (meta.date_min && meta.date_max ? Math.round((Date.parse(meta.date_max) - Date.parse(meta.date_min)) / 86400000) + 1 : 0))}</div></div>
      <div class="kpi"><div class="label">Строк фактов</div><div class="value">${fmtInt(meta.counts.fact_campaign_day)}</div><div class="sub">campaign-level за все дни</div></div>
      <div class="kpi"><div class="label">Объекты</div><div class="value">${fmtInt(meta.targets)}<span style="font-size:14px;color:var(--muted)"> / ${fmtInt(meta.negatives)}</span></div><div class="sub">таргетов / негативов</div></div>
      <div class="kpi"><div class="label">События журнала</div><div class="value">${fmtInt(meta.counts.changes)}</div><div class="sub">${meta.counts.changes ? 'изменения настроек и история Amazon' : 'появятся после следующей синхронизации'}</div></div>
    </div>

    <div class="card">
      <div class="card-head"><div><div class="card-title">Как обновляются данные</div>
        <div class="card-sub">вручную, по запросу владельца</div></div></div>
      <div class="card-body">
        <div class="notice" style="border-color:rgba(74,168,255,.3);background:rgba(74,168,255,.06);color:#c9e2ff">
          Сайт только читает базу и ничего не меняет в рекламе. Свежие отчёты подтягивает агент,
          когда владелец просит синхронизацию: кнопки обновления здесь нет намеренно.
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-head"><div><div class="card-title">Покрытие по дням</div><div class="card-sub">зелёный — все датасеты OK, жёлтый — есть PARTIAL, красный — пропуск</div></div></div>
      <div class="card-body">
        <div class="cov-grid">${days.map(([d, s]) => `
          <div class="cov-day ${s.total === 0 ? 'missing' : (s.ok === s.total ? 'ok' : 'partial')}" title="${esc(d)}: ${s.total === 0 ? 'данных нет' : s.ok + '/' + s.total + ' датасетов OK'}">${esc(d.slice(8))}</div>`).join('')}</div>
        <div class="cov-legend"><span><i style="background:rgba(52,211,153,.55)"></i>OK</span><span><i style="background:rgba(251,191,36,.6)"></i>PARTIAL</span><span><i style="background:rgba(248,113,113,.55)"></i>пропуск</span></div>
      </div>
    </div>

    <div class="card">
      <div class="card-head"><div><div class="card-title">Последние запуски</div></div></div>
      ${tableHtml('runs', [
        { key: 'run_id', label: 'Run', left: true, render: (r) => `<span class="mono">${esc(r.run_id)}</span>` },
        { key: 'started_at', label: 'Начало (UTC)', left: true, render: (r) => esc((r.started_at || '').replace('T', ' ')) },
        { key: 'finished_at', label: 'Конец (UTC)', left: true, render: (r) => esc((r.finished_at || '').replace('T', ' ')) },
        { key: 'status', label: 'Статус', left: true, render: (r) => `<span class="tag ${r.status === 'OK' ? 'ok' : ''}">${esc(r.status)}</span>` },
        { key: 'files', label: 'Файлов', render: (r) => fmtInt(r.files) },
        { key: 'rows', label: 'Строк', render: (r) => fmtInt(r.rows) },
        { key: 'source', label: 'Источник', left: true, render: (r) => esc(r.source || '') },
      ], cov.runs, { defaultSort: 'started_at' })}
    </div>`;
};

/* ---------- render ---------- */
function subtitle() {
  const base = TITLES[state.view][1];
  const max = state.meta && state.meta.date_max;
  if (!max) return base;
  const tail = max === todayISO() ? `данные по ${fmtDate(max)}` : `<b style="color:#ffd48a">данные по ${fmtDate(max)}</b>`;
  return `${base} · ${tail}`;
}

async function render() {
  const token = ++state.token;
  $('#view-sub').innerHTML = subtitle();
  const view = $('#view');
  view.innerHTML = skeleton();
  try {
    const html = await views[state.view]();
    if (token !== state.token) return;
    view.innerHTML = html;
    afterRender();
  } catch (err) {
    if (token !== state.token) return;
    view.innerHTML = `<div class="card"><div class="card-body"><div class="empty"><b>Не удалось загрузить</b>${esc(err.message)}</div></div></div>`;
  }
}

function afterRender() {
  if (state.view === 'overview') {
    const canvas = $('#main-chart');
    if (canvas) {
      const data = canvas._data || null;
      api('/api/overview?' + query() + '&grain=' + ((Date.parse(state.end) - Date.parse(state.start)) > 62 * 86400000 ? 'week' : 'day'))
        .then((d) => {
          comboChart(canvas, d.series, state.chart);
          bindChartHover(canvas);
          $$('[data-spark]').forEach((c) => {
            const key = c.dataset.spark;
            const color = key === 'sales' || key === 'impressions' ? '#4aa8ff' : (key === 'acos' ? '#f87171' : '#ff9900');
            const values = d.series.map((r) => r[key] || 0);
            if (values.every((v) => !v)) return;
            sparkline(c, values, color);
          });
        });
    }
    $$('#chart-modes button').forEach((b) => {
      b.onclick = () => {
        state.chart = b.dataset.mode;
        $$('#chart-modes button').forEach((x) => x.classList.toggle('active', x === b));
        render();
      };
    });
    bindSorts($('#view'), render);
  }

  if (state.view === 'campaigns') {
    const input = $('#cmp-search');
    let timer;
    input.oninput = () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        const q = input.value.toLowerCase();
        $$('#cmp-table tbody tr').forEach((tr) => {
          tr.style.display = tr.textContent.toLowerCase().includes(q) ? '' : 'none';
        });
      }, 120);
    };
    bindSorts($('#view'), render);
  }

  if (state.view === 'targets') {
    $$('#target-kind button').forEach((b) => {
      b.onclick = () => { state.targetKind = b.dataset.kind; render(); };
    });
    const input = $('#tgt-search');
    let timer;
    input.oninput = () => {
      clearTimeout(timer);
      timer = setTimeout(() => { state.targetSearch = input.value.trim(); render(); }, 350);
    };
    bindSorts($('#view'), render);
  }

  if (state.view === 'terms') {
    const input = $('#term-search');
    let timer;
    input.oninput = () => {
      clearTimeout(timer);
      timer = setTimeout(() => { state.termSearch = input.value.trim(); render(); }, 350);
    };
    $('#term-orders').onchange = (e) => { state.termOrders = e.target.checked; render(); };
    bindSorts($('#view'), render);
  }

  if (state.view === 'changes') {
    const input = $('#chg-search');
    let timer;
    input.oninput = () => {
      clearTimeout(timer);
      timer = setTimeout(() => { state.changeSearch = input.value.trim(); render(); }, 350);
    };
  }

  if (['overview', 'products', 'placements'].includes(state.view)) bindSorts($('#view'), render);

}

/* ---------- meta / picker ---------- */
async function loadMeta() {
  state.meta = await api('/api/meta');
  const acct = state.meta.account;
  state.account = acct.id;
  localStorage.setItem('jd_account', acct.id);
  const select = $('#account-select');
  select.innerHTML = (state.meta.accounts || [{ id: acct.id, label: acct.name }])
    .map((a) => `<option value="${esc(a.id)}" ${a.id === acct.id ? 'selected' : ''}>${esc(a.label)}</option>`)
    .join('');
  select.onchange = async () => {
    state.account = select.value;
    localStorage.setItem('jd_account', select.value);
    state.selected.clear();
    state.all = true;
    await loadMeta();
    render();
  };
  $('#acct-meta').textContent = `${state.meta.campaigns.length} кампаний · данные по ${acct.data_through || '—'}`;
  $('#nav-changes').textContent = state.meta.counts.changes || '';
  if (window.J_STATIC && state.meta.date_max) {
    // В статической версии «сегодня» — последний день с данными.
    state.today = state.meta.date_max;
    if (!['custom'].includes(state.preset)) setPeriod(state.preset);
  }
  buildPicker();
}

function buildPicker() {
  const list = $('#picker-list');
  const camps = state.meta.campaigns;
  list.innerHTML = camps.map((c) => `
    <label class="picker-item">
      <input type="checkbox" value="${esc(c.campaign_id)}" ${state.all || state.selected.has(c.campaign_id) ? 'checked' : ''} ${state.all ? 'disabled' : ''}>
      <div><div class="nm">${esc(c.name || c.campaign_id)}</div><div class="id">${esc(c.campaign_id)} · ${esc(c.state)} · $${fmtMoney0(c.budget)}</div></div>
    </label>`).join('');
  $$('#picker-list input').forEach((cb) => {
    cb.onchange = () => {
      if (cb.checked) state.selected.add(cb.value); else state.selected.delete(cb.value);
      if (!state.selected.size) { state.all = true; $('#picker-all').checked = true; buildPicker(); }
      updatePickerLabel();
    };
  });
  $('#picker-all').checked = state.all;
  updatePickerLabel();
}

function updatePickerLabel() {
  const total = state.meta ? state.meta.campaigns.length : 0;
  $('#picker-label').textContent = state.all ? 'Все кампании'
    : (state.selected.size === 1 ? (state.meta.campaigns.find((c) => c.campaign_id === [...state.selected][0])?.name || '1 кампания')
      : `${state.selected.size} из ${total}`);
}

/* ---------- bindings ---------- */
function bindChrome() {
  $$('#nav button').forEach((b) => { b.onclick = () => setView(b.dataset.view); });
  $$('#presets button').forEach((b) => {
    b.onclick = () => { setPeriod(b.dataset.preset); if (b.dataset.preset === 'custom') $('#date-start').focus(); render(); };
  });
  $('#date-start').onchange = () => {
    state.start = $('#date-start').value; state.end = $('#date-end').value;
    if (state.start > state.end) { state.end = state.start; $('#date-end').value = state.end; }
    state.preset = 'custom'; $$('#presets button').forEach((x) => x.classList.toggle('active', x.dataset.preset === 'custom'));
    updatePill(); render();
  };
  $('#date-end').onchange = () => {
    state.end = $('#date-end').value; state.start = $('#date-start').value;
    if (state.start > state.end) { state.start = state.end; $('#date-start').value = state.start; }
    state.preset = 'custom'; $$('#presets button').forEach((x) => x.classList.toggle('active', x.dataset.preset === 'custom'));
    updatePill(); render();
  };

  const panel = $('#picker-panel');
  $('#picker-btn').onclick = (e) => { e.stopPropagation(); panel.classList.toggle('open'); };
  panel.onclick = (e) => e.stopPropagation();
  document.addEventListener('click', () => panel.classList.remove('open'));
  $('#picker-all').onchange = (e) => {
    state.all = e.target.checked;
    if (state.all) state.selected.clear();
    buildPicker();
  };
  $('#picker-clear').onclick = () => { state.all = true; state.selected.clear(); buildPicker(); render(); };
  $('#picker-apply').onclick = () => { panel.classList.remove('open'); render(); };
  $('#picker-search').oninput = (e) => {
    const q = e.target.value.toLowerCase();
    $$('#picker-list .picker-item').forEach((el) => { el.style.display = el.textContent.toLowerCase().includes(q) ? '' : 'none'; });
  };
  window.addEventListener('resize', () => {
    if (state.view === 'overview') {
      const canvas = $('#main-chart');
      if (canvas && canvas._meta) {
        comboChart(canvas, canvas._meta.series, state.chart);
        $$('[data-spark]').forEach((c) => {
          const key = c.dataset.spark;
          const color = key === 'sales' || key === 'impressions' ? '#4aa8ff' : (key === 'acos' ? '#f87171' : '#ff9900');
          sparkline(c, [...canvas._meta.series].map((r) => r[key] || 0), color);
        });
      }
    }
  });
}

async function boot() {
  bindChrome();
  setPeriod('7');
  try {
    await loadMeta();
    render();
  } catch (err) {
    $('#view').innerHTML = `<div class="card"><div class="card-body"><div class="empty"><b>Нет соединения с базой</b>${esc(err.message)}</div></div></div>`;
  }
}

document.addEventListener('DOMContentLoaded', boot);
