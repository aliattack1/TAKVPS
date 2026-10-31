"""Phase 14 Part 4 tests: SSH key generation + install flow.

Tests the pure `services/sshkeygen.py` helper (key generation, public-key
normalisation, install-command builder, already-installed detector). The UI
dialog itself isn't tested (per the project's "light testing" preference).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import sshkeygen as kg
from services import users as U


# ======================================================================
# Phase 14 Part 4 — key generation
# ======================================================================
class TestGenerateKeypairEd25519(unittest.TestCase):
    def setUp(self):
        try:
            import asyncssh  # noqa: F401
        except ImportError:
            self.skipTest("asyncssh not installed")

    def test_generates_valid_ed25519_key(self):
        k = kg.generate_keypair(comment="test@example")
        self.assertEqual(k.algorithm, "ssh-ed25519")
        self.assertTrue(k.private_key.startswith("-----BEGIN OPENSSH PRIVATE KEY-----"))
        self.assertTrue(k.private_key.rstrip().endswith("-----END OPENSSH PRIVATE KEY-----"))
        self.assertTrue(k.public_key.startswith("ssh-ed25519 "))
        self.assertTrue(k.fingerprint.startswith("SHA256:"))
        self.assertEqual(k.comment, "test@example")

    def test_each_generation_is_unique(self):
        k1 = kg.generate_keypair()
        k2 = kg.generate_keypair()
        self.assertNotEqual(k1.private_key, k2.private_key)
        self.assertNotEqual(k1.public_key, k2.public_key)
        self.assertNotEqual(k1.fingerprint, k2.fingerprint)

    def test_public_key_parseable_by_users_module(self):
        """The public key we generate must parse cleanly through the existing
        authorized_keys validator (services.users.parse_line)."""
        k = kg.generate_keypair(comment="phase14-test")
        parsed = U.parse_line(k.public_key)
        self.assertTrue(parsed.is_key)
        self.assertEqual(parsed.type, "ssh-ed25519")
        self.assertEqual(parsed.comment, "phase14-test")
        self.assertEqual(parsed.fingerprint, k.fingerprint)

    def test_empty_comment_allowed(self):
        k = kg.generate_keypair(comment="")
        # The public key still has the algorithm prefix; comment is empty.
        self.assertTrue(k.public_key.startswith("ssh-ed25519 "))
        self.assertEqual(k.comment, "")


class TestGenerateKeypairRSA(unittest.TestCase):
    def setUp(self):
        try:
            import asyncssh  # noqa: F401
        except ImportError:
            self.skipTest("asyncssh not installed")

    def test_generates_valid_rsa_key(self):
        k = kg.generate_keypair(algorithm="ssh-rsa", comment="rsa-test", rsa_bits=2048)
        self.assertEqual(k.algorithm, "ssh-rsa")
        self.assertTrue(k.public_key.startswith("ssh-rsa "))
        self.assertTrue(k.fingerprint.startswith("SHA256:"))

    def test_rsa_below_minimum_rejected(self):
        with self.assertRaises(ValueError):
            kg.generate_keypair(algorithm="ssh-rsa", rsa_bits=1024)

    def test_rsa_zero_bits_rejected(self):
        with self.assertRaises(ValueError):
            kg.generate_keypair(algorithm="ssh-rsa", rsa_bits=0)


class TestGenerateKeypairInvalidAlg(unittest.TestCase):
    def test_unsupported_algorithm_raises(self):
        with self.assertRaises(ValueError):
            kg.generate_keypair(algorithm="ecdsa")
        with self.assertRaises(ValueError):
            kg.generate_keypair(algorithm="")
        with self.assertRaises(ValueError):
            kg.generate_keypair(algorithm="dsa")


# ======================================================================
# Phase 14 Part 4 — default comment builder
# ======================================================================
class TestDefaultComment(unittest.TestCase):
    def test_with_user_and_host(self):
        c = kg.default_comment("alice", "my-laptop")
        self.assertEqual(c, "alice@my-laptop")

    def test_sanitises_special_chars(self):
        c = kg.default_comment("al ice!", "my lap top")
        # Spaces and special chars are stripped.
        self.assertNotIn(" ", c)
        self.assertIn("@", c)

    def test_falls_back_to_takvps_when_empty(self):
        c = kg.default_comment("", "")
        self.assertTrue(c.startswith("takvps@"))

    def test_preserves_underscores_and_dashes(self):
        c = kg.default_comment("alice_admin", "prod-server-01")
        self.assertEqual(c, "alice_admin@prod-server-01")


# ======================================================================
# Phase 14 Part 4 — public key normalisation
# ======================================================================
class TestPublicKeyForAuthorizedKeys(unittest.TestCase):
    def test_strips_trailing_whitespace(self):
        pub = "ssh-ed25519 AAAA... comment\n\n"
        self.assertEqual(kg.public_key_for_authorized_keys(pub),
                         "ssh-ed25519 AAAA... comment")

    def test_takes_first_line_only(self):
        pub = "ssh-ed25519 AAAA... comment\nssh-rsa BBBB... other"
        self.assertEqual(kg.public_key_for_authorized_keys(pub),
                         "ssh-ed25519 AAAA... comment")

    def test_empty_string(self):
        self.assertEqual(kg.public_key_for_authorized_keys(""), "")


# ======================================================================
# Phase 14 Part 4 — install command builder
# ======================================================================
class TestInstallCommand(unittest.TestCase):
    def test_builds_mkdir_and_append(self):
        mkdir_cmd, append_cmd = kg.install_command("/home/alice",
                                                     "ssh-ed25519 AAAA... comment")
        # mkdir creates ~/.ssh with 700 perms.
        self.assertIn("mkdir -p", mkdir_cmd)
        self.assertIn("/home/alice/.ssh", mkdir_cmd)
        self.assertIn("chmod 700", mkdir_cmd)
        # append uses a heredoc so the key text is safe.
        self.assertIn("cat >>", append_cmd)
        self.assertIn("authorized_keys", append_cmd)
        self.assertIn("TAKVPS_PUBKEY_EOF", append_cmd)
        self.assertIn("ssh-ed25519 AAAA... comment", append_cmd)
        # chmod 600 on the auth file.
        self.assertIn("chmod 600", append_cmd)

    def test_handles_trailing_slash_on_home(self):
        mkdir_cmd, _ = kg.install_command("/home/alice/", "ssh-ed25519 AAAA")
        self.assertIn("/home/alice/.ssh", mkdir_cmd)
        # No double slash.
        self.assertNotIn("//", mkdir_cmd)

    def test_root_home(self):
        mkdir_cmd, _ = kg.install_command("/root", "ssh-ed25519 AAAA")
        self.assertIn("/root/.ssh", mkdir_cmd)

    def test_heredoc_preserves_key_with_spaces(self):
        """The key line can contain spaces (the comment) and the heredoc
        must not break on them."""
        pub = "ssh-ed25519 AAAA... alice@my laptop"
        _, append_cmd = kg.install_command("/root", pub)
        # The full key line appears verbatim in the heredoc body.
        self.assertIn(pub, append_cmd)


# ======================================================================
# Phase 14 Part 4 — already-installed detector
# ======================================================================
class TestIsAlreadyInstalled(unittest.TestCase):
    def setUp(self):
        try:
            import asyncssh  # noqa: F401
        except ImportError:
            self.skipTest("asyncssh not installed")
        self.k = kg.generate_keypair(comment="test")

    def test_detects_same_key(self):
        existing = self.k.public_key + "\n"
        self.assertTrue(kg.is_already_installed(existing, self.k.public_key))

    def test_different_key_not_detected(self):
        other = kg.generate_keypair(comment="other")
        existing = self.k.public_key + "\n"
        self.assertFalse(kg.is_already_installed(existing, other.public_key))

    def test_same_blob_different_comment_still_detected(self):
        """We compare by blob, not by the full line — so a different comment
        on the same key is still a match."""
        # Strip the comment from the installed line.
        no_comment = self.k.public_key.split()[0] + " " + self.k.public_key.split()[1]
        existing = no_comment + "\n"
        # The new key has the original comment.
        self.assertTrue(kg.is_already_installed(existing, self.k.public_key))

    def test_empty_existing(self):
        self.assertFalse(kg.is_already_installed("", self.k.public_key))

    def test_empty_new_key(self):
        self.assertFalse(kg.is_already_installed(self.k.public_key, ""))

    def test_garbage_existing(self):
        self.assertFalse(kg.is_already_installed("not a key file\n# just a comment",
                                                   self.k.public_key))


# ======================================================================
# Phase 14 Part 4 — end-to-end: generate → validate → install command
# ======================================================================
class TestEndToEndKeygenFlow(unittest.TestCase):
    def setUp(self):
        try:
            import asyncssh  # noqa: F401
        except ImportError:
            self.skipTest("asyncssh not installed")

    def test_generate_validate_install_roundtrip(self):
        """The full flow: generate a key, validate it through the existing
        authorized_keys validator, build the install command, and verify
        the command would correctly append the key."""
        # 1. Generate
        k = kg.generate_keypair(comment="e2e-test")
        # 2. Normalise the public key
        pub_line = kg.public_key_for_authorized_keys(k.public_key)
        self.assertEqual(pub_line, k.public_key)     # already clean
        # 3. Validate through the existing users module
        parsed, err = U.validate_new_key(pub_line, [])
        self.assertEqual(err, "", f"validation failed: {err}")
        self.assertTrue(parsed.is_key)
        self.assertEqual(parsed.fingerprint, k.fingerprint)
        # 4. Build install command
        mkdir_cmd, append_cmd = kg.install_command("/home/alice", pub_line)
        self.assertIn(pub_line, append_cmd)
        # 5. The installed key would be detected as already-present
        existing_after = pub_line + "\n"
        self.assertTrue(kg.is_already_installed(existing_after, pub_line))


# ======================================================================
# Phase 14 Part 4 — constants
# ======================================================================
class TestConstants(unittest.TestCase):
    def test_default_algorithm_is_ed25519(self):
        self.assertEqual(kg.DEFAULT_ALG, "ssh-ed25519")

    def test_supported_algs_includes_ed25519_and_rsa(self):
        self.assertIn("ssh-ed25519", kg.SUPPORTED_ALGS)
        self.assertIn("ssh-rsa", kg.SUPPORTED_ALGS)

    def test_default_rsa_bits_is_4096(self):
        self.assertEqual(kg.DEFAULT_RSA_BITS, 4096)


if __name__ == "__main__":
    unittest.main()
