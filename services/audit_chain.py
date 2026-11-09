"""Tamper-evident activity log via a chained hash.

Each activity record gets a `prev_hash` (the hash of the previous record, or
`"genesis"` for the first one) and a `hash` field (SHA-256 of
`prev_hash + ":" + canonical_json(record_without_hash)`). Tampering with any
record breaks the chain: the hash won't match, or the `prev_hash` link will
be wrong.

The chain is **tamper-evident, not tamper-proof** — an attacker with write
access to `activity.jsonl` can still rewrite the whole file (including
recomputing every hash). What they CAN'T do is edit a single record in place
without also re-hashing every subsequent record. This catches the common case
of "quietly edit one line to cover your tracks" and makes it visible.

Pure: no Flet, no asyncssh. Uses only stdlib (`hashlib`, `json`). Fully testable.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Optional

GENESIS = "genesis"     # the prev_hash of the first record in the chain


class VerifyStatus(str, Enum):
    """The verification result for a single record."""
    VERIFIED = "verified"           # hash matches and chain link is intact
    UNVERIFIED = "unverified"       # no hash field (old record, pre-Phase-14-Part-7)
    TAMPERED = "tampered"           # hash doesn't match — someone edited the record
    BROKEN_CHAIN = "broken_chain"   # prev_hash doesn't match the previous record's hash


@dataclass
class VerifyResult:
    """The verification result for one record."""
    line_index: int                 # 0-based line number in the file
    status: VerifyStatus
    expected_hash: str = ""         # what the hash should be (for TAMPERED)
    actual_hash: str = ""           # what the hash actually is
    detail: str = ""                # human-readable explanation


def canonical_json(record: dict) -> str:
    """Deterministic JSON serialization: sorted keys, no extra whitespace.

    This is what we hash, so the hash is reproducible regardless of dict
    insertion order or pretty-printing.
    """
    # Strip the hash field before serializing — the hash is over the record
    # WITHOUT the hash, so you can't "hash the hash".
    r = {k: v for k, v in record.items() if k != "hash"}
    return json.dumps(r, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_hash(record: dict, prev_hash: str) -> str:
    """SHA-256 of `prev_hash + ":" + canonical_json(record)`.

    The `prev_hash` is included so changing any earlier record cascades
    through every subsequent hash — you can't edit one record in isolation.
    """
    payload = f"{prev_hash}:{canonical_json(record)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def add_chain_fields(record: dict, prev_hash: str) -> dict:
    """Return a copy of `record` with `prev_hash` and `hash` added.

    Does NOT mutate the input. The caller should use the returned dict when
    writing to the file.
    """
    out = dict(record)
    out["prev_hash"] = prev_hash
    out["hash"] = compute_hash(out, prev_hash)
    return out


def verify_records(records: list[dict]) -> list[VerifyResult]:
    """Verify the hash chain across a list of parsed records.

    Returns one `VerifyResult` per record, in order. The caller can then
    decide how to display them (e.g. flag TAMPERED rows in red).

    A record is VERIFIED when:
      1. It has a `hash` field.
      2. Recomputing the hash from the record (minus `hash`) matches.
      3. Its `prev_hash` matches the previous record's `hash` (or GENESIS
         for the first record).

    Records without a `hash` field are UNVERIFIED (old format, pre-Part-7).
    """
    results: list[VerifyResult] = []
    expected_prev = GENESIS
    for i, rec in enumerate(records):
        actual_hash = rec.get("hash", "")
        if not actual_hash:
            # Old record without a hash — not tampered, just unverified.
            results.append(VerifyResult(
                line_index=i, status=VerifyStatus.UNVERIFIED,
                detail="No hash field (record predates tamper-evidence)."))
            # The chain is broken from here on — we can't verify subsequent
            # records because we don't know what this record's hash should be.
            # We continue with prev_hash = "" so the next record will show
            # BROKEN_CHAIN if it has a hash.
            expected_prev = ""
            continue
        # Recompute the expected hash.
        actual_prev = rec.get("prev_hash", "")
        expected_hash = compute_hash(rec, actual_prev or expected_prev)
        if expected_hash != actual_hash:
            results.append(VerifyResult(
                line_index=i, status=VerifyStatus.TAMPERED,
                expected_hash=expected_hash, actual_hash=actual_hash,
                detail="Hash mismatch — the record was edited after it was logged."))
            # A tampered record breaks the chain for subsequent records.
            expected_prev = actual_hash     # use the (wrong) actual hash to continue
            continue
        # Hash matches. Now check the chain link.
        if actual_prev != expected_prev:
            results.append(VerifyResult(
                line_index=i, status=VerifyStatus.BROKEN_CHAIN,
                expected_hash=expected_hash, actual_hash=actual_hash,
                detail=f"Chain broken: prev_hash is {actual_prev!r} but expected {expected_prev!r}. "
                       "A record may have been inserted or deleted."))
            expected_prev = actual_hash
            continue
        # All good.
        results.append(VerifyResult(
            line_index=i, status=VerifyStatus.VERIFIED,
            actual_hash=actual_hash,
            detail="Hash and chain link verified."))
        expected_prev = actual_hash
    return results


def verify_summary(results: list[VerifyResult]) -> dict:
    """Count records by status. Returns a dict like
    `{"verified": 95, "unverified": 5, "tampered": 1, "broken_chain": 0}`."""
    counts = {s.value: 0 for s in VerifyStatus}
    for r in results:
        counts[r.status.value] += 1
    return counts


def is_log_intact(results: list[VerifyResult]) -> bool:
    """True if every record is VERIFIED or UNVERIFIED (old format).
    False if any record is TAMPERED or BROKEN_CHAIN."""
    return all(r.status in (VerifyStatus.VERIFIED, VerifyStatus.UNVERIFIED) for r in results)


def summary_text(results: list[VerifyResult]) -> str:
    """One-line human-readable summary for the UI.
    e.g. '✓ 95 verified, 5 old (unverified)' or '⚠ 1 tampered, 2 broken chain'."""
    counts = verify_summary(results)
    parts = []
    if counts["verified"]:
        parts.append(f"✓ {counts['verified']} verified")
    if counts["unverified"]:
        parts.append(f"{counts['unverified']} old (unverified)")
    if counts["tampered"]:
        parts.append(f"⚠ {counts['tampered']} tampered")
    if counts["broken_chain"]:
        parts.append(f"⚠ {counts['broken_chain']} broken chain")
    return " · ".join(parts) if parts else "No records."
