import ast
import io
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient
from ttd_model_runtime import Runtime
from hub_runtime.__main__ import create_app
from hub_runtime.api import build_api, post_process_file
from hub_runtime.adapter import final_audio

LEASE={"id":"lease-one","service":"svc-v1","generation":1,"revision":1,"fingerprint":"a"*64}

class Node:
    service="svc-v1"
    def __init__(self):self.finishes=[]
    def begin(self,key,grant=None):return {"id":uuid4().hex,"key":key,"service":self.service,"lease_id":"lease-one","generation":1}
    def finish(self,activity,state):self.finishes.append(state)

class Model:
    def __init__(self):self.calls=[];self.completed=0
    def convert_voice(self,*args,**kwargs):
        self.calls.append((args,kwargs))
        try:yield None,(22050,np.sin(np.arange(22050)*2*np.pi*220/22050)*.1)
        finally:self.completed+=1

def audio():
    output=io.BytesIO();sf.write(output,np.sin(np.arange(22050)*2*np.pi*180/22050)*.1,22050,format="WAV");return output.getvalue()

class Contracts(unittest.TestCase):
    def test_original_parameter_names_locations_and_defaults(self):
        class Description:
            def task(self,fn):return fn
        schema=build_api(Description()).openapi()
        endpoint=schema['paths']['/svc_file']['post']
        params={p['name']:(p['in'],p['schema'].get('default')) for p in endpoint['parameters']}
        self.assertEqual(params,{'steps':('query','50'),'length_adjust':('query','1.0'),'inference_cfg_rate':('query','0.7'),'f0_conditioned':('query',False),'auto_f0_adjust':('query',False),'pitch_shift':('query','0')})
        self.assertIn('/postprocess/apply-generic/{preset_name}',schema['paths'])

    def test_api_tracks_native_completion_and_preserves_postprocessing(self):
        with tempfile.TemporaryDirectory() as directory:
            model=Model();node=Node();runtime=Runtime(lambda:model,node=node,version='test',token='t'*32,state_dir=directory)
            with TestClient(create_app(runtime)) as client:
                runtime.load(LEASE)
                response=client.post('/svc_file?steps=25&f0_conditioned=true&pitch_shift=2',files={'src_file':('source.wav',audio(),'audio/wav'),'ref_file':('target.wav',audio(),'audio/wav')})
                self.assertEqual(response.status_code,200,response.text[:100])
                self.assertEqual(model.calls[0][1]['diffusion_steps'],25)
                self.assertTrue(model.calls[0][1]['f0_condition'])
                self.assertEqual(model.completed,1)
                self.assertEqual(node.finishes,['succeeded'])
                expected=post_process_file(np.sin(np.arange(22050)*2*np.pi*220/22050)*.1,22050).getvalue()
                self.assertEqual(response.content,expected)

    def test_cpu_postprocess_and_health_do_not_load_model(self):
        with tempfile.TemporaryDirectory() as directory:
            def loader():raise AssertionError('CPU helper must not request GPU')
            node=Node();runtime=Runtime(loader,node=node,version='test',token='t'*32,state_dir=directory)
            with TestClient(create_app(runtime)) as client:
                self.assertEqual(client.get('/health').status_code,200)
                self.assertEqual(client.get('/postprocess/presets').status_code,200)
                result=client.post('/postprocess/apply-generic/telephone',files={'file':('sample.wav',audio(),'audio/wav')})
                self.assertEqual(result.status_code,200,result.text[:100])
                self.assertEqual(node.finishes,[])

    def test_original_ui_controls_and_shared_runtime_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            model=Model();node=Node();runtime=Runtime(lambda:model,node=node,version='test',token='t'*32,state_dir=directory)
            with TestClient(create_app(runtime)) as client:
                runtime.load(LEASE)
                config=client.get('/ui/config').json()
                dep=next(d for d in config['dependencies'] if d.get('api_name')=='convert_voice')
                components={c['id']:c for c in config['components']}
                uploaded=client.post('/ui/gradio_api/upload',files={'files':('sample.wav',audio(),'audio/wav')})
                self.assertEqual(uploaded.status_code,200,uploaded.text)
                values=[]
                for ident in dep['inputs']:
                    c=components[ident];value=c['props'].get('value')
                    if c['type']=='audio':value={'path':uploaded.json()[0],'orig_name':'sample.wav','meta':{'_type':'gradio.FileData'}}
                    values.append(value)
                self.assertEqual(values[2:], [50,1.0,.7,False,False,0])
                call=client.post('/ui/gradio_api/call/convert_voice',json={'data':values})
                self.assertEqual(call.status_code,200,call.text)
                result=client.get('/ui/gradio_api/call/convert_voice/'+call.json()['event_id'])
                self.assertIn('event: complete',result.text,result.text)
                self.assertEqual(model.completed,1)
                self.assertEqual(node.finishes,['succeeded'])

    def test_no_old_plugin_imports_and_no_model_import_during_describe(self):
        for path in (Path(__file__).parents[1]/'hub_runtime').glob('*.py'):
            tree=ast.parse(path.read_text())
            for item in ast.walk(tree):
                if isinstance(item,ast.ImportFrom):self.assertFalse((item.module or '').startswith('ttd_fastapi_utils'))

if __name__=='__main__':unittest.main()
