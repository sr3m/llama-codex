#!/usr/bin/env python3
"""Unified Codex menu: providers, models, catalog, and llama.cpp. Python >= 3.11."""
from __future__ import annotations

import contextlib

import fcntl

import json

import os

from pathlib import Path

import re

import shutil

import subprocess

import tempfile

import tomllib

CODEX_DIR = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')).expanduser().resolve()

CONFIG = CODEX_DIR / 'config.toml'

CATALOG = CODEX_DIR / 'local-models/catalog.json'

DESKTOP_CLI = Path('/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex')

BEGIN = '# BEGIN CODEX PROVIDER SWITCH (managed)'

END = '# END CODEX PROVIDER SWITCH'

MANAGED_KEYS = {
    'model', 'model_provider', 'model_catalog_json', 'model_reasoning_effort',
    'model_context_window', 'model_auto_compact_token_limit', 'web_search',
    'model_verbosity', 'service_tier', 'profile',
}

@contextlib.contextmanager
def locked():
    CODEX_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (CODEX_DIR / '.provider-switch.lock').open('a') as handle:
        os.chmod(handle.name, 0o600)
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield

def atomic_write(path: Path, data: str | bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            os.chmod(temporary, path.stat().st_mode & 0o777 if path.exists() else 0o600)
            handle.write(data.encode() if isinstance(data, str) else data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + '\n'

def read_toml(path: Path):
    return tomllib.loads(path.read_text())

def config_with_selection(original: str, selection: dict):
    """Keep unrelated TOML and comments byte-for-byte, replacing only root keys."""
    parsed = tomllib.loads(original)
    # All managed root values must be scalars. Refuse unfamiliar multiline syntax.
    original = re.sub(r'(?ms)^' + re.escape(BEGIN) + r'\n.*?^' + re.escape(END) + r'\n?', '', original)
    result = []
    root = True
    for line in original.splitlines(keepends=True):
        if line.lstrip().startswith('['):
            root = False
        match = re.match(r'^\s*([A-Za-z_][A-Za-z_0-9]*)\s*=', line)
        if root and match and match[1] in MANAGED_KEYS:
            if not isinstance(parsed.get(match[1]), (str, int, bool, float)):
                raise ValueError('Unsupported root value: ' + match[1])
            continue
        result.append(line)
    block = BEGIN + '\n'
    for key, value in selection.items():
        block += key + ' = ' + json.dumps(value, ensure_ascii=False) + '\n'
    block += END + '\n\n'
    updated = block + ''.join(result).lstrip('\n')
    check = tomllib.loads(updated)
    for key, value in selection.items():
        if check.get(key) != value:
            raise ValueError('TOML verification failed: ' + key)
    return updated

def cli_paths():
    candidates = [DESKTOP_CLI,
        Path('/Applications/Codex.app/Contents/Resources/codex'),
        Path.home() / 'Applications/Codex.app/Contents/Resources/codex',
        Path.home() / 'Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex']
    if os.environ.get('CODEX_CLI_BIN'):
        candidates.insert(0, Path(os.environ['CODEX_CLI_BIN']).expanduser())
    system_cli = shutil.which('codex')
    if system_cli:
        candidates.append(Path(system_cli))
    return list(dict.fromkeys(str(p) for p in candidates if p.exists()))

def validate_catalog(path: Path):
    """Use both installed Rust readers, not just JSON syntax validation."""
    data = json.loads(path.read_text())
    expected = {m['slug'] for m in data['models']}
    if not expected or len(expected) != len(data['models']):
        raise ValueError('Catalog must contain unique models')
    binaries = cli_paths()
    if not binaries:
        raise RuntimeError('Cannot locate Codex to validate model catalog')
    for binary in binaries:
        cmd = [binary, '-c', 'model_provider="llamacpp"', '-c',
               'model_providers.llamacpp={name="llama.cpp",base_url="http://127.0.0.1:9931/v1",wire_api="responses"}', '-c',
               'model_catalog_json=' + json.dumps(str(path)), 'debug', 'models']
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=40)
        if proc.returncode:
            raise RuntimeError(f'{binary}: {proc.stderr.strip()}')
        actual = {m['slug'] for m in json.loads(proc.stdout)['models']}
        if not expected.issubset(actual):
            raise ValueError(f'{binary} did not load all local models')
    return len(expected)

import argparse

import copy

import hashlib

import json

from pathlib import Path

import subprocess

import sys

import tempfile

import time

import urllib.error

import urllib.request
import urllib.parse

class NoLocalModels(RuntimeError):
    pass


def discover(offline=False):
    models = {}
    for path in sorted(CODEX_DIR.glob('llama-*.config.toml')):
        profile = read_toml(path)
        if profile.get('model_provider') != 'llamacpp' or not profile.get('model'):
            continue
        slug = profile['model']
        context = int(profile.get('model_context_window', 32768))
        models[slug] = {'context': context, 'modalities': ['text'], 'source': path.name,
                        'loaded': False}
    warning = None
    if not offline:
        config = read_toml(CODEX_DIR / 'config.toml')
        base = config.get('model_providers', {}).get('llamacpp', {}).get('base_url',
                                                                              'http://127.0.0.1:9931/v1')
        # Local discovery must never follow redirects or send credentials elsewhere.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        try:
            with opener.open(base.rstrip('/') + '/models', timeout=5) as response:
                remote = json.load(response)
            if not isinstance(remote.get('data'), list):
                raise ValueError('/v1/models does not contain data[]')
            for item in remote['data']:
                slug = item.get('id')
                if not isinstance(slug, str) or not slug.strip():
                    continue
                old = models.get(slug, {})
                # Actual loaded context overrides training context. Profile limits are caps.
                context = item.get('meta', {}).get('n_ctx') or item.get('context_length') or old.get('context', 32768)
                context = int(context)
                if slug in models:
                    context = min(context, old['context'])
                modalities = item.get('architecture', {}).get('input_modalities', ['text'])
                models[slug] = {'context': context, 'modalities': [x for x in modalities if x in ('text', 'image')],
                                'source': 'llama.cpp /v1/models',
                                'loaded': item.get('status', {}).get('value') == 'loaded'}
        except (OSError, ValueError) as error:
            warning = f'Server unavailable: {error}. Using local profiles.'
    if not models:
        raise NoLocalModels('No models found. Start llama.cpp or create a llama-*.config.toml profile.')
    return models, warning

def template():
    # The bundled reader exposes base_instructions, which the cache omits in this build.
    for binary in cli_paths():
        proc = subprocess.run([binary, 'debug', 'models', '--bundled'],
                              capture_output=True, text=True, timeout=30)
        if proc.returncode == 0:
            entries = json.loads(proc.stdout)['models']
            candidate = next((m for m in entries if m['slug'] == 'gpt-5.5'), entries[0])
            if candidate.get('base_instructions'):
                return candidate
    raise RuntimeError('Could not read the model template from the installed Codex CLI.')

def build_catalog(offline=False):
    discovered, warning = discover(offline)
    base = template()
    # Copy every field, including any newly required by a later installed version.
    cache = CODEX_DIR / 'models_cache.codex_work_desktop.json'
    if cache.exists():
        cached = json.loads(cache.read_text()).get('models', [])
        cached_template = next((m for m in cached if m['slug'] == base['slug']), None)
        if cached_template:
            base = {**cached_template, **base}
    entries = []
    for index, (slug, details) in enumerate(sorted(discovered.items(), key=lambda p: (not p[1]['loaded'], p[0].lower()))):
        model = copy.deepcopy(base)
        model.update({
            'slug': slug, 'display_name': slug + ' (local)',
            'description': 'llama.cpp local · ' + details['source'],
            'priority': index, 'visibility': 'list', 'supported_in_api': True,
            'context_window': details['context'], 'max_context_window': details['context'],
            'default_reasoning_level': 'none',
            'supported_reasoning_levels': [{'effort': 'none', 'description': 'Local configuration without additional reasoning effort'}],
            'base_instructions': 'You are a helpful coding assistant running locally through llama.cpp. '
                                 'Use the available tools to inspect and modify files when requested. '
                                 'Follow the user instructions and report results accurately.',
            'model_messages': None, 'upgrade': None, 'availability_nux': None,
            'additional_speed_tiers': [], 'service_tiers': [], 'default_service_tier': None,
            'available_access_programs': {'cyber': []}, 'support_verbosity': False,
            'supports_search_tool': False, 'supports_image_detail_original': False,
            'supports_experimental_context': False, 'use_responses_lite': False,
            'supports_reasoning_effort_updates': False, 'experimental_supported_tools': [],
            'input_modalities': details['modalities'] or ['text'],
            'multi_agent_reasoning_effort': None, 'node_repl_auto_review_required': False,
            'comp_hash': 'local-' + hashlib.sha256(slug.encode()).hexdigest()[:12],
        })
        for optional in ('tool_mode', 'multi_agent_version', 'model_specialty'):
            model.pop(optional, None)
        entries.append(model)
    return {'models': entries}, warning

def update(offline=False, quiet=False):
    with locked():
        data, warning = build_catalog(offline)
        encoded = json_text(data)
        CATALOG.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.catalog-check-', suffix='.json', dir=CATALOG.parent)
        import os
        os.close(fd)
        check_path = Path(name)
        try:
            check_path.write_text(encoded)
            count = validate_catalog(check_path)
            changed = not CATALOG.exists() or CATALOG.read_text() != encoded
            if changed:
                atomic_write(CATALOG, encoded)
        finally:
            check_path.unlink(missing_ok=True)
        if warning and not quiet:
            print(warning, file=sys.stderr)
        if not quiet or changed:
            print(f'Catalog {"updated" if changed else "unchanged"}: {count} models · {CATALOG}', flush=True)
        return data

import argparse

import contextlib

import datetime

import fcntl

import json

from pathlib import Path

import shutil

import sqlite3

import sys

STATE = CODEX_DIR / 'provider_switch_state.json'

PROVIDERS = {'local': 'llamacpp', 'cloud': 'openai'}

ELIGIBLE = "('openai','llamacpp','local_models','codex-unified')"

def rewrite_metadata(path, provider, model, effort):
    """Only session provider and the latest model-selection metadata may change.

    All message/event lines remain byte-for-byte unchanged. The current Desktop
    repairs its SQL index from these fields when thread/list scans the rollouts.
    """
    original = path.read_bytes()
    lines = original.splitlines(keepends=True)
    if not lines:
        raise ValueError(f'Empty session: {path}')
    meta = json.loads(lines[0])
    if meta.get('type') != 'session_meta':
        raise ValueError(f'Unknown session header: {path}')
    changed = False
    if meta['payload'].get('model_provider') != provider:
        meta['payload']['model_provider'] = provider
        lines[0] = (json.dumps(meta, ensure_ascii=False, separators=(',', ':')) + '\n').encode()
        changed = True
    # Updating just SQL.model is also undone by the scan of the latest turn_context.
    for index in range(len(lines) - 1, -1, -1):
        if b'"turn_context"' not in lines[index]:
            continue
        event = json.loads(lines[index])
        if event.get('type') != 'turn_context':
            continue
        payload = event['payload']
        before = json.dumps(payload, sort_keys=True)
        payload['model'] = model
        payload['effort'] = effort
        settings = (payload.get('collaboration_mode') or {}).get('settings')
        if isinstance(settings, dict):
            settings['model'] = model
            settings['reasoning_effort'] = effort
        if before != json.dumps(payload, sort_keys=True):
            lines[index] = (json.dumps(event, ensure_ascii=False, separators=(',', ':')) + '\n').encode()
            changed = True
        break
    return original, b''.join(lines) if changed else original

def database_path(config, explicit=None):
    if explicit:
        candidates = [Path(explicit).expanduser().resolve()]
    else:
        root = Path(config.get('sqlite_home', str(CODEX_DIR))).expanduser()
        candidates = sorted(root.glob('state_*.sqlite'),
                            key=lambda p: int(p.stem.split('_')[-1]) if p.stem.split('_')[-1].isdigit() else -1,
                            reverse=True)
    for path in candidates:
        if not path.is_file():
            continue
        con = sqlite3.connect(f'{path.as_uri()}?mode=ro', uri=True)
        try:
            columns = {r[1] for r in con.execute('PRAGMA table_info(threads)')}
            if {'id', 'model_provider', 'rollout_path'}.issubset(columns):
                return path
        finally:
            con.close()
    raise RuntimeError('No valid state_N.sqlite database found in the configured directory.')

def initial_state(config):
    saved = json.loads(STATE.read_text()) if STATE.exists() else {}
    if not saved:
        saved = {'version': 1, 'preferences': {}, 'selection': {}, 'cloud_options': {
            key: config[key] for key in ('model_reasoning_effort', 'web_search', 'model_verbosity', 'service_tier')
            if key in config}}
        if config.get('model_provider', 'openai') == 'openai' and config.get('model'):
            saved['selection']['cloud'] = {'model': config['model']}
    if saved.get('version') != 1:
        raise RuntimeError('Unknown provider_switch_state.json version')
    return saved

def select_options(mode, config, state, requested=None):
    profile_path = CODEX_DIR / ('local-models.config.toml' if mode == 'local' else 'openai-cloud.config.toml')
    profile = read_toml(profile_path) if profile_path.exists() else {}
    if mode == 'local':
        models = json.loads(CATALOG.read_text())['models']
        available = {m['slug']: m for m in models}
        model = requested or state['selection'].get('local', {}).get('model') or models[0]['slug']
        if model not in available:
            raise ValueError(f'Local model missing from the catalog: {model}. Run llama-codex.py catalog.')
        return {'model_provider': 'llamacpp', 'model': model,
                'model_catalog_json': str(CATALOG), 'model_reasoning_effort': 'none',
                'web_search': 'disabled'}
    model = requested or state['selection'].get('cloud', {}).get('model') or profile.get('model')
    if not model:
        raise RuntimeError('Set a model in openai-cloud.config.toml or use --model.')
    return {'model_provider': 'openai', 'model': model, **state['cloud_options']}

def status(config, db):
    print('Configured provider:', config.get('model_provider', 'openai'))
    print('Configured model:', config.get('model', '(default)'))
    print('Catalog:', config.get('model_catalog_json') or config.get('model_providers',{}).get(config.get('model_provider','openai'),{}).get('model_catalog_url') or '(OpenAI catalog)')
    print('SQLite:', db)
    with sqlite3.connect(f'{db.as_uri()}?mode=ro', uri=True) as con:
        print('Chats by provider:', dict(con.execute('SELECT model_provider, COUNT(*) FROM threads GROUP BY model_provider')))

def switch(args):
    if args.mode == 'local' and not args.dry_run:
        update()
    with locked():
        original = CONFIG.read_text()
        config = read_toml(CONFIG)
        db = database_path(config, args.db)
        if args.status:
            status(config, db)
            return
        state = initial_state(config)
        selection = select_options(args.mode, config, state, args.model)
        updated = config_with_selection(original, selection)
        # Ensure the provider exists and has the required Responses transport.
        provider = config.get('model_providers', {}).get('llamacpp', {})
        if selection['model_provider'] == 'llamacpp' and (
                provider.get('base_url') != 'http://127.0.0.1:9931/v1' or provider.get('wire_api') != 'responses'):
            raise RuntimeError('The llamacpp provider does not have the expected URL or wire_api.')
        con = sqlite3.connect(f'{db.as_uri()}?mode=rw', uri=True, timeout=15)
        con.row_factory = sqlite3.Row
        backup = None
        config_written = False
        state_written = False
        previous_state = STATE.read_bytes() if STATE.exists() else None
        rollout_backups = []
        writer_locks = contextlib.ExitStack()
        skipped = []
        missing = 0
        changed_count = 0
        try:
            columns = [row[1] for row in con.execute('PRAGMA table_info(threads)')]
            rows = con.execute('SELECT * FROM threads WHERE model_provider IN ' + ELIGIBLE).fetchall()
            if args.dry_run:
                print(json_text({'mode': args.mode, 'selection': selection, 'database': str(db), 'threads': len(rows)}))
                return
            timestamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
            backup = CODEX_DIR / 'backups' / ('switch-' + timestamp + '-' + args.mode)
            backup.mkdir(parents=True, mode=0o700)
            shutil.copy2(CONFIG, backup / 'config.toml')
            if STATE.exists():
                shutil.copy2(STATE, backup / STATE.name)
            # SQLite backup API includes WAL contents and is safe with the app open.
            dest = sqlite3.connect(backup / db.name)
            try:
                con.backup(dest)
            finally:
                dest.close()
            (backup / db.name).chmod(0o600)
            con.execute('BEGIN IMMEDIATE')
            # Re-read after obtaining the write lock, including any newly created threads.
            rows = con.execute('SELECT * FROM threads WHERE model_provider IN ' + ELIGIBLE).fetchall()
            atomic_write(backup / 'threads-before.json', json_text([{
                key: row[key] for key in ('id', 'model_provider', 'model', 'reasoning_effort') if key in columns
            } for row in rows]))
            destination = PROVIDERS[args.mode]
            local_ids = {m['slug'] for m in json.loads(CATALOG.read_text())['models']} if CATALOG.exists() else set()
            for row in rows:
                thread_id = row['id']
                rollout = Path(row['rollout_path'])
                if rollout.is_file():
                    lock_path = CODEX_DIR / 'thread-writer-locks' / (thread_id + '.lock')
                    lock_path.parent.mkdir(parents=True, exist_ok=True)
                    handle = lock_path.open('a')
                    try:
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        handle.close()
                        skipped.append(thread_id)
                        continue
                    writer_locks.enter_context(handle)
                else:
                    missing += 1
                prefs = state['preferences'].setdefault(thread_id, {})
                old_provider = 'llamacpp' if row['model_provider'] == 'local_models' else row['model_provider']
                prefs[old_provider] = {k: row[k] for k in ('model', 'reasoning_effort') if k in columns}
                preference = prefs.get(destination, {})
                model = preference.get('model') or selection['model']
                if args.model or (args.mode == 'local' and model not in local_ids):
                    model = selection['model']
                reasoning = preference.get('reasoning_effort') or selection.get('model_reasoning_effort', 'low')
                if args.mode == 'local':
                    reasoning = 'none'
                assignments = {'model_provider': destination}
                if 'model' in columns:
                    assignments['model'] = model
                if 'reasoning_effort' in columns:
                    assignments['reasoning_effort'] = reasoning
                if rollout.is_file():
                    original_rollout, new_rollout = rewrite_metadata(rollout, destination, model, reasoning)
                    if new_rollout != original_rollout:
                        backup_path = backup / 'rollouts' / (thread_id + '.jsonl')
                        atomic_write(backup_path, original_rollout)
                        rollout_backups.append((rollout, backup_path))
                        atomic_write(rollout, new_rollout)
                con.execute('UPDATE threads SET ' + ', '.join(k + '=?' for k in assignments) + ' WHERE id=?',
                            [*assignments.values(), thread_id])
                changed_count += 1
            protected = [k for k in columns if k not in ('model_provider', 'model', 'reasoning_effort')]
            after = {row['id']: row for row in con.execute('SELECT * FROM threads WHERE model_provider IN ' + ELIGIBLE)}
            if len(after) != len(rows) or any(
                    any(row[k] != after[row['id']][k] for k in protected) for row in rows):
                raise RuntimeError('Verification detected changes outside the provider/model fields.')
            state['selection'][args.mode] = {'model': selection['model']}
            state['active'] = args.mode
            state['last_backup'] = str(backup)
            state['database'] = str(db)
            state['pending_active_threads'] = skipped
            atomic_write(backup / 'rollout-manifest.json', json_text([
                {'path': str(path), 'backup': str(copy)} for path, copy in rollout_backups]))
            atomic_write(STATE, json_text(state))
            state_written = True
            # Detect an editor/desktop write since the start; never overwrite it silently.
            if CONFIG.read_text() != original:
                raise RuntimeError('config.toml changed during the switch; run the command again.')
            atomic_write(CONFIG, updated)
            config_written = True
            con.commit()
        except BaseException:
            con.rollback()
            for path, copy in reversed(rollout_backups):
                atomic_write(path, copy.read_bytes())
            if config_written:
                atomic_write(CONFIG, original)
            if state_written:
                if previous_state is None:
                    STATE.unlink(missing_ok=True)
                else:
                    atomic_write(STATE, previous_state)
            raise
        finally:
            con.close()
            writer_locks.close()
        print(f'Switched to {args.mode}: {selection["model"]}')
        print(f'{changed_count} entries synchronized in {db}; message content and stored context preserved.')
        print(f'{len(rollout_backups)} session files: provider/model metadata synchronized and backed up.')
        if missing:
            print(f'Note: {missing} entries already pointed to missing histories; their index is preserved without fabricating content.')
        if skipped:
            print(f'Pending: {len(skipped)} locked active chats. Close the app/CLI and run the same command again.')
        print('Backup:', backup)
        print('Close and reopen Codex Desktop to apply the provider and refresh the model picker.')


# Interactive entry point. Everything above is embedded here so this script
# does not import or invoke any of the previous Python scripts.
import shlex
import signal

LLAMA = Path(os.environ.get('LLAMA_CPP_BIN') or shutil.which('llama') or Path.home() / '.local/bin/llama').expanduser()
SERVER_URL = 'http://127.0.0.1:9931'
SERVER_PRESET = CODEX_DIR / 'llamacpp-codex.ini'
SERVER_LOG = CODEX_DIR / 'logs/llamacpp-provider-switch.log'
SERVER_PID = CODEX_DIR / 'llamacpp-provider-switch.pid'
SERVER_COMMAND = [str(LLAMA), 'serve', '--host', '127.0.0.1', '--port', '9931']
if SERVER_PRESET.is_file():
    SERVER_COMMAND += ['--models-preset', str(SERVER_PRESET)]


def server_models():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(SERVER_URL + '/v1/models', timeout=2) as response:
        payload = json.load(response)
    if not isinstance(payload.get('data'), list):
        raise ValueError('Port 9931 does not return a compatible model list.')
    return payload['data']


def server_online():
    try:
        server_models()
        return True
    except (OSError, ValueError):
        return False


def listener_pids():
    result = subprocess.run(['/usr/sbin/lsof', '-t', '-iTCP:9931', '-sTCP:LISTEN'],
                            capture_output=True, text=True, timeout=5)
    return sorted({int(p) for p in result.stdout.split()})


def process_command(pid):
    result = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'command='],
                            capture_output=True, text=True, timeout=5)
    return shlex.split(result.stdout.strip()) if result.returncode == 0 else []


