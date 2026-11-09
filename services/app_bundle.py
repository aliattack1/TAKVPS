"""Encrypted export bundles.

Bundles the entire app state (connections + snippets + deploys + AI profiles +
settings) into one encrypted blob, so moving to a new phone is one paste
instead of five. Uses the same passphrase-based encryption as the existing
connections export (`storage.vault.encrypt_with_passphrase`).

The bundle is a JSON object encrypted with a user-chosen passphrase, then
base64-encoded for easy copy-paste. On import, the user enters the same
passphrase; the bundle is decrypted and the stores are repopulated.

Pure: no Flet, no asyncssh. The stores are passed in as objects with the
expected interface (list/save methods). Fully testable with fakes.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from storage.vault import Vault, decrypt_with_passphrase, encrypt_with_passphrase

BUNDLE_VERSION = 1


@dataclass
class BundleMeta:
    """Metadata about a bundle — shown to the user before import."""
    version: int
    created: float
    counts: dict = field(default_factory=dict)    # {"connections": 5, "snippets": 12, ...}
    app: str = "takvps"

    def summary(self) -> str:
        """One-line summary for the import dialog."""
        parts = [f"{v} {k}" for k, v in self.counts.items() if v]
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(self.created))
        return f"Bundle from {when} · " + " · ".join(parts) if parts else f"Bundle from {when} (empty)"


def collect_bundle(*, connections_store, snippets_store, ai_profiles_store,
                    settings_store, vault: Vault) -> dict:
    """Collect all serializable app state into a dict.

    Secrets (passwords, private keys, API keys) are decrypted from the vault
    and included as plaintext in the bundle — they'll be re-encrypted with
    the user's passphrase. The bundle itself is then encrypted, so the
    plaintext never touches disk in that form.
    """
    bundle: dict[str, Any] = {
        "v": BUNDLE_VERSION,
        "app": "takvps",
        "created": time.time(),
    }
    # Connections: reuse the existing export logic (which decrypts secrets).
    # We call the store's own export and then decrypt the inner payload so
    # we can re-bundle it. But that's circular — instead, we inline the logic.
    profiles_out = []
    for p in connections_store.list():
        d = _profile_to_dict(p, vault)
        profiles_out.append(d)
    bundle["connections"] = profiles_out
    # Snippets + deploys
    snippets_out = []
    for s in snippets_store.list_snippets(""):
        snippets_out.append(_snippet_to_dict(s))
    for s in snippets_store.list_snippets("__all__"):
        if s.scope:    # per-server snippets
            snippets_out.append(_snippet_to_dict(s))
    bundle["snippets"] = snippets_out
    deploys_out = []
    try:
        for d in snippets_store.list_deploys(""):
            deploys_out.append(_deploy_to_dict(d))
    except Exception:  # noqa: BLE001
        pass
    bundle["deploys"] = deploys_out
    # AI profiles
    ai_out = []
    try:
        for p in ai_profiles_store.list():
            d = _ai_profile_to_dict(p, ai_profiles_store, vault)
            ai_out.append(d)
    except Exception:  # noqa: BLE001
        pass
    bundle["ai_profiles"] = ai_out
    # Settings (excluding known_hosts and ai_active_profile which are device-specific)
    try:
        s = settings_store.data
        settings_out = {
            "theme_mode": s.theme_mode, "preset": s.preset, "accent": s.accent,
            "font_scale": s.font_scale, "monitor_interval": s.monitor_interval,
            "nav_order": list(s.nav_order), "safe_mode": s.safe_mode,
            "connect_on_launch": s.connect_on_launch,
            "ai_default_mode": s.ai_default_mode, "ai_step_limit": s.ai_step_limit,
            "ai_native_tools": s.ai_native_tools, "ai_show_tool_calls": s.ai_show_tool_calls,
            "ai_context": dict(s.ai_context),
            "alerts_enabled": s.alerts_enabled, "alert_interval": s.alert_interval,
            "alert_services": list(s.alert_services),
            "editor_font_size": s.editor_font_size, "terminal_font_size": s.terminal_font_size,
            "editor_wrap": s.editor_wrap, "editor_backup": s.editor_backup,
            "monitor_line_charts": getattr(s, "monitor_line_charts", True),
            "files_sort": s.files_sort, "files_reverse": s.files_reverse,
            "files_hidden": s.files_hidden,
            "thr_cpu": list(s.thr_cpu), "thr_ram": list(s.thr_ram), "thr_disk": list(s.thr_disk),
            # known_hosts excluded — device-specific trust decisions.
        }
    except Exception:  # noqa: BLE001
        settings_out = {}
    bundle["settings"] = settings_out
    return bundle


def encrypt_bundle(bundle: dict, passphrase: str) -> str:
    """Encrypt the bundle with a user passphrase. Returns a JSON string
    (the encrypted blob) suitable for copy-paste."""
    payload = json.dumps(bundle, ensure_ascii=False).encode("utf-8")
    blob = encrypt_with_passphrase(payload, passphrase)
    return json.dumps(blob)


def decrypt_bundle(text: str, passphrase: str) -> dict:
    """Decrypt a bundle. Raises ValueError on bad passphrase / corrupt data."""
    try:
        blob = json.loads(text.strip())
    except ValueError as e:
        raise ValueError("That is not a valid TAKVPS bundle.") from e
    try:
        payload = decrypt_with_passphrase(blob, passphrase)
    except Exception as e:
        raise ValueError("Wrong passphrase or corrupt bundle.") from e
    try:
        bundle = json.loads(payload)
    except ValueError as e:
        raise ValueError("The bundle is corrupt.") from e
    if not isinstance(bundle, dict) or "v" not in bundle:
        raise ValueError("The bundle is missing its version marker.")
    return bundle


def bundle_meta(bundle: dict) -> BundleMeta:
    """Extract metadata from a decrypted bundle (for the pre-import preview)."""
    counts = {}
    for key in ("connections", "snippets", "deploys", "ai_profiles"):
        counts[key] = len(bundle.get(key, []))
    return BundleMeta(
        version=bundle.get("v", 0),
        created=bundle.get("created", 0),
        counts=counts,
        app=bundle.get("app", "takvps"),
    )


def apply_bundle(bundle: dict, *, connections_store, snippets_store, ai_profiles_store,
                  settings_store, vault: Vault) -> dict:
    """Import the bundle into the stores. Returns a summary of what was imported.

    Existing data is NOT cleared — the bundle is merged on top. This is
    intentional: if you import a bundle on a phone that already has some
    servers, the new ones are added without deleting the old ones. IDs are
    remapped so there are no collisions.
    """
    summary_counts: dict[str, int] = {}
    # Connections
    n_conns = 0
    for d in bundle.get("connections", []):
        try:
            _import_profile(connections_store, d, vault)
            n_conns += 1
        except Exception:  # noqa: BLE001
            pass
    summary_counts["connections"] = n_conns
    # Snippets
    n_snip = 0
    for d in bundle.get("snippets", []):
        try:
            _import_snippet(snippets_store, d)
            n_snip += 1
        except Exception:  # noqa: BLE001
            pass
    summary_counts["snippets"] = n_snip
    # Deploys
    n_dep = 0
    for d in bundle.get("deploys", []):
        try:
            _import_deploy(snippets_store, d)
            n_dep += 1
        except Exception:  # noqa: BLE001
            pass
    summary_counts["deploys"] = n_dep
    # AI profiles
    n_ai = 0
    for d in bundle.get("ai_profiles", []):
        try:
            _import_ai_profile(ai_profiles_store, d, vault)
            n_ai += 1
        except Exception:  # noqa: BLE001
            pass
    summary_counts["ai_profiles"] = n_ai
    # Settings (merge — only update fields present in the bundle)
    settings = bundle.get("settings", {})
    if settings:
        try:
            _apply_settings(settings_store, settings)
        except Exception:  # noqa: BLE001
            pass
    summary_counts["settings"] = 1 if settings else 0
    return summary_counts


# ---- per-store import helpers ---------------------------------------------

def _profile_to_dict(p, vault: Vault) -> dict:
    """Serialize a ConnectionProfile, decrypting secrets with the vault."""
    from dataclasses import asdict
    d = asdict(p)
    d["password"] = vault.decrypt(p.password_enc) if p.password_enc else ""
    d["private_key"] = vault.decrypt(p.key_enc) if p.key_enc else ""
    d["key_passphrase"] = vault.decrypt(p.passphrase_enc) if p.passphrase_enc else ""
    for k in ("password_enc", "key_enc", "passphrase_enc", "last_connected"):
        d.pop(k, None)
    return d


def _snippet_to_dict(s) -> dict:
    from dataclasses import asdict
    return asdict(s)


def _deploy_to_dict(d) -> dict:
    from dataclasses import asdict
    return asdict(d)


def _ai_profile_to_dict(p, store, vault: Vault) -> dict:
    from dataclasses import asdict
    d = asdict(p)
    # Decrypt the API key if present.
    if hasattr(p, "api_key_enc") and p.api_key_enc:
        try:
            d["api_key"] = vault.decrypt(p.api_key_enc)
        except Exception:  # noqa: BLE001
            d["api_key"] = ""
    d.pop("api_key_enc", None)
    return d


def _import_profile(store, d: dict, vault: Vault) -> None:
    """Import one connection profile, re-encrypting secrets with the vault."""
    from storage.connections import ConnectionProfile, Secrets, new_id
    p = ConnectionProfile(
        name=d["name"], host=d["host"], username=d["username"],
        port=int(d.get("port", 22)), auth=d.get("auth", "password"),
        tags=list(d.get("tags", [])), color=d.get("color", "#3B82F6"),
        jump_id="", id=new_id(),
        ai_context=d.get("ai_context", ""))
    sec = Secrets(
        password=d.get("password") or None,
        private_key=d.get("private_key") or None,
        passphrase=d.get("key_passphrase") or None,
    )
    store.save(p, sec, vault=vault)


def _import_snippet(store, d: dict) -> None:
    from services.snippets import Snippet
    s = Snippet(**{k: v for k, v in d.items() if k in Snippet.__dataclass_fields__})
    # Give it a new ID so it doesn't collide.
    from services.snippets import new_id
    s.id = new_id()
    store.save_snippet(s)


def _import_deploy(store, d: dict) -> None:
    from services.deploy import Deploy
    dep = Deploy(**{k: v for k, v in d.items() if k in Deploy.__dataclass_fields__})
    from services.snippets import new_id
    dep.id = new_id()
    store.save_deploy(dep)


def _import_ai_profile(store, d: dict, vault: Vault) -> None:
    from storage.ai_profiles import AIProfile
    api_key = d.pop("api_key", "") or None
    p = AIProfile(**{k: v for k, v in d.items() if k in AIProfile.__dataclass_fields__})
    from services.snippets import new_id
    p.id = new_id()
    store.save(p, api_key=api_key, vault=vault)


def _apply_settings(store, settings: dict) -> None:
    """Merge settings from the bundle into the live settings. Only updates
    fields that are present in the bundle; leaves device-specific fields
    (known_hosts, ai_active_profile, download_dir) untouched."""
    s = store.data
    field_map = {
        "theme_mode", "preset", "accent", "font_scale", "monitor_interval",
        "nav_order", "safe_mode", "connect_on_launch",
        "ai_default_mode", "ai_step_limit", "ai_native_tools", "ai_show_tool_calls",
        "ai_context", "alerts_enabled", "alert_interval", "alert_services",
        "editor_font_size", "terminal_font_size", "editor_wrap", "editor_backup",
        "monitor_line_charts", "files_sort", "files_reverse", "files_hidden",
        "thr_cpu", "thr_ram", "thr_disk",
    }
    for k, v in settings.items():
        if k in field_map and hasattr(s, k):
            setattr(s, k, v)
    store.save()
