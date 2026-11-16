'use strict';
const vscode = require('vscode');
const assert = require('node:assert/strict');
const http = require('node:http');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

async function run() {
  let requests = 0;
  const server = http.createServer(async (req, res) => {
    let raw = '';
    for await (const chunk of req) raw += chunk;
    res.writeHead(200, { 'Content-Type': 'application/json' });
    if (req.url === '/v1/models') res.end(JSON.stringify({ data: [{ id: 'synthetic-coder', status: { value: 'loaded' } }] }));
    else {
      requests++;
      const body = JSON.parse(raw);
      assert.equal(req.url, '/infill');
      assert.ok(body.input_prefix.endsWith('return '));
      res.end(JSON.stringify({ content: 'a + b' }));
    }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    const config = vscode.workspace.getConfiguration('llamaCppInline');
    const endpoint = `http://127.0.0.1:${server.address().port}`;
    for (const [name, value] of Object.entries({ endpoint, enabled: true, automatic: false, model: 'synthetic-coder', debounceMs: 0 })) {
      await config.update(name, value, vscode.ConfigurationTarget.Global);
    }
    await vscode.workspace.getConfiguration('editor').update('inlineSuggest.enabled', true, vscode.ConfigurationTarget.Global);
    const extension = vscode.extensions.getExtension('sr3m.llama-cpp-inline');
    assert.ok(extension, 'Development extension was not registered');
    await extension.activate();
    const doc = await vscode.workspace.openTextDocument({ language: 'python', content: 'def sumar(a, b):\n    return ' });
    const editor = await vscode.window.showTextDocument(doc);
    const position = doc.positionAt(doc.getText().length);
    editor.selection = new vscode.Selection(position, position);
    await delay(500);
    await vscode.commands.executeCommand('llamaCppInline.complete');
    const deadline = Date.now() + 7000;
    while (!requests && Date.now() < deadline) await delay(100);
    assert.ok(requests > 0, 'VS Code did not invoke the registered provider');
    await delay(800);
    await vscode.commands.executeCommand('editor.action.inlineSuggest.commit');
    assert.equal(doc.getText(), 'def sumar(a, b):\n    return a + b', 'Ghost completion was not accepted into the real editor');
    console.log('PASS: actual VS Code activation, registered inline provider, ghost text and accept command.');
    if (process.env.LLAMA_INLINE_REAL_SERVER === '1') {
      await config.update('endpoint', 'http://127.0.0.1:9931', vscode.ConfigurationTarget.Global);
      await config.update('model', process.env.LLAMA_INLINE_TEST_MODEL, vscode.ConfigurationTarget.Global);
      await config.update('mode', 'infill', vscode.ConfigurationTarget.Global);
      const realDoc = await vscode.workspace.openTextDocument({ language: 'python', content: 'def sumar(a, b):\n    return ' });
      const realEditor = await vscode.window.showTextDocument(realDoc);
      const end = realDoc.positionAt(realDoc.getText().length);
      realEditor.selection = new vscode.Selection(end, end);
      await delay(500);
      await vscode.commands.executeCommand('llamaCppInline.complete');
      await delay(1800);
      await vscode.commands.executeCommand('editor.action.inlineSuggest.commit');
      assert.equal(realDoc.getText(), 'def sumar(a, b):\n    return a + b', 'Real local-model completion was not accepted by VS Code');
      console.log('PASS: actual llama.cpp → registered inline provider → ghost text → accepted a + b in VS Code.');
      await vscode.commands.executeCommand('workbench.action.revertAndCloseActiveEditor');
    }
    await vscode.commands.executeCommand('workbench.action.revertAndCloseActiveEditor');
  } finally {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
}
module.exports = { run };
