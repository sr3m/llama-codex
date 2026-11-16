'use strict';
const { CompletionClient, friendlyError } = require('./client');

function sensitiveDocument(uri) {
  const path = decodeURIComponent(uri.path || '').replace(/\\/g, '/');
  return /(?:^|\/)\.env(?:\.[^/]*)?$|\.(?:pem|key|p12|pfx)$|(?:^|\/)(?:auth\.json|credentials(?:\.json)?|id_rsa|id_ed25519)$|(?:^|\/)\.git\//i.test(path);
}

function wait(ms, signal) {
  return new Promise((resolve, reject) => {
    const abort = () => { clearTimeout(timer); signal.removeEventListener('abort', abort); reject(Object.assign(new Error('cancelled'), { code: 'CANCELLED' })); };
    const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, ms);
    if (signal.aborted) abort();
    else signal.addEventListener('abort', abort, { once: true });
  });
}

function createProvider(vscode, services) {
  const client = services.client || new CompletionClient();
  let active;
  let generation = 0;
  let cache;
  let cooldownUntil = 0;
  const cancel = () => { generation++; active?.abort(); active = undefined; services.idle(); };
  return {
    cancel,
    dispose() { cancel(); cache = undefined; },
    async provideInlineCompletionItems(document, position, context, token) {
      const config = services.config(document.uri);
      const automatic = context.triggerKind === vscode.InlineCompletionTriggerKind.Automatic;
      if (!vscode.workspace.isTrusted || !config.enabled || (automatic && !config.automatic) ||
          token.isCancellationRequested || config.excludedLanguages.includes(document.languageId) ||
          sensitiveDocument(document.uri) || context.selectedCompletionInfo ||
          (automatic && Date.now() < cooldownUntil)) return [];
      cancel();
      const current = generation;
      const controller = new AbortController();
      active = controller;
      const subscription = token.onCancellationRequested(() => controller.abort());
      const version = document.version;
      const offset = document.offsetAt(position);
      const prefix = document.getText(new vscode.Range(document.positionAt(Math.max(0, offset - config.prefixChars)), position));
      const suffix = document.getText(new vscode.Range(position, document.positionAt(offset + config.suffixChars)));
      if (!prefix.trim()) { subscription.dispose(); active = undefined; return []; }
      try {
        await wait(automatic ? config.debounceMs : 0, controller.signal);
        const model = await services.model(config, controller.signal);
        if (!model) throw new Error('No model');
        const key = JSON.stringify([document.uri.toString(), version, offset, config.endpoint, model, config.mode, config.multiline]);
        let result;
        if (cache?.key === key && Date.now() - cache.time < 15000) result = cache.result;
        else {
          services.status('loading', model);
          const apiKey = await services.apiKey(config.endpoint);
          result = await client.complete({ ...config, model, prefix, suffix, language: document.languageId, signal: controller.signal, apiKey });
          if (controller.signal.aborted || generation !== current || document.version !== version) return [];
          cache = { key, result, time: Date.now() };
        }
        if (controller.signal.aborted || generation !== current || document.version !== version || token.isCancellationRequested) return [];
        cooldownUntil = 0;
        services.status('ready', model, result.mode);
        if (!result.text) return [];
        return [new vscode.InlineCompletionItem(result.text, new vscode.Range(position, position))];
      } catch (error) {
        if (controller.signal.aborted || token.isCancellationRequested || generation !== current) return [];
        cooldownUntil = Date.now() + 10000;
        services.status('error', friendlyError(error));
        services.log(error.code || 'ERROR', error.status || 0);
        return [];
      } finally {
        subscription.dispose();
        if (generation === current) {
          active = undefined;
          services.idle();
        }
      }
    }
  };
}
module.exports = { createProvider, sensitiveDocument, wait };
