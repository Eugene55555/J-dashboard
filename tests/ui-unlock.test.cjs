// Ворота статической версии: в sessionStorage должен лежать производный ключ,
// а не пароль владельца. Криптография настоящая (WebCrypto), данные выдуманные.
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {readFileSync} = require('node:fs');

const LOGIN = 'tester';
const PASSWORD = 'synthetic-pass-for-test';
const PAYLOAD = {
  meta: {date_min: '2026-11-01', date_max: '2026-11-02', counts: {}},
  facts: {campaign_day: [], target_day: []}, coverage: [], runs: [],
};

const b64 = (bytes) => Buffer.from(bytes).toString('base64');

async function makeCfg(iterations = 1000) {
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const nonce = crypto.getRandomValues(new Uint8Array(12));
  const base = await crypto.subtle.importKey(
    'raw', new TextEncoder().encode(`${LOGIN}\u0000${PASSWORD}`), 'PBKDF2', false, ['deriveKey']);
  const key = await crypto.subtle.deriveKey(
    {name: 'PBKDF2', salt, iterations, hash: 'SHA-256'},
    base, {name: 'AES-GCM', length: 256}, true, ['encrypt', 'decrypt']);
  const ct = await crypto.subtle.encrypt({name: 'AES-GCM', iv: nonce}, key,
    new TextEncoder().encode(JSON.stringify(PAYLOAD)));
  return {
    cfg: {v: 1, kdf: 'PBKDF2-SHA256', iter: iterations, login: LOGIN,
      salt: b64(salt), nonce: b64(nonce), ct: b64(new Uint8Array(ct))},
    rawKey: new Uint8Array(await crypto.subtle.exportKey('raw', key)),
  };
}

const stubEl = () => ({
  innerHTML: '', textContent: '', value: '', checked: false, disabled: false, dataset: {}, style: {},
  classList: {add() {}, remove() {}, toggle() {}, contains: () => false},
  setAttribute() {}, focus() {}, select() {}, closest: () => null,
});

function harness(cfg, savedKey) {
  const store = new Map();     // sessionStorage
  const local = new Map();     // localStorage
  const clock = {now: 1_700_000_000_000};
  class FakeDate extends Date { static now() { return clock.now; } }
  const els = {};
  const doc = {
    addEventListener() {}, body: stubEl(),
    getElementById: (id) => (els[`#${id}`] ||= stubEl()),
    querySelector: (sel) => (els[sel] ||= stubEl()),
    querySelectorAll: () => [],
  };
  const memory = (map) => ({
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: (k) => map.delete(k),
  });
  const sessionStorage = memory(store);
  const localStorage = memory(local);
  if (savedKey) sessionStorage.setItem('jd_key', savedKey);
  const ctx = vm.createContext({
    window: {J_STATIC: {dataUrl: 'data.enc.json', encrypted: true}},
    fetch: async () => ({json: async () => cfg}),
    document: doc, sessionStorage, localStorage, crypto, Date: FakeDate,
    atob: (v) => Buffer.from(v, 'base64').toString('binary'),
    btoa: (v) => Buffer.from(v, 'binary').toString('base64'),
    TextEncoder, TextDecoder, Intl, URLSearchParams, URL, console,
  });
  vm.runInContext(readFileSync(require.resolve('../app/static/local.js'), 'utf8'), ctx);
  return {store, local, clock, els, load: () => vm.runInContext('window.LocalAPI.load()', ctx)};
}

async function waitFor(probe, tries = 200) {
  for (let i = 0; i < tries; i++) {
    if (probe()) return true;
    await new Promise((r) => setTimeout(r, 5));
  }
  throw new Error('условие не наступило');
}

test('static gate: saved key decrypts without a password and drops the legacy entry', async () => {
  const {cfg, rawKey} = await makeCfg();
  const h = harness(cfg, b64(rawKey));
  h.store.set('jd_password', 'leftover-plain-password');   // след прежней версии

  const data = await h.load();

  assert.equal(data.meta.date_max, '2026-11-02');
  assert.deepEqual([...data.facts.campaign_day], []);
  assert.equal(h.store.get('jd_key'), b64(rawKey), 'ключ сохранён как base64');
  assert.equal(h.store.has('jd_password'), false, 'пароль из прежней версии удалён');
  assert.equal(h.els['#gate-form'] && h.els['#gate-form'].onsubmit, undefined,
    'форма входа не запрашивалась');
});