def start_server():
    with locked():
        if server_online():
            print('llama.cpp is already available at ' + SERVER_URL)
            return
        if listener_pids():
            raise RuntimeError('Port 9931 is occupied by a service that does not respond as llama.cpp.')
        if not LLAMA.is_file():
            raise RuntimeError('Could not find llama. Start llama.cpp on port 9931 or set LLAMA_CPP_BIN.')
        SERVER_LOG.parent.mkdir(parents=True, exist_ok=True)
        with SERVER_LOG.open('ab') as output:
            process = subprocess.Popen(SERVER_COMMAND, stdin=subprocess.DEVNULL, stdout=output,
                                       stderr=output, start_new_session=True)
        atomic_write(SERVER_PID, str(process.pid) + '\n')
        print('Starting llama.cpp in the background...', flush=True)
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f'llama.cpp exited during startup. Check {SERVER_LOG}')
            if server_online():
                print('Server ready at ' + SERVER_URL)
                return
            time.sleep(0.25)
        raise RuntimeError(f'The server is still starting. Check its status or inspect {SERVER_LOG}')


def stop_server():
    with locked():
        pids = listener_pids()
        if not pids:
            print('The local server is already stopped.')
            return
        if len(pids) != 1 or process_command(pids[0]) != SERVER_COMMAND:
            raise RuntimeError('The server on port 9931 was not started with this preset. '
                               'Stop that server from its original terminal.')
        pid = pids[0]
        # Refuse to interrupt an active generation. GET /slots is read-only.
        for model in server_models():
            if model.get('status', {}).get('value') != 'loaded':
                continue
            query = urllib.parse.urlencode({'model': model['id']})
            with urllib.request.urlopen(SERVER_URL + '/slots?' + query, timeout=3) as response:
                slots = json.load(response)
            if not isinstance(slots, list) or any(slot.get('is_processing') for slot in slots):
                raise RuntimeError('A local generation is in progress; wait for it to finish and try again.')
        if process_command(pid) != SERVER_COMMAND:
            raise RuntimeError('The server process changed; check its status again.')
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if pid not in listener_pids():
                SERVER_PID.unlink(missing_ok=True)
                print('llama.cpp server stopped.')
                return
            time.sleep(0.25)
        raise RuntimeError('The server is shutting down. Check its status before starting it again.')


