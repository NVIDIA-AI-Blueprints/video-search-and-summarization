from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

import yaml


CHART = Path(__file__).resolve().parents[1] / "helm/industry-profiles/warehouse-operations/warehouse-2d-app"


def render(*overrides: str) -> dict[str, list[str]]:
    command = ["helm", "template", "test", str(CHART), "--set", "agent.enabled=true", "--set", "vss-alert-bridge.enabled=true"]
    for override in overrides:
        command.extend(["--set-string", override])
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    values: dict[str, list[str]] = {}
    for document in yaml.safe_load_all(result.stdout):
        if not document or document.get("kind") not in ("Deployment", "StatefulSet"):
            continue
        containers = document["spec"]["template"]["spec"].get("containers", [])
        for container in containers:
            for entry in container.get("env", []):
                if entry["name"] in ("VST_BASE_URL", "VST_INGRESS_ENDPOINT", "EXTERNAL_IP"):
                    values.setdefault(entry["name"], []).append(entry.get("value"))
    return values


@unittest.skipUnless(shutil.which("helm"), "helm is required")
class VstNodePortUrls(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not (CHART / "charts").is_dir():
            subprocess.run(["helm", "dependency", "build", str(CHART)], check=True, capture_output=True, text=True)

    def test_no_external_host(self) -> None:
        values = render("global.vstExternalPort=30888")
        self.assertIn("http://vss-vios-ingress:30888", values["VST_BASE_URL"])
        self.assertIn("http://vss-vios-ingress:30888/vst", values["VST_INGRESS_ENDPOINT"])
        self.assertIn("vss-vios-ingress:30888/vst", values["VST_INGRESS_ENDPOINT"])
        self.assertIn("127.0.0.1", values["EXTERNAL_IP"])

    def test_port_precedence_and_fallback(self) -> None:
        for overrides, port in (
            (("global.externalHost=node.test",), ""),
            (("global.externalHost=node.test", "global.externalPort=8443"), ":8443"),
            (("global.externalHost=node.test", "global.externalPort=8443", "global.vstExternalPort=30888"), ":30888"),
        ):
            with self.subTest(overrides=overrides):
                values = render(*overrides)
                self.assertIn(f"http://node.test{port}", values["VST_BASE_URL"])
                self.assertIn(f"http://node.test{port}/vst", values["VST_INGRESS_ENDPOINT"])
                self.assertIn(f"node.test{port}/vst", values["VST_INGRESS_ENDPOINT"])
                self.assertIn(f"node.test{port}", values["EXTERNAL_IP"])

    def test_explicit_overrides(self) -> None:
        values = render(
            "global.externalHost=node.test",
            "global.vstExternalPort=30888",
            "agent.vss-agent.vstBaseUrl=http://agent.test/vst",
            "vios.vss-vios-sensor.vstIngressEndpoint=http://sensor.test/vst",
            "vios.vss-vios-streamprocessing.vstIngressEndpoint=stream.test/vst",
            "vss-alert-bridge.externalIp=bridge.test:9443",
        )
        self.assertIn("http://agent.test/vst", values["VST_BASE_URL"])
        self.assertIn("http://sensor.test/vst", values["VST_INGRESS_ENDPOINT"])
        self.assertIn("stream.test/vst", values["VST_INGRESS_ENDPOINT"])
        self.assertIn("bridge.test:9443", values["EXTERNAL_IP"])


if __name__ == "__main__":
    unittest.main()
