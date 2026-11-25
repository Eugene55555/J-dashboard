const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {readFileSync} = require('node:fs');
function ui(mobile = true) {
  const ctx = vm.createContext({window:{matchMedia:()=>({matches:mobile})}, localStorage:{getItem:()=>null}, document:{addEventListener(){}}, Intl, URLSearchParams});
  vm.runInContext(readFileSync(require.resolve('../app/static/app.js'),'utf8'),ctx);
  return expr=>vm.runInContext(expr,ctx);
}
test('mobile: sort before the list, reach every sorted result in bounded pages',()=>{
  const run=ui();run(`const rows=Array.from({length:351},(_,n)=>({n}));const list=()=>tableHtml('targets',[{key:'n',label:'Число'}],rows,{defaultSort:'n'});`);
  const first=run('list()');assert.ok(first.indexOf('data-sort-select')<first.indexOf('<tbody>'));assert.match(first,/data-page=/);assert.match(first,/data-sort-direction=/);
  assert.match(first,/data-label="Число">350<\/td>/);assert.doesNotMatch(first,/data-label="Число">0<\/td>/);
  run('state.pages.targets=999');assert.match(run('list()'),/data-label="Число">0<\/td>/);
});
test('campaign search filters IDs and names before count, totals and pagination', async()=>{
  const run=ui();run(`api=async()=>[{name:'Beta',campaign_id:'alpha-12',spend:8},{name:'Gamma',campaign_id:'3',spend:23}];state.campaignSearch='ALPHA';`);
  const html=await run('views.campaigns()');assert.match(html,/Beta/);assert.doesNotMatch(html,/Gamma/);assert.match(html,/Расход \$8,00/);
});
test('chart: money series share a dollar scale; different units use distinct axes',()=>{
  const run=ui(); const limits=run(`chartAxes([{spend:1,sales:20,cpc:.3,acos:30,clicks:2,impressions:200}], 'money')`);
  assert.equal(limits.left,limits.right);assert.ok(limits.left>=20);
  assert.ok(run(`chartAxes([{acos:40,cpc:.5}], 'efficiency').right < 1`));
  assert.ok(run(`chartAxes([{clicks:3,impressions:800}], 'volume').left < 10`));
});
test('entire-history date inputs match the changes actually displayed',()=>{
  const run=ui();run(`document.querySelector=()=>({});document.querySelectorAll=()=>[];Object.assign(state,{view:'changes',today:'2026-05-30',meta:{changes_min:'2026-02-28',date_min:'2026-04-01',date_max:'2026-05-30'}});setPeriod('all');`);
  assert.equal(run('state.start'),'2026-02-28');run(`state.view='products';setPeriod('all')`);assert.equal(run('state.start'),'2026-04-01');
});
test('an explicit empty selection never falls back to all campaign results',()=>{
 const run=ui();run('state.all=false;state.selected.clear()');assert.equal(run("query().get('campaigns')"),'__none__');
});
test('coverage gaps: day count and shortened list',()=>{
  const run=ui();
  assert.equal(run("gapDays([['2026-06-28','2026-08-22']])"),56);
  assert.equal(run("gapDays([['2026-06-28','2026-08-22'],['2026-09-02','2026-09-02']])"),57);
  assert.equal(run("gapText([['2026-06-28','2026-08-22']])"),'28.06.2026—22.08.2026');
  assert.equal(run("gapText([['2026-09-02','2026-09-02']])"),'02.09.2026');
  assert.match(run("gapText([['2026-06-28','2026-08-22'],['2026-08-28','2026-08-31'],['2026-09-02','2026-09-02'],['2026-10-01','2026-10-02']])"),/и ещё 1$/);
});
test('custom period: inverted, empty or future dates are refused instead of rewritten',()=>{
  const run=ui();
  assert.match(run("customRange('2026-11-20','2026-11-10').error"),
    /Период не изменён: начало 20\.11\.2026 позже конца 10\.11\.2026/);
  assert.match(run("customRange('','2026-11-10').error"), /укажите обе даты/);
  assert.match(run("customRange('2099-01-01','2099-02-01').error"), /отчётов ещё нет/);
  const ok=run("customRange('2026-11-03','2026-11-09')");
  assert.equal(ok.start,'2026-11-03');assert.equal(ok.end,'2026-11-09');assert.equal(ok.error,undefined);
  assert.equal(run("customRange('2026-11-09','2026-11-09').error"),undefined,'один день допустим');
});
test('overview: incomplete previous window is flagged, fully empty one is not', async()=>{
  const base={
    period:{start:'2026-11-03',end:'2026-11-09',days:7,previous:{start:'2026-10-27',end:'2026-11-02'}},
    current:{impressions:700,clicks:10,spend:5,orders:1,sales:20,units:1,ctr:1.43,cpc:0.5,cpa:5,acos:25,roas:4,cvr:10,aov:20},
    previous:{impressions:600,clicks:8,spend:4,orders:1,sales:16,units:1,ctr:1.33,cpc:0.5,cpa:4,acos:25,roas:4,cvr:12.5,aov:16},
    series:[{bucket:'2026-11-03',impressions:700,clicks:10,spend:5,orders:1,sales:20,units:1,ctr:1.43,cpc:0.5,acos:25,roas:4}],
    campaigns:[{campaign_id:'c1',name:'Blue',state:'ENABLED',budget:1,impressions:700,clicks:10,spend:5,orders:1,sales:20,units:1,ctr:1.43,cpc:0.5,acos:25,roas:4}],
    data_range:{min:'2026-11-03',max:'2026-11-09',days:7},
    coverage:{days:7,missing:[]},
    previous_coverage:{days:6,missing:[['2026-10-31','2026-10-31']]},
  };
  const run=ui();
  run(`api=async()=>(${JSON.stringify(base)});`);
  const flagged=await run('views.overview()');
  assert.match(flagged,/Сравнение неполное/);
  assert.match(flagged,/31\.10\.2026/);

  const empty={...base,previous_coverage:{days:0,missing:[['2026-10-27','2026-11-02']]}};
  run(`api=async()=>(${JSON.stringify(empty)});`);
  assert.doesNotMatch(await run('views.overview()'),/Сравнение неполное/);
});