def show_status():
    config = read_toml(CONFIG)
    status(config, database_path(config))
    try:
        models = server_models()
        print(f'llama.cpp: running · {len(models)} models available')
        loaded = [m['id'] for m in models if m.get('status', {}).get('value') == 'loaded']
        if loaded:
            print('Loaded:', ', '.join(loaded))
    except (OSError, ValueError):
        print('llama.cpp: stopped or unavailable')
    if STATE.exists():
        saved = json.loads(STATE.read_text())
        print('Last backup:', saved.get('last_backup', '(none)'))
        if saved.get('pending_active_threads'):
            print('Pending chats from the last switch:', len(saved['pending_active_threads']))


def choose_model(mode):
    if mode == 'local':
        start_server()
        catalog = update()
        entries = [m for m in catalog['models'] if catalog.get('routes',{}).get(m['slug'],'local')=='local']
    else:
        entries = []
        # The Desktop cache may contain the merged local/cloud list. Prefer
        # route metadata so the OpenAI submenu contains only cloud models.
        unified = read_toml(CONFIG).get('model_provider') == UNIFIED_PROVIDER
        for name in ('unified/catalog.json', 'models_cache.codex_work_desktop.json', 'models_cache.json'):
            if name == 'unified/catalog.json' and not unified:
                continue
            path = CODEX_DIR / name
            if path.exists():
                cached = json.loads(path.read_text())
                entries = [m for m in cached.get('models', [])
                           if m.get('visibility') == 'list' and m.get('slug')
                           and (name != 'unified/catalog.json' or cached.get('routes', {}).get(m['slug']) == 'cloud')]
                if entries:
                    break
    config = read_toml(CONFIG)
    saved = initial_state(config)
    default = saved.get('selection', {}).get(mode, {}).get('model', '(default)')
    print('\n  ' + terminal_text('LOCAL MODELS' if mode == 'local' else 'OPENAI MODELS', '1;36'))
    print(f'  [0] Restore per-chat preferences (last default: {default})')
    for number, model in enumerate(entries, 1):
        print('  ' + terminal_text(f'[{number}]', '36') + ' ' + model.get('display_name', model['slug']))
    print('  [m] Enter a model identifier')
    print('  [v] Return to the menu')
    while True:
        answer = input(terminal_text('\n  model [0] > ', '1;36')).strip().lower()
        if answer == 'v':
            return False, None
        if not answer or answer == '0':
            # A stale saved default must not prevent switching to a currently available model.
            if mode == 'local' and default not in {m['slug'] for m in entries}:
                return True, entries[0]['slug']
            return True, None
        if answer == 'm':
            model = input('Model identifier (leave empty to go back): ').strip()
            if model:
                if mode == 'local' and model not in {m['slug'] for m in entries}:
                    print('That model is not in the local catalog.')
                    continue
                return True, model
        elif answer.isdigit() and 1 <= int(answer) <= len(entries):
            return True, entries[int(answer) - 1]['slug']
        else:
            print('Choose a valid number, m, or v.')


