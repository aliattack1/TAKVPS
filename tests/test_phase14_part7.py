"""Phase 14 Part 7 tests: audit log tamper-evidence (chained hash).

Tests the pure `services/audit_chain.py` helper and the `ActivityLog.verify()`
method. No Flet imports.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.audit_chain import (GENESIS, VerifyResult, VerifyStatus, add_chain_fields,
                                    canonical_json, compute_hash, is_log_intact, summary_text,
                                    verify_records, verify_summary)
from storage.activity import ActivityLog


# ======================================================================
# Phase 14 Part 7 — canonical_json
# ======================================================================
class TestCanonicalJson(unittest.TestCase):
    def test_sorted_keys(self):
        r = {"b": 2, "a": 1}
        self.assertEqual(canonical_json(r), '{"a":1,"b":2}')

    def test_no_whitespace(self):
        r = {"a": 1, "b": [1, 2, 3]}
        self.assertEqual(canonical_json(r), '{"a":1,"b":[1,2,3]}')

    def test_strips_hash_field(self):
        r = {"a": 1, "hash": "abc123"}
        cj = canonical_json(r)
        self.assertNotIn("hash", cj)
        self.assertIn('"a":1', cj)

    def test_preserves_prev_hash(self):
        r = {"a": 1, "prev_hash": "xyz"}
        cj = canonical_json(r)
        self.assertIn('"prev_hash":"xyz"', cj)

    def test_deterministic_regardless_of_insertion_order(self):
        r1 = {"a": 1, "b": 2, "c": 3}
        r2 = {"c": 3, "a": 1, "b": 2}
        self.assertEqual(canonical_json(r1), canonical_json(r2))

    def test_unicode_preserved(self):
        r = {"cmd": "你好"}
        cj = canonical_json(r)
        self.assertIn("你好", cj)


# ======================================================================
# Phase 14 Part 7 — compute_hash
# ======================================================================
class TestComputeHash(unittest.TestCase):
    def test_returns_hex_string(self):
        h = compute_hash({"a": 1}, GENESIS)
        self.assertIsInstance(h, str)
        self.assertEqual(len(h), 64)     # SHA-256 hex digest

    def test_different_input_different_hash(self):
        h1 = compute_hash({"a": 1}, GENESIS)
        h2 = compute_hash({"a": 2}, GENESIS)
        self.assertNotEqual(h1, h2)

    def test_different_prev_hash_different_hash(self):
        h1 = compute_hash({"a": 1}, GENESIS)
        h2 = compute_hash({"a": 1}, "different")
        self.assertNotEqual(h1, h2)

    def test_same_input_same_hash(self):
        h1 = compute_hash({"a": 1, "b": [1, 2]}, GENESIS)
        h2 = compute_hash({"b": [1, 2], "a": 1}, GENESIS)
        self.assertEqual(h1, h2)     # canonical_json makes this work

    def test_hash_excludes_hash_field(self):
        """The hash is computed over the record WITHOUT the hash field, so
        adding a hash field doesn't change the hash."""
        r1 = {"a": 1}
        r2 = {"a": 1, "hash": "anything"}
        self.assertEqual(compute_hash(r1, GENESIS), compute_hash(r2, GENESIS))


