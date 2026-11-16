'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const { CompletionClient, normalizeEndpoint, listModels, chooseDefaultModel, cleanCompletion, requestJson, friendlyError } = require('../lib/client');

async function server(t, handler) {
  const seen = [];
  const instance = http.createServer(async (req, res) => {
    let raw = '';
    for await (const chunk of req) raw += chunk;
    seen.push({ path: req.url, headers: req.headers, body: raw ? JSON.parse(raw) : undefined });
    await handler(req, res, seen.at(-1));
  });
  await new Promise(resolve => instance.listen(0, '127.0.0.1', resolve));
  t.after(() => { instance.closeAllConnections(); return new Promise(resolve => instance.close(resolve)); });
  return { endpoint: `http://127.0.0.1:${instance.address().port}`, seen };
}
const json = (res, data, code = 200) => { res.writeHead(code, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(data)); };
const input = endpoint => ({ endpoint, model: 'test-coder', prefix: 'def add(a, b):\n    return ', suffix: '\n', language: 'python' });

test('accepts root and /v1 endpoint; rejects embedded credentials and queries', () => {
  assert.equal(normalizeEndpoint('http://127.0.0.1:9931/v1/'), 'http://127.0.0.1:9931');
  for (const value of ['bad', 'file:///tmp/model', 'https://user:password@example.com', 'https://example.com?token=x']) {
    assert.throws(() => normalizeEndpoint(value), { code: 'INVALID_ENDPOINT' });
  }
});
test('discovers models and favors a loaded coding model', async t => {
  const mock = await server(t, (_, res) => json(res, { data: [{ id: 'general', status: { value: 'loaded' } }, { id: 'test-coder', status: { value: 'loaded' } }, { id: '' }] }));
  const models = await listModels(mock.endpoint);
  assert.equal(models.length, 2);
  assert.equal(chooseDefaultModel(models), 'test-coder');
  assert.equal(mock.seen[0].path, '/v1/models');
});
test('FIM sends both sides of cursor and returns insertion without duplicating suffix', async t => {
  const mock = await server(t, (_, res) => json(res, { content: 'a + b\n' }));
  const result = await new CompletionClient().complete(input(mock.endpoint));
  assert.deepEqual(result, { text: 'a + b', mode: 'infill' });
  assert.equal(mock.seen[0].path, '/infill');
  assert.equal(mock.seen[0].body.input_prefix, input(mock.endpoint).prefix);
  assert.equal(mock.seen[0].body.input_suffix, '\n');
  assert.deepEqual(mock.seen[0].body.stop, ['\n']);
  assert.equal(mock.seen[0].headers.authorization, undefined);
});
test('auto falls back to chat only for unsupported FIM and remembers per-model capability', async t => {
  const mock = await server(t, (req, res) => req.url === '/infill' ? json(res, { error: { message: 'model does not support infill' } }, 400) : json(res, { choices: [{ message: { content: '```python\na + b\n```' } }] }));
  const client = new CompletionClient();
  assert.deepEqual(await client.complete(input(mock.endpoint)), { text: 'a + b', mode: 'chat' });
  await client.complete(input(mock.endpoint));
  assert.deepEqual(mock.seen.map(r => r.path), ['/infill', '/v1/chat/completions', '/v1/chat/completions']);
  const context = JSON.parse(mock.seen[1].body.messages[1].content);
  assert.equal(context.before, input(mock.endpoint).prefix);
  assert.equal(context.after, '\n');
});
test('auth errors and unrelated bad requests do not silently switch protocols', async t => {
  for (const status of [400, 401, 403, 500]) {
    const mock = await server(t, (_, res) => json(res, { error: { message: 'sensitive server details' } }, status));
    await assert.rejects(new CompletionClient().complete(input(mock.endpoint)), { code: 'HTTP_ERROR', status });
    assert.equal(mock.seen.length, 1);
  }
});
test('explicit FIM does not fall back; explicit chat bypasses /infill', async t => {
  const mock = await server(t, (req, res) => req.url === '/infill' ? json(res, {}, 404) : json(res, { choices: [{ message: { content: 'a + b' } }] }));
  await assert.rejects(new CompletionClient().complete({ ...input(mock.endpoint), mode: 'infill' }), { code: 'FIM_UNSUPPORTED' });
  assert.equal((await new CompletionClient().complete({ ...input(mock.endpoint), mode: 'chat' })).mode, 'chat');
  assert.deepEqual(mock.seen.map(r => r.path), ['/infill', '/v1/chat/completions']);
});
test('never follows HTTP redirects or forwards key to redirected server', async t => {
  const target = await server(t, (_, res) => json(res, {}));
  const source = await server(t, (_, res) => { res.writeHead(307, { Location: target.endpoint + '/capture' }); res.end(); });
  await assert.rejects(requestJson(source.endpoint, '/infill', { body: { privateCode: 'synthetic' }, apiKey: 'synthetic-test-key' }), { code: 'CONNECTION_FAILED' });
  assert.equal(source.seen[0].headers.authorization, 'Bearer synthetic-test-key');
  assert.equal(target.seen.length, 0);
});
test('cancellation and timeout terminate a pending request', async t => {
  const mock = await server(t, () => {});
  const controller = new AbortController();
  const pending = requestJson(mock.endpoint, '/wait', { signal: controller.signal, timeoutMs: 2000 });
  setTimeout(() => controller.abort(), 20);
  await assert.rejects(pending, { code: 'CANCELLED' });
  await assert.rejects(requestJson(mock.endpoint, '/wait', { timeoutMs: 20 }), { code: 'TIMEOUT' });
});
test('malformed completion is rejected instead of showing undefined', async t => {
  const mock = await server(t, (_, res) => json(res, { content: 27 }));
  await assert.rejects(new CompletionClient().complete(input(mock.endpoint)), { code: 'INVALID_RESPONSE' });
});
test('clean completion preserves indentation, trims suffix and excludes reasoning', () => {
  assert.equal(cleanCompletion('    return value\nnext()', 'def f():\n', '', false), '    return value');
  assert.equal(cleanCompletion('item)', 'call(', ')', false), 'item');
  assert.equal(cleanCompletion('<think>private reasoning</think>a + b', '', '', false), 'a + b');
  assert.equal(cleanCompletion('<think>unfinished', '', '', false), '');
  assert.equal(cleanCompletion('return a + b', 'return ', '', false), 'a + b');
  assert.equal(cleanCompletion('\n    return 1', 'def f():', '', true), '\n    return 1');
  assert.ok(!friendlyError({ message: 'sensitive server text' }).includes('sensitive'));
});
