<p align="center">
  <img src="assets/llama-codex.svg" alt="llama-codex" width="900">
</p>

# llama-codex

Use OpenAI cloud models and local llama.cpp models in Codex through a single Python script, an interactive terminal menu, and a unified model provider. This repository also includes **llama.cpp Inline**, a VS Code extension for local code completion.

**Community project; experimental integration.** This project is independent of OpenAI and llama.cpp. Desktop integration and model catalog support depend on the installed Codex version. The script validates the catalog with your installed CLI before activation; a Codex update may require changes to the integration.

## What is included

- **Codex switch and gateway:** `llama-codex.py` combines local and cloud models under one provider.
- **VS Code inline completion:** [`vscode-extension/`](vscode-extension/README.md) sends code around the cursor directly to llama.cpp and displays suggestions you accept with Tab.
- **Development workspace:** `llama-codex.code-workspace`, with test tasks and an extension debugging configuration.
- **Architecture guide:** [How it works](docs/how-it-works.md).
- **Contributor guide:** [Local development](docs/development.md).

The script and extension can share the same llama.cpp server. They work independently: the extension does not require Codex or the unified gateway.

## Codex features

- Interactive menu with a llama.cpp-inspired lowercase block `llama-codex` wordmark, grouped actions, and the current provider/model. The wordmark uses neutral white lettering; menu colors respect `NO_COLOR`. Narrow terminals and non-Unicode output use a compact text heading.
- HTTP gateway on `127.0.0.1:9932`, using a stable provider named `codex-unified`.
- Local model discovery through llama.cpp at `127.0.0.1:9931/v1/models` and `llama-*.config.toml` profiles.
- Combined model catalog refreshed by the gateway every 10 seconds. Codex controls when its interface fetches the catalog again.
- After initial activation and one Codex restart, local and cloud models can be selected in the same chat without restarting for each model change.
- Configuration and SQLite backups before migrating existing chats. Unified migration updates provider metadata in the index and session header while preserving messages and per-chat models.
- Compatibility handling for Desktop tool notifications and other Responses API input shapes used with llama.cpp.

Conversation continuity uses the visible history that Codex replays. Each model retains its context limit, tool capabilities, and response quality. Model internals, KV caches, and another provider's encrypted reasoning are not transferred.

## Codex requirements

- macOS for the automatic LaunchAgent service.
- **Python 3.14 recommended for Desktop:** it includes the `zstd` decoder used by some requests. Python 3.11–3.13 can run the menu and tests, but cannot decode zstd-compressed requests.
- Codex signed in with ChatGPT, plus an installed CLI supporting `debug models --bundled` and `debug models`. The script searches installed apps and `PATH`; set `CODEX_CLI_BIN` to provide an explicit executable.
- For local models, llama.cpp with `/v1/responses`, listening on `127.0.0.1:9931`. A server offering only `/v1/chat/completions` is insufficient for the Codex gateway.

The cloud route uses the ChatGPT session supplied by Codex and the ChatGPT Codex service. This implementation does not provide OpenAI API-key authentication or alternative cloud providers.

## Install the Codex script

Clone the repository and review the script before installing:

```bash
git clone https://github.com/sr3m/llama-codex.git
cd llama-codex
python3 llama-codex.py --help
python3 llama-codex.py install
```

`install` is the complete setup command on macOS. It copies the script to `~/.codex/llama-codex.py`, validates the combined model catalog, installs or upgrades the login service, configures `model_provider = "codex-unified"`, and synchronizes eligible existing chats with backups. It respects `CODEX_HOME` if you use another directory. Finish active turns before installation. Reopen Codex once after initial activation.

Choose the initial model route during installation:

```bash
# Combined picker; preserve the current model when it is available
python3 llama-codex.py install

# Combined picker with a local model as the default; starts llama.cpp if needed
python3 llama-codex.py install --mode local --model 'LOCAL-MODEL-ID'

# Combined picker with an OpenAI model as the default
python3 llama-codex.py install --mode cloud --model 'CLOUD-MODEL-ID'

# Copy only, without changing the provider, service, or chats
python3 llama-codex.py install --copy-only
```

All three modes use the stable `codex-unified` provider so both services remain available in the picker. The selected model determines which upstream receives requests. `--model` is optional; when supplied it must match the selected route. This does not select a chat execution environment or grant access to local files.

