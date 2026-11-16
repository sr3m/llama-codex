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
    bar.tooltip = !enabled ? 'Autocompletado desactivado' : statusKind === 'error' ? currentModel :
      `Modelo: ${model || 'selección automática'}${lastMode ? '\nModo: ' + lastMode : ''}\nPulsa para elegir modelo. Acepta sugerencias con Tab.`;
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
    const models = await vscode.window.withProgress({ location: vscode.ProgressLocation.Window, title: 'Consultando modelos de llama.cpp' },
      () => listModels(settings.endpoint, { timeoutMs: settings.timeoutMs, apiKey: key }));
    if (!models.length) { vscode.window.showWarningMessage('llama.cpp no anuncia modelos disponibles.'); return; }
    const best = chooseDefaultModel(models);
    const items = [...models].sort((a, b) => Number(b.id === best) - Number(a.id === best) || a.id.localeCompare(b.id)).map(m => ({
      label: m.id, description: m.loaded ? 'Cargado' : 'Se cargará al usarlo',
      detail: m.id === best ? 'Preferencia automática: modelo Coder disponible' : undefined
    }));
    const picked = await vscode.window.showQuickPick(items, { title: 'llama.cpp · Modelo para completar código', placeHolder: 'Elige un modelo de código; uno pequeño suele responder antes' });
    if (!picked) return;
    await context.globalState.update('model:' + normalizeEndpoint(settings.endpoint), picked.label);
    // An explicit model setting takes priority; selecting here intentionally clears it.
    await vscode.workspace.getConfiguration('llamaCppInline').update('model', '', vscode.ConfigurationTarget.Global);
    provider.cancel(); discovered = undefined; status('ready', picked.label);
  });
  command('llamaCppInline.configureServer', async () => {
    const value = await vscode.window.showInputBox({ title: 'Servidor llama.cpp', value: config().endpoint,
      prompt: 'URL raíz o /v1. Se enviará contexto del archivo activo a este servidor.',
      validateInput: text => { try { normalizeEndpoint(text); return undefined; } catch { return 'URL http/https válida, sin credenciales ni parámetros.'; } } });
    if (!value) return;
    await vscode.workspace.getConfiguration('llamaCppInline').update('endpoint', normalizeEndpoint(value), vscode.ConfigurationTarget.Global);
    discovered = undefined; currentModel = ''; refreshBar();
  });
  command('llamaCppInline.setApiKey', async () => {
    const endpoint = config().endpoint;
    const value = await vscode.window.showInputBox({ title: 'Clave de llama.cpp', password: true,
      prompt: 'Se guarda en SecretStorage de VS Code. Vacío elimina la clave de este servidor.' });
    if (value === undefined) return;
    if (value) await context.secrets.store(secretName(endpoint), value);
    else await context.secrets.delete(secretName(endpoint));
    vscode.window.showInformationMessage('Clave del servidor actualizada.');
  });
  command('llamaCppInline.checkConnection', async () => {
    const settings = config();
    const models = await listModels(settings.endpoint, { timeoutMs: settings.timeoutMs, apiKey: await apiKey(settings.endpoint) });
    vscode.window.showInformationMessage(`llama.cpp conectado: ${models.length} modelos disponibles.`);
  });
  command('llamaCppInline.toggle', async () => {
    const settings = vscode.workspace.getConfiguration('llamaCppInline');
    await settings.update('enabled', !settings.get('enabled', true), vscode.ConfigurationTarget.Global);
    provider.cancel(); refreshBar();
  });
  command('llamaCppInline.complete', async () => {
    if (!vscode.window.activeTextEditor) return;
    if (!config(vscode.window.activeTextEditor.document.uri).enabled) { vscode.window.showInformationMessage('Activa llama.cpp con Activar/desactivar autocompletado.'); return; }
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
