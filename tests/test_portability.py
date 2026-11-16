import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'llama-codex.py'

with tempfile.TemporaryDirectory(prefix='switch-codex-portable-') as name:
    home = Path(name)
    env = {**os.environ, 'CODEX_HOME': str(home / 'runtime')}
    subprocess.run([sys.executable, str(SOURCE), '--help'], env=env, check=True, capture_output=True)
    assert not (home / 'runtime').exists(), 'Help must have no runtime side effects'
    subprocess.run([sys.executable, str(SOURCE), 'install'], env=env, check=True, capture_output=True)
    installed = home / 'runtime' / 'llama-codex.py'
    assert installed.read_bytes() == SOURCE.read_bytes()
    config = installed.parent / 'config.toml'
    config.write_text('# Keep my configuration\n[desktop]\nkeep = true\n')
    subprocess.run([sys.executable, str(SOURCE), 'install'], env=env, check=True, capture_output=True)
    assert config.read_text().startswith('# Keep my configuration')
    assert len(list((installed.parent / 'backups').glob('script-*.py'))) == 1
    with patch.dict(os.environ, env):
        spec = importlib.util.spec_from_file_location('portable_gateway', SOURCE)
        g = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(g)
    assert g.CODEX_DIR == installed.parent.resolve()
    assert g.CATALOG.is_relative_to(g.CODEX_DIR)
    assert '/Users/' not in SOURCE.read_text()
    bundled = {'models': [{'slug': 'gpt-test', 'base_instructions': 'Synthetic instructions', 'visibility': 'list'}]}
    fake_cli = subprocess.CompletedProcess([], 0, json.dumps(bundled), '')
    # An ordinary Codex cache is accepted even without the Desktop-specific name.
    (g.CODEX_DIR / 'models_cache.json').write_text(json.dumps(bundled))
    g.unified_prepare()
    assert json.loads(g.UNIFIED_CLOUD_CATALOG.read_text()) == bundled
    # Cloud-only startup is allowed while the local server is offline.
    with patch.object(g, 'build_catalog', side_effect=g.NoLocalModels('offline')), \
         patch.object(g, 'cli_paths', return_value=['synthetic-codex']), \
         patch.object(g.subprocess, 'run', return_value=fake_cli):
        catalog, warning = g.unified_catalog_build()
    assert catalog['routes'] == {'gpt-test': 'cloud'} and warning == 'offline'
    g.atomic_write(g.UNIFIED_CATALOG, g.json_text(catalog))
    # First installation has no SQLite database yet; activation still works.
    with patch.object(g, 'unified_catalog_update'), patch.object(g, 'unified_start'):
        g.unified_activate()
    parsed = g.read_toml(g.CONFIG)
    assert parsed['model_provider'] == 'codex-unified'
    assert parsed['model'] == 'gpt-test'
    assert parsed['desktop']['keep'] is True
    assert list((g.CODEX_DIR / 'backups').glob('unified-initial-*/config.toml'))
    # A missing cache can also be seeded from the installed CLI's bundled schema.
    g.UNIFIED_CLOUD_CATALOG.unlink()
    (g.CODEX_DIR / 'models_cache.json').unlink()
    with patch.object(g, 'cli_paths', return_value=['synthetic-codex']), \
         patch.object(g.subprocess, 'run', return_value=fake_cli):
        g.unified_prepare()
    assert json.loads(g.UNIFIED_CLOUD_CATALOG.read_text()) == bundled
print('PASS: isolated install, backups, CODEX_HOME, cache/CLI fallback, cloud-only catalog, activation without existing chats.')