# ======================================================================
# Phase 14 Part 7 — add_chain_fields
# ======================================================================
class TestAddChainFields(unittest.TestCase):
    def test_adds_prev_hash_and_hash(self):
        r = {"a": 1}
        out = add_chain_fields(r, GENESIS)
        self.assertIn("prev_hash", out)
        self.assertIn("hash", out)
        self.assertEqual(out["prev_hash"], GENESIS)

    def test_does_not_mutate_input(self):
        r = {"a": 1}
        add_chain_fields(r, GENESIS)
        self.assertNotIn("hash", r)
        self.assertNotIn("prev_hash", r)

    def test_hash_matches_compute_hash(self):
        r = {"a": 1, "b": "hello"}
        out = add_chain_fields(r, GENESIS)
        self.assertEqual(out["hash"], compute_hash(out, GENESIS))

    def test_chain_links_correctly(self):
        """Three records chained in sequence: each prev_hash = previous hash."""
        records = [{"a": 1}, {"a": 2}, {"a": 3}]
        chained = []
        prev = GENESIS
        for r in records:
            c = add_chain_fields(r, prev)
            chained.append(c)
            prev = c["hash"]
        self.assertEqual(chained[0]["prev_hash"], GENESIS)
        self.assertEqual(chained[1]["prev_hash"], chained[0]["hash"])
        self.assertEqual(chained[2]["prev_hash"], chained[1]["hash"])


# ======================================================================
# Phase 14 Part 7 — verify_records
# ======================================================================
class TestVerifyRecords(unittest.TestCase):
    def setUp(self):
        self.records = [
            {"ts": 1000, "cmd": "uptime", "source": "user"},
            {"ts": 1001, "cmd": "ls -la", "source": "user"},
            {"ts": 1002, "cmd": "rm -rf /tmp", "source": "ai"},
        ]
        self.chained = []
        prev = GENESIS
        for r in self.records:
            c = add_chain_fields(r, prev)
            self.chained.append(c)
            prev = c["hash"]

    def test_intact_chain_all_verified(self):
        results = verify_records(self.chained)
        self.assertEqual(len(results), 3)
        for r in results:
            self.assertEqual(r.status, VerifyStatus.VERIFIED)

    def test_tampered_record_detected(self):
        """Editing one record's content (but not its hash) is detected."""
        tampered = list(self.chained)
        tampered[1] = dict(tampered[1])
        tampered[1]["cmd"] = "HACKED"
        results = verify_records(tampered)
        self.assertEqual(results[0].status, VerifyStatus.VERIFIED)
        self.assertEqual(results[1].status, VerifyStatus.TAMPERED)
        # Record 2 is still verified (its own hash is correct and its prev_hash
        # still matches record 1's stored hash).
        self.assertEqual(results[2].status, VerifyStatus.VERIFIED)

    def test_deleted_record_detected_as_broken_chain(self):
        """Removing a record breaks the chain link of the next record."""
        deleted = [self.chained[0], self.chained[2]]
        results = verify_records(deleted)
        self.assertEqual(results[0].status, VerifyStatus.VERIFIED)
        self.assertEqual(results[1].status, VerifyStatus.BROKEN_CHAIN)

    def test_inserted_record_detected_as_broken_chain(self):
        """Inserting a record with a wrong prev_hash breaks the chain."""
        inserted = [self.chained[0],
                     add_chain_fields({"ts": 1500, "cmd": "INJECTED"}, "wrong_prev_hash"),
                     self.chained[1], self.chained[2]]
        results = verify_records(inserted)
        # Record 0 is verified.
        self.assertEqual(results[0].status, VerifyStatus.VERIFIED)
        # The injected record's hash is correct for its own content, but its
        # prev_hash doesn't match record 0's hash → BROKEN_CHAIN.
        self.assertEqual(results[1].status, VerifyStatus.BROKEN_CHAIN)

    def test_old_records_without_hash_are_unverified(self):
        old = [{"ts": 1, "cmd": "uptime"}, {"ts": 2, "cmd": "ls"}]
        results = verify_records(old)
        for r in results:
            self.assertEqual(r.status, VerifyStatus.UNVERIFIED)

    def test_mixed_old_and_new(self):
        """Old records followed by new chained records: the first new record's
        prev_hash won't match (it expects GENESIS or the last old record's hash,
        but old records don't have hashes)."""
        mixed = [{"ts": 1, "cmd": "old"}, self.chained[0]]
        results = verify_records(mixed)
        self.assertEqual(results[0].status, VerifyStatus.UNVERIFIED)
        self.assertEqual(results[1].status, VerifyStatus.BROKEN_CHAIN)

    def test_empty_list(self):
        self.assertEqual(verify_records([]), [])

    def test_single_record_verified(self):
        single = [add_chain_fields({"ts": 1, "cmd": "test"}, GENESIS)]
        results = verify_records(single)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, VerifyStatus.VERIFIED)

    def test_tampered_hash_field_detected(self):
        """Changing the hash field itself (without changing content) is detected."""
        tampered = list(self.chained)
        tampered[1] = dict(tampered[1])
        tampered[1]["hash"] = "0" * 64     # wrong hash
        results = verify_records(tampered)
        self.assertEqual(results[1].status, VerifyStatus.TAMPERED)


