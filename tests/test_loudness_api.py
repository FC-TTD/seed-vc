"""Real CPU audio through the managed V1 HTTP contract; no model or GPU."""
import io
import unittest

import numpy as np
import pyloudnorm as pyln
import soundfile as sf
from fastapi.testclient import TestClient

from hub_runtime.api import build_api


class NoGPU:
    def task(self, function):
        def forbidden(*args, **kwargs):
            raise AssertionError("CPU postprocess entered a GPU task")
        return forbidden


def wav_bytes(audio, sr=48000):
    output = io.BytesIO()
    sf.write(output, audio, sr, format="WAV", subtype="FLOAT")
    return output.getvalue()


class LoudnessAPI(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(build_api(NoGPU()))

    def call(self, audio, **params):
        return self.client.post(
            "/postprocess/normalize-loudness",
            files={"file": ("source.wav", wav_bytes(audio), "audio/wav")},
            data=params,
        )

    def test_stereo_target_and_common_gain_preserve_audio(self):
        t = np.arange(96000) / 48000
        source = np.column_stack((.1*np.sin(2*np.pi*220*t), .025*np.sin(2*np.pi*470*t)))
        result = self.call(source, target_loudness=-18, enable_limiter="false")
        self.assertEqual(result.status_code, 200, result.text[:100] if result.status_code != 200 else "")
        actual, sr = sf.read(io.BytesIO(result.content))
        self.assertEqual(sr, 48000)
        self.assertEqual(actual.shape, source.shape)
        self.assertAlmostEqual(pyln.Meter(sr).integrated_loudness(actual), -18, places=2)
        gain = np.sum(source * actual) / np.sum(source * source)
        np.testing.assert_allclose(actual, source * gain, atol=2e-7)

    def test_silence_and_short_audio_keep_frames(self):
        for source in (np.zeros((4800, 2)), .1*np.sin(np.arange(4800)*.1)):
            with self.subTest(shape=source.shape):
                response = self.call(source, lufs=-23)
                self.assertEqual(response.status_code, 200)
                actual, sr = sf.read(io.BytesIO(response.content))
                self.assertEqual(actual.shape, source.shape)
                self.assertTrue(np.isfinite(actual).all())
                if not np.any(source):
                    self.assertFalse(np.any(actual))

    def test_limiter_and_legacy_alias(self):
        source = np.sin(np.arange(96000)*.1)*.01
        source[2000] = .99
        response = self.call(source, target_loudness=-30, lufs=-10,
                             enable_limiter="true", limiter_threshold=.5)
        self.assertEqual(response.status_code, 200)
        actual, _ = sf.read(io.BytesIO(response.content))
        self.assertLessEqual(np.max(np.abs(actual)), .5000001)
        self.assertGreater(np.sqrt(np.mean(actual**2)), .05)

    def test_invalid_audio_and_parameters_fail_explicitly(self):
        for data in (b"not audio", wav_bytes(np.empty(0)), wav_bytes(np.array([np.nan]*48000))):
            response = self.client.post("/postprocess/normalize-loudness",
                files={"file": ("bad.wav", data, "audio/wav")})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.call(np.zeros(48000), target_loudness=-5).status_code, 422)


if __name__ == "__main__":
    unittest.main()
