"""WireGuard, K8s, and Reverse-Proxy UI pages."""
from __future__ import annotations
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services.wireguard import parse_config, build_config, parse_status, WireGuardConfig, WireGuardPeer
from services.k8s import parse_pods, parse_deployments, parse_services, classify_kubectl_command
from services.reverse_proxy import parse_detect, template_for, ProxyInfo


class TestWireGuardUI(unittest.TestCase):
    def test_parse_full_config(self):
        text = "[Interface]\nPrivateKey = abc123\nAddress = 10.0.0.1/24\nListenPort = 51820\n\n[Peer]\nPublicKey = peer1\nAllowedIPs = 10.0.0.2/32\nEndpoint = 1.2.3.4:51820\nPersistentKeepalive = 25\n"
        cfg = parse_config(text)
        self.assertEqual(cfg.interface_private_key, "abc123")
        self.assertEqual(cfg.interface_address, "10.0.0.1/24")
        self.assertEqual(cfg.interface_listen_port, 51820)
        self.assertEqual(len(cfg.peers), 1)
        self.assertEqual(cfg.peers[0].public_key, "peer1")
        self.assertEqual(cfg.peers[0].allowed_ips, "10.0.0.2/32")
        self.assertEqual(cfg.peers[0].endpoint, "1.2.3.4:51820")
        self.assertEqual(cfg.peers[0].persistent_keepalive, 25)

    def test_status_running(self):
        s = parse_status("interface: wg0\n  listening port: 51820\n  peer: abc\n    endpoint: 1.2.3.4:51820\n    latest handshake: 12 seconds ago")
        self.assertTrue(s["running"])
        self.assertIn("peers", s)
        self.assertEqual(len(s["peers"]), 1)

    def test_status_not_running(self):
        s = parse_status("NOT_RUNNING")
        self.assertFalse(s["running"])
        self.assertEqual(s["peers"], [])

    def test_empty_config(self):
        cfg = parse_config("")
        self.assertEqual(cfg.interface_private_key, "")
        self.assertEqual(cfg.peers, [])

    def test_build_roundtrip_multiple_peers(self):
        cfg = WireGuardConfig(
            interface_private_key="key", interface_address="10.0.0.1/24",
            interface_listen_port=51820,
            peers=[WireGuardPeer(public_key="pk1", allowed_ips="10.0.0.2/32"),
                   WireGuardPeer(public_key="pk2", allowed_ips="10.0.0.3/32")])
        text = build_config(cfg)
        cfg2 = parse_config(text)
        self.assertEqual(len(cfg2.peers), 2)
        self.assertEqual(cfg2.peers[0].public_key, "pk1")
        self.assertEqual(cfg2.peers[1].public_key, "pk2")


class TestK8sUI(unittest.TestCase):
    def test_parse_pods_with_restart_count(self):
        import json
        data = {"items": [{"metadata": {"name": "web-abc", "namespace": "default"},
                           "status": {"phase": "Running", "containerStatuses": [{"ready": True, "restartCount": 3}]},
                           "spec": {"nodeName": "node-1"}}]}
        pods = parse_pods(json.dumps(data))
        self.assertEqual(len(pods), 1)
        self.assertEqual(pods[0].restarts, 3)

    def test_parse_pods_empty(self):
        self.assertEqual(parse_pods('{"items": []}'), [])
        self.assertEqual(parse_pods(""), [])
        self.assertEqual(parse_pods("not json"), [])

    def test_parse_deployments_not_ready(self):
        import json
        data = {"items": [{"metadata": {"name": "api"}, "spec": {"replicas": 3},
                           "status": {"readyReplicas": 1}}]}
        deps = parse_deployments(json.dumps(data))
        self.assertEqual(len(deps), 1)
        self.assertEqual(deps[0].ready, "1/3")

    def test_parse_services_with_external_ip(self):
        import json
        data = {"items": [{"metadata": {"name": "lb"},
                           "spec": {"type": "LoadBalancer", "clusterIP": "10.96.0.1",
                                    "externalIPs": ["1.2.3.4"],
                                    "ports": [{"port": 443, "protocol": "TCP"}]}}]}
        svcs = parse_services(json.dumps(data))
        self.assertEqual(len(svcs), 1)
        self.assertEqual(svcs[0].type, "LoadBalancer")
        self.assertIn("1.2.3.4", svcs[0].external_ip)
        self.assertIn("443/TCP", svcs[0].ports)

    def test_classify_kubectl_commands(self):
        self.assertEqual(classify_kubectl_command("kubectl get pods"), "safe")
        self.assertEqual(classify_kubectl_command("kubectl describe pod web"), "safe")
        self.assertEqual(classify_kubectl_command("kubectl logs web"), "safe")
        self.assertEqual(classify_kubectl_command("kubectl delete pod web"), "destructive")
        self.assertEqual(classify_kubectl_command("kubectl apply -f deploy.yaml"), "writes")
        self.assertEqual(classify_kubectl_command("kubectl exec -it web -- sh"), "writes")


class TestReverseProxyUI(unittest.TestCase):
    def test_parse_detect_caddy_running(self):
        text = "##CADDY\ncaddy v2.7.6\n##TRAEFIK\n\n##NGINX\n\n##STATUS\nactive"
        info = parse_detect(text)
        self.assertEqual(info.kind, "caddy")
        self.assertIn("v2.7.6", info.version)

    def test_parse_detect_traefik(self):
        text = "##CADDY\n\n##TRAEFIK\nVersion: v2.10\n##NGINX\n\n##STATUS\nactive"
        info = parse_detect(text)
        self.assertEqual(info.kind, "traefik")

    def test_parse_detect_none_installed(self):
        text = "##CADDY\n\n##TRAEFIK\n\n##NGINX\n\n##STATUS\n"
        info = parse_detect(text)
        self.assertEqual(info.kind, "")

    def test_template_static_has_file_server(self):
        tmpl = template_for("caddy", "static", domain="example.com", doc_root="/var/www")
        self.assertIn("file_server", tmpl)
        self.assertIn("example.com", tmpl)

    def test_template_proxy_has_reverse_proxy(self):
        tmpl = template_for("caddy", "proxy", domain="api.example.com",
                             backend_host="127.0.0.1", backend_port="8080")
        self.assertIn("reverse_proxy", tmpl)
        self.assertIn("8080", tmpl)

    def test_template_php_has_fastcgi(self):
        tmpl = template_for("caddy", "php", domain="app.example.com",
                             doc_root="/var/www", php_host="127.0.0.1", php_port="9000")
        self.assertIn("php_fastcgi", tmpl)
        self.assertIn("9000", tmpl)

    def test_template_traefik_labels(self):
        tmpl = template_for("traefik", "static", name="myapp", domain="example.com")
        self.assertIn("traefik.enable", tmpl)
        self.assertIn("example.com", tmpl)


if __name__ == "__main__":
    unittest.main()
