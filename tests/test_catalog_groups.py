"""Picker presentation and optional online-only visibility must preserve routes."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import urllib.error
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'llama-codex.py'
spec = importlib.util.spec_from_file_location('grouped_picker', SOURCE)
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

original = {'models': [
    {'slug': 'service/z/shared', 'display_name': 'Same name [z] · shared', 'priority': 0, 'context_window': 4096},
    {'slug': 'local-b', 'display_name': 'local-b · Local', 'priority': 3, 'context_window': 8192},
    {'slug': 'cloud-b', 'display_name': 'Cloud B', 'priority': 2, 'context_window': 16384},
    {'slug': 'service/a/shared', 'display_name': 'Same name [a] · shared', 'priority': 999, 'context_window': 4096},
    {'slug': 'cloud-a', 'display_name': 'OpenAI · Cloud A', 'priority': 1, 'context_window': 16384},
    {'slug': 'local-a', 'display_name': 'local-a · Local', 'priority': 2, 'context_window': 8192}],
    'routes': {'cloud-a': 'cloud', 'cloud-b': 'cloud', 'local-a': 'local', 'local-b': 'local',
               'service/a/shared': 'service:a', 'service/z/shared': 'service:z'},
    'upstream_models': {'service/a/shared': 'shared', 'service/z/shared': 'shared'}}
snapshot = copy.deepcopy(original)
result = g.grouped_catalog(original)
assert original == snapshot, 'Presentation mutated the input catalog'
assert result['routes'] == original['routes'] and result['upstream_models'] == original['upstream_models']
assert [m['slug'] for m in result['models']] == ['cloud-a', 'cloud-b', 'local-a', 'local-b', 'service/a/shared', 'service/z/shared']
assert [m['priority'] for m in result['models']] == list(range(6))
assert result['models'][0]['display_name'] == 'OpenAI · Cloud A'
assert result['models'][2]['display_name'] == 'Local · local-a'
for model in result['models']:
    before = next(m for m in original['models'] if m['slug'] == model['slug'])
    assert {k: v for k, v in before.items() if k not in ('display_name', 'priority')} == {
        k: v for k, v in model.items() if k not in ('display_name', 'priority')}
assert g.grouped_catalog(result) == result, 'Repeated grouping changed labels or ordering'

with tempfile.TemporaryDirectory(prefix='llama-codex-visibility-') as name:
    root = Path(name).resolve()
    g.CODEX_DIR = root
    g.CONFIG = root / 'config.toml'
    g.UNIFIED_HOME = root / 'unified'
    g.UNIFIED_HOME.mkdir()
    g.UNIFIED_CATALOG = g.UNIFIED_HOME / 'catalog.json'
    g.UNIFIED_CLOUD_CATALOG = g.UNIFIED_HOME / 'cloud-models.json'
    assert g.services_document()['only_online'] is False
    assert not (root / 'services.json').exists()
    for service_id in ('a', 'z'):
        g.service_add(service_id, 'custom', 'http://127.0.0.1:9/v1', 'Same name')
    base = {'slug': 'cloud-a', 'display_name': 'Cloud A', 'priority': 0, 'base_instructions': 'synthetic'}
    def healthy(service):
        return [{'id': 'shared', 'context': 4096}]
    with patch.object(g, 'service_discover', side_effect=healthy):
        models, routes, upstream, warnings = g.services_catalog(base)
    assert {m['display_name'] for m in models} == {'Same name [a] · shared', 'Same name [z] · shared'}
    assert warnings == []
    initial = {'models': models, 'routes': routes, 'upstream_models': upstream}
    g.UNIFIED_CATALOG.write_text(json.dumps(initial))
    # An upgrade can seed its persistent cache from the previous catalog.
    cache_path = g.UNIFIED_HOME / 'service-models-cache.json'
    cache_path.unlink()
    with patch.object(g, 'service_discover', side_effect=OSError('PRIVATE-ENDPOINT')):
        cached = g.services_catalog(base)
    assert cached[1] == routes and cache_path.exists()
    assert 'PRIVATE-ENDPOINT' not in repr(cached)
    g.services_visibility('online')
    def one_offline(service):
        if service['id'] == 'z':
            raise OSError('PRIVATE-ENDPOINT')
        return healthy(service)
    with patch.object(g, 'service_discover', side_effect=one_offline):
        visible = g.services_catalog(base)
    assert visible[1] == {'service/a/shared': 'service:a'}
    # Simulate publishing the filtered catalog: z must still survive in cache.
    g.UNIFIED_CATALOG.write_text(json.dumps({'models': visible[0], 'routes': visible[1], 'upstream_models': visible[2]}))
    assert 'z' in json.loads(cache_path.read_text())['services']
    g.services_visibility('all')
    with patch.object(g, 'service_discover', side_effect=OSError('offline')):
        restored = g.services_catalog(base)
    assert restored[1] == routes and restored[2] == upstream
    g.services_visibility('online')
    with patch.object(g, 'service_discover') as discover:
        offline = g.services_catalog(base, offline=True)
        discover.assert_not_called()
    assert offline[0] == []
    # Registry edits retain visibility preferences; disabled services are not probed.
    g.service_change('z', 'disable')
    assert g.services_document()['only_online'] is True
    with patch.object(g, 'service_discover', side_effect=healthy) as discover:
        g.services_catalog(base)
        assert [c.args[0]['id'] for c in discover.call_args_list] == ['a']
    g.service_change('z', 'enable')
    g.service_add('manual', 'custom', 'http://127.0.0.1:9/v1', 'Manual', ['declared'])
    assert g.services_document()['only_online'] is True
    def manual_missing_models(service):
        if service['id'] == 'manual':
            raise urllib.error.HTTPError('http://127.0.0.1:9/v1/models', 404, 'no model list', {}, None)
        return healthy(service)
    with patch.object(g, 'service_discover', side_effect=manual_missing_models):
        assert 'service/manual/declared' in g.services_catalog(base)[1]
    def unauthorized(service):
        raise urllib.error.HTTPError('http://127.0.0.1:9/v1/models', 401, 'unauthorized', {}, None)
    with patch.object(g, 'service_discover', side_effect=unauthorized):
        assert g.services_catalog(base)[0] == []
    # The filter applies only to additional services, never the built-in sources.
    g.UNIFIED_CLOUD_CATALOG.write_text(json.dumps({'models': [base]}))
    local = {**base, 'slug': 'local-a', 'display_name': 'Local A'}
    bundled = subprocess.CompletedProcess([], 0, json.dumps({'models': [base]}), '')
    with patch.object(g, 'build_catalog', return_value=({'models': [local]}, None)), \
         patch.object(g, 'cli_paths', return_value=['synthetic-codex']), \
         patch.object(g.subprocess, 'run', return_value=bundled), \
         patch.object(g, 'service_discover', side_effect=OSError('offline')):
        catalog, _ = g.unified_catalog_build()
    assert catalog['routes'] == {'cloud-a': 'cloud', 'local-a': 'local'}
    assert [m['display_name'] for m in catalog['models']] == ['OpenAI · Cloud A', 'Local · local-a']
    g.service_change('manual', 'remove')
    assert g.services_document()['only_online'] is True
    # The cache stores only model IDs/context and removes deleted service entries.
    with patch.object(g, 'service_discover', side_effect=healthy):
        g.services_catalog(base)
    raw = cache_path.read_text()
    assert 'manual' not in json.loads(raw)['services'] and 'http://' not in raw

print('PASS: grouped names/order, unchanged IDs/routes/metadata, idempotency, online filtering, manual endpoints, auth failures, cache restoration, policy persistence, built-in source preservation.')
