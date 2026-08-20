"""One operational sender, one subject prefix, and no path left behind.

WHAT WENT WRONG, AND WHY A TEST IS THE RIGHT ANSWER
---------------------------------------------------
Operational mail moved off the WordPress host and onto Resend so the alarm
would stop depending on the thing it monitors. In the sibling tracker that
change converted three callers and left nine, and NOBODY NOTICED, because a
wrong From line produces no error anywhere. The mail arrives. It just arrives
wearing the wrong face.

The unconverted ones went on POSTing to `/wp-json/talent/v1/alert`, which calls
bare `wp_mail()`. On this install the Brevo plugin intercepts `wp_mail` and
replaces the whole From line with the SUBSCRIBER newsletter identity. So an
operational alarm arrives from the newsletter address, under the newsletter's
display name, beside mail the owner subscribed to.

That is not cosmetic. It is the eight-emails-in-an-afternoon failure with a
longer fuse: mail that looks like a newsletter gets filed with the newsletter,
and after that the alarm is decoration. The owner asked for exactly one thing,
a system he can sort on, and one From plus one prefix is that system.

So the invariant is asserted rather than remembered. A tenth caller cannot be
added with its own sender without this file going red.

NO LIVE MAIL IS SENT BY ANYTHING HERE. Every send is either rendered or
intercepted, and the ledger is redirected to a temporary file so the committed
one is never touched.
"""
import ast
import contextlib
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import opsmail            # noqa: E402
import ops_notify         # noqa: E402

WORKFLOWS = REPO / ".github" / "workflows"

#: The route that hands a message to `wp_mail`, and through it to the reader
#: newsletter's From line. Nothing in this repo's Python may build a request
#: to it.
ALERT_ROUTE = "wp-json/talent/v1/alert"

#: Modules that legitimately talk to the transport directly. `opsmail` IS the
#: transport; `ci_alert` is the ledger that rules on a message before handing it
#: over; `ops_notify` is the door every other caller uses; `ci_noise_report` and
#: `host_watch` own their own hold/drain paths and are reviewed. Everything else
#: goes through `ops_notify`. Keep this list SHORT: each entry is a place the
#: From line could drift without anything noticing.
DIRECT_SENDERS = {"opsmail.py", "ops_notify.py", "ci_alert.py",
                  "ci_noise_report.py", "host_watch.py"}

#: A reader-facing path, if one is ever added here. Different provider,
#: different budget, different identity, and none of this file's rules apply to
#: it except "stay separate". There is no such module in this repo today; the
#: subscriber relay lives in the sibling tracker. These names exist so that
#: adding one silently is not possible.
READER_MODULES = {"digest_transport.py", "digest_send.py", "digest_layout.py",
                  "digest_slot.py", "subscriber_digest.py"}


