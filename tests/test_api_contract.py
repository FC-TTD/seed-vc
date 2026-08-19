import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class ApiContractTests(unittest.TestCase):
    def test_v2_is_api_only_and_uses_lazy_model_lifecycle(self):
        source = (ROOT / "api_v2.py").read_text(encoding="utf-8")
        self.assertIn('@app.post("/svc_file")', source)
        self.assertIn('@app.post("/unload")', source)
        self.assertIn("SmartModel(", source)
        self.assertIn("model_manager.unload()", source)
        self.assertNotIn("gradio", source.lower())

    def test_v2_contract_exposes_distinct_controls(self):
        source = (ROOT / "api_v2.py").read_text(encoding="utf-8")
        for field in (
            "intelligibility_cfg_rate",
            "similarity_cfg_rate",
            "convert_style",
            "anonymization_only",
            "repetition_penalty",
        ):
            self.assertIn(field, source)

    def test_compose_runs_one_image_as_two_services(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        self.assertEqual(compose.count("image: ${SVC_IMAGE:"), 1)
        self.assertIn("svc-v1:", compose)
        self.assertIn("svc-v2:", compose)
        self.assertIn('caddy_1: http://seed-vc', compose)
        self.assertIn('caddy: http://seed-vc', compose)
        self.assertIn('"caddy_1.@svc_v1.path": "/v1 /v1/*"', compose)
        self.assertIn('caddy_1.route: "@svc_v1"', compose)
        self.assertIn('"caddy.@svc_v2.path": "/v2 /v2/*"', compose)
        self.assertIn('caddy.route: "@svc_v2"', compose)
        self.assertIn("http://svc-api", compose)

    def test_formal_deploy_uses_committed_snapshot_and_immutable_image(self):
        deploy = (ROOT / "deploy.sh").read_text(encoding="utf-8")
        playbook = (ROOT / "ansible" / "site.yml").read_text(encoding="utf-8")
        self.assertIn('git -C "${root_dir}" archive "${source_commit}"', deploy)
        self.assertIn("role: docker_ci_cd", playbook)
        self.assertIn("registry.ttd/seed-vc/svc:h-*", playbook)
        self.assertIn("SVC_REPLACE_LEGACY_V1", playbook)


if __name__ == "__main__":
    unittest.main()
