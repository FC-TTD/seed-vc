import os
os.environ["HUB_MODEL_VARIANT"] = "v2"
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
from hub_runtime.api_v2 import build_api
from hub_runtime.api import post_process_file
from hub_runtime.adapter import final_audio

LEASE = {
    "id": "lease-one",
    "service": "svc-v2",
    "generation": 1,
    "revision": 1,
    "fingerprint": "a" * 64,
}


class Node:
    service = "svc-v2"

    def __init__(self):
        self.finishes = []

    def begin(self, key, grant=None):
        return {
            "id": uuid4().hex,
            "key": key,
            "service": self.service,
            "lease_id": "lease-one",
            "generation": 1,
        }

    def finish(self, activity, state):
        self.finishes.append(state)


class Model:
    def __init__(self):
        self.calls = []
        self.completed = 0

    def convert_voice_with_streaming(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        try:
            yield (
                None,
                (22050, np.sin(np.arange(22050) * 2 * np.pi * 220 / 22050) * 0.1),
            )
        finally:
            self.completed += 1


def audio():
    output = io.BytesIO()
    sf.write(
        output,
        np.sin(np.arange(22050) * 2 * np.pi * 180 / 22050) * 0.1,
        22050,
        format="WAV",
    )
    return output.getvalue()


class Contracts(unittest.TestCase):
    def test_original_parameter_names_locations_and_defaults(self):
        class Description:
            def task(self, fn):
                return fn

        schema = build_api(Description()).openapi()
        endpoint = schema["paths"]["/svc_file"]["post"]
        ref = endpoint["requestBody"]["content"]["multipart/form-data"]["schema"]["$ref"].split("/")[-1]
        params = schema["components"]["schemas"][ref]["properties"]
        self.assertEqual({k: v.get("default") for k,v in params.items() if k not in ("src_file", "ref_file")},
            dict(steps=30,length_adjust=1.0,intelligibility_cfg_rate=0.0,similarity_cfg_rate=0.7,
                 top_p=0.9,temperature=1.0,repetition_penalty=1.0,convert_style=False,
                 anonymization_only=False,post_process=True,lufs=-23.0,trim_silence=False,enable_eq=True))
        self.assertNotIn("parameters", endpoint)

    def test_api_tracks_native_completion_and_preserves_postprocessing(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Model()
            node = Node()
            runtime = Runtime(
                lambda: model,
                node=node,
                version="test",
                token="t" * 32,
                state_dir=directory,
            )
            with TestClient(
                create_app(runtime), headers={"X-Forwarded-Host": "testserver"}
            ) as client:
                runtime.load(LEASE)
                response = client.post(
                    "/svc_file",
                    data={"steps":25, "intelligibility_cfg_rate":0.4,"convert_style":"true"},
                    files={
                        "src_file": ("source.wav", audio(), "audio/wav"),
                        "ref_file": ("target.wav", audio(), "audio/wav"),
                    },
                )
                self.assertEqual(response.status_code, 200, response.text[:100])
                self.assertEqual(model.calls[0][1]["diffusion_steps"], 25)
                self.assertTrue(model.calls[0][1]["convert_style"])
                self.assertEqual(model.calls[0][1]["intelligebility_cfg_rate"], 0.4)
                self.assertEqual(model.completed, 1)
                self.assertEqual(node.finishes, ["succeeded"])
                wave, rate = sf.read(io.BytesIO(response.content))
                self.assertEqual(rate,22050)
                self.assertEqual(len(wave),22050)

    def test_cpu_postprocess_and_health_do_not_load_model(self):
        with tempfile.TemporaryDirectory() as directory:

            def loader():
                raise AssertionError("CPU helper must not request GPU")

            node = Node()
            runtime = Runtime(
                loader, node=node, version="test", token="t" * 32, state_dir=directory
            )
            with TestClient(create_app(runtime), headers={"X-Forwarded-Host": "testserver"}) as client:
                self.assertEqual(client.get("/health").status_code, 200)
                self.assertEqual(client.get("/capabilities").json()["model"],"svc-v2")
                self.assertEqual(client.post("/unload").status_code,409)
                self.assertEqual(node.finishes, [])

    def test_original_ui_controls_and_shared_runtime_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Model()
            node = Node()
            runtime = Runtime(
                lambda: model,
                node=node,
                version="test",
                token="t" * 32,
                state_dir=directory,
            )
            with TestClient(create_app(runtime), headers={"X-Forwarded-Host": "testserver"}) as client:
                runtime.load(LEASE)
                config = client.get("/ui/config").json()
                dep = next(
                    d
                    for d in config["dependencies"]
                    if d.get("api_name") == "convert_voice"
                )
                components = {c["id"]: c for c in config["components"]}
                uploaded = client.post(
                    "/ui/gradio_api/upload",
                    files={"files": ("sample.wav", audio(), "audio/wav")},
                )
                self.assertEqual(uploaded.status_code, 200, uploaded.text)
                values = []
                for ident in dep["inputs"]:
                    c = components[ident]
                    value = c["props"].get("value")
                    if c["type"] == "audio":
                        value = {
                            "path": uploaded.json()[0],
                            "orig_name": "sample.wav",
                            "meta": {"_type": "gradio.FileData"},
                        }
                    values.append(value)
                self.assertEqual(values[2:], [30, 1.0, 0.0, 0.7, 0.9, 1.0, 1.0, False, False])
                call = client.post(
                    "/ui/gradio_api/call/convert_voice", json={"data": values}
                )
                self.assertEqual(call.status_code, 200, call.text)
                result = client.get(
                    "/ui/gradio_api/call/convert_voice/" + call.json()["event_id"]
                )
                self.assertIn("event: complete", result.text, result.text)
                self.assertEqual(model.completed, 1)
                self.assertEqual(node.finishes, ["succeeded"])

    def test_no_old_plugin_imports_and_no_model_import_during_describe(self):
        for path in (Path(__file__).parents[1] / "hub_runtime").glob("*.py"):
            tree = ast.parse(path.read_text())
            for item in ast.walk(tree):
                if isinstance(item, ast.ImportFrom):
                    self.assertFalse(
                        (item.module or "").startswith("ttd_fastapi_utils")
                    )


if __name__ == "__main__":
    unittest.main()