def _evaluated_strings(path):
    """Every string literal a module actually EVALUATES, docstrings excluded.

    Prose is not behaviour, and several modules must go on describing the
    `/alert` route because that history is why the current design exists. What
    none of them may do is build a request to it.
    """
    tree = ast.parse(Path(path).read_text())
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and \
                    isinstance(body[0].value, ast.Constant) and \
                    isinstance(body[0].value.value, str):
                docs.add(id(body[0].value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docs]


def _modules():
    """Every top-level module in the repo. The collectors and the pipeline are
    scanned too, because a source that grows an alert is exactly the caller
    nobody would think to look at."""
    out = sorted(REPO.glob("*.py"))
    for sub in ("collectors", "pipeline", "analysis"):
        out.extend(sorted((REPO / sub).rglob("*.py")))
    return out


@contextlib.contextmanager
def _scratch_ledger():
    """Send for real, but never into the COMMITTED ledger.

    `ops_notify.notify` calls `ci_alert.post_alert`, which claims
    data/alert_state.json before sending. That is the correct production
    ordering and it means an unguarded test rewrites a tracked file. A test that
    mutates repository state is a test that has to be remembered, and this one
    does not have to be.
    """
    with tempfile.TemporaryDirectory() as tmp:
        with mock.patch.dict(
                os.environ,
                {"ALERT_STATE_PATH": str(Path(tmp) / "alert_state.json"),
                 "ALERT_STATE_COMMIT": "false"}):
            yield


class NobodyMailsTheOwnerBehindTheHelpersBack(unittest.TestCase):

    def test_no_module_can_still_build_a_request_to_the_alert_route(self):
        """The root cause, closed for every module rather than for three.

        The callers that were NOT converted are exactly the ones a check
        scoped to the converted ones does not look at, which is how this
        defect survives a change that was specifically about it. So the scope
        is every module in the repo.
        """
        offenders = []
        for path in _modules():
            for literal in _evaluated_strings(path):
                if ALERT_ROUTE in literal:
                    offenders.append(str(path.relative_to(REPO)))
                    break
        self.assertEqual(offenders, [], f"{offenders} can still POST to the "
                         "site's /alert route, which wp_mail hands to the "
                         "reader newsletter's From line. Use ops_notify.")

    def test_only_the_helpers_talk_to_the_transport_directly(self):
        """A new sender has to come through the door, not around it.

        Anything that imports `opsmail` is choosing its own send call, and a
        send call is where a From line gets invented. Modules that need to are
        named above and reviewed; everything else uses `ops_notify`, which owns
        no From line of its own either.
        """
        offenders = []
        for path in _modules():
            if path.name in DIRECT_SENDERS:
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                if any(n.split(".")[0] == "opsmail" for n in names):
                    offenders.append(str(path.relative_to(REPO)))
                    break
        self.assertEqual(sorted(set(offenders)), [],
                         "these import the transport directly instead of "
                         "ops_notify; add them to DIRECT_SENDERS only with a "
                         "reason, because each one is a From line that can "
                         "drift without anything noticing")

    def test_the_from_line_has_exactly_one_definition(self):
        """`OPS_MAIL_FROM` is READ in one place and nowhere else.

        A second reader is a second default, and a second default is how two
        alarms end up in two folders.

        Naming the variable in a diagnostic string is not reading it, and
        several modules rightly do that: when a send fails on a bad sender, the
        message that says "check OPS_MAIL_FROM" is the useful one. So this looks
        for an actual environment access, via the AST, rather than for the words.
        """
        readers = []
        for path in _modules():
            for node in ast.walk(ast.parse(path.read_text())):
                key = None
                # os.environ.get("OPS_MAIL_FROM", ...)
                if isinstance(node, ast.Call) and node.args and \
                        isinstance(node.args[0], ast.Constant):
                    fn = node.func
                    if isinstance(fn, ast.Attribute) and fn.attr == "get" and \
                            isinstance(fn.value, ast.Attribute) and \
                            fn.value.attr == "environ":
                        key = node.args[0].value
                # os.environ["OPS_MAIL_FROM"]
                elif isinstance(node, ast.Subscript) and \
                        isinstance(node.value, ast.Attribute) and \
                        node.value.attr == "environ" and \
                        isinstance(node.slice, ast.Constant):
                    key = node.slice.value
                if key == "OPS_MAIL_FROM":
                    readers.append(path.name)
                    break
        self.assertEqual(sorted(set(readers)), ["opsmail.py"], readers)

    def test_the_operational_sender_is_not_a_newsletter(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPS_MAIL_FROM", None)
            sender = opsmail.sender()
        self.assertIn("Ops", sender)
        self.assertNotIn("newsletter@", sender)
        # Pinned exactly, because the whole ask was ONE sortable identity
        # matching the sibling tracker's, so one mail rule catches both.
        self.assertEqual(
            sender, "Talent Intelligence Tracker Ops <ops@asktherecruiter.com>")


class OneSubjectPrefixSoOneMailRuleCatchesEverything(unittest.TestCase):

    def test_the_prefix_is_exactly_what_the_endpoint_stamped(self):
        """Byte for byte, trailing space included. The owner filters on it, so
        changing it is changing his mail rules for him."""
        self.assertEqual(opsmail.SUBJECT_PREFIX, "[Talent Intelligence Tracker] ")

    def test_every_operational_subject_is_stamped(self):
        """Applied by the transport, so no caller can forget it."""
        seen = {}

        def spy(method, path, body=None, extra_headers=None):
            seen.update(body or {})
            return 200, {"id": "re_1"}

        with mock.patch.dict(os.environ, {"RESEND_API_KEY": "k"}), \
                mock.patch.object(opsmail, "_request", spy):
            opsmail.send_once("a collector broke", "b")
        self.assertTrue(seen["subject"].startswith(opsmail.SUBJECT_PREFIX),
                        seen["subject"])
        self.assertEqual(seen["from"], opsmail.sender())

    def test_no_caller_adds_a_prefix_of_its_own(self):
        """Two prefixes is a subject the owner's one mail rule still catches and
        a subject line that looks broken. The transport stamps it; a caller that
        stamps it too gets `[Talent Intelligence Tracker] [Talent Intelligence
        Tracker] ...`."""
        marker = opsmail.SUBJECT_PREFIX.strip()
        offenders = []
        for path in _modules():
            if path.name == "opsmail.py":
                continue
            for literal in _evaluated_strings(path):
                if marker in literal:
                    offenders.append(f"{path.name}: {literal[:60]}")
        self.assertEqual(offenders, [], offenders)

    def test_the_prefix_is_stamped_once_end_to_end(self):
        sent = []
        with _scratch_ledger(), \
                mock.patch.dict(os.environ, {"RESEND_API_KEY": "k"}), \
                mock.patch.object(
                    opsmail, "send_once",
                    lambda s, b, i="": (sent.append(s) or
                                        (True, "emailed the owner", False))):
            ops_notify.notify("a collector broke", "body", what="test")
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].count(opsmail.SUBJECT_PREFIX.strip()), 0,
                         "ops_notify must hand the transport an UNstamped "
                         "subject; the transport is what stamps it")