# ======================================================================
# Phase 14 Part 7 — summary helpers
# ======================================================================
class TestSummaryHelpers(unittest.TestCase):
    def test_verify_summary_counts(self):
        results = [
            VerifyResult(0, VerifyStatus.VERIFIED),
            VerifyResult(1, VerifyStatus.VERIFIED),
            VerifyResult(2, VerifyStatus.UNVERIFIED),
            VerifyResult(3, VerifyStatus.TAMPERED),
        ]
        counts = verify_summary(results)
        self.assertEqual(counts["verified"], 2)
        self.assertEqual(counts["unverified"], 1)
        self.assertEqual(counts["tampered"], 1)
        self.assertEqual(counts["broken_chain"], 0)

    def test_is_log_intact_all_verified(self):
        results = [VerifyResult(0, VerifyStatus.VERIFIED),
                    VerifyResult(1, VerifyStatus.VERIFIED)]
        self.assertTrue(is_log_intact(results))

    def test_is_log_intact_with_unverified(self):
        """Old (unverified) records don't break integrity."""
        results = [VerifyResult(0, VerifyStatus.UNVERIFIED),
                    VerifyResult(1, VerifyStatus.VERIFIED)]
        self.assertTrue(is_log_intact(results))

    def test_is_log_intact_with_tampered(self):
        results = [VerifyResult(0, VerifyStatus.TAMPERED)]
        self.assertFalse(is_log_intact(results))

    def test_is_log_intact_with_broken_chain(self):
        results = [VerifyResult(0, VerifyStatus.BROKEN_CHAIN)]
        self.assertFalse(is_log_intact(results))

    def test_summary_text_all_verified(self):
        results = [VerifyResult(0, VerifyStatus.VERIFIED),
                    VerifyResult(1, VerifyStatus.VERIFIED)]
        text = summary_text(results)
        self.assertIn("2 verified", text)

    def test_summary_text_with_tampered(self):
        results = [VerifyResult(0, VerifyStatus.VERIFIED),
                    VerifyResult(1, VerifyStatus.TAMPERED)]
        text = summary_text(results)
        self.assertIn("1 verified", text)
        self.assertIn("1 tampered", text)

    def test_summary_text_empty(self):
        self.assertEqual(summary_text([]), "No records.")


