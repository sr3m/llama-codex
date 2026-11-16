import importlib.util
import json
import http.server
from pathlib import Path
import tempfile
import threading
import urllib.request
import urllib.error

spec=importlib.util.spec_from_file_location('gateway',Path(__file__).resolve().parents[1]/'llama-codex.py')
g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
seen=[]
g.server_online=lambda: True
stream=(b'event: response.created\ndata: {"type":"response.created"}\n\n'
        b'event: response.output_item.done\ndata: {"type":"response.output_item.done","item":{"type":"function_call","name":"echo","call_id":"call_test","arguments":"{}"}}\n\n'
        b'event: response.completed\ndata: {"type":"response.completed"}\n\n')

class Upstream(http.server.BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        seen.append((self.path,dict(self.headers),body))
        status=429 if body.get('test_failure') else 200
        data=b'{"error":{"message":"test rate limit"}}' if status==429 else stream
        self.send_response(status);self.send_header('Content-Type','text/event-stream' if status==200 else 'application/json')
        self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)

up=http.server.ThreadingHTTPServer(('127.0.0.1',0),Upstream)
threading.Thread(target=up.serve_forever,daemon=True).start()
with tempfile.TemporaryDirectory(prefix='gateway-tests-') as temporary:
    root=Path(temporary);g.UNIFIED_HOME=root;g.UNIFIED_SETTINGS=root/'settings.json';g.UNIFIED_CATALOG=root/'catalog.json'
    g.UNIFIED_SETTINGS.write_text(json.dumps({'key':'local-test-key'}))
    g.UNIFIED_CATALOG.write_text(json.dumps({'models':[],'routes':{'test-cloud':'cloud','test-local':'local'}}))
    g.OPENAI_UPSTREAM=f'http://127.0.0.1:{up.server_port}/cloud'
    g.LOCAL_UPSTREAM=f'http://127.0.0.1:{up.server_port}/local'
    server=g.UnifiedServer(('127.0.0.1',0));threading.Thread(target=server.serve_forever,daemon=True).start()
    def post(body,extra=None,path='/v1/responses',key=True):
        headers={'Content-Type':'application/json','Authorization':'Bearer test-cloud-credential',
                 'ChatGPT-Account-ID':'test-account'}
        if key:headers[g.UNIFIED_HEADER]='local-test-key'
        headers.update(extra or {})
        request=urllib.request.Request(f'http://127.0.0.1:{server.server_port}'+path,
                                       data=json.dumps(body).encode(),headers=headers)
        try:
            with urllib.request.urlopen(request,timeout=3) as response:return response.status,response.read()
        except urllib.error.HTTPError as e:return e.code,e.read()
    item={'type':'function_call_output','call_id':'call_test','output':'PRIVATE_TEST_TEXT'}
    assert post({'model':'test-cloud','input':[item]})==(200,stream)
    path,headers,body=seen[-1]
    assert path=='/cloud/responses'
    assert headers.get('Authorization')=='Bearer test-cloud-credential'
    assert headers.get('Chatgpt-Account-Id')=='test-account'
    assert not any(k.lower()==g.UNIFIED_HEADER.lower() for k in headers)
    assert body['input']==[item]
    assert post({'model':'test-local','input':[{'type':'reasoning','encrypted_content':'OPAQUE'},item],
                 'service_tier':'priority'})==(200,stream)
    path,headers,body=seen[-1]
    assert path=='/local/responses' and body['input']==[item]
    assert not any(k.lower() in ('authorization','chatgpt-account-id',g.UNIFIED_HEADER.lower()) for k in headers)
    assert 'service_tier' not in body
    assert post({'model':'test-local'},key=False)[0]==401
    assert post({'model':'test-local'},extra={'Origin':'https://example.com'})[0]==403
    assert post({'model':'unknown'})[0]==400
    assert post({'model':'test-cloud'},path='/v1/arbitrary')[0]==404
    assert post({'model':'test-local','previous_response_id':'resp_old'})[0]==409
    assert post({'model':'test-cloud','test_failure':True})[0]==429
    audit=(root/'routes.jsonl').read_text()
    for secret in ('test-cloud-credential','test-account','PRIVATE_TEST_TEXT','local-test-key','OPAQUE'):
        assert secret not in audit
    server.shutdown();server.server_close()
up.shutdown();up.server_close()
print('PASS: credential isolation, exact SSE/function-call relay, replayed tool results, authentication/origin/path validation, upstream errors, redacted logs.')
