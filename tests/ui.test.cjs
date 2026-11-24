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
