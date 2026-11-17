"""Optional API services: private state, discovery, routing, credentials, and removal."""
import contextlib
import copy
import http.server
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'llama-codex.py'
spec = importlib.util.spec_from_file_location('optional_services', SOURCE)
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)
seen = []
stream = (b'event: response.output_item.done\ndata: {"type":"response.output_item.done","item":'
          b'{"type":"function_call","name":"echo","call_id":"test-call","arguments":"{}"}}\n\n'
          b'event: response.completed\ndata: {"type":"response.completed"}\n\n')

class Upstream(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        seen.append(('GET', self.path, dict(self.headers), None))
        if self.path.startswith('/redirect/'):
            self.send_response(302)
            self.send_header('Location', '/trap')
            self.end_headers()
            return
        data = json.dumps({'data': [{'id': 'shared-model', 'context_length': 65536}]}).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        seen.append(('POST', self.path, dict(self.headers), body))
        if body.get('simulate_redirect'):
            self.send_response(307)
            self.send_header('Location', '/trap')
            self.end_headers()
            return
        status = 429 if body.get('simulate_failure') else 200
        data = b'{"error":{"message":"synthetic rate limit"}}' if status == 429 else stream
        self.send_response(status)
        self.send_header('Content-Type', 'text/event-stream' if status == 200 else 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

up = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
threading.Thread(target=up.serve_forever, daemon=True).start()
try:
    with tempfile.TemporaryDirectory(prefix='llama-codex-services-') as name:
        root = Path(name).resolve()
        g.CODEX_DIR = root
        g.CONFIG = root / 'config.toml'
        g.STATE = root / 'provider_switch_state.json'
        g.UNIFIED_HOME = root / 'unified'
        g.UNIFIED_HOME.mkdir()
        g.UNIFIED_SETTINGS = g.UNIFIED_HOME / 'settings.json'
        g.UNIFIED_CATALOG = g.UNIFIED_HOME / 'catalog.json'
        g.UNIFIED_SETTINGS.write_text(json.dumps({'key': 'synthetic-gateway-key'}))
        g.CONFIG.write_text('model_provider = "codex-unified"\nmodel = "base-cloud"\n')
        assert g.services_load() == [] and not (root / 'services.json').exists()
        with patch.object(g.sys.stdin, 'isatty', return_value=False):
            try:
                g.service_prompt_key()
                raise AssertionError('Noninteractive hidden-key prompt accepted')
            except RuntimeError:
                pass
        # Optional setup can be skipped without creating service state.
        with patch('builtins.input', return_value=''):
            g.services_setup()
        assert not (root / 'services.json').exists()
        base = f'http://127.0.0.1:{up.server_port}'
        g.service_add('server-a', 'llamacpp', base + '/a/v1', 'Server A', context_window=8192, key='synthetic-key-A')
        g.service_add('server-b', 'custom', base + '/b/v1', 'Server B', context_window=16384, key='synthetic-key-B')
        values = g.services_load()
        assert stat.S_IMODE((root / 'services.json').stat().st_mode) == 0o600
        assert stat.S_IMODE(g.service_key_path('server-a').stat().st_mode) == 0o600
        assert stat.S_IMODE(g.service_key_path('server-a').parent.stat().st_mode) == 0o700
        assert 'synthetic-key-A' not in (root / 'services.json').read_text()
        # Default listing hides URLs and keys; explicit URL display is available.
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            g.services_list()
        assert base not in output.getvalue() and 'synthetic-key' not in output.getvalue()
        assert 'Server A' in output.getvalue()
        prototype = {'slug': 'prototype', 'base_instructions': 'synthetic'}
        models, routes, upstream_models, warnings = g.services_catalog(prototype)
        assert warnings == [] and len(models) == 2
        aliases = {s: 'service/' + s + '/shared-model' for s in ('server-a', 'server-b')}
        assert {m['slug'] for m in models} == set(aliases.values())
        assert {m['context_window'] for m in models} == {8192, 16384}
        assert all(m == 'shared-model' for m in upstream_models.values())
        catalog = {'models': models, 'routes': routes, 'upstream_models': upstream_models}
        assert base not in json.dumps(catalog) and 'synthetic-key' not in json.dumps(catalog)
        g.UNIFIED_CATALOG.write_text(json.dumps(catalog))
        # Offline catalogs preserve known models without contacting their services.
        before = len(seen)
        cached = g.services_catalog(prototype, offline=True)
        assert cached[1] == routes and len(seen) == before
        with patch.object(g, 'service_discover', side_effect=OSError('PRIVATE-ENDPOINT')):
            cached = g.services_catalog(prototype)
        assert cached[1] == routes and 'PRIVATE-ENDPOINT' not in repr(cached[3])
        # Validate configuration without publishing any actual personal endpoint.
        for changes in ({'url': 'file:///tmp/x'}, {'url': 'http://user:secret@localhost/v1'},
                        {'url': 'http://localhost:9932/v1'}, {'url': 'https://example.invalid/v1?key=x'},
                        {'url': 'http://localhost:99999/v1'}, {'id': '../bad'},
                        {'context_window': -1}, {'url': None}, {'name': 'bad\nname'}):
            try:
                g.service_validate({**values[0], **changes})
                raise AssertionError('Invalid configuration accepted')
            except ValueError:
                pass
        with patch.dict(os.environ, {'SYNTHETIC_SERVICE_KEY': 'synthetic-env-key'}):
            service = {**values[0], 'api_key_env': 'SYNTHETIC_SERVICE_KEY'}
            assert g.service_headers(service)['Authorization'] == 'Bearer synthetic-env-key'
        with patch.dict(os.environ, {}, clear=True):
            try:
                g.service_headers(service)
                raise AssertionError('Missing environment key accepted')
            except RuntimeError:
                pass
        # Neither discovery nor request forwarding follows redirects.
        try:
            g.service_discover({**values[0], 'url': base + '/redirect/v1'})
            raise AssertionError('Discovery redirect accepted')
        except urllib.error.HTTPError as e:
            assert e.code == 302
        assert not any(record[1] == '/trap' for record in seen)
        server = g.UnifiedServer(('127.0.0.1', 0))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            def post(alias, **extra):
                body = {'model': alias, 'input': [{'role': 'assistant', 'content': 'VISIBLE-CONTEXT'},
                    {'type': 'reasoning', 'encrypted_content': 'OPAQUE-REASONING'}], **extra}
                request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/v1/responses',
                    data=json.dumps(body).encode(), headers={g.UNIFIED_HEADER: 'synthetic-gateway-key',
                    'Authorization': 'Bearer synthetic-ChatGPT-key', 'ChatGPT-Account-ID': 'synthetic-account',
                    'Cookie': 'synthetic-cookie'}, method='POST')
                try:
                    with urllib.request.urlopen(request, timeout=3) as r:
                        return r.status, r.read()
                except urllib.error.HTTPError as e:
                    return e.code, e.read()
            with patch.object(g, 'start_server', side_effect=AssertionError('A remote service started a local server')):
                for service_id, expected in (('server-a', 'A'), ('server-b', 'B')):
                    assert post(aliases[service_id]) == (200, stream)
                    method, path, headers, body = seen[-1]
                    assert path == '/' + ('a' if expected == 'A' else 'b') + '/v1/responses'
                    assert body['model'] == 'shared-model'
                    assert 'OPAQUE-REASONING' not in json.dumps(body)
                    assert 'VISIBLE-CONTEXT' in json.dumps(body)
                    assert headers['Authorization'] == 'Bearer synthetic-key-' + expected
                    assert not any(k.lower() in ('chatgpt-account-id', 'cookie', g.UNIFIED_HEADER.lower()) for k in headers)
                # Native Responses function calls/results survive full replay.
                replay = [{'type': 'function_call', 'name': 'echo', 'call_id': 'paired', 'arguments': '{}'},
                          {'type': 'function_call_output', 'call_id': 'paired', 'output': 'RESULT'}]
                assert post(aliases['server-b'], input=replay) == (200, stream)
                assert seen[-1][3]['input'] == replay
                assert post(aliases['server-a'], simulate_failure=True)[0] == 429
                before = len(seen)
                assert post(aliases['server-a'], simulate_redirect=True)[0] == 307
                assert len(seen) == before + 1
                # Disabling blocks even an alias still in a stale Desktop catalog.
                g.service_change('server-a', 'disable')
                before = len(seen)
                assert post(aliases['server-a'])[0] == 400 and len(seen) == before
                assert aliases['server-a'] not in g.services_catalog(prototype)[1]
                g.service_change('server-a', 'enable')
                assert post(aliases['server-a'])[0] == 200
                g.service_change('server-a', 'remove')
                assert not g.service_key_path('server-a').exists()
                assert post(aliases['server-a'])[0] == 400
            audit = (root / 'unified/routes.jsonl').read_text()
            for private in ('synthetic-key-A', 'synthetic-key-B', 'synthetic-ChatGPT-key', 'synthetic-account',
                            'synthetic-cookie', 'synthetic-gateway-key', 'VISIBLE-CONTEXT', base):
                assert private not in audit
        finally:
            server.shutdown()
            server.server_close()
        # Explicit models work when a service has no /models endpoint.
        g.service_add('manual', 'custom', base + '/manual/v1', 'Manual', ['explicit-model'])
        with patch.object(g, 'service_discover', side_effect=OSError('offline')):
            assert 'service/manual/explicit-model' in g.services_catalog(prototype)[1]
        # Private-key and configuration writes roll back together on failure.
        old = (root / 'services.json').read_bytes()
        real_write = g.atomic_write
        def fail_config(path, data):
            if path == root / 'services.json':
                raise OSError('synthetic write failure')
            return real_write(path, data)
        with patch.object(g, 'atomic_write', side_effect=fail_config):
            try:
                g.service_add('failed', 'custom', base + '/fail/v1', key='synthetic-unused-key')
                raise AssertionError('Write failure ignored')
            except OSError:
                pass
        assert (root / 'services.json').read_bytes() == old
        assert not g.service_key_path('failed').exists()
        # The public CLI stores URLs only in the user-selected runtime directory.
        env = {**os.environ, 'CODEX_HOME': str(root / 'cli-runtime')}
        subprocess.run([sys.executable, str(SOURCE), 'services', 'add', 'cli-service',
            '--preset', 'lmstudio', '--url', base + '/cli/v1', '--model', 'declared'],
            env=env, capture_output=True, text=True, check=True)
        private = json.loads((root / 'cli-runtime/services.json').read_text())
        assert private['services'][0]['url'] == base + '/cli/v1'
        listing = subprocess.run([sys.executable, str(SOURCE), 'services', 'list'],
            env=env, capture_output=True, text=True, check=True)
        assert 'cli-service' in listing.stdout and base not in listing.stdout
finally:
    up.shutdown()
    up.server_close()
print('PASS: opt-in setup, private URL/key storage, duplicate model names, discovery/cache, explicit models, exact SSE/tool relay, credential isolation, disabled/removed routes, redirects, write rollback.')
