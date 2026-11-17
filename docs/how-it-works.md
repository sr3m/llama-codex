# How llama-codex works

`llama-codex` configures Codex and can run a small intermediary server. It routes requests by the model identifier you select; it does not evaluate task difficulty or choose a model automatically.

## Request flow

```text
Choose a model in Codex
          |
          v
codex-unified provider
http://127.0.0.1:9932
          |
          +-- Local model --> llama.cpp: http://127.0.0.1:9931/v1
          |
          +-- Cloud model --> OpenAI's Codex service
          |
          +-- Remote model --> User-configured inference server
```

Localhost refers to the computer running the application. The gateway and llama.cpp use the loopback address `127.0.0.1` to communicate on that computer, with port `9932` for the gateway and port `9931` for llama.cpp.

## Installation and service upgrades

`install` performs activation automatically. `--mode local` or `--mode cloud` selects an initial model route while retaining `codex-unified`; `--copy-only` opts out of activation. The installer validates model selection before changing the provider or migrating chats. Selecting local also starts llama.cpp using the existing startup logic.

The LaunchAgent points to the installed `llama-codex.py` and records a source hash to detect upgrades. Old jobs are recognized by their script command and Codex home, rather than a user-specific label. Before replacing jobs the installer checks established gateway connections. It backs up old definitions, removes obsolete login jobs, starts the replacement, and checks health. Failed upgrades restore the previous definitions and previously loaded jobs. Repeating installation with the same service definition does not restart it.

The installer does not relocate ChatGPT cloud chats or change their execution permissions. Model routing and chat file access are separate settings; see the README's cloud-chat notice troubleshooting section.

## 1. Back up and inspect configuration

The script works with `config.toml`, model catalogs, and the `state_N.sqlite` database in the Codex directory. Before migration it backs up the configuration and uses SQLite's backup API, which includes data still in the WAL.

Copying only a SQLite file while an application is writing to it can omit recent changes; the backup API avoids that problem.

## 2. The original switch changed providers

The original approach switched between `openai` and `llamacpp`, updating model selections and chat metadata. Codex needed to reload configuration to apply the new provider, so Desktop had to be reopened.

## 3. A stable provider enables the combined picker

In unified mode, Codex always uses `codex-unified`. Its URL points to the gateway on your Mac. Selecting another model changes the upstream that receives that request.

The script maintains a routing table equivalent to:

```python
routes = {
    "local-model": "local",
    "cloud-model": "cloud"
}

destination = routes[request["model"]]
```

Local requests go to llama.cpp. Cloud requests go to OpenAI's Codex service. Optional `service:<id>` routes resolve to privately configured Responses-compatible endpoints. Their catalog slugs use `service/<id>/<upstream-model-id>`; the gateway restores the original model ID before forwarding. A local failure does not trigger an automatic cloud fallback.

Initial activation requires reopening Codex once. Subsequent model changes use the same provider.

## 4. The catalog tells Codex which models to offer

A list of names is insufficient. Codex needs model entries with identifiers, display names, context limits, reasoning capabilities, modalities, and other schema fields.

The script reads a template from the installed Codex CLI, creates entries for llama.cpp models, and combines them with OpenAI entries. Local models are discovered through `/v1/models` and `llama-*.config.toml` profiles. The generated JSON is validated with the installed CLI's actual catalog reader.

The gateway serves the combined catalog and refreshes it periodically. Codex determines when to fetch it again, so the app's cache can delay the appearance of a newly added model.

## 5. Synchronize provider metadata to retain existing chats

Two representations matter:

- SQLite contains the index used to list chats.
- JSONL files contain chat events and the `session_meta` header.

Changing only `config.toml` can make the app list a different set of chats for the selected provider. Changing only SQLite is insufficient because Codex can rebuild provider metadata from the history header.

Unified migration therefore updates `threads.model_provider` and the provider in `session_meta`, with backups and rollback. It preserves messages, per-chat models, and other database fields. Histories locked by an active Codex instance are left pending.

The script does not create a new memory system or transform model weights. Codex sends the applicable conversation context to the next model. Context limits, summaries, and model capabilities still apply: preserving files does not allow a model to process an unlimited history.

## 6. Adapt input shapes for llama.cpp

Codex can send additional tool definitions, messages without an explicit `type`, notifications without `call_id`, and encrypted reasoning from a cloud model.

For local requests, the gateway normalizes these structures: it supplies required types, preserves notifications as user data, converts certain tool calls, and collects system/developer instructions into one initial block for model templates that require it. Encrypted reasoning from another provider is excluded because it cannot be interpreted locally.

These adaptations resolved the tested cases of `Cannot determine type of 'item'`. Different Codex versions, llama.cpp builds, and model templates can introduce additional compatibility issues.

## 7. Keep credentials within the correct route

Codex supplies session authentication to the gateway. The gateway forwards the required credentials only to OpenAI. It creates fresh local request headers without the ChatGPT token, cookies, or account identifier. It does not need to read `auth.json`.

The gateway listens on loopback, requires a local key, and refuses redirects. Logs record routing status and timing, not prompts or generated responses. Selecting cloud sends the context included by Codex to OpenAI, even if earlier messages were generated locally.

## The VS Code extension follows a separate path

Code completion does not need to pass through Codex:

```text
Code before the cursor + code after the cursor
                       |
                       v
              llama.cpp Inline extension
                       |
                       v
              llama.cpp /infill or chat
                       |
                       v
              Suggested code in gray
                       |
                       v
              Tab inserts it into the editor
```

FIM means *Fill In the Middle*: the model receives the code before and after the cursor and generates the missing section. This allows completion of an expression without rewriting the entire file.

The extension waits for a short typing pause, requests a completion, and cancels it if you continue typing. A suggestion is inserted only when accepted. The extension uses the configured llama.cpp server directly and does not modify Codex configuration or chats.

## Optional API services

`services.json` is private runtime state, outside the repository. `services add` or the setup assistant registers a named endpoint; no additional services are enabled by default. Presets supply protocol defaults, while users choose URLs and optional credentials. Service models are discovered from `/models`, declared explicitly, or retained from the last catalog during an outage. Disabled services are omitted and also checked again on each generation request.

Each service request rebuilds headers from its own key file or environment variable. Client ChatGPT credentials and cookies are not forwarded. The llama.cpp adapter reuses the tested local normalization; the generic Responses adapter preserves native tool calls while removing provider-specific reasoning and metadata. Services do not share upstream response IDs. No cross-service fallback occurs.

## Picker grouping and availability

The final catalog is sorted into OpenAI, local llama.cpp, and additional service groups. Display names use `OpenAI ·`, `Local ·`, or `<service name> ·` prefixes. An internal service ID is appended only to disambiguate duplicate or reserved service names. Consecutive priorities preserve group order. This is a presentation step: it does not rename model IDs or alter the route/upstream-model maps, instructions, capabilities, or context limits. Rebuilding the same catalog does not duplicate prefixes.

The optional `only_online` preference filters additional services through authenticated discovery probes. The default remains false for backward compatibility. Known remote model metadata is stored separately from the visible catalog, so filtering an unavailable service does not discard its models. The cache contains model IDs/context limits, without service URLs or credentials. Registry edits preserve the preference; deleting a service removes its cache entry on the next build. The gateway still checks enabled service state on each generation request, independently of presentation.
