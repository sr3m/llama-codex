'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { createProvider, sensitiveDocument } = require('../lib/provider');

function fixture(overrides = {}) {
  class Range { constructor(start, end) { this.start = start; this.end = end; } }
  class Item { constructor(insertText, range) { this.insertText = insertText; this.range = range; } }
  const vscode = { Range, InlineCompletionItem: Item, InlineCompletionTriggerKind: { Automatic: 1, Invoke: 0 }, workspace: { isTrusted: true } };
  let text = '# compute sum\ndef add(a, b):\n    return ';
  const doc = { languageId: 'python', version: 1, uri: { path: '/demo.py', toString: () => 'file:///demo.py' },
    offsetAt: p => p, positionAt: i => Math.min(i, text.length), getText: range => text.slice(range.start, range.end) };
  const calls = []; const statuses = [];
  const settings = { enabled: true, automatic: true, excludedLanguages: [], prefixChars: 20, suffixChars: 10, debounceMs: 0, endpoint: 'http://127.0.0.1:9931', ...overrides };
  const services = { config: () => settings, model: async () => 'coder', apiKey: async () => undefined,
    client: { complete: async body => { calls.push(body); return { text: 'a + b', mode: 'infill' }; } },
    status: (...a) => statuses.push(a), idle() {}, log() {} };
  const token = { isCancellationRequested: false, onCancellationRequested: () => ({ dispose() {} }) };
  return { vscode, doc, calls, services, token, statuses, settings, position: text.length };
}
test('provider returns a genuine insertion range with bounded cursor context and caches repeated request', async () => {
  const f = fixture(); const p = createProvider(f.vscode, f.services);
  const items = await p.provideInlineCompletionItems(f.doc, f.position, { triggerKind: 0 }, f.token);
  assert.equal(items[0].insertText, 'a + b');
  assert.equal(items[0].range.start, f.position);
  assert.equal(items[0].range.end, f.position);
  assert.equal(f.calls[0].prefix.length, 20);
  assert.equal(f.calls[0].suffix, '');
  await p.provideInlineCompletionItems(f.doc, f.position, { triggerKind: 0 }, f.token);
  assert.equal(f.calls.length, 1);
  p.dispose();
});
test('disabled, manual-only automatic calls, untrusted and sensitive docs send no code', async () => {
  for (const scenario of ['disabled', 'manual', 'untrusted', 'secret', 'language', 'cancelled', 'widget']) {
    const f = fixture(); const p = createProvider(f.vscode, f.services);
    if (scenario === 'disabled') f.settings.enabled = false;
    if (scenario === 'manual') f.settings.automatic = false;
    if (scenario === 'untrusted') f.vscode.workspace.isTrusted = false;
    if (scenario === 'secret') f.doc.uri.path = '/project/.env.local';
    if (scenario === 'language') f.settings.excludedLanguages = ['python'];
    if (scenario === 'cancelled') f.token.isCancellationRequested = true;
    const context = { triggerKind: 1, ...(scenario === 'widget' ? { selectedCompletionInfo: {} } : {}) };
    assert.deepEqual(await p.provideInlineCompletionItems(f.doc, f.position, context, f.token), []);
    assert.equal(f.calls.length, 0, scenario);
    p.dispose();
  }
});
test('edited document never receives stale response', async () => {
  const f = fixture(); let release; let started;
  const ready = new Promise(r => { started = r; });
  f.services.client.complete = async () => { started(); return new Promise(r => { release = r; }); };
  const p = createProvider(f.vscode, f.services);
  const pending = p.provideInlineCompletionItems(f.doc, f.position, { triggerKind: 0 }, f.token);
  await ready; f.doc.version++; release({ text: 'stale', mode: 'infill' });
  assert.deepEqual(await pending, []);
  p.dispose();
});
test('new request cancels earlier generation, even if upstream ignores abort', async () => {
  const f = fixture(); let release; let started;
  const ready = new Promise(r => { started = r; }); let count = 0;
  f.services.client.complete = async body => {
    count++;
    if (count === 1) { f.calls.push(body); started(); return new Promise(r => { release = r; }); }
    return { text: 'fresh', mode: 'infill' };
  };
  const p = createProvider(f.vscode, f.services);
  const first = p.provideInlineCompletionItems(f.doc, f.position, { triggerKind: 0 }, f.token);
  await ready;
  const second = await p.provideInlineCompletionItems(f.doc, f.position, { triggerKind: 0 }, f.token);
  assert.equal(f.calls[0].signal.aborted, true);
  release({ text: 'old', mode: 'infill' });
  assert.deepEqual(await first, []); assert.equal(second[0].insertText, 'fresh'); p.dispose();
});
test('cancellation during debounce does not contact server', async () => {
  const f = fixture({ debounceMs: 60 }); const p = createProvider(f.vscode, f.services);
  const pending = p.provideInlineCompletionItems(f.doc, f.position, { triggerKind: 1 }, f.token);
  p.cancel(); assert.deepEqual(await pending, []); assert.equal(f.calls.length, 0); p.dispose();
});
test('recognizes private-key and environment file names', () => {
  for (const path of ['/x/.env', '/x/.env.production', '/x/key.pem', '/x/.git/config', '/x/auth.json', '/x/id_ed25519']) assert.equal(sensitiveDocument({ path }), true);
  assert.equal(sensitiveDocument({ path: '/x/demo.py' }), false);
});