class ReaderMailWouldKeepItsOwnIdentity(unittest.TestCase):
    """The other direction, and it is not symmetric politeness.

    There is no subscriber relay in this repo today. If one is added, it must
    not gain the ops prefix or the ops From: somebody subscribed to it, and a
    subject stamped `[Talent Intelligence Tracker]` in front of an edition they
    asked for reads as machine noise. It must also not share Resend's small
    allowance, or a bad afternoon of red CI eats what readers depend on.
    """

    def test_a_reader_path_never_imports_the_operational_transport(self):
        for name in READER_MODULES:
            path = REPO / name
            if not path.exists():
                continue
            imported = set()
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Import):
                    imported.update(a.name.split(".")[0] for a in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imported.add((node.module or "").split(".")[0])
            self.assertNotIn("opsmail", imported, name)
            self.assertNotIn("ops_notify", imported, name)

    def test_no_reader_subject_would_carry_the_operational_prefix(self):
        marker = opsmail.SUBJECT_PREFIX.strip()
        for name in READER_MODULES:
            path = REPO / name
            if not path.exists():
                continue
            for literal in _evaluated_strings(path):
                self.assertNotIn(marker, literal,
                                 f"{name} would stamp a reader subject with "
                                 "the operations prefix")


class TheDoorItselfIsWellBehaved(unittest.TestCase):

    def test_it_never_prints_the_subject_or_the_body(self):
        """A run log is public on this repo. A reporter that carries an employer
        name must be able to reach the owner's inbox without that name reaching
        Actions, and a debug print in the one shared door would defeat every
        caller at once."""
        secret = "Zzyzxcorp"
        buf = io.StringIO()
        with _scratch_ledger(), \
                mock.patch.dict(os.environ, {"RESEND_API_KEY": "k"}), \
                mock.patch.object(opsmail, "send_once",
                                  lambda s, b, i="": (True, "emailed the owner",
                                                      False)), \
                redirect_stdout(buf):
            ops_notify.notify(f"missing {secret}", f"body about {secret}",
                              what="test report")
        self.assertNotIn(secret.lower(), buf.getvalue().lower())

    def test_an_unconfigured_relay_is_a_state_and_never_an_exception(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RESEND_API_KEY", None)
            self.assertFalse(ops_notify.notify("s", "b", what="test report"))

    def test_a_raising_transport_does_not_take_the_caller_down(self):
        """Every caller is a reporting tail on a job that already did its real
        work. A notifier that raises while handling somebody else's failure has
        told nobody anything and reddened a run for it."""
        import ci_alert

        def boom(*a, **k):
            raise RuntimeError("relay exploded")

        with mock.patch.dict(os.environ, {"RESEND_API_KEY": "k"}), \
                mock.patch.object(ci_alert, "post_alert", boom):
            self.assertFalse(ops_notify.notify("s", "b", what="test report"))


class EveryJobThatMailsCarriesTheKeyThatLetsItMail(unittest.TestCase):
    """A ported caller with no `RESEND_API_KEY` in its workflow is silent.

    This is the specific way this change could go wrong and be invisible: the
    code is right, the run is green, and the email simply never arrives, because
    the job's env still lists the WordPress credential the old route needed.
    `configured()` returns False, the job prints one line nobody reads, and
    exits 0.
    """

    #: script name -> workflow file that runs it.
    JOBS = {
        "ci_alert.py": "ci-alert.yml",
        "ci_noise_report.py": "ci-noise-report.yml",
        "health_digest.py": "health-digest.yml",
        "run_benchmark_diff.py": "benchmark-diff.yml",
        "train_gate_classifier.py": "gate-classifier.yml",
        "host_watch.py": "host-watch.yml",
    }

    def test_each_workflow_passes_the_resend_key(self):
        missing = []
        for script, workflow in self.JOBS.items():
            path = WORKFLOWS / workflow
            if not path.exists():
                missing.append(f"{workflow} (no such workflow)")
                continue
            text = path.read_text()
            if script not in text:
                missing.append(f"{workflow} no longer runs {script}")
            elif "RESEND_API_KEY" not in text:
                missing.append(f"{workflow} runs {script} but passes no "
                               "RESEND_API_KEY, so its mail is silent")
        self.assertEqual(missing, [], missing)

    def test_the_sender_override_travels_with_the_key(self):
        """`OPS_MAIL_FROM` goes wherever `RESEND_API_KEY` goes.

        The variable is unset today, so `opsmail`'s default applies everywhere
        and nothing is visibly wrong. THAT IS THE TRAP. The day somebody sets
        that variable to move the sender, the jobs that pass it would move and
        the jobs that do not would keep the old From. Two From lines is exactly
        the defect this whole change exists to close, arriving later and by a
        different route.
        """
        missing = []
        for path in sorted(WORKFLOWS.glob("*.yml")):
            text = path.read_text()
            if "RESEND_API_KEY" in text and "OPS_MAIL_FROM" not in text:
                missing.append(path.name)
        self.assertEqual(missing, [], missing)

    def test_the_claim_is_committed_where_alarms_are_actually_raised(self):
        """`ALERT_STATE_COMMIT` is what makes `git push` the compare-and-swap.

        Without it the ledger is written to the runner's disk and thrown away
        with the runner, so two concurrent runners both read "nothing is open"
        and both mail. Dedup would then be a property of a single runner, which
        is not dedup.
        """
        for workflow in ("ci-alert.yml", "ci-noise-report.yml"):
            text = (WORKFLOWS / workflow).read_text()
            self.assertIn("ALERT_STATE_COMMIT", text, workflow)


if __name__ == "__main__":
    unittest.main()
