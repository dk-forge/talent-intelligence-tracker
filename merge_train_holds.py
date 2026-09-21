"""LIFT A HOLD THE TRAIN PLACED ITSELF, ONCE THE HEAD IT JUDGED IS GONE.

`needs-human` is a hold label, so a pull request carrying it is skipped. The
train adds that label when it reports a problem (a red check, a conflict it
may not resolve, an unstick cap). Nothing ever removed it. On 2026-09-21 the
train labelled #1063 for a red check on head a975f49; the cause was fixed and
b761861 pushed; the label stayed, and the train would have skipped the pull
request forever with every gate green. That is a silent permanent stall, and
it defeats "fix it and the train takes it".

THE RULE. The label is removed only when ALL of this holds:
  * the train's own marker comment is on the pull request (a `needs-human`
    with no train marker was put there by a person and is never touched);
  * NO train marker names the current head SHA (an escalation recorded
    against this very head still stands);
  * every legacy marker (`sha=escalated`, written before the SHA was
    recorded) is OLDER than the head commit. A time that cannot be read keeps
    the hold.
Lifting is not merging: the pull request becomes an ordinary candidate and is
judged from scratch, so a head that is still red is simply reported again,
against the new SHA.

`hold`, `do-not-merge` and `blocked` are never removed by anything here. Any
error keeps the hold: an unreadable answer is never a reason to lift.

Imports nothing from merge_train.py on purpose (that file runs as `__main__`).
"""
from __future__ import annotations

import re
import subprocess
from datetime import datetime
from typing import Any, Callable

_PROBLEM_RE = re.compile(r"<!-- merge-train:pr-problem:[0-9a-f]+:sha=([0-9a-fA-F]{7,40}) -->")
_UNSTICK_RE = re.compile(r"<!-- merge-train:unstick sha=(\S+) action=needs-human -->")
LIFTED_PREFIX = "<!-- merge-train:hold-lifted"
LEGACY = "escalated"


def lifted_marker(head: str) -> str:
    return f"{LIFTED_PREFIX} sha={head} -->"


def _gh_remove_label(repo: str, number: int, label: str) -> None:
    proc = subprocess.run(
        ["gh", "pr", "edit", str(number), "-R", repo, "--remove-label", label],
        capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"gh pr edit --remove-label exited {proc.returncode}: "
                           f"{(proc.stderr or proc.stdout).strip()[:300]}")


def _when(raw: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def train_escalations(comments: list[dict]) -> list[tuple[str, datetime | None]]:
    """(sha, created_at) for every escalation marker the train left."""
    out: list[tuple[str, datetime | None]] = []
    for c in comments:
        body = str(c.get("body", ""))
        for rx in (_PROBLEM_RE, _UNSTICK_RE):
            for sha in rx.findall(body):
                out.append((sha.lower(), _when(c.get("created_at"))))
    return out


def hold_is_stale(head: str, escalations: list[tuple[str, datetime | None]],
                  head_committed_at: Callable[[], datetime | None]) -> tuple[bool, str]:
    """Pure decision. (lift?, why)."""
    head = head.lower()
    if not head:
        return False, "the head SHA could not be read"
    if not escalations:
        return False, "no merge-train marker: a person set this hold"
    legacy = []
    for sha, at in escalations:
        if sha == LEGACY:
            legacy.append(at)
        elif head.startswith(sha) or sha.startswith(head):
            return False, "the train escalated THIS head"
    if legacy:
        committed = head_committed_at()
        if committed is None or any(at is None or at >= committed for at in legacy):
            return False, "an undated escalation is not provably older than the head"
    return True, "every escalation was recorded against an earlier head"


def lift_stale_holds(client: Any, cfg: Any, prs: list[dict], say: Callable[[str], None], *,
                     dry_run: bool = True,
                     remove_label: Callable[[str, int, str], None] = _gh_remove_label) -> list[int]:
    """Remove the train's own stale `needs-human`. Returns the numbers lifted.

    Mutates `pr["labels"]` so this same run judges the pull request as an
    ordinary candidate. Never raises.
    """
    label = str(cfg.needs_human_label)
    lifted: list[int] = []
    for pr in prs:
        if label.lower() not in {str(l).lower() for l in pr.get("labels") or []}:
            continue
        number, head = int(pr["number"]), str(pr.get("headRefOid", ""))
        try:
            comments = client.comments(number)

            def committed() -> datetime | None:
                data = client._api(f"repos/{client.repo}/commits/{head}")
                return _when(data["commit"]["committer"]["date"])

            stale, why = hold_is_stale(head, train_escalations(comments), committed)
            if not stale:
                say(f"  #{number}: `{label}` stays ({why})")
                continue
            if dry_run:
                say(f"  would lift `{label}` from #{number}: {why}")
            else:
                remove_label(client.repo, number, label)
                if not any(lifted_marker(head) in str(c.get("body", "")) for c in comments):
                    client.comment(number, (
                        f"merge train: the `{label}` hold is lifted because the head moved "
                        f"to {head[:8]}. The earlier escalation was recorded against an older "
                        f"head, so this pull request is judged again from scratch."
                        f"\n\n{lifted_marker(head)}"))
                say(f"  lifted `{label}` from #{number}: {why}")
            pr["labels"] = [l for l in pr["labels"] if str(l).lower() != label.lower()]
            lifted.append(number)
        except Exception as exc:  # noqa: BLE001 (any doubt keeps the hold)
            say(f"  #{number}: `{label}` stays, the hold could not be judged: {exc}")
    return lifted
