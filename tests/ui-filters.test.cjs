// Фильтры «Ключей и целей» и журнала: серверный путь (app.js) и статический (local.js).
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {readFileSync} = require('node:fs');

const stubEl = () => ({
  innerHTML:'', textContent:'', value:'', checked:false, dataset:{}, style:{},
  classList:{toggle(){},add(){},remove(){},contains:()=>false},
  setAttribute(){}, focus(){}, closest:()=>null,
});

function ui(appData) {
  const doc = {
    addEventListener(){}, body:stubEl(),
    querySelector:()=>stubEl(), querySelectorAll:()=>[],
  };
  const ctx = vm.createContext({window:{matchMedia:()=>({matches:false})}, localStorage:{getItem:()=>null},
    sessionStorage:{getItem:()=>null,setItem(){},removeItem(){}}, document:doc, Intl, URLSearchParams});
  vm.runInContext(readFileSync(require.resolve('../app/static/app.js'),'utf8'),ctx);
  const run = expr => vm.runInContext(expr, ctx);
  run(`api = async (path) => { globalThis.lastPath = path; return globalThis.APIDATA; };`);
  run('globalThis.APIDATA = ' + JSON.stringify(appData));
  return run;
}

const TARGET_ROWS = [{
  target_id:'k1', campaign_id:'c1', campaign_name:'Blue', targeting:'крем', match_type:'EXACT',
  state:'ENABLED', bid:0.3, effective_bid:0.3, is_negative:0, created_at:'2026-10-01T09:00:00Z',
  impressions:0, clicks:0, spend:0, orders:0, sales:0, units:0, idle:1,
  last_field:'STATUS', last_old:'ENABLED', last_new:'PAUSED', last_date:'2026-11-21',
  last_ts:'2026-11-21T08:00:00+00:00', last_precision:'EXACT_TS', last_source:'AMAZON_HISTORY',
}];

test('targets: колонки «Добавлен» и «Последнее изменение» и флаг «без показов»', async () => {
  const run = ui(TARGET_ROWS);
  run(`state.view='targets'; state.start='2026-11-01'; state.end='2026-11-30'; state.all=true;`);
  const html = await run('views.targets()');
  assert.match(html, /Добавлен/);
  assert.match(html, /2026-10-01/);
  assert.match(html, /Последнее изменение/);
  assert.match(html, /21\.11/);
  assert.match(html, /статус: ENABLED → PAUSED/);
  assert.match(html, /без показов 1/);
});

test('targets: переключатель «только без показов» уходит в запрос', async () => {
  const run = ui(TARGET_ROWS);
  run(`state.view='targets'; state.start='2026-11-01'; state.end='2026-11-30'; state.targetIdle=true;`);
  await run('views.targets()');
  assert.match(run('globalThis.lastPath'), /idle=true/);
});

test('changes: фильтры типа и объекта уходят в запрос, объект показывается с кампанией', async () => {
  const run = ui([{
    change_id:'a', change_date:'2026-11-21', change_ts:'2026-11-21T08:00:00+00:00',
    date_precision:'EXACT_TS', entity_type:'KEYWORD', entity_id:'k1', entity_label:'крем для рук · точное',
    campaign_id:'c1', campaign_name:'Blue', field:'BID_AMOUNT', old_value:'0.4', new_value:'0.2',
    delta:'-0.2', source:'AMAZON_HISTORY', actor:'UNKNOWN', summary:'',
  }]);
  run(`state.view='changes'; state.start='2026-11-01'; state.end='2026-11-30';
       state.changeGroup='bid'; state.changeEntity='KEYWORD';`);
  const html = await run('views.changes()');
  assert.match(run('globalThis.lastPath'), /group=bid/);
  assert.match(run('globalThis.lastPath'), /entity=KEYWORD/);
  assert.match(html, /кампания: Blue/);
  assert.match(html, /ставка/);
  assert.match(html, /ключ/);
  assert.doesNotMatch(html, /BID_AMOUNT/, 'поле показывается человеческим названием');
});

test('picker: поиск сужает список, «Выбрать найденные» берёт только видимые', async () => {
  // Один и тот же набор элементов на каждый запрос: поиск правит их style, и правки видны дальше.
  const rows = [{search:'лето\u0000c1\u0000ENABLED'}, {search:'зима\u0000c2\u0000PAUSED'}]
    .map((dataset) => ({style:{}, dataset, disabled:false}));
  const doc = {
    addEventListener(){}, body:stubEl(),
    querySelector:()=>stubEl(),
    querySelectorAll:()=>rows,
  };
  const ctx = vm.createContext({window:{matchMedia:()=>({matches:false})}, localStorage:{getItem:()=>null},
    document:doc, Intl, URLSearchParams, CSS:{escape:(v)=>v}});
  vm.runInContext(readFileSync(require.resolve('../app/static/app.js'),'utf8'), ctx);
  const run = expr => vm.runInContext(expr, ctx);
  assert.equal(run("pickerVisible().length"), 2);
  assert.equal(run("applyPickerSearch('зим')"), 1);
  assert.equal(run("pickerVisible().length"), 1);
  assert.equal(run("applyPickerSearch('нет-такой')"), 0);
  assert.equal(run("applyPickerSearch('')"), 2);
});