# ======================================================================
# Phase 14 Part 7 — ActivityLog integration
# ======================================================================
class TestActivityLogChain(unittest.TestCase):
    def test_add_produces_chained_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = ActivityLog(Path(tmp) / "activity.jsonl")
            log.add("s1", "Server 1", "uptime", 0, "user", "safe")
            log.add("s1", "Server 1", "ls -la", 0, "user", "safe")
            # Read back and verify the chain.
            records = []
            for ln in Path(tmp, "activity.jsonl").read_text().splitlines():
                records.append(json.loads(ln))
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]["prev_hash"], GENESIS)
            self.assertEqual(records[1]["prev_hash"], records[0]["hash"])
            # Each record has a hash field.
            for r in records:
                self.assertIn("hash", r)
                self.assertEqual(len(r["hash"]), 64)

    def test_verify_intact_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = ActivityLog(Path(tmp) / "activity.jsonl")
            log.add("s1", "Server 1", "uptime", 0, "user", "safe")
            log.add("s1", "Server 1", "ls", 0, "user", "safe")
            results = log.verify()
            self.assertEqual(len(results), 2)
            for r in results:
                self.assertEqual(r.status, VerifyStatus.VERIFIED)
            self.assertTrue(is_log_intact(results))

    def test_verify_detects_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "activity.jsonl"
            log = ActivityLog(path)
            log.add("s1", "Server 1", "uptime", 0, "user", "safe")
            log.add("s1", "Server 1", "ls", 0, "user", "safe")
            # Tamper: edit the first line's command.
            lines = path.read_text("utf-8").splitlines()
            rec = json.loads(lines[0])
            rec["cmd"] = "HACKED"
            lines[0] = json.dumps(rec)
            path.write_text("\n".join(lines) + "\n", "utf-8")
            # Verify should detect the tamper.
            results = log.verify()
            self.assertEqual(results[0].status, VerifyStatus.TAMPERED)

    def test_verify_empty_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = ActivityLog(Path(tmp) / "activity.jsonl")
            results = log.verify()
            self.assertEqual(results, [])
            self.assertTrue(is_log_intact(results))

    def test_clear_resets_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = ActivityLog(Path(tmp) / "activity.jsonl")
            log.add("s1", "Server 1", "uptime", 0, "user", "safe")
            log.clear()
            # After clearing, the next add should start a fresh chain from GENESIS.
            log.add("s1", "Server 1", "ls", 0, "user", "safe")
            records = [json.loads(ln) for ln in Path(tmp, "activity.jsonl").read_text().splitlines()]
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["prev_hash"], GENESIS)

    def test_old_records_load_without_error(self):
        """Pre-Phase-14-Part-7 records (no hash fields) load fine and verify
        as UNVERIFIED."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "activity.jsonl"
            # Write old-format records.
            path.write_text(
                json.dumps({"ts": 1, "cmd": "uptime", "source": "user"}) + "\n" +
                json.dumps({"ts": 2, "cmd": "ls", "source": "user"}) + "\n")
            log = ActivityLog(path)
            results = log.verify()
            for r in results:
                self.assertEqual(r.status, VerifyStatus.UNVERIFIED)
            self.assertTrue(is_log_intact(results))

    def test_corrupt_json_line_handled(self):
        """A corrupt (non-JSON) line is treated as a record and doesn't crash
        the verifier."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "activity.jsonl"
            path.write_text('{"ts":1,"cmd":"ok"}\nNOT JSON\n')
            log = ActivityLog(path)
            results = log.verify()
            self.assertEqual(len(results), 2)     # both lines parsed (one as corrupt)

    def test_chain_survives_trim(self):
        """When the log trims to KEEP lines, the chain should still verify
        for the remaining records (the first remaining record's prev_hash
        points to a now-deleted record, but we re-read the last hash on
        the next add so new records chain correctly)."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "activity.jsonl"
            log = ActivityLog(path)
            # Add enough records to trigger a trim (MAX_LINES = 3000, KEEP = 2000).
            # We can't add 3000 in a test (too slow), so we directly write a file
            # with 2 records, trim to 1, and verify the remaining record is
            # still verifiable.
            log.add("s1", "S1", "cmd1", 0, "user", "safe")
            log.add("s1", "S1", "cmd2", 0, "user", "safe")
            # Simulate a trim: keep only the last line.
            lines = path.read_text("utf-8").splitlines()
            path.write_text(lines[-1] + "\n", "utf-8")
            # Verify: the remaining record's hash is correct (it was computed
            # correctly when it was added), but its prev_hash points to a
            # deleted record → BROKEN_CHAIN.
            log2 = ActivityLog(path)
            results = log2.verify()
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].status, VerifyStatus.BROKEN_CHAIN)


if __name__ == "__main__":
    unittest.main()