def perform_switch(mode, model=None, dry_run=False, db=None):
    if read_toml(CONFIG).get('model_provider') == UNIFIED_PROVIDER:
        unified_select(mode, model, dry_run)
        return
    switch(argparse.Namespace(mode=mode, model=model, dry_run=dry_run, db=db, status=False))


def watch_catalog(interval=5):
    if interval < 2:
        raise ValueError('The interval must be at least two seconds.')
    print(f'Updating every {interval:g} seconds. Ctrl+C returns to the menu.', flush=True)
    print('In unified mode, the catalog is served dynamically; reopen the picker to view it.')
    try:
        while True:
            try:
                update(quiet=True)
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                print(f'Update error: {error}', file=sys.stderr)
            time.sleep(interval)
    except KeyboardInterrupt:
        print('\nCatalog watcher stopped.')


def terminal_text(text, style):
    """Use color only on terminals that support it, honoring NO_COLOR."""
    if sys.stdout.isatty() and os.environ.get('TERM') != 'dumb' and 'NO_COLOR' not in os.environ:
        return '\033[' + style + 'm' + text + '\033[0m'
    return text


def print_banner():
    # Lowercase half-block lettering, like the llama.cpp startup wordmark.
    # Keep it self-contained: no font packages, images, or extra processes.
    font = {
        'l': ('██', '██', '██', '██', '██'),
        'a': ('     ', '     ', '▀▀█▄ ', '▄█▀██', '▀█▄██'),
        'm': ('        ', '        ', '███▄██▄ ', '██ ██ ██', '██ ██ ██'),
        '-': ('   ', '   ', '   ', '███', '   '),
        'c': ('     ', '     ', '▄████', '██   ', '▀████'),
        'o': ('     ', '     ', '▄███▄', '██ ██', '▀███▀'),
        'd': ('   ██', '   ██', '▄████', '██ ██', '▀████'),
        'e': ('     ', '     ', '▄███▄', '██▄██', '▀█▄▄▄'),
        'x': ('     ', '     ', '▀█▄█▀', ' ▄█▄ ', '▄█▀█▄'),
    }
    rows = ['  ' + ' '.join(font[letter][row] for letter in 'llama-codex').rstrip()
            for row in range(5)]
    width = shutil.get_terminal_size(fallback=(80, 24)).columns
    try:
        ''.join(rows).encode(sys.stdout.encoding or 'utf-8')
        supports_blocks = True
    except UnicodeEncodeError:
        supports_blocks = False
    print()
    if supports_blocks and width >= max(map(len, rows)):
        for row in rows:
            print(terminal_text(row, '37'))
    else:
        print(terminal_text('  llama-codex', '1;37'))
    print('  ' + terminal_text('llama-codex / OpenAI + llama.cpp', '1'))
    print('  ' + terminal_text('Local and cloud models. One place.', '2'))


def print_menu(config):
    width = max(1, min(shutil.get_terminal_size(fallback=(80, 24)).columns - 2, 68))
    print('\n  ' + terminal_text('-' * width, '2'))
    print('  ' + terminal_text('Provider', '2') + '  ' + str(config.get('model_provider', 'openai')))
    print('  ' + terminal_text('Model   ', '2') + '  ' + str(config.get('model', 'default')))
    print('  ' + terminal_text('-' * width, '2'))
    groups = (
        ('MODELS', (('1', 'Choose a local model'), ('2', 'Choose an OpenAI model'))),
        ('CATALOG', (('3', 'Refresh models'), ('4', 'Watch for models every 5 seconds'))),
        ('SERVER', (('5', 'Start llama.cpp'), ('6', 'Stop llama.cpp'))),
        ('SETUP', (('7', 'Status and backups'), ('8', 'Enable / synchronize unified provider'),
                   ('9', 'Restore direct OpenAI access'))),
    )
    for label, entries in groups:
        print('\n  ' + terminal_text(label, '1;2'))
        for key, label in entries:
            print('  ' + terminal_text('[' + key + ']', '36') + ' ' + label)
    print('\n  ' + terminal_text('[0]', '36') + ' Exit')
    print('  ' + terminal_text('Local selection starts llama.cpp when needed.', '2'))


def menu():
    print_banner()
    if not sys.stdin.isatty():
        # Piped input is useful for scripting and smoke tests; EOF exits cleanly.
        print('Reading menu input from stdin. Use --help to list direct commands.')
    while True:
        try:
            config = read_toml(CONFIG)
            print_menu(config)
            choice = input(terminal_text('\n  llama-codex > ', '1;36')).strip()
            if choice == '0':
                print('Goodbye.')
                return 0
            if choice in ('1', '2'):
                mode = 'local' if choice == '1' else 'cloud'
                selected, model = choose_model(mode)
                if selected:
                    perform_switch(mode, model)
            elif choice == '3':
                update()
                print('Catalog updated. All unified catalog models share the same provider.')
            elif choice == '4':
                watch_catalog()
            elif choice == '5':
                start_server()
            elif choice == '6':
                stop_server()
            elif choice == '7':
                show_status()
            elif choice == '8':
                unified_activate()
            elif choice == '9':
                unified_restore()
            else:
                print('Invalid option; choose a menu number.')
        except EOFError:
            print('\nMenu closed.')
            return 0
        except KeyboardInterrupt:
            print('\nOperation cancelled; returning to the menu.')
        except (OSError, ValueError, RuntimeError, sqlite3.Error, subprocess.SubprocessError) as error:
            print(f'Error: {error}', file=sys.stderr)


# Unified gateway: this section is embedded in llama-codex.py at installation.
import collections
import datetime
import gzip
import hmac
import http.server
import plistlib
import secrets
import threading
import zlib

UNIFIED_HOME = CODEX_DIR / 'unified'
UNIFIED_SETTINGS = UNIFIED_HOME / 'settings.json'
UNIFIED_CATALOG = UNIFIED_HOME / 'catalog.json'
UNIFIED_CLOUD_CATALOG = UNIFIED_HOME / 'cloud-models.json'
UNIFIED_PROVIDER = 'codex-unified'
UNIFIED_PORT = 9932
UNIFIED_URL = 'http://127.0.0.1:9932'
UNIFIED_HEADER = 'X-Codex-Unified-Key'
UNIFIED_LABEL = 'io.github.switch-codex.gateway'
UNIFIED_AGENT = Path.home() / 'Library/LaunchAgents' / (UNIFIED_LABEL + '.plist')
OPENAI_UPSTREAM = 'https://chatgpt.com/backend-api/codex'
LOCAL_UPSTREAM = 'http://127.0.0.1:9931/v1'
MAX_UNIFIED_BODY = 64 * 1024 * 1024


def unified_settings():
    return json.loads(UNIFIED_SETTINGS.read_text())


def unified_prepare():
    UNIFIED_HOME.mkdir(parents=True, exist_ok=True, mode=0o700)
    UNIFIED_HOME.chmod(0o700)
    if not UNIFIED_SETTINGS.exists():
        atomic_write(UNIFIED_SETTINGS, json_text({'version': 1, 'key': secrets.token_urlsafe(32)}))
    UNIFIED_SETTINGS.chmod(0o600)
    if not UNIFIED_CLOUD_CATALOG.exists():
        cache = {'models': []}
        for filename in ('models_cache.codex_work_desktop.json', 'models_cache.json'):
            path = CODEX_DIR / filename
            if path.exists():
                cache = json.loads(path.read_text())
                if cache.get('models'):
                    break
        if not cache.get('models'):
            for binary in cli_paths():
                result = subprocess.run([binary, 'debug', 'models', '--bundled'],
                                        capture_output=True, text=True, timeout=30)
                if result.returncode == 0:
                    cache = json.loads(result.stdout)
                    break
        if not cache.get('models'):
            raise RuntimeError('Open Codex and sign in, or install a CLI that supports debug models --bundled.')
        # Preserve a separate upstream catalog; Codex's own cache will eventually
        # contain the merged gateway catalog and must not become its cloud seed.
        cloud = [m for m in cache['models'] if m['slug'].startswith(('gpt-', 'codex-'))]
        atomic_write(UNIFIED_CLOUD_CATALOG, json_text({'models':cloud}))


