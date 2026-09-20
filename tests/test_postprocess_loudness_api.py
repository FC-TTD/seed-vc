import io
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from postprocess_api import router


class NormalizeLoudnessEndpointTest(unittest.TestCase):
    def _wav_bytes(self):
        buf = io.BytesIO()
        sf.write(buf, np.zeros(1600, dtype=np.float32), 16000, format="WAV")
        buf.seek(0)
        return buf.getvalue()

    def test_normalize_loudness_uses_loudnorm_without_preset_effects(self):
        app = FastAPI()
        app.include_router(router, prefix="/postprocess")
        client = TestClient(app)

        with patch("postprocess_api.postprocess.loudnorm", return_value=(np.zeros(1600, dtype=np.float32), -16.0)) as loudnorm, \
             patch("postprocess_api.apply_preset") as apply_preset:
            response = client.post(
                "/postprocess/normalize-loudness",
                files={"file": ("input.wav", self._wav_bytes(), "audio/wav")},
                data={"target_loudness": "-16", "enable_limiter": "true", "limiter_threshold": "0.98"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "audio/wav")
        loudnorm.assert_called_once()
        self.assertEqual(loudnorm.call_args.kwargs["target_loudness"], -16.0)
        self.assertEqual(loudnorm.call_args.kwargs["threshold"], 0.98)
        apply_preset.assert_not_called()

    def test_normalize_loudness_still_normalizes_when_limiter_disabled(self):
        app = FastAPI()
        app.include_router(router, prefix="/postprocess")
        client = TestClient(app)

        with patch("postprocess_api.postprocess.loudnorm", return_value=(np.zeros(1600, dtype=np.float32), -16.0)) as loudnorm:
            response = client.post(
                "/postprocess/normalize-loudness",
                files={"file": ("input.wav", self._wav_bytes(), "audio/wav")},
                data={"target_loudness": "-16", "enable_limiter": "false", "limiter_threshold": "0.98"},
            )

        self.assertEqual(response.status_code, 200)
        loudnorm.assert_called_once()
        self.assertEqual(loudnorm.call_args.kwargs["target_loudness"], -16.0)
        self.assertEqual(loudnorm.call_args.kwargs["threshold"], 1.0)

    def test_preset_metadata_fallback_uses_summary_key(self):
        with patch("postprocess_api.list_presets", return_value=["telephone"]):
            self.assertEqual(
                {"telephone": {"summary": "telephone"}},
                __import__("postprocess_api")._fallback_preset_metadata(),
            )


if __name__ == "__main__":
    unittest.main()