test('static gate: successful unlock stores the key and clears the password field', async () => {
  const {cfg, rawKey} = await makeCfg();
  const h = harness(cfg, null);
  const pending = h.load();
  await waitFor(() => h.els['#gate-form'] && h.els['#gate-form'].onsubmit);

  h.els['#gate-login'].value = LOGIN;
  h.els['#gate-password'].value = PASSWORD;
  h.els['#gate-form'].onsubmit({preventDefault() {}});
  const data = await pending;

  assert.equal(data.meta.date_max, '2026-11-02');
  assert.equal(h.store.get('jd_key'), b64(rawKey));
  assert.equal(h.store.has('jd_password'), false, 'пароль в браузере не хранится');
  assert.equal(h.els['#gate-password'].value, '', 'поле пароля очищено');
});

test('static gate: unusable saved key is dropped and the gate asks again', async () => {
  const {cfg} = await makeCfg();
  const other = await makeCfg();          // ключ от другого пароля
  const h = harness(cfg, b64(other.rawKey));
  const pending = h.load();
  await waitFor(() => h.els['#gate-form'] && h.els['#gate-form'].onsubmit);

  assert.equal(h.store.has('jd_key'), false, 'нерабочий ключ удалён');

  h.els['#gate-login'].value = LOGIN;
  h.els['#gate-password'].value = PASSWORD;
  h.els['#gate-form'].onsubmit({preventDefault() {}});
  assert.equal((await pending).meta.date_max, '2026-11-02');
});

test('static gate: wrong password stores nothing, then the right one opens', async () => {
  const {cfg} = await makeCfg();
  const h = harness(cfg, null);
  const pending = h.load();
  await waitFor(() => h.els['#gate-form'] && h.els['#gate-form'].onsubmit);

  h.els['#gate-login'].value = LOGIN;
  h.els['#gate-password'].value = 'definitely-wrong';
  h.els['#gate-form'].onsubmit({preventDefault() {}});
  await waitFor(() => h.els['#gate-error'].textContent !== '');

  assert.match(h.els['#gate-error'].textContent, /Неверный/);
  assert.equal(h.store.has('jd_key'), false);
  assert.equal(h.store.has('jd_password'), false);

  // Вторая попытка с верным паролем проходит: ворота не залипают.
  h.els['#gate-password'].value = PASSWORD;
  h.els['#gate-form'].onsubmit({preventDefault() {}});
  assert.equal((await pending).meta.date_max, '2026-11-02');
  assert.ok(h.store.get('jd_key'));
});

test('static gate: pauses after repeated failures and clears the counter on success', async () => {
  const {cfg} = await makeCfg();
  const h = harness(cfg, null);
  const pending = h.load();
  await waitFor(() => h.els['#gate-form'] && h.els['#gate-form'].onsubmit);
  h.els['#gate-login'].value = LOGIN;

  for (let i = 0; i < 4; i += 1) {
    h.els['#gate-error'].textContent = '';
    h.els['#gate-password'].value = 'wrong-password';
    h.els['#gate-form'].onsubmit({preventDefault() {}});
    await waitFor(() => h.els['#gate-error'].textContent !== '');
  }
  const state = JSON.parse(h.local.get('jd_gate_fails'));
  assert.equal(state.fails, 4);
  assert.match(h.els['#gate-error'].textContent, /Следующая попытка через 1 с/);

  // Пятая попытка сразу — отказ по паузе, даже с верным паролем.
  h.els['#gate-error'].textContent = '';
  h.els['#gate-password'].value = PASSWORD;
  h.els['#gate-form'].onsubmit({preventDefault() {}});
  await waitFor(() => /Подождите/.test(h.els['#gate-error'].textContent));
  assert.equal(h.store.has('jd_key'), false, 'во время паузы вход не выполняется');

  // Пауза истекла — вход проходит, счётчик очищен.
  h.clock.now += 5000;
  h.els['#gate-error'].textContent = '';
  h.els['#gate-form'].onsubmit({preventDefault() {}});
  assert.equal((await pending).meta.date_max, '2026-11-02');
  assert.equal(h.local.has('jd_gate_fails'), false);
});