Upgrades recognize the former `switch-codex.py gateway-serve` service by its command and Codex directory. They back up and replace obsolete jobs without touching unrelated LaunchAgents. Identical service definitions are left running. If connections are active, the upgrade stops with an explanation before unloading any service; finish the turns and rerun `install`. A failed service upgrade restores the previous LaunchAgent definitions and restarts previously loaded jobs. The copied script is retained if activation fails; the command reports failure, and can be retried after correcting the reported issue.

Start llama.cpp with your models on the expected port. If you use the `llama` executable with its `serve` subcommand, the menu can start it automatically. An optional preset can be placed at `~/.codex/llamacpp-codex.ini`. With `llama-server`, start the server manually using your usual model arguments and `--host 127.0.0.1 --port 9931`.

## Synchronize pending chats

`install` already enables the combined picker. If chats were locked during installation, finish active turns and close Codex, then retry synchronization:

```bash
python3 ~/.codex/llama-codex.py unified
```

Reopen Codex. Choose models in its model picker; local entries have a `· Local` suffix. **You do not need to restart the app for each model change.** Initial activation requires one restart. If a Codex instance holds a chat's writer lock, the script leaves that chat pending; close the instance and run `unified` again.

The gateway is registered as a user LaunchAgent and starts at macOS login. You can activate a cloud-only catalog while the local server and profiles are unavailable, then add local models later.

## Everyday use

```bash
# Interactive menu
python3 ~/.codex/llama-codex.py

# Refresh and validate the combined catalog
python3 ~/.codex/llama-codex.py hybrid-catalog

# Show gateway status without revealing keys
python3 ~/.codex/llama-codex.py hybrid-status

# Change the default model for future sessions
python3 ~/.codex/llama-codex.py local --model 'LOCAL-MODEL-ID'
python3 ~/.codex/llama-codex.py cloud --model 'CLOUD-MODEL-ID'

# CLI: use the configured provider with an explicit model
codex -m 'MODEL-ID'
```

For an open chat, select its model in Codex. Changing the default from the terminal does not force an existing chat to change models.

After starting llama.cpp, the gateway discovers models every 10 seconds. To force a refresh, run `hybrid-catalog` and reopen the picker. Codex may cache its model list; the script does not force the app's interface to refresh.

Models can also be declared while the server is offline. Example `~/.codex/llama-my-model.config.toml`:

```toml
model_provider = "llamacpp"
model = "my-model"
model_context_window = 32768
```

Use the exact model identifier exposed by llama.cpp and a context limit supported by both the model and server. Profiles advertise models; they do not download or load model weights themselves.

## VS Code code completion

See the [extension README](vscode-extension/README.md) for model selection, settings, and troubleshooting. To build an installable VSIX from this repository:

```bash
npm --prefix vscode-extension ci --ignore-scripts
npm --prefix vscode-extension run package
```

Install the generated `llama-cpp-inline-0.1.1.vsix` from **Extensions → … → Install from VSIX…**. Open a trusted project and run **llama.cpp: Select Model** from the Command Palette. Suggestions appear inline; accept them with **Tab** or dismiss them with **Esc**.

## Configuration and privacy

The repository contains source code, synthetic tests, and documentation. Runtime data is stored outside the repository in `CODEX_HOME`, which defaults to `~/.codex`:

| File | Purpose |
| --- | --- |
| `config.toml` | Provider, default model, and private gateway key |
| `unified/settings.json` | Random gateway key and activation state |
| `unified/catalog.json` | Combined model catalog and per-model routes |
| `unified/cloud-models.json` | Cloud catalog obtained by your installation |
| `unified/routes.jsonl` | HTTP status, local/cloud route, model, timing, and tool types |
| `backups/` | Local configuration, SQLite, and modified-history backups |

The gateway listens on loopback, requires a key for model access, and rejects requests with an `Origin` header. ChatGPT credentials are forwarded only to the fixed cloud upstream, never to llama.cpp. The gateway does not read `auth.json`. It does not log prompts, generated responses, authentication tokens, or headers. Logs do contain model identifiers; treat them as local information.

There is no automatic fallback between local and cloud services. Selecting a cloud model sends the conversation context included by Codex to OpenAI, even if some messages were generated locally.

