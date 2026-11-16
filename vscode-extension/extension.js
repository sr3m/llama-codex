'use strict';
const vscode = require('vscode');
const crypto = require('node:crypto');
const { normalizeEndpoint, listModels, chooseDefaultModel, friendlyError } = require('./lib/client');
const { createProvider } = require('./lib/provider');

function activate(context) {
  const output = vscode.window.createOutputChannel('llama.cpp Inline');
  const bar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 90);
  bar.name = 'llama.cpp Inline'; bar.command = 'llamaCppInline.selectModel';
  const defaults = { enabled: true, automatic: true, endpoint: 'http://127.0.0.1:9931', model: '', mode: 'auto',
    multiline: false, debounceMs: 450, maxTokens: 64, prefixChars: 4000, suffixChars: 1500,
    timeoutMs: 15000, excludedLanguages: ['plaintext', 'markdown', 'scminput', 'git-commit'] };
  const config = uri => {
    const settings = vscode.workspace.getConfiguration('llamaCppInline', uri);
    return Object.fromEntries(Object.entries(defaults).map(([k, v]) => [k, settings.get(k, v)]));
  };
  let currentModel = '';
  let statusKind = 'ready';
  let lastMode = '';
  let discovered;
  const secretName = endpoint => 'server-key-' + crypto.createHash('sha256').update(normalizeEndpoint(endpoint)).digest('hex');
  const apiKey = endpoint => context.secrets.get(secretName(endpoint));
  const savedModel = endpoint => {
    try { return context.globalState.get('model:' + normalizeEndpoint(endpoint), ''); } catch { return ''; }
  };
  const refreshBar = () => {
    const settings = config(vscode.window.activeTextEditor?.document.uri);
    const enabled = settings.enabled && vscode.workspace.isTrusted;
    const icon = !enabled ? 'circle-slash' : statusKind === 'loading' ? 'sync~spin' : statusKind === 'error' ? 'warning' : 'code';
    const model = currentModel || settings.model || savedModel(settings.endpoint);
    bar.text = `$(${icon}) llama.cpp${!enabled ? ' OFF' : ''}`;
    bar.tooltip = !enabled ? 'Code completion disabled' : statusKind === 'error' ? currentModel :
      `Model: ${model || 'automatic selection'}${lastMode ? '\nMode: ' + lastMode : ''}\nClick to choose a model. Accept suggestions with Tab.`;
    bar.show();
  };
  const status = (kind, label, mode) => {
    statusKind = kind; currentModel = label; lastMode = mode || ''; refreshBar();
  };
  const resolveModel = async (settings, signal) => {
    if (settings.model) return settings.model;
    const saved = savedModel(settings.endpoint);
    if (saved) return saved;
    const endpoint = normalizeEndpoint(settings.endpoint);
    if (!discovered || discovered.endpoint !== endpoint || Date.now() - discovered.time > 30000) {
      const models = await listModels(endpoint, { signal, timeoutMs: settings.timeoutMs, apiKey: await apiKey(endpoint) });
      discovered = { endpoint, models, time: Date.now() };
    }
    return chooseDefaultModel(discovered.models);
  };
  const provider = createProvider(vscode, {
    config, apiKey, model: resolveModel, status,
    idle: () => { if (statusKind === 'loading') { statusKind = 'ready'; refreshBar(); } },
    log: (code, http) => output.appendLine(`${new Date().toISOString()} ${code} HTTP ${http}`)
  });
  const command = (name, action) => context.subscriptions.push(vscode.commands.registerCommand(name, async () => {
    try { return await action(); }
    catch (error) { vscode.window.showErrorMessage('llama.cpp: ' + friendlyError(error)); }
  }));
  command('llamaCppInline.selectModel', async () => {
    const settings = config(vscode.window.activeTextEditor?.document.uri);
    const key = await apiKey(settings.endpoint);
    const models = await vscode.window.withProgress({ location: vscode.ProgressLocation.Window, title: 'Fetching llama.cpp models' },
      () => listModels(settings.endpoint, { timeoutMs: settings.timeoutMs, apiKey: key }));
    if (!models.length) { vscode.window.showWarningMessage('llama.cpp does not report any available models.'); return; }
    const best = chooseDefaultModel(models);
    const items = [...models].sort((a, b) => Number(b.id === best) - Number(a.id === best) || a.id.localeCompare(b.id)).map(m => ({
      label: m.id, description: m.loaded ? 'Loaded' : 'Loads on first use',
      detail: m.id === best ? 'Automatic preference: available coding model' : undefined
    }));
    const picked = await vscode.window.showQuickPick(items, { title: 'llama.cpp · Code completion model', placeHolder: 'Choose a coding model; smaller models usually respond faster' });
    if (!picked) return;
    await context.globalState.update('model:' + normalizeEndpoint(settings.endpoint), picked.label);
    // An explicit model setting takes priority; selecting here intentionally clears it.
    await vscode.workspace.getConfiguration('llamaCppInline').update('model', '', vscode.ConfigurationTarget.Global);
    provider.cancel(); discovered = undefined; status('ready', picked.label);
  });
  command('llamaCppInline.configureServer', async () => {
    const value = await vscode.window.showInputBox({ title: 'llama.cpp server', value: config().endpoint,
      prompt: 'Root URL or /v1. Context from the active file will be sent to this server.',
      validateInput: text => { try { normalizeEndpoint(text); return undefined; } catch { return 'Enter a valid http/https URL without credentials or query parameters.'; } } });
    if (!value) return;
    await vscode.workspace.getConfiguration('llamaCppInline').update('endpoint', normalizeEndpoint(value), vscode.ConfigurationTarget.Global);
    discovered = undefined; currentModel = ''; refreshBar();
  });
  command('llamaCppInline.setApiKey', async () => {
    const endpoint = config().endpoint;
    const value = await vscode.window.showInputBox({ title: 'llama.cpp server key', password: true,
      prompt: 'Stored in VS Code SecretStorage. Leave empty to remove the key for this server.' });
    if (value === undefined) return;
    if (value) await context.secrets.store(secretName(endpoint), value);
    else await context.secrets.delete(secretName(endpoint));
    vscode.window.showInformationMessage('Server key updated.');
  });
  command('llamaCppInline.checkConnection', async () => {
    const settings = config();
    const models = await listModels(settings.endpoint, { timeoutMs: settings.timeoutMs, apiKey: await apiKey(settings.endpoint) });
    vscode.window.showInformationMessage(`llama.cpp connected: ${models.length} models available.`);
  });
  command('llamaCppInline.toggle', async () => {
    const settings = vscode.workspace.getConfiguration('llamaCppInline');
    await settings.update('enabled', !settings.get('enabled', true), vscode.ConfigurationTarget.Global);
    provider.cancel(); refreshBar();
  });
  command('llamaCppInline.complete', async () => {
    if (!vscode.window.activeTextEditor) return;
    if (!config(vscode.window.activeTextEditor.document.uri).enabled) { vscode.window.showInformationMessage('Enable llama.cpp using Toggle Code Completion.'); return; }
    return vscode.commands.executeCommand('editor.action.inlineSuggest.trigger');
  });
  context.subscriptions.push(output, bar, provider,
    vscode.languages.registerInlineCompletionItemProvider([
      { scheme: 'file' }, { scheme: 'untitled' }, { scheme: 'vscode-notebook-cell' }
    ], provider),
    vscode.workspace.onDidChangeConfiguration(event => {
      if (event.affectsConfiguration('llamaCppInline')) { provider.cancel(); discovered = undefined; currentModel = ''; refreshBar(); }
    }),
    vscode.workspace.onDidChangeTextDocument(event => {
      if (event.document.uri.toString() === vscode.window.activeTextEditor?.document.uri.toString()) provider.cancel();
    }),
    vscode.window.onDidChangeActiveTextEditor(() => { provider.cancel(); refreshBar(); })
  );
  refreshBar();
  // Useful for isolated extension-host tests; it never reads secrets or workspace files on activation.
  return { provider };
}
module.exports = { activate };
