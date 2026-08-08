import unittest
from pathlib import Path


class LabContractTests(unittest.TestCase):
    def test_ui_contains_two_version_tabs_and_endpoints(self):
        html = (Path(__file__).parents[1] / "static" / "index.html").read_text(encoding="utf-8")
        for expected in ("Seed-VC v1.0", "Seed-VC v2.0", "/api/v1/convert", "/api/v2/convert"):
            self.assertIn(expected, html)

    def test_preview_compose_keeps_v1_as_upstream_and_pins_gpu2(self):
        compose = (Path(__file__).parents[1] / "compose.preview.yaml").read_text(encoding="utf-8")
        self.assertIn("SEED_VC_V1_UPSTREAM: http://svc-api", compose)
        self.assertIn('device_ids: ["2"]', compose)
        self.assertIn("17879:7856", compose)
        self.assertIn("dockerfile: Dockerfile.preview", compose)

    def test_preview_image_uses_pinned_seed_vc_base(self):
        dockerfile = (Path(__file__).parents[1] / "Dockerfile.preview").read_text(encoding="utf-8")
        self.assertIn("registry.ttd/svc-api@sha256:", dockerfile)
        self.assertNotIn(":latest", dockerfile)


if __name__ == "__main__":
    unittest.main()
