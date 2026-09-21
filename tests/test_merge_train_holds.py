"""A hold the train placed on an OLD head must not outlive that head.

2026-09-21 (sandbox): #1063 was labelled `needs-human` on a975f49, fixed by b761861,
and would have been skipped forever with every gate green.
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import merge_train_holds as holds  # noqa: E402

OLD, NEW = "a975f49" + "0" * 33, "b761861" + "1" * 33
CFG = SimpleNamespace(needs_human_label="needs-human")


def problem(sha, at="2026-09-21T10:00:00Z"):
    return {"created_at": at,
            "body": f"red\n\n<!-- merge-train:pr-problem:0123456789ab:sha={sha} -->"}


def legacy(at):
    return {"created_at": at,
            "body": "<!-- merge-train:unstick sha=escalated action=needs-human -->\nstopped"}


class Client:
    repo = "o/r"

    def __init__(self, comments, committed="2026-09-21T12:24:00Z", broken=False):
        self._comments, self.committed, self.broken = comments, committed, broken
        self.posted = []

    def comments(self, number):
        if self.broken:
            raise RuntimeError("HTTP 502")
        return self._comments

    def comment(self, number, body):
        self.posted.append(body)

    def _api(self, path, **_):
        return {"commit": {"committer": {"date": self.committed}}}


def go(client, labels=("needs-human",), head=NEW, dry_run=False):
    pr = {"number": 1063, "headRefOid": head, "labels": list(labels)}
    removed, said = [], []
    lifted = holds.lift_stale_holds(client, CFG, [pr], said.append, dry_run=dry_run,
                                    remove_label=lambda *a: removed.append(a))
    return pr, lifted, removed, said


class LiftStaleHolds(unittest.TestCase):

    def test_the_trains_hold_on_an_older_head_is_lifted(self):
        client = Client([problem(OLD)])
        pr, lifted, removed, _ = go(client)
        self.assertEqual(lifted, [1063])
        self.assertEqual(removed, [("o/r", 1063, "needs-human")])
        self.assertEqual(pr["labels"], [])
        self.assertEqual(len(client.posted), 1)
        self.assertIn(holds.lifted_marker(NEW), client.posted[0])

    def test_the_trains_hold_on_THIS_head_stays(self):
        """THE CORE GUARD. Without it the train would un-hold what it just held."""
        client = Client([problem(OLD), problem(NEW)])
        pr, lifted, removed, _ = go(client)
        self.assertEqual((lifted, removed, client.posted), ([], [], []))
        self.assertEqual(pr["labels"], ["needs-human"])

    def test_a_hold_a_person_set_is_never_removed(self):
        client = Client([{"created_at": "2026-09-21T10:00:00Z", "body": "please wait for me"}])
        pr, lifted, removed, _ = go(client)
        self.assertEqual((lifted, removed), ([], []))
        self.assertEqual(pr["labels"], ["needs-human"])

    def test_hold_and_do_not_merge_are_never_touched(self):
        client = Client([problem(OLD)])
        pr, _, removed, _ = go(client, labels=("hold", "do-not-merge", "blocked", "needs-human"))
        self.assertEqual(removed, [("o/r", 1063, "needs-human")])
        self.assertEqual(pr["labels"], ["hold", "do-not-merge", "blocked"])
        _, lifted, removed, _ = go(Client([problem(OLD)]), labels=("hold", "do-not-merge"))
        self.assertEqual((lifted, removed), ([], []))

    def test_a_legacy_marker_older_than_the_head_commit_is_lifted(self):
        _, lifted, _, _ = go(Client([legacy("2026-09-21T10:00:00Z")]))
        self.assertEqual(lifted, [1063])

    def test_a_legacy_marker_newer_than_the_head_commit_stays(self):
        _, lifted, removed, _ = go(Client([legacy("2026-09-21T13:00:00Z")]))
        self.assertEqual((lifted, removed), ([], []))

    def test_a_legacy_marker_with_an_unreadable_time_stays(self):
        _, lifted, _, _ = go(Client([legacy("2026-09-21T10:00:00Z")], committed="not a date"))
        self.assertEqual(lifted, [])
        _, lifted, _, _ = go(Client([legacy(None)]))
        self.assertEqual(lifted, [])

    def test_the_lift_comment_is_deduped_by_the_new_sha(self):
        client = Client([problem(OLD), {"created_at": "x", "body": holds.lifted_marker(NEW)}])
        _, lifted, removed, _ = go(client)
        self.assertEqual((lifted, len(removed), client.posted), ([1063], 1, []))

    def test_a_dry_run_writes_nothing(self):
        client = Client([problem(OLD)])
        _, _, removed, said = go(client, dry_run=True)
        self.assertEqual((removed, client.posted), ([], []))
        self.assertTrue(any("would lift" in s for s in said))

    def test_an_unreadable_comment_list_keeps_the_hold_and_does_not_raise(self):
        pr, lifted, removed, said = go(Client([], broken=True))
        self.assertEqual((lifted, removed), ([], []))
        self.assertEqual(pr["labels"], ["needs-human"])
        self.assertTrue(any("could not be judged" in s for s in said))

    def test_an_empty_head_sha_keeps_the_hold(self):
        _, lifted, _, _ = go(Client([problem(OLD)]), head="")
        self.assertEqual(lifted, [])

    def test_the_train_calls_it_before_judging(self):
        src = (Path(__file__).resolve().parents[1] / "merge_train.py").read_text(encoding="utf-8")
        self.assertLess(src.index("lift_stale_holds(client, cfg, prs"), src.index("candidates = order_candidates(prs"))


class TheWriterRecordsTheRealHead(unittest.TestCase):
    """`sha=escalated` could only be dated, never matched. The writer now names
    the head, and the reader treats that marker as "escalated THIS head"."""

    def _escalate(self, head):
        import merge_train as mt
        posted = []
        client = SimpleNamespace(add_label=lambda n, l: None,
                                 comment=lambda n, body: posted.append(body))
        cfg = SimpleNamespace(needs_human_label="needs-human", repo="o/r")
        real, mt.notify = mt.notify, lambda *a, **k: "stubbed"
        try:
            mt._escalate(client, cfg, 7, SimpleNamespace(say=lambda s: None),
                         "why", head=head, dry_run=False)
        finally:
            mt.notify = real
        return posted[0]

    def test_the_escalation_marker_names_the_head_it_judged(self):
        body = self._escalate(NEW)
        self.assertIn(f"sha={NEW} action=needs-human", body)
        self.assertNotIn("sha=escalated", body)

    def test_what_the_writer_writes_the_reader_holds_and_then_lifts(self):
        body = self._escalate(OLD)
        at = "2026-09-21T10:00:00Z"
        _, lifted, _, _ = go(Client([{"created_at": at, "body": body}]), head=OLD)
        self.assertEqual(lifted, [])
        _, lifted, _, _ = go(Client([{"created_at": at, "body": body}]), head=NEW)
        self.assertEqual(lifted, [1063])

    def test_an_escalation_is_not_counted_as_an_unstick_attempt_on_the_head(self):
        import merge_train as mt
        per_sha, per_pr = mt.unstick_attempts([{"body": self._escalate(NEW)}], NEW)
        self.assertEqual((per_sha, per_pr), (0, 1))


if __name__ == "__main__":
    unittest.main()