def unified_catalog_build(offline=False):
    try:
        local, warning = build_catalog(offline)
    except NoLocalModels as error:
        local, warning = {'models': []}, str(error)
    if warning and UNIFIED_CATALOG.exists():
        previous = json.loads(UNIFIED_CATALOG.read_text())
        known = {m['slug'] for m in local['models']}
        local['models'].extend(m for m in previous['models']
            if previous.get('routes',{}).get(m['slug']) == 'local' and m['slug'] not in known)
    bundled = None
    for binary in cli_paths():
        result = subprocess.run([binary, 'debug', 'models', '--bundled'], capture_output=True,
                                text=True, timeout=30)
        if result.returncode == 0:
            bundled = {m['slug']: m for m in json.loads(result.stdout)['models']}
            break
    if not bundled:
        raise RuntimeError('Could not read the catalog bundled with Codex.')
    cloud = json.loads(UNIFIED_CLOUD_CATALOG.read_text()).get('models', [])
    fallback = bundled.get('gpt-6.1-sol', next(iter(bundled.values())))
    models = []
    routes = {}
    for remote in cloud:
        slug = remote['slug']
        model = copy.deepcopy({**bundled.get(slug, fallback), **remote})
        model['slug'] = slug
        model.setdefault('base_instructions', bundled.get(slug, fallback)['base_instructions'])
        # A single HTTP Responses transport supports replay when switching upstreams.
        model['use_responses_lite'] = False
        models.append(model)
        routes[slug] = 'cloud'
    for index, model in enumerate(local['models']):
        slug = model['slug']
        if slug in routes:
            raise ValueError('Duplicate model across providers: ' + slug)
        model['display_name'] = model['display_name'].removesuffix(' (local)') + ' · Local'
        model['priority'] = 100 + index
        model['use_responses_lite'] = False
        models.append(model)
        routes[slug] = 'local'
    return {'models': models, 'routes': routes}, warning


def unified_catalog_update(validate=True, quiet=False, offline=False):
    unified_prepare()
    with locked():
        catalog, warning = unified_catalog_build(offline)
        data = json_text(catalog)
        if validate:
            check = UNIFIED_HOME / '.catalog-check.json'
            try:
                atomic_write(check, data)
                validate_catalog(check)
            finally:
                check.unlink(missing_ok=True)
        changed = not UNIFIED_CATALOG.exists() or UNIFIED_CATALOG.read_text() != data
        if changed:
            atomic_write(UNIFIED_CATALOG, data)
    if not quiet:
        counts = collections.Counter(catalog['routes'].values())
        print(f'Unified catalog: {counts["cloud"]} OpenAI models and {counts["local"]} local models.')
        if warning:
            print(warning)
    return catalog


class UnifiedNoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def unified_upstream_headers(headers, route):
    # This is the credential boundary: llama.cpp receives no client credentials,
    # account IDs, cookies, or cloud request metadata.
    if route == 'local':
        return {'Content-Type': 'application/json', 'Accept': 'text/event-stream'}
    forwarded = {'Content-Type': 'application/json'}
    names = {'authorization', 'chatgpt-account-id', 'openai-beta', 'openai-organization',
             'openai-project', 'originator', 'user-agent', 'accept', 'session_id',
             'conversation_id', 'version'}
    for name, value in headers.items():
        lower = name.lower()
        if lower in names or lower.startswith(('x-codex-', 'x-openai-')):
            if lower != UNIFIED_HEADER.lower():
                forwarded[name] = value
    return forwarded


def unified_local_body(body):
    body = copy.deepcopy(body)
    normalized = []
    announced_tools = []
    if isinstance(body.get('input'), list):
        for item in body['input']:
            if not isinstance(item, dict):
                raise ValueError('Local Responses input must contain objects.')
            kind = item.get('type')
            if kind == 'reasoning':
                # Encrypted cloud reasoning is not visible conversation content.
                continue
            if kind == 'additional_tools':
                # Codex can announce tools as input metadata. llama.cpp accepts
                # tool definitions only in the top-level tools collection.
                tools = item.get('tools', [])
                if isinstance(tools, list):
                    announced_tools.extend(tools)
                continue
            if kind in ('function_call_output', 'custom_tool_call_output') and not item.get('call_id'):
                # Desktop emits out-of-band tool notifications with name/namespace
                # and output but no call_id. They are data, not a paired tool reply.
                label = '.'.join(str(item.get(k)) for k in ('namespace', 'name') if item.get(k))
                output = item.get('output', '')
                if not isinstance(output, str):
                    output = json.dumps(output, ensure_ascii=False)
                normalized.append({'type': 'message', 'role': 'user', 'content': [
                    {'type': 'input_text', 'text': f'Tool notification ({label or "unnamed"}):\n{output}'}]})
                continue
            if kind == 'custom_tool_call':
                # Preserve already-executed freeform calls when replaying history.
                item = {**item, 'type': 'function_call',
                        'arguments': json.dumps({'input': item.get('input', '')}, ensure_ascii=False)}
                item.pop('input', None)
            elif kind == 'custom_tool_call_output':
                item['type'] = 'function_call_output'
            elif kind is None and item.get('role') in ('user', 'assistant', 'developer', 'system'):
                # EasyInputMessage permits an omitted type; llama.cpp's assistant
                # branch requires it explicitly, unlike its user-message branch.
                item['type'] = 'message'
            normalized.append(item)
        # llama.cpp maps every developer message to a system message. Several
        # model templates require exactly one initial system block. Preserve the
        # instruction text and its source role, keeping user/tool data separate.
        instruction_parts = []
        original_instructions = body.pop('instructions', None)
        if original_instructions is not None:
            if not isinstance(original_instructions, str):
                raise ValueError('Responses instructions must be a string.')
            if original_instructions:
                instruction_parts.append({'type': 'input_text', 'text': original_instructions})
        conversation = []
        for item in normalized:
            if item.get('type') == 'message' and item.get('role') in ('system', 'developer'):
                content = item.get('content', [])
                if isinstance(content, str):
                    content = [{'type': 'input_text', 'text': content}]
                if not isinstance(content, list):
                    raise ValueError('Message content must be text or a list.')
                instruction_parts.append({'type': 'input_text',
                                          'text': f'\n\n[{item["role"]} instructions]\n'})
                instruction_parts.extend(content)
            else:
                conversation.append(item)
        if instruction_parts:
            conversation.insert(0, {'type': 'message', 'role': 'system', 'content': instruction_parts})
        body['input'] = conversation
    body.pop('previous_response_id', None)
    body.pop('service_tier', None)
    body.pop('include', None)
    body.pop('prompt_cache_key', None)
    body.pop('client_metadata', None)
    body['reasoning'] = {'effort': 'none'}
    tools = list(body.get('tools') or []) + announced_tools
    if tools:
        accepted = []
        seen = set()
        for tool in tools:
            if not isinstance(tool, dict) or tool.get('type') not in ('function', 'custom', 'namespace'):
                continue
            signature = json.dumps(tool, sort_keys=True, ensure_ascii=False)
            if signature not in seen:
                seen.add(signature)
                accepted.append(tool)
        body['tools'] = accepted
    return body


class UnifiedServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address):
        super().__init__(address, UnifiedHandler)
        self.key = unified_settings()['key']
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), UnifiedNoRedirect())
        self.audit_lock = threading.Lock()
        self.recent = collections.deque(maxlen=30)
        self.stop_refresh = threading.Event()
        self.cloud_catalog_refresh_at = 0
        self.cloud_catalog_lock = threading.Lock()

    def audit(self, record):
        # No request bodies, generated text, URLs, tokens or headers are logged.
        record = {'time': datetime.datetime.now(datetime.timezone.utc).isoformat(), **record}
        with self.audit_lock:
            self.recent.append(record)
            path = UNIFIED_HOME / 'routes.jsonl'
            with path.open('a') as log:
                os.chmod(path, 0o600)
                log.write(json.dumps(record) + '\n')


class UnifiedHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def reply(self, status, body, content_type='application/json'):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def allowed(self):
        if self.headers.get('Origin'):
            self.reply(403, {'error': {'message': 'Browser-origin requests are not accepted.'}})
            return False
        actual = self.headers.get(UNIFIED_HEADER, '').encode()
        if not hmac.compare_digest(actual, self.server.key.encode()):
            self.reply(401, {'error': {'message': 'Local gateway credential required.'}})
            return False
        return True

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == '/health':
            self.reply(200, {'status': 'ok', 'service': 'codex-unified', 'pid': os.getpid()})
            return
        if not self.allowed():
            return
        if path == '/status':
            catalog = json.loads(UNIFIED_CATALOG.read_text())
            self.reply(200, {'status': 'ok', 'models': dict(collections.Counter(catalog['routes'].values())),
                             'recent_requests': list(self.server.recent)})
        elif path in ('/models', '/v1/models', '/codex/models', '/backend-api/codex/models'):
            self.refresh_cloud_catalog()
            catalog = json.loads(UNIFIED_CATALOG.read_text())
            self.reply(200, {'models': catalog['models']})
        else:
            self.reply(404, {'error': {'message': 'Endpoint not enabled.'}})

    def refresh_cloud_catalog(self):
        if not self.headers.get('Authorization','').startswith('Bearer '):
            return
        with self.server.cloud_catalog_lock:
            if time.monotonic()-self.server.cloud_catalog_refresh_at < 60:
                return
            self.server.cloud_catalog_refresh_at = time.monotonic()
            try:
                request = urllib.request.Request(OPENAI_UPSTREAM+'/models?client_version=0.169.0',
                        headers=unified_upstream_headers(self.headers,'cloud'))
                with self.server.opener.open(request,timeout=10) as response:
                    data=json.load(response)
                if not isinstance(data.get('models'),list) or not data['models']:
                    return
                atomic_write(UNIFIED_CLOUD_CATALOG,json_text({'models':data['models']}))
                unified_catalog_update(validate=False,quiet=True)
                self.server.audit({'route':'cloud_catalog','status':200,'models':len(data['models'])})
            except (OSError,ValueError,KeyError,subprocess.SubprocessError):
                pass

    def do_POST(self):
        if not self.allowed():
            return
        path = urllib.parse.urlsplit(self.path).path
        suffix = next((s for s in ('/responses/compact', '/responses/input_tokens', '/responses')
                       if path == s or path in ('/v1' + s, '/backend-api/codex' + s)), None)
        if suffix is None:
            self.reply(404, {'error': {'message': 'Endpoint not enabled.'}})
            return
        try:
            length = int(self.headers.get('Content-Length', '-1'))
            if not 0 <= length <= MAX_UNIFIED_BODY:
                self.reply(413, {'error': {'message': 'Invalid request size.'}})
                return
            raw = self.rfile.read(length)
            encoding = self.headers.get('Content-Encoding', '').lower()
            if encoding == 'gzip':
                raw = gzip.decompress(raw)
            elif encoding == 'deflate':
                raw = zlib.decompress(raw)
            elif encoding == 'zstd':
                from compression import zstd
                raw = zstd.decompress(raw)
            elif encoding not in ('', 'identity'):
                self.reply(415, {'error': {'message': 'Unsupported request encoding.'}})
                return
            if len(raw) > MAX_UNIFIED_BODY:
                raise ValueError('Decoded request too large')
            body = json.loads(raw)
            model = body['model']
            route = json.loads(UNIFIED_CATALOG.read_text())['routes'][model]
        except (ValueError, KeyError, TypeError, ImportError):
            self.reply(400, {'error': {'message': 'Unknown model or malformed request.'}})
            return
        if body.get('previous_response_id'):
            self.reply(409, {'error': {'message': 'This gateway requires full replayed input; disable WebSockets/incremental transport.'}})
            return
        if route == 'cloud' and not self.headers.get('Authorization', '').startswith('Bearer '):
            self.reply(401, {'error': {'message': 'Codex ChatGPT sign-in required for cloud models.'}})
            return
        if route == 'local' and suffix == '/responses/compact':
            self.reply(400, {'error': {'message': 'Local models require client-side compaction.'}})
            return
        upstream = OPENAI_UPSTREAM if route == 'cloud' else LOCAL_UPSTREAM
        try:
            payload = body if route == 'cloud' else unified_local_body(body)
        except (ValueError,TypeError):
            self.reply(400, {'error': {'message': 'Invalid local Responses input structure.'}})
            return
        if route == 'local' and not server_online():
            try:
                start_server()
            except (OSError, RuntimeError, subprocess.SubprocessError):
                self.reply(502, {'error': {'message': 'Could not start llama.cpp; inspect its server log.'}})
                return
        request = urllib.request.Request(upstream + suffix, data=json.dumps(payload).encode(),
                                         headers=unified_upstream_headers(self.headers, route), method='POST')
        started = time.monotonic()
        try:
            response = self.server.opener.open(request, timeout=900)
        except urllib.error.HTTPError as error:
            data = error.read(1024 * 1024)
            self.server.audit({'route': route, 'model': model, 'status': error.code})
            self.reply(error.code, data, error.headers.get('Content-Type', 'application/json'))
            return
        except (OSError, urllib.error.URLError):
            self.server.audit({'route': route, 'model': model, 'status': 502})
            self.reply(502, {'error': {'message': f'{route} upstream unavailable; no fallback to another service was attempted.'}})
            return
        size = 0
        with response:
            self.send_response(response.status)
            self.send_header('Content-Type', response.headers.get('Content-Type', 'application/json'))
            for name in ('x-request-id', 'x-codex-turn-state', 'x-codex-turn-metadata'):
                if response.headers.get(name):
                    self.send_header(name, response.headers[name])
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.close_connection = True
            complete = False
            tail = b''
            try:
                while chunk := response.read1(65536):
                    tail = (tail + chunk)[-65536:]
                    if b'response.completed' in tail:
                        complete = True
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    size += len(chunk)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                self.server.audit({'route': route, 'model': model, 'status': response.status,
                                   'completed': complete, 'bytes_out': size,
                                   'elapsed_seconds': round(time.monotonic() - started, 3),
                                   'tool_types': dict(collections.Counter(t.get('type','unknown') for t in payload.get('tools',[])))})


def unified_serve():
    unified_prepare()
    if not UNIFIED_CATALOG.exists():
        unified_catalog_update()
    server = UnifiedServer(('127.0.0.1', UNIFIED_PORT))
    def refresh():
        while not server.stop_refresh.wait(10):
            try:
                unified_catalog_update(validate=False, quiet=True)
            except Exception:
                # Preserve the last working catalog. Never print request/auth details.
                pass
    threading.Thread(target=refresh, daemon=True).start()
    try:
        server.serve_forever()
    finally:
        server.stop_refresh.set()
        server.server_close()


def unified_request(path='/health'):
    request = urllib.request.Request(UNIFIED_URL + path,
                                     headers={UNIFIED_HEADER: unified_settings()['key']})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=3) as response:
        return json.load(response)


