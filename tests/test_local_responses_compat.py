import copy
import importlib.util
import json
from pathlib import Path
import threading
import urllib.request
import urllib.error

spec=importlib.util.spec_from_file_location('gateway',Path(__file__).resolve().parents[1]/'llama-codex.py')
g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
tool={'type':'function','name':'probe','description':'Test probe','parameters':{'type':'object','properties':{}}}
notice={'type':'function_call_output','id':'notice-test','namespace':'codex_app',
        'name':'state_changed','output':'Synthetic notification: CODE-7391'}
message={'type':'message','role':'user','content':[{'type':'input_text','text':'Reply only OK.'}]}
body={'model':'ggml-org/Qwen2.5-Coder-3B-Q8_0-GGUF:Q8_0','stream':False,
      'max_output_tokens':32,'input':[{'type':'additional_tools','role':'developer','tools':[tool]},
                                    notice,{'role':'assistant','content':'Noted.'},message]}
before=copy.deepcopy(body)
normalized=g.unified_local_body(body)
assert body==before
assert normalized['tools']==[tool]
assert [i['type'] for i in normalized['input']]==['message','message','message']
assert 'CODE-7391' in normalized['input'][0]['content'][0]['text']
assert normalized['input'][0]['role']=='user'
assert normalized['input'][1]['role']=='assistant'
paired={'type':'function_call_output','call_id':'call-paired','output':'unchanged'}
assert g.unified_local_body({'input':[paired]})['input']==[paired]
custom=[{'type':'custom_tool_call','call_id':'c1','name':'apply_patch','input':'PATCH'},
        {'type':'custom_tool_call_output','call_id':'c1','output':'APPLIED'}]
translated=g.unified_local_body({'input':custom})['input']
assert translated[0]['type']=='function_call' and json.loads(translated[0]['arguments'])=={'input':'PATCH'}
assert translated[1]['type']=='function_call_output'
privileged={'instructions':'ROOT-INSTRUCTION','input':[
    {'type':'message','role':'developer','content':[{'type':'input_text','text':'DEV-ONE'}]},
    message,
    {'type':'message','role':'developer','content':'DEV-TWO'},notice]}
prepared=g.unified_local_body(privileged)
assert 'instructions' not in prepared
assert prepared['input'][0]['role']=='system'
assert all(i.get('role') not in ('system','developer') for i in prepared['input'][1:])
instruction_text=''.join(i.get('text','') for i in prepared['input'][0]['content'])
assert all(t in instruction_text for t in ('ROOT-INSTRUCTION','DEV-ONE','DEV-TWO'))
assert 'CODE-7391' not in instruction_text
assert prepared['input'][1]==message
print('PASS: original request unchanged; notifications retained as data; metadata tools preserved; untyped assistant messages normalized; paired tool results preserved.',flush=True)
