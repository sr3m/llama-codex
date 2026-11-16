# llama.cpp Inline

Local code completion in VS Code using your llama.cpp server. Type, wait for a gray inline suggestion, and accept it with **Tab**. Press **Esc** to dismiss it.

The extension talks directly to the configured server. It does not require Codex, Copilot, or the `llama-codex` gateway.

## Install a VSIX

1. Open Extensions in VS Code.
2. From the `…` menu, select **Install from VSIX…**.
3. Choose `llama-cpp-inline-0.1.1.vsix`.
4. Reload the window if VS Code requests it.

To build the package from source, see [Development and tests](#development-and-tests).

## Get started

1. Run llama.cpp at `http://127.0.0.1:9931`, or configure your server URL.
2. Open a trusted project. Restricted Mode disables this extension.
3. Open the Command Palette with **Cmd+Shift+P** on macOS or **Ctrl+Shift+P** on Windows/Linux.
4. Run **llama.cpp: Check Connection**.
5. Run **llama.cpp: Select Model** and choose a coding model.
6. Open a code file and type. Pause briefly, then accept the suggestion with **Tab**.

For example, place the cursor after the space following `return` in this Python function:

```python
def add(a, b):
    return
```

A suitable model may suggest `a + b`. Output depends on the model and its context; review generated code before using it.

Request a suggestion manually with **llama.cpp: Complete Code Now**, **Cmd+Alt+Space** on macOS, or **Ctrl+Alt+Space** elsewhere. If your operating system intercepts the shortcut, reassign it in Keyboard Shortcuts or use the Command Palette. Click `llama.cpp` in the status bar to select a model.

## How completion works

- Reads up to 4,000 characters before the cursor and 1,500 after it, from the active document or notebook cell only.
- Waits for a 450 ms typing pause before requesting an automatic suggestion.
- In `auto` mode, calls llama.cpp's `/infill` endpoint with the code prefix and suffix.
- If the server reports that the model does not support FIM, uses `/v1/chat/completions` on the same server. That capability result is remembered for the session.
- Displays the generated fragment through VS Code's inline completion API.
- Cancels pending work when you keep typing or switch documents, and discards responses for an older document version.

By default, suggestions complete **one line**, with a maximum of 64 generated tokens. Enable `multiline` for blocks of up to 12 lines. A small coding model usually offers lower latency than a large conversational model. The extension does not download weights or start servers.

## Settings

Search for **llama.cpp Inline** in Settings. Example user configuration:

```json
{
  "editor.inlineSuggest.enabled": true,
  "llamaCppInline.endpoint": "http://127.0.0.1:9931",
  "llamaCppInline.mode": "auto",
  "llamaCppInline.enabled": true,
  "llamaCppInline.automatic": true,
  "llamaCppInline.multiline": false,
  "llamaCppInline.debounceMs": 450,
  "llamaCppInline.maxTokens": 64,
  "llamaCppInline.timeoutMs": 15000
}
```

| Setting | Purpose |
| --- | --- |
| `endpoint` | Server root URL or `/v1` URL. Defaults to local port 9931. |
| `model` | Optional exact model ID. When empty, uses your saved selection, or prefers an available loaded coding model. |
| `mode` | `auto`, `infill`, or `chat`. |
| `automatic` | Set to `false` to request suggestions manually only. |
| `multiline` | Allow suggestions spanning multiple lines. |
| `prefixChars` / `suffixChars` | Limit active-file context sent to the server. |
| `timeoutMs` | Maximum wait per HTTP request. Increase it when a model needs loading. |
| `excludedLanguages` | Languages without suggestions. Defaults to plain text, Markdown, and Git input. |

`endpoint` and `model` are machine-scoped settings: a repository cannot override them through workspace configuration. Selected models are stored separately for each endpoint. The extension runs on the UI side (`extensionKind: ui`), so in Remote SSH/WSL, `127.0.0.1` refers to the machine running the VS Code interface.

If your server requires authentication, run **llama.cpp: Set Server API Key**. The key is stored in VS Code **SecretStorage**, separately for each endpoint. Leave the input empty to remove the saved key.

## Troubleshooting

- Check the connection and model selection from the Command Palette.
- Wait for model loading; the first request may be slower. Increase `timeoutMs` if needed.
- Make sure `editor.inlineSuggest.enabled` is enabled and the document has the correct language mode.
- If the standard IntelliSense menu is open, dismiss it with **Esc** and request completion again. This version avoids competing with a selected entry in that menu.
- Hover over the status bar item to see the model, mode, or error message.
- Open **Output → llama.cpp Inline** for error codes and HTTP status. Request bodies are not logged.
- If an instruct model performs poorly with FIM, select `chat` mode or use a FIM-compatible coding model.
- Use **llama.cpp: Toggle Code Completion** to pause or resume suggestions.

## Data handling and scope

The extension has no telemetry or cloud fallback. It sends context to **the endpoint you configure**. With the default URL, code stays on your local server. If you configure another machine, that machine receives the code fragment.

It does not scan the repository or read other files to assemble context. It does not use ChatGPT credentials or modify Codex configuration or conversations. Generated code is inserted only when you accept a suggestion. HTTP redirects are rejected so code and credentials are not forwarded to a different destination.

Untrusted workspaces and files such as `.env`, private keys, and `auth.json` do not receive suggestions. This reduces accidental exposure, but any code file can contain sensitive information; choose your server accordingly.

## Development and tests

Use a Node.js version supported by the packaging tools:

```bash
npm ci --ignore-scripts
npm test
npm run test:editor
npm run package
```

The generated VSIX is written one directory above `vscode-extension/`, at the repository root.

`npm test` uses simulated HTTP servers to check cancellation, errors, redirects, bounded context, and stale responses. `test:editor` opens an isolated VS Code profile, registers the provider, and accepts a suggestion in the real editor. On macOS it attempts to use the installed application; set `VSCODE_EXECUTABLE_PATH` for another executable.

Optional real-model testing requires `LLAMA_INLINE_REAL_SERVER=1` and `LLAMA_INLINE_TEST_MODEL` set to a local FIM model identifier. It sends only a synthetic addition example.

This extension is distributed as a local VSIX and does not require Marketplace publication. MIT license.

## References

- [VS Code inline completion API](https://code.visualstudio.com/api/language-extensions/programmatic-language-features#show-inline-completions)
- [llama.cpp server and /infill API](https://github.com/ggml-org/llama.cpp/tree/master/tools/server)
- [VSIX packaging](https://code.visualstudio.com/api/working-with-extensions/publishing-extension#packaging-extensions)