def unified_start():
    unified_prepare()
    healthy = False
    try:
        healthy = unified_request()['service'] == 'codex-unified'
    except (OSError, ValueError, KeyError):
        pass
    if sys.platform != 'darwin':
        raise RuntimeError('The background service requires macOS. Run gateway-serve in the foreground.')
    executable = str(Path(sys.executable).resolve())
    if not executable:
        raise RuntimeError('Could not find Python 3.')
    logs = CODEX_DIR / 'logs'
    logs.mkdir(exist_ok=True)
    installed = CODEX_DIR / 'llama-codex.py'
    script = installed if installed.is_file() else Path(__file__).resolve()
    definition = {'Label': UNIFIED_LABEL,
                  'ProgramArguments': [executable, str(script), 'gateway-serve'],
                  'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 10,
                  'WorkingDirectory': str(CODEX_DIR),
                  'EnvironmentVariables': {'CODEX_HOME': str(CODEX_DIR),
                    'LLAMA_CODEX_REVISION': hashlib.sha256(script.read_bytes()).hexdigest(),
                    **({'LLAMA_CPP_BIN': str(LLAMA)} if LLAMA.is_file() else {})},
                  'StandardOutPath': str(logs/'unified-gateway.log'),
                  'StandardErrorPath': str(logs/'unified-gateway.error.log')}
    domain = 'gui/' + str(os.getuid())
    with locked():
        agents = unified_managed_agents()
        if UNIFIED_AGENT.exists() and UNIFIED_AGENT not in {path for path, _ in agents}:
            raise RuntimeError('The gateway LaunchAgent path contains an unrecognized job; it was left untouched.')
        if healthy and len(agents) == 1 and agents[0][1] == definition:
            return
        if healthy or agents:
            # Do not interrupt a response while replacing an old service. Fail
            # closed if the connection check itself cannot be performed.
            result = subprocess.run(['/usr/sbin/lsof', '-nP', '-iTCP:' + str(UNIFIED_PORT),
                                     '-sTCP:ESTABLISHED', '-t'], capture_output=True, text=True, timeout=5)
            if result.returncode not in (0, 1):
                raise RuntimeError('Cannot check gateway connections; service upgrade postponed.')
            if result.stdout.strip():
                raise RuntimeError('Gateway is in use. Finish active turns and retry installation; no service was stopped.')
            if healthy and not agents:
                raise RuntimeError('A gateway is running without a recognized LaunchAgent; it was left untouched.')
        snapshot = CODEX_DIR / 'backups' / ('gateway-upgrade-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
        snapshot.mkdir(parents=True, mode=0o700)
        previous = []
        loaded = []
        for path, old in agents:
            raw = path.read_bytes()
            previous.append((path, raw, old['Label']))
            atomic_write(snapshot / path.name, raw)
            if subprocess.run(['launchctl', 'print', domain + '/' + old['Label']], capture_output=True).returncode == 0:
                loaded.append((path, old['Label']))
        new_started = False
        try:
            for path, label in loaded:
                subprocess.run(['launchctl', 'bootout', domain + '/' + label], check=True, capture_output=True)
            for path, _, _ in previous:
                path.unlink()
            UNIFIED_AGENT.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(UNIFIED_AGENT, plistlib.dumps(definition))
            new_started = True
            unified_bootstrap(domain, UNIFIED_AGENT)
            unified_wait_ready()
        except BaseException:
            if new_started:
                subprocess.run(['launchctl', 'bootout', domain + '/' + UNIFIED_LABEL], capture_output=True)
            UNIFIED_AGENT.unlink(missing_ok=True)
            for path, raw, _ in previous:
                atomic_write(path, raw)
            for path, label in loaded:
                # A failed bootout can leave the old job running already.
                if subprocess.run(['launchctl', 'print', domain + '/' + label], capture_output=True).returncode:
                    unified_bootstrap(domain, path)
            raise
    print('Unified gateway available at ' + UNIFIED_URL)


def unified_bootstrap(domain, path):
    # launchd can briefly reject re-registering a label after bootout even when
    # that call succeeded. Retry only while the label is absent, and bound it.
    label = plistlib.loads(path.read_bytes())['Label']
    for attempt in range(8):
        try:
            subprocess.run(['launchctl', 'bootstrap', domain, str(path)], check=True, capture_output=True)
            return
        except subprocess.CalledProcessError as error:
            if (error.returncode != 5 or attempt == 7 or
                    subprocess.run(['launchctl', 'print', domain + '/' + label], capture_output=True).returncode == 0):
                raise
            time.sleep(.25)


def unified_managed_agents():
    """Recognize renamed jobs by their command and home, never by user name."""
    agents = []
    for path in sorted(UNIFIED_AGENT.parent.glob('*.plist')):
        try:
            value = plistlib.loads(path.read_bytes())
            if not isinstance(value, dict):
                continue
            args = value.get('ProgramArguments', [])
            environment = value.get('EnvironmentVariables', {})
            if not isinstance(environment, dict):
                continue
            if (len(args) == 3 and args[2] == 'gateway-serve'
                    and Path(args[1]).name in ('switch-codex.py', 'llama-codex.py')
                    and Path(args[1]).parent.resolve() == CODEX_DIR
                    and Path(environment.get('CODEX_HOME', str(CODEX_DIR))).expanduser().resolve() == CODEX_DIR
                    and isinstance(value.get('Label'), str)):
                agents.append((path, value))
        except (OSError, ValueError, TypeError, plistlib.InvalidFileException):
            continue
    return agents


def unified_wait_ready():
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            if unified_request()['service'] == 'codex-unified':
                return
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(.25)
    raise RuntimeError('The gateway failed to start; check ~/.codex/logs/unified-gateway.error.log')


def unified_config(original, model=None):
    data = tomllib.loads(original)
    selection = {key: data[key] for key in MANAGED_KEYS
                 if key in data and key not in ('profile', 'model_catalog_json',
                                               'model_context_window', 'model_auto_compact_token_limit')}
    catalog = json.loads(UNIFIED_CATALOG.read_text())
    selection['model_provider'] = UNIFIED_PROVIDER
    selection['model'] = model or data.get('model')
    if selection['model'] not in catalog['routes']:
        selection['model'] = next(iter(catalog['routes']))
    updated = config_with_selection(original, selection)
    updated = re.sub(r'(?ms)^\[model_providers\.codex-unified\]\s*\n.*?(?=^\[|\Z)', '', updated)
    updated = updated.rstrip() + '\n\n[model_providers.codex-unified]\n'
    options = {'name': 'OpenAI + llama.cpp', 'base_url': UNIFIED_URL+'/v1',
               'wire_api': 'responses', 'requires_openai_auth': True,
               'supports_websockets': False, 'stream_idle_timeout_ms': 900000,
               'model_catalog_url': UNIFIED_URL+'/codex/models'}
    for key, value in options.items():
        updated += key + ' = ' + json.dumps(value) + '\n'
    updated += 'http_headers = { ' + json.dumps(UNIFIED_HEADER) + ' = ' + json.dumps(unified_settings()['key']) + ' }\n'
    parsed = tomllib.loads(updated)
    assert parsed['model_provider'] == UNIFIED_PROVIDER
    assert 'model_catalog_json' not in parsed
    return updated


def unified_activate(mode='unified', model=None):
    catalog = unified_catalog_update()
    if mode != 'unified':
        if model is None:
            saved = initial_state(read_toml(CONFIG))
            candidate = saved.get('selection', {}).get(mode, {}).get('model')
            model = candidate if catalog['routes'].get(candidate) == mode else next(
                (slug for slug, route in catalog['routes'].items() if route == mode), None)
        if not model or catalog['routes'].get(model) != mode:
            raise ValueError('No matching model available for ' + mode + '. Start llama.cpp or refresh the catalog.')
    elif model is not None and model not in catalog['routes']:
        raise ValueError('Model is not in the unified catalog: ' + model)
    unified_start()
    with locked():
        original = CONFIG.read_text()
        config = tomllib.loads(original)
        try:
            db = database_path(config)
        except RuntimeError:
            sqlite_root = Path(config.get('sqlite_home', str(CODEX_DIR))).expanduser()
            if any(sqlite_root.glob('state_*.sqlite')):
                raise
            backup = CODEX_DIR / 'backups' / ('unified-initial-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
            atomic_write(backup / 'config.toml', original)
            settings = unified_settings()
            atomic_write(backup / 'settings.json', json_text(settings))
            updated = unified_config(original, model)
            settings.update({'enabled': True, 'last_backup': str(backup), 'pending_active_threads': []})
            try:
                atomic_write(CONFIG, updated)
                atomic_write(UNIFIED_SETTINGS, json_text(settings))
            except BaseException:
                atomic_write(CONFIG, original)
                atomic_write(UNIFIED_SETTINGS, (backup / 'settings.json').read_bytes())
                raise
            print('Unified provider configured. No existing chats need migration. Reopen Codex once.')
            return
        settings_before = UNIFIED_SETTINGS.read_bytes()
        settings = json.loads(settings_before)
        updated = unified_config(original, model)
        backup = CODEX_DIR/'backups'/('unified-switch-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
        backup.mkdir(parents=True, mode=0o700)
        atomic_write(backup/'config.toml', original)
        atomic_write(backup/'settings.json', settings_before)
        con = sqlite3.connect(f'{db.as_uri()}?mode=rw', uri=True, timeout=15)
        con.row_factory = sqlite3.Row
        dest = sqlite3.connect(backup/db.name)
        try:con.backup(dest)
        finally:dest.close()
        (backup/db.name).chmod(0o600)
        copies = []
        skipped = []
        missing = 0
        changed = 0
        locks = contextlib.ExitStack()
        config_written = False
        settings_written = False
        eligible = "('openai','llamacpp','local_models','codex-unified')"
        try:
            con.execute('BEGIN IMMEDIATE')
            rows = con.execute('SELECT * FROM threads WHERE model_provider IN '+eligible).fetchall()
            atomic_write(backup/'providers-before.json', json_text([
                {'id':r['id'],'model_provider':r['model_provider']} for r in rows]))
            for row in rows:
                path = Path(row['rollout_path'])
                if path.is_file():
                    if row['model_provider'] == UNIFIED_PROVIDER:
                        # Already synchronized chats need no write lock or
                        # rewrite, including ones currently open in Desktop.
                        with path.open('rb') as stream:
                            current = json.loads(stream.readline())
                        if (current.get('type') == 'session_meta' and
                                current.get('payload', {}).get('model_provider') == UNIFIED_PROVIDER):
                            continue
                    lockpath = CODEX_DIR/'thread-writer-locks'/(row['id']+'.lock')
                    lockpath.parent.mkdir(exist_ok=True)
                    handle = lockpath.open('a')
                    try:fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        handle.close();skipped.append(row['id']);continue
                    locks.enter_context(handle)
                    before = path.read_bytes()
                    header, separator, rest = before.partition(b'\n')
                    meta = json.loads(header)
                    if meta.get('type') != 'session_meta':
                        raise ValueError('Unknown session header: '+str(path))
                    if meta['payload'].get('model_provider') != UNIFIED_PROVIDER:
                        meta['payload']['model_provider'] = UNIFIED_PROVIDER
                        copy = backup/'rollouts'/(row['id']+'.jsonl')
                        atomic_write(copy, before)
                        copies.append((path,copy))
                        atomic_write(path, json.dumps(meta,ensure_ascii=False,separators=(',',':')).encode()+separator+rest)
                else:
                    missing += 1
                    if row['model_provider'] == UNIFIED_PROVIDER:
                        continue
                con.execute('UPDATE threads SET model_provider=? WHERE id=?', (UNIFIED_PROVIDER,row['id']))
                changed += 1
            after = {r['id']:r for r in con.execute('SELECT * FROM threads WHERE model_provider IN '+eligible)}
            if len(after)!=len(rows) or any(any(row[k]!=after[row['id']][k]
                    for k in row.keys() if k!='model_provider') for row in rows):
                raise RuntimeError('Migration changed fields other than the provider.')
            settings.setdefault('initial_backup', str(backup))
            settings.update({'enabled':True,'last_backup':str(backup),'pending_active_threads':skipped})
            atomic_write(backup/'rollout-manifest.json',json_text([
                {'path':str(p),'backup':str(b)} for p,b in copies]))
            if CONFIG.read_text()!=original:
                raise RuntimeError('Configuration changed during activation; run the command again.')
            atomic_write(CONFIG,updated);config_written=True
            atomic_write(UNIFIED_SETTINGS,json_text(settings));settings_written=True
            con.commit()
        except BaseException:
            con.rollback()
            for path,copy in reversed(copies):atomic_write(path,copy.read_bytes())
            if config_written:atomic_write(CONFIG,original)
            if settings_written:atomic_write(UNIFIED_SETTINGS,settings_before)
            raise
        finally:
            con.close();locks.close()
        print(f'Unified provider enabled. {changed} entries synchronized; models and history preserved.')
        print('Backup:',backup)
        if skipped:print(f'Pending: {len(skipped)} open chats. Close Codex and run again: python3 ~/.codex/llama-codex.py unified')
        if missing:print(f'Preserved {missing} entries whose history files were already missing.')
        print('After activation, reopen Codex once. Then switch between local/OpenAI models in the picker.')


def unified_select(mode, model=None, dry_run=False):
    catalog = unified_catalog_update()
    if model is not None and catalog['routes'].get(model) != mode:
        raise ValueError('Model is not available for ' + mode + ': ' + model)
    state = initial_state(read_toml(CONFIG))
    model = model or state.get('selection',{}).get(mode,{}).get('model')
    if not model or catalog['routes'].get(model)!=mode:
        model = next((m for m,r in catalog['routes'].items() if r==mode), None)
        if model is None:
            raise ValueError('No models available for ' + mode)
    if dry_run:
        print(json_text({'provider':UNIFIED_PROVIDER,'model':model,'route':mode,'changes_to_threads':0}))
        return
    with locked():
        original = CONFIG.read_text()
        backup = CODEX_DIR/'backups'/('unified-default-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.toml')
        atomic_write(backup,original)
        atomic_write(CONFIG,unified_config(original,model))
        state.setdefault('selection',{})[mode]={'model':model}
        atomic_write(STATE,json_text(state))
    print('Default model:',model)
    print('The provider remains codex-unified; no chats were modified.')
    print('To change the model in an open chat, use the Codex Desktop model picker.')


def unified_restore():
    # An explicit rollback uses the already tested direct-provider switch. It
    # does not replace SQLite with an old snapshot or erase newer conversations.
    config = read_toml(CONFIG)
    saved = initial_state(config)
    profile = CODEX_DIR / 'openai-cloud.config.toml'
    restore_model = None
    if not saved.get('selection', {}).get('cloud', {}).get('model') and not (profile.exists() and read_toml(profile).get('model')):
        catalog = json.loads(UNIFIED_CATALOG.read_text())
        restore_model = next((m for m, route in catalog['routes'].items() if route == 'cloud'), None)
        if restore_model is None:
            raise RuntimeError('No cloud model is available to restore the direct provider.')
    switch(argparse.Namespace(mode='cloud', model=restore_model, dry_run=False, db=None, status=False))
    with locked():
        settings=unified_settings();settings['enabled']=False
        atomic_write(UNIFIED_SETTINGS,json_text(settings))
    print('Direct OpenAI access restored. Reopen Codex to apply it.')


legacy_update_catalog = update
legacy_show_status = show_status

def show_status():
    if read_toml(CONFIG).get('model_provider') != UNIFIED_PROVIDER:
        return legacy_show_status()
    config=read_toml(CONFIG)
    status(config,database_path(config))
    try:
        state=unified_request('/status')
        print('Gateway: running · '+str(state['models'].get('local',0))+' local / '+str(state['models'].get('cloud',0))+' OpenAI entries')
    except (OSError,ValueError):
        print('Gateway: unavailable; use hybrid-start to start it.')
    print('llama.cpp: '+('running' if server_online() else 'will start when a local model is selected'))
    saved=unified_settings()
    print('Last backup:',saved.get('last_backup','(none)'))
    if saved.get('pending_active_threads'):
        print('Pending chats from the initial migration:',len(saved['pending_active_threads']))

def update(offline=False, quiet=False):
    if read_toml(CONFIG).get('model_provider') == UNIFIED_PROVIDER:
        return unified_catalog_update(quiet=quiet,offline=offline)
    return legacy_update_catalog(offline,quiet)


def install_script(mode='unified', model=None, copy_only=False):
    CODEX_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = CODEX_DIR / 'llama-codex.py'
    source = Path(__file__).resolve()
    if destination.resolve() != source:
        if destination.exists():
            stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
            atomic_write(CODEX_DIR / 'backups' / ('script-' + stamp + '.py'), destination.read_bytes())
        atomic_write(destination, source.read_bytes())
    if not CONFIG.exists():
        atomic_write(CONFIG, '')
    print('Script installed at:', destination)
    if copy_only:
        print('Copy only: provider and chats were not changed.')
        return
    if mode == 'local':
        start_server()
    unified_activate(mode, model)
    print('Installation complete. Configured provider:', read_toml(CONFIG)['model_provider'])
    print('Default model:', read_toml(CONFIG)['model'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command')
    install = commands.add_parser('install', help='Install, configure the provider, and upgrade the background service')
    install.add_argument('--mode', choices=('unified', 'local', 'cloud'), default='unified',
                         help='Initial model route; all modes keep the combined provider (default: unified)')
    install.add_argument('--model', help='Initial model identifier from the catalog')
    install.add_argument('--copy-only', action='store_true', help='Copy the script without activating or migrating chats')
    for mode in ('local', 'cloud'):
        command = commands.add_parser(mode, help='Switch to provider ' + mode)
        command.add_argument('--model', help='Model identifier')
        command.add_argument('--dry-run', action='store_true', help='Show changes without applying them')
        command.add_argument('--db', help='Explicit SQLite path')
    commands.add_parser('status', help='Show status')
    commands.add_parser('start', help='Start llama.cpp if it is stopped')
    commands.add_parser('stop', help='Stop the server started with the Codex preset')
    catalog = commands.add_parser('catalog', help='Update the catalog')
    catalog.add_argument('--offline', action='store_true', help='Use local profiles only')
    watch = commands.add_parser('watch', help='Update periodically until Ctrl+C')
    watch.add_argument('--interval', type=float, default=5, help='Seconds between updates (minimum 2)')
    commands.add_parser('gateway-serve', help=argparse.SUPPRESS)
    commands.add_parser('unified', help='Enable one local + OpenAI provider and synchronize pending chats')
    commands.add_parser('restore', help='Restore the direct OpenAI provider')
    commands.add_parser('hybrid-catalog', help='Update the local and OpenAI catalog')
    commands.add_parser('hybrid-start', help='Start the unified gateway')
    commands.add_parser('hybrid-status', help='Show unified gateway status')
    args = parser.parse_args()
    if args.command == 'install':
        install_script(args.mode, args.model, args.copy_only)
        return 0
    if not CONFIG.exists():
        raise RuntimeError('Run llama-codex.py install first, or start Codex to create config.toml.')
    if args.command == 'gateway-serve':
        unified_serve()
        return 0
    if args.command == 'unified':
        unified_activate()
        return 0
    if args.command == 'restore':
        unified_restore()
        return 0
    if args.command == 'hybrid-catalog':
        unified_catalog_update()
        return 0
    if args.command == 'hybrid-start':
        unified_start()
        return 0
    if args.command == 'hybrid-status':
        print(json_text(unified_request('/status')))
        return 0
    if args.command is None:
        return menu()
    if args.command in ('local', 'cloud'):
        if args.command == 'local' and not args.dry_run:
            start_server()
        perform_switch(args.command, args.model, args.dry_run, args.db)
    elif args.command == 'status':
        show_status()
    elif args.command == 'start':
        start_server()
    elif args.command == 'stop':
        stop_server()
    elif args.command == 'catalog':
        update(args.offline)
    elif args.command == 'watch':
        watch_catalog(args.interval)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('\nOperation cancelled.')
        sys.exit(130)
    except (OSError, ValueError, RuntimeError, sqlite3.Error, subprocess.SubprocessError) as error:
        print(f'Error: {error}', file=sys.stderr)
        sys.exit(1)