Do not publish your `.codex` directory, personal configurations, generated catalogs, databases, backups, or logs. The included `.gitignore` reduces common mistakes, but does not replace reviewing what you commit. The gateway and local model server communicate through localhost (`127.0.0.1`) on the same computer.

## Restore direct OpenAI access

```bash
python3 ~/.codex/llama-codex.py restore
```

This migrates eligible chats to the direct OpenAI provider and updates the configuration without replacing SQLite with an older snapshot. Reopen Codex to apply the change. Backups and the background service are retained for future activation. To unload the service:

```bash
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/io.github.switch-codex.gateway.plist"
```

You can then remove that `.plist` to prevent startup at the next login. Keep your histories and database.

## Legacy direct-provider mode

The commands `local`, `cloud`, `catalog`, `watch`, `start`, `stop`, and `status` remain available. When the active provider is not `codex-unified`, `local` and `cloud` perform the earlier switch, synchronizing provider/model metadata across eligible chats and requiring a Desktop restart. In that mode, define `llamacpp` with `base_url = "http://127.0.0.1:9931/v1"` and `wire_api = "responses"`. For cloud selection, provide `--model` or create `openai-cloud.config.toml`.

Enable `unified` first to switch models in the picker without restarting for each change.

## Cloud chat file-access notice

If a chat shows **“This chat was started in the cloud, so ChatGPT won't be able to access files on your computer unless you add them to your conversation”**, it is a ChatGPT cloud-chat notice, not a llama.cpp connection error. A cloud model and a cloud-hosted chat are different concepts: a local Codex chat can use an OpenAI model and still use its permitted local tools.

The provider switch configures Codex model routing. It does not move ChatGPT chats into the local Codex SQLite database or grant them filesystem permissions. To work with files on this computer, use the chat menu's **Continue in Work mode** option and select the computer execution option when offered, or open a local Codex chat in the project folder and provide the relevant context. You can also attach files to the existing cloud chat. Availability and wording depend on the installed app. Existing cloud chats are left intact.

## Tests and development

### Verified macOS environment

The local verification below was performed on **November 16, 2026**:

| Component | Verified version |
| --- | --- |
| macOS | 27.2.1, build `26B131` |
| Architecture | Apple Silicon (`arm64`) |
| Python used for the local regression suite | 3.14.6 |
| Standalone Codex CLI | `codex-cli 0.169.0` |
| Codex CLI bundled with the desktop app | `codex-cli 0.169.0-alpha.11.1` |
| Desktop host application | ChatGPT 26.1111.21308, build `27194` |
| Running llama.cpp server | `b12375-9d25e0014`, reported by `/props` |

The local Python regression suite passed. Live checks also confirmed that the installed script matched the repository, the gateway responded, both installed Codex catalog readers accepted the 23-model catalog (10 cloud and 13 local), and the chat database passed SQLite's integrity check. These checks do not establish compatibility with every model or every Codex release.

The GitHub Actions regression matrix also passed on `macos-latest` and `ubuntu-latest`, with Python **3.11** and **3.14**. Those jobs use isolated fixtures and simulated services; they do not run the desktop app, a real llama.cpp model, or authenticated OpenAI requests. Runner images and Python patch versions can change; consult the individual workflow logs for their exact environment.

### Run the tests

```bash
python3 tests/run.py
npm --prefix vscode-extension test
```

The Python tests use temporary directories, synthetic SQLite databases, simulated LaunchAgents, and loopback HTTP servers. They do not read your authentication, modify your chats, or send requests to OpenAI or a real model. They check automatic setup, initial route selection, legacy service migration, active connection protection, idempotency, credential isolation, SSE forwarding, upstream errors, message normalization, rollback, and preservation of chat data.

Open `llama-codex.code-workspace` for test tasks and extension debugging with F5. See [Local development](docs/development.md) for editor integration tests and packaging. Optional `dist/` and `local-archive/` directories in a local working copy contain generated packages or session records and are ignored by Git.

Compatibility depends on Codex, llama.cpp, and the selected model. Include versions and a synthetic example in bug reports. Remove IP addresses, hostnames, personal paths, and credentials from attachments and screenshots.

## License

MIT. See [LICENSE](LICENSE).
