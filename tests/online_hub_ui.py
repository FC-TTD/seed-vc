"""CPU Gradio client inside the model image, invoking the real managed UI."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import soundfile as sf
from gradio_client import Client,handle_file

p=argparse.ArgumentParser();p.add_argument('--url',required=True);p.add_argument('--source',required=True);p.add_argument('--reference',required=True);p.add_argument('--output',required=True);a=p.parse_args()
client=Client(a.url,verbose=False)
result=client.predict(handle_file(a.source),handle_file(a.reference),12,1.0,.7,False,False,0,api_name='/convert_voice')
final=result[1]
if isinstance(final,dict):final=final.get('path') or final.get('value')
data,rate=sf.read(final);assert len(data)>0 and rate==22050
out=Path(a.output);shutil.copyfile(final,out)
print(json.dumps({'status':'passed','url':a.url,'sample_rate':rate,'duration':len(data)/rate,'bytes':out.stat().st_size,'sha256':hashlib.sha256(out.read_bytes()).hexdigest()}))
