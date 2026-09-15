"""CPU Gradio client inside the model image, invoking the real managed UI."""
import argparse
import hashlib
import json
from pathlib import Path
import io
from urllib import request,parse
import soundfile as sf
from gradio_client import Client,handle_file

p=argparse.ArgumentParser();p.add_argument('--url',required=True);p.add_argument('--source',required=True);p.add_argument('--reference',required=True);p.add_argument('--output',required=True);p.add_argument('--variant',choices=['v1','v2'],default='v1');a=p.parse_args()
client=Client(a.url,verbose=False,download_files=False)
params=[12,1.0,.7,False,False,0] if a.variant=='v1' else [12,1.0,0.0,.7,.9,1.0,1.0,False,False]
result=client.predict(handle_file(a.source),handle_file(a.reference),*params,api_name='/convert_voice')
opener=request.build_opener(request.ProxyHandler({}))
stream,final=result
# Gradio 5.23 Client's generic downloader incorrectly treats HLS playlists as
# regular file endpoints. Fetch the actual stream URL used by its browser UI.
stream_path=stream['path'] if isinstance(stream,dict) else stream
stream_url=a.url.rstrip('/')+'/gradio_api/stream/'+stream_path
with opener.open(stream_url,timeout=20) as r:playlist=r.read().decode()
assert '#EXTM3U' in playlist and '#EXT-X-ENDLIST' in playlist
segment=next(line for line in playlist.splitlines() if line and not line.startswith('#'))
with opener.open(parse.urljoin(stream_url,segment),timeout=20) as r:assert len(r.read())>0
file_url=final['url'] if isinstance(final,dict) else a.url.rstrip('/')+'/gradio_api/file='+final
with opener.open(file_url,timeout=20) as r:body=r.read()
data,rate=sf.read(io.BytesIO(body));assert len(data)>0 and rate==22050
out=Path(a.output);out.write_bytes(body)
print(json.dumps({'status':'passed','url':a.url,'stream_playlist':True,'stream_segment':True,'sample_rate':rate,'duration':len(data)/rate,'bytes':out.stat().st_size,'sha256':hashlib.sha256(out.read_bytes()).hexdigest()}))
