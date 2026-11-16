'use strict';

class ServerError extends Error {
  constructor(code, status = 0) {
    super(code);
    this.name = 'ServerError';
    this.code = code;
    this.status = status;
  }
}

function normalizeEndpoint(value) {
  let url;
  try { url = new URL(String(value).trim()); } catch { throw new ServerError('INVALID_ENDPOINT'); }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
    throw new ServerError('INVALID_ENDPOINT');
  }
  url.pathname = url.pathname.replace(/\/+$/, '').replace(/\/v1$/, '') + '/';
  return url.toString().replace(/\/$/, '');
}

async function requestJson(endpoint, path, { body, apiKey, signal, timeoutMs = 15000 } = {}) {
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (signal?.aborted) controller.abort();
  signal?.addEventListener('abort', abort, { once: true });
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
  try {
    const headers = { Accept: 'application/json' };
    if (body) headers['Content-Type'] = 'application/json';
    if (apiKey) headers.Authorization = 'Bearer ' + apiKey;
    const response = await fetch(normalizeEndpoint(endpoint) + path, {
      method: body ? 'POST' : 'GET', headers, body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal, redirect: 'error'
    });
    // Server errors may contain prompts or file text. Never expose them in logs/UI.
    if (!response.ok) {
      let fimUnsupported = [404, 501].includes(response.status);
      if (response.status === 400) {
        const text = (await response.text()).slice(0, 8192);
        fimUnsupported = /(?:does not support|not support|unsupported|missing|not available|no).{0,100}(?:infill|fim)|(?:infill|fim).{0,100}(?:not support|unsupported|not available|missing)/i.test(text);
      }
      throw new ServerError(path === '/infill' && fimUnsupported ? 'FIM_UNSUPPORTED' : 'HTTP_ERROR', response.status);
    }
    const data = await response.json();
    if (!data || typeof data !== 'object') throw new ServerError('INVALID_RESPONSE');
    return data;
  } catch (error) {
    if (error instanceof ServerError) throw error;
    if (timedOut) throw new ServerError('TIMEOUT');
    if (signal?.aborted) throw new ServerError('CANCELLED');
    throw new ServerError('CONNECTION_FAILED');
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', abort);
  }
}

async function listModels(endpoint, options) {
  const data = await requestJson(endpoint, '/v1/models', options);
  if (!Array.isArray(data.data)) throw new ServerError('INVALID_MODELS');
  return data.data.filter(m => typeof m.id === 'string' && m.id.trim()).map(m => ({
    id: m.id, loaded: m.status?.value === 'loaded'
  }));
}

function chooseDefaultModel(models) {
  const score = m => (/coder|codegemma|starcoder|deepseek.*code/i.test(m.id) ? 20 : 0) + (m.loaded ? 10 : 0);
  return [...models].sort((a, b) => score(b) - score(a) || a.id.localeCompare(b.id))[0]?.id;
}

function cleanCompletion(text, prefix, suffix, multiline) {
  if (typeof text !== 'string') throw new ServerError('INVALID_RESPONSE');
  let value = text.replace(/\r\n/g, '\n').replace(/<think>[\s\S]*?<\/think>/g, '');
  if (value.includes('<think>')) return '';
  value = value.replace(/^\s*```[^\n]*\n/, '').replace(/\n```\s*$/, '');
  const currentLine = prefix.slice(prefix.lastIndexOf('\n') + 1);
  if (currentLine.trim() && value.startsWith(currentLine)) value = value.slice(currentLine.length);
  const marker = value.search(/<\|(?:fim_|im_end|endoftext|eot_id)/);
  if (marker >= 0) value = value.slice(0, marker);
  if (!multiline) value = value.split('\n')[0];
  else value = value.split('\n').slice(0, 12).join('\n');
  for (let size = Math.min(suffix.length, value.length, 2000); size > 0; size--) {
    if (value.endsWith(suffix.slice(0, size))) { value = value.slice(0, -size); break; }
  }
  return value.trim() ? value : '';
}

class CompletionClient {
  constructor() { this.chatOnly = new Set(); }
  async complete({ endpoint, model, mode = 'auto', prefix, suffix, language, maxTokens = 64, multiline = false, ...options }) {
    const base = normalizeEndpoint(endpoint);
    const key = base + '\n' + model;
    const useInfill = mode === 'infill' || (mode === 'auto' && !this.chatOnly.has(key));
    let data;
    if (useInfill) {
      try {
        data = await requestJson(base, '/infill', { ...options, body: {
          model, input_prefix: prefix, input_suffix: suffix, n_predict: maxTokens,
          temperature: 0, stream: false, cache_prompt: true, ...(multiline ? {} : { stop: ['\n'] })
        } });
        if (typeof data.content !== 'string') throw new ServerError('INVALID_RESPONSE');
        return { text: cleanCompletion(data.content, prefix, suffix, multiline), mode: 'infill' };
      } catch (error) {
        if (mode !== 'auto' || error.code !== 'FIM_UNSUPPORTED') throw error;
        this.chatOnly.add(key);
      }
    }
    data = await requestJson(base, '/v1/chat/completions', { ...options, body: {
      model, stream: false, temperature: 0, max_tokens: maxTokens,
      reasoning_effort: 'none', chat_template_kwargs: { enable_thinking: false },
      ...(multiline ? {} : { stop: ['\n'] }),
      messages: [
        { role: 'system', content: 'You complete code at the cursor. The user supplies JSON with language, before and after. Return ONLY the missing code inserted between before and after. Do not repeat existing code, explain, or use Markdown fences.' },
        { role: 'user', content: JSON.stringify({ language, before: prefix, after: suffix }) }
      ]
    } });
    const text = data.choices?.[0]?.message?.content;
    return { text: cleanCompletion(text, prefix, suffix, multiline), mode: 'chat' };
  }
}

function friendlyError(error) {
  if (error.code === 'TIMEOUT') return 'The model took too long. Try a smaller model or increase the timeout.';
  if (error.code === 'INVALID_ENDPOINT') return 'Use an http/https URL without a username, password, or query parameters.';
  if (error.code === 'FIM_UNSUPPORTED') return 'The model does not support /infill. Select auto or chat mode.';
  if ([401, 403].includes(error.status)) return 'The server requires a valid key. Use Set Server API Key.';
  if (error.code === 'CONNECTION_FAILED') return 'Could not connect to llama.cpp. Check the server and port.';
  if (error.code === 'CANCELLED') return 'Request cancelled.';
  return 'The server did not return a valid response. Check the model and its compatibility.';
}

module.exports = { ServerError, normalizeEndpoint, requestJson, listModels, chooseDefaultModel, cleanCompletion, CompletionClient, friendlyError };
