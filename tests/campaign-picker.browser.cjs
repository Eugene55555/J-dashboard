// Requires Playwright with Chromium installed: node --test tests/campaign-picker.browser.cjs
const {test, before, after} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {chromium} = require('playwright');

const campaigns = Array.from({length: 60}, (_, i) => ({
  campaign_id: `demo-${i + 1}`, name: i === 0 ? 'Лето <A&B>' : `Демо ${i + 1}`,
  state: 'ENABLED', budget: 1,
}));
const origin = 'http://dashboard.test';
let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

// No server, database, external requests or credentials. Exercise the real boot and render.
async function fixture(t, viewport = {width: 1280, height: 800}) {
  const page = await browser.newPage({viewport});
  t.after(() => page.close());
  const errors = [];
  page.on('pageerror', err => errors.push(err.message));
  t.after(() => assert.deepEqual(errors, []));
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    const files = {'/': 'index.html', '/static/app.js': 'app.js', '/static/style.css': 'style.css'};
    if (url.origin === origin && Object.hasOwn(files, url.pathname)) {
      const file = files[url.pathname];
      return route.fulfill({body: readFileSync(require.resolve('../app/static/' + file)),
        contentType: {'index.html':'text/html; charset=utf-8', 'app.js':'text/javascript', 'style.css':'text/css'}[file]});
    }
    if (url.origin === origin && url.pathname === '/api/meta') {
      return route.fulfill({json: {account: {id:'demo', name:'Demo'}, campaigns, counts:{changes:0}}});
    }
    if (url.origin === origin && url.pathname === '/api/campaigns') {
      const ids = url.searchParams.get('campaigns')?.split(',');
      return route.fulfill({json: campaigns.filter(c => !ids || ids.includes(c.campaign_id))});
    }
    errors.push(`Unexpected request: ${url}`);
    return route.abort();
  });
  await page.addInitScript(() => {
    if (!localStorage.getItem('jd_state')) localStorage.setItem('jd_state', JSON.stringify({view:'campaigns', filtersOpen:true}));
  });
  await page.goto(origin);
  await page.locator('#cmp-search').waitFor();
  return page;
}
const prefs = page => page.evaluate(() => JSON.parse(localStorage.getItem('jd_state')));

test('Только эта applies a single result immediately and survives reload', async t => {
  const page = await fixture(t);
  await page.locator('#picker-btn').click();
  const only = page.getByRole('button', {name: /^Только эта/}).first();
  assert.equal(await only.isVisible(), true, 'only-this action is visible without scrolling');
  await only.click();
  await page.waitForFunction(() => document.querySelectorAll('#view tbody tr').length === 1);
  assert.match(await page.locator('#view tbody').textContent(), /Лето <A&B>/);
  assert.deepEqual((await prefs(page)).selected, ['demo-1']);
  assert.equal(await page.locator('#picker-panel').isVisible(), false);
  await page.reload();
  await page.locator('#cmp-search').waitFor();
  assert.equal(await page.locator('#picker-label').textContent(), 'Лето <A&B>');
  assert.equal(await page.locator('#view tbody tr').count(), 1);
});

for (const viewport of [{width:1280,height:800}, {width:360,height:640}]) {
  test(`multi-select by name or ID without scrolling to Apply (${viewport.width}px)`, async t => {
    const page = await fixture(t, viewport);
    const trigger = page.locator('#picker-btn');
    const search = page.locator('#picker-search');
    await trigger.click();
    const first = page.getByRole('checkbox', {name:/Лето <A&B>/});
    assert.ok(await first.isEnabled(), 'start a subset directly from All');
    await first.check();
    await search.fill(' DEMO-60 ');
    await page.getByRole('checkbox', {name:/Демо 60/}).check();
    await search.fill('');
    assert.equal((await prefs(page)).all, true, 'draft has not changed the report');
    assert.match(await page.getByRole('status').innerText(), /2 из 60.*Лето <A&B>.*Демо 60/);
    const apply = page.getByRole('button', {name:/Применить/});
    // isVisible()/click() alone can pass for an off-screen button (Playwright auto-scrolls).
    assert.ok(await apply.evaluate(el => {
      const {top,bottom,left,right,width,height} = el.getBoundingClientRect();
      return top >= 0 && bottom <= innerHeight && left >= 0 && right <= innerWidth &&
        el.contains(document.elementFromPoint(left + width/2, top + height/2));
    }), 'Apply is reachable while the full 60-row list is displayed');
    await apply.click();
    await page.waitForFunction(() => document.querySelectorAll('#view tbody tr').length === 2);
    const names = await page.locator('#view tbody .nm').allTextContents();
    assert.deepEqual(new Set(names), new Set(['Лето <A&B>', 'Демо 60']));
    assert.match(await trigger.innerText(), /2 из 60.*Лето <A&B>/);
    assert.deepEqual((await prefs(page)).selected, ['demo-1','demo-60']);
  });
}
