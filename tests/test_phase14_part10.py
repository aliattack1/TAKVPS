"""Phase 14 Part 10 tests: encrypted export bundles."""
from __future__ import annotations
import json, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services.app_bundle import (BUNDLE_VERSION, BundleMeta, apply_bundle, bundle_meta,
                                    collect_bundle, decrypt_bundle, encrypt_bundle)
from storage.vault import Vault
from storage.connections import ConnectionStore, ConnectionProfile
from storage.snippets import SnippetStore
from storage.ai_profiles import AIProfileStore
from storage.settings import SettingsStore


class TestBundleRoundtrip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        self.vault = Vault(self.path)
        self.conns = ConnectionStore(self.path / "conns.json")
        self.snips = SnippetStore(self.path / "snips.json")
        self.ai = AIProfileStore(self.path / "ai.json")
        self.settings = SettingsStore(self.path / "settings.json")
        # Add test data
        p = ConnectionProfile(name="prod", host="10.0.0.1", username="root", ai_context="prod note")
        self.conns.save(p, None)

    def tearDown(self):
        self.tmp.cleanup()

    def test_collect_has_all_sections(self):
        b = collect_bundle(connections_store=self.conns, snippets_store=self.snips,
                           ai_profiles_store=self.ai, settings_store=self.settings, vault=self.vault)
        for key in ("v", "app", "created", "connections", "snippets", "deploys", "ai_profiles", "settings"):
            self.assertIn(key, b)
        self.assertEqual(b["v"], BUNDLE_VERSION)
        self.assertEqual(len(b["connections"]), 1)
        self.assertEqual(b["connections"][0]["name"], "prod")

    def test_encrypt_decrypt_roundtrip(self):
        b = collect_bundle(connections_store=self.conns, snippets_store=self.snips,
                           ai_profiles_store=self.ai, settings_store=self.settings, vault=self.vault)
        blob = encrypt_bundle(b, "testpass123")
        self.assertIsInstance(blob, str)
        b2 = decrypt_bundle(blob, "testpass123")
        self.assertEqual(b2["connections"][0]["name"], "prod")

    def test_wrong_passphrase_rejected(self):
        b = collect_bundle(connections_store=self.conns, snippets_store=self.snips,
                           ai_profiles_store=self.ai, settings_store=self.settings, vault=self.vault)
        blob = encrypt_bundle(b, "rightpass")
        with self.assertRaises(ValueError):
            decrypt_bundle(blob, "wrongpass")

    def test_corrupt_blob_rejected(self):
        with self.assertRaises(ValueError):
            decrypt_bundle("not json at all", "pass")
        with self.assertRaises(ValueError):
            decrypt_bundle("{}", "pass")

    def test_bundle_meta(self):
        b = collect_bundle(connections_store=self.conns, snippets_store=self.snips,
                           ai_profiles_store=self.ai, settings_store=self.settings, vault=self.vault)
        meta = bundle_meta(b)
        self.assertEqual(meta.version, BUNDLE_VERSION)
        self.assertEqual(meta.counts["connections"], 1)
        self.assertIn("1 connections", meta.summary())

    def test_apply_bundle_adds_without_deleting(self):
        b = collect_bundle(connections_store=self.conns, snippets_store=self.snips,
                           ai_profiles_store=self.ai, settings_store=self.settings, vault=self.vault)
        blob = encrypt_bundle(b, "pass")
        # Clear stores by re-creating them
        self.conns = ConnectionStore(self.path / "conns2.json")
        self.assertEqual(len(self.conns.list()), 0)
        b2 = decrypt_bundle(blob, "pass")
        counts = apply_bundle(b2, connections_store=self.conns, snippets_store=self.snips,
                              ai_profiles_store=self.ai, settings_store=self.settings, vault=self.vault)
        self.assertEqual(counts["connections"], 1)
        self.assertEqual(len(self.conns.list()), 1)

    def test_secrets_decrypted_in_bundle(self):
        """Secrets are decrypted from the vault and included as plaintext in
        the bundle (before the passphrase encryption)."""
        from storage.connections import Secrets
        p = ConnectionProfile(name="secret2", host="h2", username="u2", auth="password")
        self.conns.save(p, Secrets(password="mypassword"), vault=self.vault)
        b = collect_bundle(connections_store=self.conns, snippets_store=self.snips,
                           ai_profiles_store=self.ai, settings_store=self.settings, vault=self.vault)
        secret_profile = [c for c in b["connections"] if c["name"] == "secret2"][0]
        self.assertEqual(secret_profile.get("password", ""), "mypassword")

    def test_empty_bundle(self):
        """A bundle with no data still roundtrips correctly."""
        empty_conns = ConnectionStore(self.path / "empty.json")
        b = collect_bundle(connections_store=empty_conns, snippets_store=self.snips,
                           ai_profiles_store=self.ai, settings_store=self.settings, vault=self.vault)
        blob = encrypt_bundle(b, "pass")
        b2 = decrypt_bundle(blob, "pass")
        self.assertEqual(b2["connections"], [])

    def test_settings_included(self):
        self.settings.data.theme_mode = "dark"
        self.settings.save()
        b = collect_bundle(connections_store=self.conns, snippets_store=self.snips,
                           ai_profiles_store=self.ai, settings_store=self.settings, vault=self.vault)
        self.assertEqual(b["settings"]["theme_mode"], "dark")

    def test_known_hosts_excluded(self):
        """known_hosts is device-specific and should NOT be in the bundle."""
        self.settings.data.known_hosts = {"host:22": "sha256:abc"}
        self.settings.save()
        b = collect_bundle(connections_store=self.conns, snippets_store=self.snips,
                           ai_profiles_store=self.ai, settings_store=self.settings, vault=self.vault)
        self.assertNotIn("known_hosts", b["settings"])


if __name__ == "__main__":
    unittest.main()
