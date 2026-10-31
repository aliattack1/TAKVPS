"""Phase 14 Part 11 tests: conversation branching."""
from __future__ import annotations
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services.chat_branch import branch_from, find_branchable, branch_summary, can_branch
from services.ai.chat import Msg


class TestBranchFrom(unittest.TestCase):
    def setUp(self):
        self.msgs = [
            Msg("user", "hello"),
            Msg("assistant", "hi there"),
            Msg("user", "check disk"),
            Msg("assistant", "df -h"),
            Msg("result", "output", cmd="df -h"),
        ]

    def test_branch_at_index_1(self):
        branched = branch_from(self.msgs, 1)
        self.assertEqual(len(branched), 2)
        self.assertEqual(branched[0].text, "hello")
        self.assertEqual(branched[1].text, "hi there")

    def test_branch_at_last_index(self):
        branched = branch_from(self.msgs, 4)
        self.assertEqual(len(branched), 5)

    def test_branch_at_index_0(self):
        branched = branch_from(self.msgs, 0)
        self.assertEqual(len(branched), 1)
        self.assertEqual(branched[0].text, "hello")

    def test_out_of_range_raises(self):
        with self.assertRaises(IndexError):
            branch_from(self.msgs, -1)
        with self.assertRaises(IndexError):
            branch_from(self.msgs, 5)

    def test_empty_list_raises(self):
        with self.assertRaises(IndexError):
            branch_from([], 0)

    def test_does_not_mutate_original(self):
        original_len = len(self.msgs)
        branch_from(self.msgs, 2)
        self.assertEqual(len(self.msgs), original_len)


class TestFindBranchable(unittest.TestCase):
    def test_finds_assistant_messages_with_followers(self):
        msgs = [
            Msg("user", "q1"),
            Msg("assistant", "a1"),
            Msg("user", "q2"),
            Msg("assistant", "a2"),
            Msg("result", "r"),
        ]
        indices = find_branchable(msgs)
        # a1 (index 1) has followers; a2 (index 3) has followers; result (4) doesn't count
        self.assertEqual(indices, [1, 3])

    def test_last_message_not_branchable(self):
        msgs = [Msg("user", "q"), Msg("assistant", "a")]
        self.assertEqual(find_branchable(msgs), [])

    def test_empty(self):
        self.assertEqual(find_branchable([]), [])


class TestBranchSummary(unittest.TestCase):
    def test_summary(self):
        msgs = [Msg("user", "q"), Msg("assistant", "a"), Msg("user", "q2"), Msg("assistant", "a2")]
        self.assertEqual(branch_summary(msgs, 1), "Keep 2 messages, discard 2")

    def test_singular(self):
        msgs = [Msg("user", "q"), Msg("assistant", "a")]
        self.assertEqual(branch_summary(msgs, 0), "Keep 1 message, discard 1")


class TestCanBranch(unittest.TestCase):
    def test_true_for_assistant_with_followers(self):
        msgs = [Msg("user", "q"), Msg("assistant", "a"), Msg("user", "q2")]
        self.assertTrue(can_branch(msgs, 1))

    def test_false_for_last_message(self):
        msgs = [Msg("user", "q"), Msg("assistant", "a")]
        self.assertFalse(can_branch(msgs, 1))

    def test_false_for_user_message(self):
        msgs = [Msg("user", "q"), Msg("assistant", "a"), Msg("user", "q2")]
        self.assertFalse(can_branch(msgs, 0))
        self.assertFalse(can_branch(msgs, 2))

    def test_false_for_result_message(self):
        msgs = [Msg("user", "q"), Msg("assistant", "a"), Msg("result", "r", cmd="ls")]
        self.assertFalse(can_branch(msgs, 2))

    def test_false_for_empty(self):
        self.assertFalse(can_branch([], 0))

    def test_false_for_out_of_range(self):
        msgs = [Msg("user", "q")]
        self.assertFalse(can_branch(msgs, -1))
        self.assertFalse(can_branch(msgs, 1))


if __name__ == "__main__":
    unittest.main()
