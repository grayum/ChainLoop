/* Exercise the real bootstrap in a small browser-independent DOM/storage harness. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {runInNewContext} = require('node:vm');
const source = readFileSync(join(__dirname, '../app/static/theme.js'), 'utf8');

function browser({stored = null, dark = false, storageBlocked = false} = {}) {
  const root = {dataset: {}};
  const values = new Map(stored === null ? [] : [['chainloop-theme', stored]]);
  const inputs = ['system', 'light', 'dark'].map(value => ({
    value, checked: value === 'system', listeners: {},
    addEventListener(type, fn) { this.listeners[type] = fn; },
  }));
  const documentEvents = {}, windowEvents = {}, mediaEvents = {};
  const media = {matches: dark, addEventListener(type, fn) { mediaEvents[type] = fn; }};
  runInNewContext(source, {
    document: {
      documentElement: root,
      querySelectorAll() { return inputs; },
      addEventListener(type, fn) { documentEvents[type] = fn; },
    },
    window: {
      matchMedia(query) { assert.equal(query, '(prefers-color-scheme: dark)'); return media; },
      addEventListener(type, fn) { windowEvents[type] = fn; },
    },
    localStorage: {
      getItem(key) { if (storageBlocked) throw Error('Storage disabled'); return values.get(key) ?? null; },
      setItem(key, value) { if (storageBlocked) throw Error('Storage disabled'); values.set(key, value); },
    },
  });
  return {
    root, values,
    ready() { documentEvents.DOMContentLoaded(); },
    select(value) { const input = inputs.find(item => item.value === value); input.checked = true; input.listeners.change(); },
    os(dark) { media.matches = dark; mediaEvents.change(); },
    storage(value, key = 'chainloop-theme') { windowEvents.storage({key, newValue: value}); },
    selected() { return inputs.filter(item => item.checked).map(item => item.value); },
  };
}

test('default is a real System preference, applied before DOMContentLoaded', () => {
  const page = browser({dark: true});
  assert.equal(page.root.dataset.themePreference, 'system');
  assert.equal(page.root.dataset.theme, 'dark');
  page.ready();
  assert.deepEqual(page.selected(), ['system']);
  assert.equal(page.values.size, 0);
  page.os(false);
  assert.equal(page.root.dataset.theme, 'light');
  page.os(true);
  assert.equal(page.root.dataset.theme, 'dark');
});

for (const stored of ['system', 'light', 'dark']) {
  test(`stored ${stored} is restored before paint and selects its control`, () => {
    const page = browser({stored, dark: true});
    assert.equal(page.root.dataset.theme, stored === 'system' ? 'dark' : stored);
    page.ready();
    assert.deepEqual(page.selected(), [stored]);
    page.os(false);
    assert.equal(page.root.dataset.theme, stored === 'system' ? 'light' : stored);
  });
}

for (const stored of ['', 'DARK', 'auto', '<script>alert(1)</script>', 'null']) {
  test(`invalid stored preference falls back to System: ${stored}`, () => {
    const page = browser({stored, dark: true});
    page.ready();
    assert.equal(page.root.dataset.themePreference, 'system');
    assert.deepEqual(page.selected(), ['system']);
    assert.equal(page.root.dataset.theme, 'dark');
  });
}

test('user overrides persist, ignore OS changes, and can return to System', () => {
  const page = browser();
  page.ready();
  page.select('dark');
  assert.equal(page.values.get('chainloop-theme'), 'dark');
  page.os(false);
  assert.equal(page.root.dataset.theme, 'dark');
  page.select('light');
  page.os(true);
  assert.equal(page.root.dataset.theme, 'light');
  page.select('system');
  assert.equal(page.values.get('chainloop-theme'), 'system');
  assert.equal(page.root.dataset.theme, 'dark');
  assert.deepEqual(page.selected(), ['system']);
});

test('blocked localStorage still permits in-page theme changes', () => {
  const page = browser({storageBlocked: true, dark: true});
  page.ready();
  assert.equal(page.root.dataset.theme, 'dark');
  page.select('light');
  assert.equal(page.root.dataset.theme, 'light');
});

test('cross-tab preferences, invalid values and storage clearing are safe', () => {
  const page = browser({dark: true});
  page.ready();
  page.storage('light');
  assert.equal(page.root.dataset.theme, 'light');
  page.storage('dark', 'unrelated-key');
  assert.equal(page.root.dataset.theme, 'light');
  page.storage('invalid');
  assert.equal(page.root.dataset.themePreference, 'system');
  assert.equal(page.root.dataset.theme, 'dark');
  page.storage('light');
  page.storage(null, null);
  assert.deepEqual(page.selected(), ['system']);
});
