"""SSH key generation in-app.

Generates ed25519 (default) and RSA key pairs in OpenSSH format, using the
`asyncssh` package that's already a project dependency. The private key goes
into the connection editor's "Private key" field (and from there into the
encrypted vault); the public key is shown to the user so they can install it
on the server via the existing `authorized_keys` flow.

Pure: imports `asyncssh` lazily so the module is testable without it (the
generation function just needs the package at call time, not at import time).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Key algorithms we support. asyncssh accepts these as `alg_name` to
# `generate_private_key`. We default to ed25519 (modern, tiny, fast).
SUPPORTED_ALGS = ("ssh-ed25519", "ssh-rsa")
DEFAULT_ALG = "ssh-ed25519"
DEFAULT_RSA_BITS = 4096


@dataclass
class GeneratedKey:
    """The result of a key generation. `private_key` is the OpenSSH-format
    private key text (PEM-like, ready to paste into the connection editor);
    `public_key` is the one-line authorized_keys entry."""
    algorithm: str               # "ssh-ed25519" or "ssh-rsa"
    private_key: str             # OpenSSH private key (-----BEGIN OPENSSH PRIVATE KEY-----)
    public_key: str              # ssh-ed25519 AAAA... comment  (authorized_keys line)
    fingerprint: str             # SHA256:...
    comment: str = ""


def generate_keypair(algorithm: str = DEFAULT_ALG, comment: str = "",
                     rsa_bits: int = DEFAULT_RSA_BITS) -> GeneratedKey:
    """Generate a new SSH key pair in OpenSSH format.

    `algorithm`: "ssh-ed25519" (default, recommended) or "ssh-rsa".
    `comment`: optional comment appended to the public key (e.g. "alice@laptop").
    `rsa_bits`: RSA key size in bits (ignored for ed25519). Default 4096.

    Raises `ValueError` for unsupported algorithms or invalid RSA sizes.
    """
    if algorithm not in SUPPORTED_ALGS:
        raise ValueError(f"Unsupported key algorithm: {algorithm!r}. "
                         f"Use one of: {', '.join(SUPPORTED_ALGS)}")
    if algorithm == "ssh-rsa" and rsa_bits < 2048:
        raise ValueError(f"RSA key size must be at least 2048 bits (got {rsa_bits}).")

    # Lazy import so the module is importable in environments without asyncssh
    # (tests that don't exercise the actual keygen can still import the helpers).
    try:
        import asyncssh
    except ImportError as e:
        raise ImportError("The 'asyncssh' package is required for key generation.") from e

    kwargs: dict = {}
    if algorithm == "ssh-rsa":
        kwargs["key_size"] = rsa_bits

    key = asyncssh.generate_private_key(algorithm, comment=comment or None, **kwargs)
    priv_bytes = key.export_private_key()
    pub_bytes = key.export_public_key()

    return GeneratedKey(
        algorithm=algorithm,
        private_key=priv_bytes.decode("utf-8"),
        public_key=pub_bytes.decode("utf-8").strip(),
        fingerprint=key.get_fingerprint(),
        comment=comment or "",
    )


def default_comment(username: str = "", host: str = "") -> str:
    """Build a sensible default comment like `alice@laptop` or `takvps@phone`.

    We don't know the device's hostname reliably (Flet doesn't expose it
    portably), so we use `takvps@<platform>` as a fallback. The user can
    always edit it in the dialog.
    """
    import platform
    user_part = username or "takvps"
    host_part = host or platform.node().split(".")[0] or "phone"
    # Sanitise: no spaces, no special chars that would break authorized_keys parsing.
    safe_user = "".join(c for c in user_part if c.isalnum() or c in "-_") or "takvps"
    safe_host = "".join(c for c in host_part if c.isalnum() or c in "-_.") or "phone"
    return f"{safe_user}@{safe_host}"


def public_key_for_authorized_keys(pub: str) -> str:
    """Normalise a public key string for appending to authorized_keys.

    Strips trailing whitespace/newlines and ensures exactly one line. The
    existing `services/users.validate_new_key` does the real validation; this
    is just a convenience trimmer.
    """
    return pub.strip().splitlines()[0] if pub.strip() else ""


def install_command(user_home: str, pub_key_line: str) -> tuple[str, str]:
    """Build the shell commands to install a public key on a server.

    Returns (mkdir_cmd, append_cmd). The caller runs them in sequence over SSH.
    We use a heredoc to append the key (so the key text can't be misinterpreted
    by the shell) and `>>` so existing keys are preserved.

    `user_home` is the target user's home directory (e.g. "/root" or "/home/alice").
    `pub_key_line` is the one-line authorized_keys entry.
    """
    import shlex
    ssh_dir = f"{user_home.rstrip('/')}/.ssh"
    auth_file = f"{ssh_dir}/authorized_keys"
    mkdir_cmd = f"mkdir -p {shlex.quote(ssh_dir)} && chmod 700 {shlex.quote(ssh_dir)}"
    # Append the key with a heredoc so quotes/spaces in the comment don't break it.
    # Touch the file first so `>>` works even on a fresh server.
    append_cmd = (
        f"touch {shlex.quote(auth_file)} && chmod 600 {shlex.quote(auth_file)} && "
        f"cat >> {shlex.quote(auth_file)} <<'TAKVPS_PUBKEY_EOF'\n"
        f"{pub_key_line}\n"
        f"TAKVPS_PUBKEY_EOF"
    )
    return mkdir_cmd, append_cmd


def is_already_installed(existing_text: str, pub_key_line: str) -> bool:
    """Check if `pub_key_line` is already in the existing authorized_keys text.

    We compare by the base64 blob (not the full line) so a different comment
    doesn't cause a false negative.
    """
    from services.users import parse_authorized_keys
    new = public_key_for_authorized_keys(pub_key_line)
    # Parse the new key to extract its blob.
    new_line = parse_authorized_keys(new)
    if not new_line or not new_line[0].is_key:
        return False
    new_blob = new_line[0].blob
    # Parse existing keys and compare blobs.
    for existing in parse_authorized_keys(existing_text):
        if existing.is_key and existing.blob == new_blob:
            return True
    return False