test('static: targetsView помечает объекты без показов и подтягивает последнее событие', async () => {
  const DATA = {
    meta:{date_min:'2026-11-01', date_max:'2026-11-30', counts:{}, targets:2, negatives:0,
      account:{id:'eugene', name:'Eugene'}},
    campaigns:[{campaign_id:'c1', name:'Blue', state:'ENABLED', budget:5}],
    adgroups:[{ad_group_id:'g1', campaign_id:'c1', name:'Group'}],
    targets:[
      {target_id:'k1', campaign_id:'c1', ad_group_id:'g1', targeting:'крем', match_type:'EXACT',
       is_negative:0, state:'ENABLED', bid:0.3, effective_bid:0.3, created_at:'2026-10-01T09:00:00Z'},
      {target_id:'k2', campaign_id:'c1', ad_group_id:'g1', targeting:'маска', match_type:'EXACT',
       is_negative:0, state:'PAUSED', bid:0.2, effective_bid:0.2, created_at:'2026-10-02T09:00:00Z'},
    ],
    facts:{campaign_day:[], target_day:[['2026-11-20','c1','g1','k1',50,2,1.5,0,0,0]]},
    changes:[{change_id:'a', change_date:'2026-11-21', change_ts:'2026-11-21T08:00:00+00:00',
      date_precision:'EXACT_TS', entity_type:'KEYWORD', entity_id:'k2', entity_label:'маска',
      campaign_id:'c1', field:'STATUS', old_value:'ENABLED', new_value:'PAUSED', source:'AMAZON_HISTORY'}],
    coverage:[], runs:[],
  };
  const ctx = vm.createContext({window:{J_STATIC:{dataUrl:'data.json'}},
    fetch:async () => ({json:async () => DATA}), Intl, URLSearchParams, URL, console});
  vm.runInContext(readFileSync(require.resolve('../app/static/local.js'),'utf8'), ctx);
  const handle = (p) => vm.runInContext(`window.LocalAPI.handle(${JSON.stringify(p)})`, ctx);
  const rows = await handle('/api/targets?start=2026-11-01&end=2026-11-30&kind=positive');
  const byId = Object.fromEntries(rows.map((r) => [r.target_id, r]));
  assert.equal(byId.k1.idle, 0);
  assert.equal(byId.k2.idle, 1);
  assert.equal(byId.k2.last_field, 'STATUS');
  assert.equal(byId.k2.last_new, 'PAUSED');
  const idle = await handle('/api/targets?start=2026-11-01&end=2026-11-30&kind=positive&idle=true');
  assert.deepEqual(idle.map((r) => r.target_id), ['k2']);
});

test('static: журнал фильтруется по группе полей и типу объекта без тихого среза', async () => {
  const changes = Array.from({length:2100}, (_, i) => ({
    change_id:'c' + i, change_date:'2026-11-21', change_ts:'2026-11-21T08:00:00+00:00',
    entity_type: i % 2 ? 'KEYWORD' : 'CAMPAIGN', entity_id:'e' + i, entity_label:'obj' + i,
    campaign_id:'c1', field: i % 2 ? 'BID_AMOUNT' : 'BUDGET_AMOUNT', old_value:'1', new_value:'2',
    source:'AMAZON_HISTORY',
  }));
  const DATA = {
    meta:{date_min:'2026-11-01', date_max:'2026-11-30', counts:{}, targets:0, negatives:0,
      account:{id:'eugene', name:'Eugene'}},
    campaigns:[{campaign_id:'c1', name:'Blue', state:'ENABLED'}], adgroups:[],
    targets:[], facts:{campaign_day:[], target_day:[]}, changes, coverage:[], runs:[],
  };
  const ctx = vm.createContext({window:{J_STATIC:{dataUrl:'data.json'}},
    fetch:async () => ({json:async () => DATA}), Intl, URLSearchParams, URL, console});
  vm.runInContext(readFileSync(require.resolve('../app/static/local.js'),'utf8'), ctx);
  const handle = (p) => vm.runInContext(`window.LocalAPI.handle(${JSON.stringify(p)})`, ctx);
  const all = await handle('/api/changes?start=2026-11-01&end=2026-11-30');
  assert.equal(all.length, 2100, 'список не обрезается молча');
  assert.equal(all[0].campaign_name, 'Blue');
  const bids = await handle('/api/changes?start=2026-11-01&end=2026-11-30&group=bid');
  assert.equal(bids.length, 1050);
  assert.ok(bids.every((r) => r.field === 'BID_AMOUNT'));
  const keys = await handle('/api/changes?start=2026-11-01&end=2026-11-30&entity=KEYWORD');
  assert.equal(keys.length, 1050);
});

test('static: ставка без своего значения показывается как ставка группы', async () => {
  const DATA = {
    meta:{date_min:'2026-11-01', date_max:'2026-11-30', counts:{}, targets:1, negatives:0,
      account:{id:'eugene', name:'Eugene'}},
    campaigns:[{campaign_id:'c1', name:'Blue', state:'ENABLED'}],
    adgroups:[{ad_group_id:'g1', campaign_id:'c1', name:'Group', default_bid:0.75}],
    targets:[{target_id:'k1', campaign_id:'c1', ad_group_id:'g1', targeting:'крем', match_type:'EXACT',
      is_negative:0, state:'PAUSED', bid:null, effective_bid:null}],
    facts:{campaign_day:[], target_day:[]}, changes:[], coverage:[], runs:[],
  };
  const ctx = vm.createContext({window:{J_STATIC:{dataUrl:'data.json'}},
    fetch:async () => ({json:async () => DATA}), Intl, URLSearchParams, URL, console});
  vm.runInContext(readFileSync(require.resolve('../app/static/local.js'),'utf8'), ctx);
  const rows = await vm.runInContext(
    `window.LocalAPI.handle('/api/targets?start=2026-11-01&end=2026-11-30&kind=positive')`, ctx);
  assert.equal(rows[0].bid, null);
  assert.equal(rows[0].effective_bid, 0.75);
  assert.equal(rows[0].bid_source, 'AD_GROUP_DEFAULT');
});
