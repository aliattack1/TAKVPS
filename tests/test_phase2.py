import json
import tempfile
import unittest
from pathlib import Path

from core.fmt import human_duration, human_size, time_ago
from services.sysinfo import parse_sysinfo
from storage import vault as vault_mod
from storage.connections import (ConnectionConfigError, ConnectionProfile,
                                 ConnectionStore, Secrets)
from storage.vault import Vault, VaultError, VaultLocked

vault_mod.SCRYPT_N = 2 ** 10   # fast KDF for tests

SAMPLE = """##HOST
web-1
##OS
NAME="Ubuntu"
PRETTY_NAME="Ubuntu 24.04.1 LTS"
##KERNEL
Linux 6.8.0-45-generic
##ARCH
x86_64
##UPTIME
93784.12 345000.55
##LOAD
0.15 0.20 0.25 1/300 12345
##MEM
MemTotal:        2005000 kB
MemAvailable:    1200000 kB
##UID
1000
##HASSUDO
yes
##SUDOOK
no
##PKG
apt-get
##SYSTEMD
yes
"""


class VaultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_device_roundtrip_and_persistence(self):
        v = Vault(self.d)
        tok = v.encrypt("hunter2")
        self.assertNotIn("hunter2", tok)
        self.assertEqual(Vault(self.d).decrypt(tok), "hunter2")

    def test_passphrase_mode(self):
        v = Vault(self.d)
        tok = v.encrypt("secret")
        v.enable_passphrase("correct horse")
        self.assertFalse((self.d / "vault.key").exists())
        v2 = Vault(self.d)
        self.assertTrue(v2.locked)
        with self.assertRaises(VaultLocked):
            v2.decrypt(tok)
        self.assertFalse(v2.unlock("wrong"))
        self.assertTrue(v2.unlock("correct horse"))
        self.assertEqual(v2.decrypt(tok), "secret")
        v2.disable_passphrase()
        self.assertEqual(Vault(self.d).decrypt(tok), "secret")

    def test_missing_keyfile_is_an_error_not_silent_reset(self):
        Vault(self.d)
        (self.d / "vault.key").unlink()
        with self.assertRaises(VaultError):
            Vault(self.d)


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.vault = Vault(self.d)
        self.store = ConnectionStore(self.d / "c.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_plaintext_on_disk(self):
        p = ConnectionProfile(name="a", host="h", username="u")
        self.store.save(p, Secrets(password="hunter2", passphrase="pp"), self.vault)
        raw = (self.d / "c.json").read_text()
        self.assertNotIn("hunter2", raw)
        creds = ConnectionStore(self.d / "c.json").credentials(p, self.vault)
        self.assertEqual(creds.password, "hunter2")

    def test_repr_hides_secrets(self):
        p = ConnectionProfile(name="a", host="h", username="u")
        self.store.save(p, Secrets(password="hunter2"), self.vault)
        self.assertNotIn("hunter2", repr(self.store.credentials(p, self.vault)))

    def test_override_and_keep(self):
        p = ConnectionProfile(name="a", host="h", username="u")
        self.store.save(p, Secrets(password="old"), self.vault)
        self.assertEqual(self.store.credentials(p, self.vault, Secrets()).password, "old")
        self.assertEqual(self.store.credentials(p, self.vault, Secrets(password="new")).password, "new")

    def test_validation(self):
        for bad in (ConnectionProfile(name="", host="h", username="u"),
                    ConnectionProfile(name="a", host="h", username="u", port=0),
                    ConnectionProfile(name="a", host="h", username="u", auth="x")):
            with self.assertRaises(ConnectionConfigError):
                self.store.save(bad)

    def test_jump_chain_and_loop(self):
        a = ConnectionProfile(name="bastion", host="b", username="u")
        self.store.save(a, Secrets(password="pa"), self.vault)
        b = ConnectionProfile(name="private", host="p", username="u", jump_id=a.id)
        self.store.save(b, Secrets(password="pb"), self.vault)
        creds = self.store.credentials(b, self.vault)
        self.assertEqual(creds.jump.host, "b")
        self.assertEqual(creds.jump.password, "pa")
        a.jump_id = b.id                      # would create a loop
        with self.assertRaises(ConnectionConfigError):
            self.store.save(a)
        with self.assertRaises(ConnectionConfigError):
            self.store.save(ConnectionProfile(name="self", host="h", username="u", id="x", jump_id="x"))

    def test_delete_clears_jump_refs_and_duplicate(self):
        a = ConnectionProfile(name="j", host="j", username="u")
        self.store.save(a)
        b = ConnectionProfile(name="t", host="t", username="u", jump_id=a.id)
        self.store.save(b)
        dup = self.store.duplicate(b.id)
        self.assertNotEqual(dup.id, b.id)
        self.store.delete(a.id)
        self.assertEqual(self.store.get(b.id).jump_id, "")

    def test_export_import_roundtrip(self):
        a = ConnectionProfile(name="bastion", host="b", username="u")
        self.store.save(a, Secrets(password="pa"), self.vault)
        b = ConnectionProfile(name="private", host="p", username="u", jump_id=a.id,
                              auth="key")
        self.store.save(b, Secrets(private_key="KEYDATA", passphrase="kp"), self.vault)
        blob = self.store.export_encrypted(self.vault, "export-pass")
        self.assertNotIn("KEYDATA", blob)
        self.assertNotIn("pa\"", blob)

        d2 = tempfile.TemporaryDirectory()
        try:
            v2 = Vault(Path(d2.name))
            s2 = ConnectionStore(Path(d2.name) / "c.json")
            with self.assertRaises(VaultError):
                s2.import_encrypted(blob, "wrong", v2)
            created = s2.import_encrypted(blob, "export-pass", v2)
            self.assertEqual(len(created), 2)
            priv = next(p for p in created if p.name == "private")
            creds = s2.credentials(priv, v2)
            self.assertEqual(creds.private_key, "KEYDATA")
            self.assertEqual(creds.jump.password, "pa")
            self.assertNotEqual(priv.id, b.id)       # ids remapped
        finally:
            d2.cleanup()

    def test_import_garbage(self):
        with self.assertRaises(ConnectionConfigError):
            self.store.import_encrypted("not json", "x", self.vault)


class SysinfoTests(unittest.TestCase):
    def test_parse(self):
        i = parse_sysinfo(SAMPLE)
        self.assertEqual(i.hostname, "web-1")
        self.assertEqual(i.distro, "Ubuntu 24.04.1 LTS")
        self.assertEqual(i.kernel, "Linux 6.8.0-45-generic")
        self.assertAlmostEqual(i.uptime_s, 93784.12)
        self.assertEqual(i.load, (0.15, 0.20, 0.25))
        self.assertEqual((i.mem_total_kb, i.mem_avail_kb), (2005000, 1200000))
        self.assertEqual(i.sudo, "password")
        self.assertFalse(i.is_root)
        self.assertEqual(i.package_manager, "apt-get")
        self.assertTrue(i.has_systemd)

    def test_root_and_garbage(self):
        self.assertEqual(parse_sysinfo("##UID\n0\n").sudo, "root")
        self.assertEqual(parse_sysinfo("").hostname, "")


class FmtTests(unittest.TestCase):
    def test_all(self):
        self.assertEqual(human_size(512), "512 B")
        self.assertEqual(human_size(1536), "1.5 KB")
        self.assertEqual(human_size(5 * 1024 ** 3), "5.0 GB")
        self.assertEqual(human_duration(93784), "1d 2h")
        self.assertEqual(human_duration(30), "<1m")
        self.assertEqual(time_ago(0), "never")
        self.assertEqual(time_ago(1000, now=1000 + 7200), "2 h ago")


if __name__ == "__main__":
    unittest.main()
