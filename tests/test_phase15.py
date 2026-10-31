"""WireGuard, K8s, reverse-proxy, AI routing, web mode."""
from __future__ import annotations
import json, sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.wireguard import (WireGuardConfig, WireGuardPeer, parse_config, build_config,
                                  parse_status, status_command, up_command, down_command)
from services.k8s import (K8sPod, parse_pods, parse_deployments, parse_services,
                            classify_kubectl_command, list_pods_command)
from services.reverse_proxy import (ProxyInfo, parse_detect, template_for,
                                      caddy_config_path, caddy_validate_command)
from services.ai_routing import is_local_profile, pick_best_profile, should_try_local
from services.web_mode import WEB_MODE_LIMITATIONS, WEB_MODE_SECURITY_NOTES, web_mode_summary


class TestWireGuard(unittest.TestCase):
    def test_parse_config(self):
        text = """[Interface]
PrivateKey = abc123
Address = 10.0.0.1/24
ListenPort = 51820

[Peer]
PublicKey = peer123
AllowedIPs = 10.0.0.2/32
Endpoint = 1.2.3.4:51820
PersistentKeepalive = 25
"""
        cfg = parse_config(text)
        self.assertEqual(cfg.interface_private_key, "abc123")
        self.assertEqual(cfg.interface_address, "10.0.0.1/24")
        self.assertEqual(cfg.interface_listen_port, 51820)
        self.assertEqual(len(cfg.peers), 1)
        self.assertEqual(cfg.peers[0].public_key, "peer123")
        self.assertEqual(cfg.peers[0].allowed_ips, "10.0.0.2/32")
        self.assertEqual(cfg.peers[0].endpoint, "1.2.3.4:51820")
        self.assertEqual(cfg.peers[0].persistent_keepalive, 25)

    def test_build_config_roundtrip(self):
        cfg = WireGuardConfig(
            interface_private_key="key", interface_address="10.0.0.1/24",
            interface_listen_port=51820,
            peers=[WireGuardPeer(public_key="pk", allowed_ips="10.0.0.2/32")])
        text = build_config(cfg)
        cfg2 = parse_config(text)
        self.assertEqual(cfg2.interface_private_key, "key")
        self.assertEqual(cfg2.peers[0].public_key, "pk")

    def test_parse_status_not_running(self):
        s = parse_status("NOT_RUNNING")
        self.assertFalse(s["running"])

    def test_parse_status_running(self):
        s = parse_status("interface: wg0\n  listening port: 51820")
        self.assertTrue(s["running"])
        self.assertEqual(s.get("listening port"), "51820")

    def test_commands_quote_interface(self):
        self.assertIn("wg0", up_command("wg0"))
        self.assertIn("wg0", down_command("wg0"))
        self.assertIn("wg0", status_command("wg0"))


class TestK8s(unittest.TestCase):
    def test_parse_pods(self):
        data = {"items": [{"metadata": {"name": "web-abc", "namespace": "default"},
                           "status": {"phase": "Running", "containerStatuses": [{"ready": True, "restartCount": 0}]},
                           "spec": {"nodeName": "node-1"}}]}
        pods = parse_pods(json.dumps(data))
        self.assertEqual(len(pods), 1)
        self.assertEqual(pods[0].name, "web-abc")
        self.assertEqual(pods[0].status, "Running")
        self.assertEqual(pods[0].ready, "1/1")
        self.assertEqual(pods[0].node, "node-1")

    def test_parse_deployments(self):
        data = {"items": [{"metadata": {"name": "web"}, "spec": {"replicas": 3},
                           "status": {"readyReplicas": 3, "updatedReplicas": 3, "availableReplicas": 3}}]}
        deps = parse_deployments(json.dumps(data))
        self.assertEqual(len(deps), 1)
        self.assertEqual(deps[0].name, "web")
        self.assertEqual(deps[0].ready, "3/3")

    def test_parse_services(self):
        data = {"items": [{"metadata": {"name": "web-svc"},
                           "spec": {"type": "ClusterIP", "clusterIP": "10.96.0.1",
                                    "ports": [{"port": 80, "protocol": "TCP"}]}}]}
        svcs = parse_services(json.dumps(data))
        self.assertEqual(len(svcs), 1)
        self.assertEqual(svcs[0].name, "web-svc")
        self.assertEqual(svcs[0].type, "ClusterIP")
        self.assertIn("80/TCP", svcs[0].ports)

    def test_classify(self):
        self.assertEqual(classify_kubectl_command("kubectl get pods"), "safe")
        self.assertEqual(classify_kubectl_command("kubectl delete pod web"), "destructive")
        self.assertEqual(classify_kubectl_command("kubectl apply -f deploy.yaml"), "writes")

    def test_parse_empty(self):
        self.assertEqual(parse_pods(""), [])
        self.assertEqual(parse_pods("not json"), [])

    def test_list_pods_command(self):
        cmd = list_pods_command("default")
        self.assertIn("kubectl get pods", cmd)
        self.assertIn("default", cmd)


