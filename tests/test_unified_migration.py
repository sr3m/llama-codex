import importlib.util
import fcntl
import json
from pathlib import Path
import sqlite3
import tempfile
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('gateway',Path(__file__).resolve().parents[1]/'llama-codex.py')
g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)

with tempfile.TemporaryDirectory(prefix='migration-tests-') as name:
    root=Path(name).resolve()
    g.CODEX_DIR=root;g.CONFIG=root/'config.toml';g.STATE=root/'provider_switch_state.json'
    g.UNIFIED_HOME=root/'unified';g.UNIFIED_HOME.mkdir()
    g.UNIFIED_SETTINGS=g.UNIFIED_HOME/'settings.json';g.UNIFIED_CATALOG=g.UNIFIED_HOME/'catalog.json'
    g.UNIFIED_SETTINGS.write_text(json.dumps({'key':'test-only-key','version':1}))
    g.UNIFIED_CATALOG.write_text(json.dumps({'models':[],'routes':{'gpt-6.1-sol':'cloud','local-model':'local'}}))
    g.CONFIG.write_text('model_provider = "openai"\nmodel = "gpt-6.1-sol"\nweb_search = "live"\n[desktop]\nkeep = true\n')
    original_config=g.CONFIG.read_bytes();original_settings=g.UNIFIED_SETTINGS.read_bytes()
    db=root/'state_9.sqlite'
    p=root/'session.jsonl'
    first=json.dumps({'type':'session_meta','payload':{'id':'a','model_provider':'openai','base_instructions':{'text':'KEEP'}}}).encode()+b'\n'
    rest=b'{"type":"response_item","payload":{"text":"KEEP ALL MESSAGE BYTES"}}\n'
    p.write_bytes(first+rest)
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE threads(id TEXT PRIMARY KEY,model_provider TEXT,model TEXT,reasoning_effort TEXT,rollout_path TEXT,title TEXT,tokens_used INTEGER)')
        c.execute('INSERT INTO threads VALUES(?,?,?,?,?,?,?)',('a','openai','gpt-6.1-sol','high',str(p),'keep title',123))
        c.execute('INSERT INTO threads VALUES(?,?,?,?,?,?,?)',('b','third-party','other-model','medium','/missing','keep other',456))
    real_write=g.atomic_write
    def fail_config(path,data):
        if path==g.CONFIG:raise OSError('simulated config write failure')
        return real_write(path,data)
    with patch.object(g,'unified_catalog_update',return_value=json.loads(g.UNIFIED_CATALOG.read_text())),patch.object(g,'unified_start'):
        with patch.object(g,'atomic_write',side_effect=fail_config):
            try:g.unified_activate();raise AssertionError('Expected failure')
            except OSError:pass
        assert p.read_bytes()==first+rest
        assert g.CONFIG.read_bytes()==original_config and g.UNIFIED_SETTINGS.read_bytes()==original_settings
        with sqlite3.connect(db) as c:
            assert c.execute('SELECT model_provider FROM threads WHERE id="a"').fetchone()[0]=='openai'
        g.unified_activate()
    assert p.read_bytes().partition(b'\n')[2]==rest
    assert json.loads(p.read_bytes().partition(b'\n')[0])['payload']['model_provider']=='codex-unified'
    cfg=g.read_toml(g.CONFIG)
    assert cfg['desktop']=={'keep':True} and cfg['web_search']=='live'
    assert cfg['model_provider']=='codex-unified' and 'model_catalog_json' not in cfg
    assert cfg['model_providers']['codex-unified']['requires_openai_auth'] is True
    # Reinstalling must not mark an already-synchronized open chat as pending.
    lock = g.CODEX_DIR/'thread-writer-locks/a.lock'
    with lock.open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with patch.object(g,'unified_catalog_update',return_value=json.loads(g.UNIFIED_CATALOG.read_text())),patch.object(g,'unified_start'):
            g.unified_activate()
        assert g.unified_settings()['pending_active_threads'] == []
        assert p.read_bytes().partition(b'\n')[2] == rest
    with sqlite3.connect(db) as c:
        assert c.execute('SELECT model_provider,model,reasoning_effort,title,tokens_used FROM threads WHERE id="a"').fetchone()==('codex-unified','gpt-6.1-sol','high','keep title',123)
        assert c.execute('SELECT model_provider FROM threads WHERE id="b"').fetchone()[0]=='third-party'
        assert c.execute('PRAGMA quick_check').fetchone()[0]=='ok'
    # Picking a default model in unified mode must never rewrite thread providers.
    saved={'version':1,'selection':{'cloud':{'model':'gpt-6.1-sol'}},'preferences':{},'cloud_options':{}}
    g.STATE.write_text(json.dumps(saved))
    before=db.read_bytes()
    with patch.object(g,'unified_catalog_update',return_value={'routes':{'gpt-6.1-sol':'cloud','local-model':'local'}}):
        g.unified_select('local','local-model')
    assert db.read_bytes()==before
    assert g.read_toml(g.CONFIG)['model_provider']=='codex-unified'
    g.unified_restore()
    assert g.read_toml(g.CONFIG)['model_provider']=='openai'
    with sqlite3.connect(db) as c:
        assert c.execute('SELECT model_provider FROM threads WHERE id="a"').fetchone()[0]=='openai'
        assert c.execute('SELECT model_provider FROM threads WHERE id="b"').fetchone()[0]=='third-party'
    assert p.read_bytes().splitlines()[1] == rest.strip()
print('PASS: migration rollback; only provider metadata changes; messages/models/titles/tokens preserved; unrelated providers preserved; changing default model leaves SQLite untouched.')
