"""Installer modes and LaunchAgent upgrades; no actual services or chats are touched."""
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'llama-codex.py'
spec = importlib.util.spec_from_file_location('installer', SOURCE)
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

with tempfile.TemporaryDirectory(prefix='llama-codex-install-') as name:
    root = Path(name).resolve()
    g.CODEX_DIR = root / 'runtime'
    g.CONFIG = g.CODEX_DIR / 'config.toml'
    g.STATE = g.CODEX_DIR / 'provider_switch_state.json'
    g.UNIFIED_HOME = g.CODEX_DIR / 'unified'
    g.UNIFIED_SETTINGS = g.UNIFIED_HOME / 'settings.json'
    g.UNIFIED_CATALOG = g.UNIFIED_HOME / 'catalog.json'
    g.UNIFIED_AGENT = root / 'LaunchAgents' / (g.UNIFIED_LABEL + '.plist')
    g.UNIFIED_AGENT.parent.mkdir()
    g.CODEX_DIR.mkdir()
    g.UNIFIED_HOME.mkdir()
    g.UNIFIED_SETTINGS.write_text(json.dumps({'key': 'synthetic-key', 'version': 1}))
    catalog = {'models': [], 'routes': {'cloud-test': 'cloud', 'local-test': 'local'}}
    g.UNIFIED_CATALOG.write_text(json.dumps(catalog))
    original = '# keep comments\nmodel_provider = "openai"\n[desktop]\nkeep = true\n'
    g.CONFIG.write_text(original)

    # The default install now configures the provider without a second command.
    with patch.object(g, 'unified_catalog_update', return_value=catalog), patch.object(g, 'unified_start'):
        g.install_script()
        assert g.read_toml(g.CONFIG)['model_provider'] == g.UNIFIED_PROVIDER
        assert g.unified_settings()['enabled'] is True
        assert g.read_toml(g.CONFIG)['desktop']['keep'] is True
        assert '# keep comments' in g.CONFIG.read_text()
        # Choose local or cloud while preserving a single combined provider.
        with patch.object(g, 'start_server') as start:
            g.install_script(mode='local', model='local-test')
            start.assert_called_once()
        assert g.read_toml(g.CONFIG)['model'] == 'local-test'
        g.install_script(mode='cloud')
        assert g.read_toml(g.CONFIG)['model'] == 'cloud-test'
        before = g.CONFIG.read_bytes()
        with patch.object(g, 'unified_start') as start:
            try:
                g.install_script(mode='cloud', model='local-test')
                raise AssertionError('Cross-route model accepted')
            except ValueError:
                pass
            start.assert_not_called()
        assert g.CONFIG.read_bytes() == before
    assert (g.CODEX_DIR / 'llama-codex.py').read_bytes() == SOURCE.read_bytes()

    # Legacy job names are found through their commands, independent of account.
    old = g.UNIFIED_AGENT.parent / 'org.example.codex-unified-gateway.plist'
    definition = {'Label': old.stem, 'ProgramArguments': [sys.executable,
                  str(g.CODEX_DIR / 'switch-codex.py'), 'gateway-serve']}
    old_raw = plistlib.dumps(definition)
    old.write_bytes(old_raw)
    unrelated = g.UNIFIED_AGENT.parent / 'org.example.other.plist'
    unrelated_raw = plistlib.dumps({'Label': unrelated.stem, 'ProgramArguments': ['/usr/bin/true']})
    unrelated.write_bytes(unrelated_raw)
    assert [p for p, _ in g.unified_managed_agents()] == [old]

    domain = 'gui/' + str(os.getuid())
    loaded = {old.stem}
    commands = []
    active = False
    fail_bootstrap = False
    transient_failures = 0
    def launch(cmd, **kwargs):
        global transient_failures
        commands.append(cmd)
        if cmd[0] == '/usr/sbin/lsof':
            return subprocess.CompletedProcess(cmd, 0 if active else 1, '123\n' if active else '', '')
        assert cmd[0] == 'launchctl'
        action = cmd[1]
        code = 0
        if action == 'print':
            code = 0 if cmd[2].split('/')[-1] in loaded else 1
        elif action == 'bootout':
            loaded.discard(cmd[2].split('/')[-1])
        elif action == 'bootstrap':
            label = plistlib.loads(Path(cmd[3]).read_bytes())['Label']
            if transient_failures and label == g.UNIFIED_LABEL:
                transient_failures -= 1
                code = 5
            elif fail_bootstrap and label == g.UNIFIED_LABEL:
                code = 5
            else:
                loaded.add(label)
        else:
            raise AssertionError(cmd)
        if code and kwargs.get('check'):
            raise subprocess.CalledProcessError(code, cmd)
        return subprocess.CompletedProcess(cmd, code, '', '')

    with patch.object(g, 'unified_prepare'), patch.object(g.sys, 'platform', 'darwin'), \
         patch.object(g, 'unified_request', return_value={'service': 'codex-unified'}), \
         patch.object(g.subprocess, 'run', side_effect=launch), patch.object(g, 'unified_wait_ready'):
        # Active turns block migration before any job or plist changes.
        active = True
        try:
            g.unified_start()
            raise AssertionError('An active connection was interrupted')
        except RuntimeError as e:
            assert 'in use' in str(e)
        assert old.read_bytes() == old_raw and loaded == {old.stem}
        assert not g.UNIFIED_AGENT.exists()
        active = False
        # A bootstrap failure restores the old plist and running job.
        fail_bootstrap = True
        try:
            g.unified_start()
            raise AssertionError('Bootstrap failure was ignored')
        except subprocess.CalledProcessError:
            pass
        assert old.read_bytes() == old_raw and loaded == {old.stem}
        assert not g.UNIFIED_AGENT.exists()
        fail_bootstrap = False
        # Successful migration removes the obsolete login job, preserves others.
        g.unified_start()
        assert not old.exists() and loaded == {g.UNIFIED_LABEL}
        new = plistlib.loads(g.UNIFIED_AGENT.read_bytes())
        assert new['ProgramArguments'][1] == str(g.CODEX_DIR / 'llama-codex.py')
        assert 'LLAMA_CODEX_REVISION' in new['EnvironmentVariables']
        assert unrelated.read_bytes() == unrelated_raw
        assert list((g.CODEX_DIR / 'backups').glob('gateway-upgrade-*/*.plist'))
        # Reinstalling identical code never restarts the gateway.
        commands.clear()
        g.unified_start()
        assert commands == []
        # If a replacement starts but fails health checks, restore the old job.
        with (g.CODEX_DIR / 'llama-codex.py').open('a') as f:
            f.write('\n# synthetic revision\n')
        before = g.UNIFIED_AGENT.read_bytes()
        with patch.object(g, 'unified_wait_ready', side_effect=RuntimeError('health failure')):
            try:
                g.unified_start()
                raise AssertionError('Health failure ignored')
            except RuntimeError as e:
                assert 'health failure' in str(e)
        assert g.UNIFIED_AGENT.read_bytes() == before
        assert loaded == {g.UNIFIED_LABEL}
        # A transient launchd error after bootout is retried successfully.
        commands.clear()
        transient_failures = 1
        g.unified_start()
        assert transient_failures == 0 and loaded == {g.UNIFIED_LABEL}
        assert len([cmd for cmd in commands if cmd[1] == 'bootstrap']) == 2

print('PASS: automatic install, initial routes, config preservation, invalid selection, legacy job migration, active-turn guard, service rollback, idempotency.')