class TestReverseProxy(unittest.TestCase):
    def test_parse_detect_caddy(self):
        text = "##CADDY\ncaddy v2.7.6\n##TRAEFIK\n\n##NGINX\n\n##STATUS\nactive"
        info = parse_detect(text)
        self.assertEqual(info.kind, "caddy")
        self.assertIn("v2.7.6", info.version)

    def test_parse_detect_traefik(self):
        text = "##CADDY\n\n##TRAEFIK\nVersion: v2.10\n##NGINX\n\n##STATUS\n"
        info = parse_detect(text)
        self.assertEqual(info.kind, "traefik")

    def test_parse_detect_none(self):
        text = "##CADDY\n\n##TRAEFIK\n\n##NGINX\n\n##STATUS\n"
        info = parse_detect(text)
        self.assertEqual(info.kind, "")

    def test_template_static(self):
        tmpl = template_for("caddy", "static", domain="example.com", doc_root="/var/www")
        self.assertIn("example.com", tmpl)
        self.assertIn("/var/www", tmpl)
        self.assertIn("file_server", tmpl)

    def test_template_proxy(self):
        tmpl = template_for("caddy", "proxy", domain="api.example.com",
                             backend_host="127.0.0.1", backend_port="8080")
        self.assertIn("api.example.com", tmpl)
        self.assertIn("127.0.0.1", tmpl)
        self.assertIn("8080", tmpl)
        self.assertIn("reverse_proxy", tmpl)

    def test_config_path(self):
        self.assertIn("Caddyfile", caddy_config_path())


class TestAIRouting(unittest.TestCase):
    def test_is_local_profile_local_url(self):
        class FakeProfile:
            base_url = "http://localhost:11434"
        self.assertTrue(is_local_profile(FakeProfile()))

    def test_is_local_profile_cloud_url(self):
        class FakeProfile:
            base_url = "https://api.openai.com/v1"
        self.assertFalse(is_local_profile(FakeProfile()))

    def test_pick_best_prefers_local(self):
        class FakeProfile:
            def __init__(self, url):
                self.base_url = url
                self.name = url
        local = FakeProfile("http://localhost:11434")
        cloud = FakeProfile("https://api.openai.com")
        best = pick_best_profile([cloud, local], prefer_local=True)
        self.assertIs(best, local)

    def test_pick_best_no_local_falls_back(self):
        class FakeProfile:
            def __init__(self, url):
                self.base_url = url
        cloud1 = FakeProfile("https://api.openai.com")
        cloud2 = FakeProfile("https://api.anthropic.com")
        best = pick_best_profile([cloud1, cloud2], prefer_local=True)
        self.assertIs(best, cloud1)

    def test_pick_best_empty(self):
        self.assertIsNone(pick_best_profile([]))

    def test_should_try_local(self):
        class FakeProfile:
            base_url = "http://localhost:11434"
        p = FakeProfile()
        self.assertTrue(should_try_local(p, "10.0.0.1"))
        self.assertFalse(should_try_local(p, "8.8.8.8"))


class TestWebMode(unittest.TestCase):
    def test_has_limitations(self):
        self.assertGreater(len(WEB_MODE_LIMITATIONS), 3)

    def test_has_security_notes(self):
        self.assertGreater(len(WEB_MODE_SECURITY_NOTES), 2)

    def test_summary_nonempty(self):
        self.assertTrue(web_mode_summary())


if __name__ == "__main__":
    unittest.main()
