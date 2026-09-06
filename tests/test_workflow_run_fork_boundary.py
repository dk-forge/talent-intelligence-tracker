"""A workflow_run listener that holds secrets admits runs from THIS repository only.

`ci-alert.yml` listens to every workflow completing (`workflows: ['*']`), and
that includes a fork's pull_request run of tests. Its job holds WP_API_KEY,
RESEND_API_KEY and a contents:write token, and until 2026-09-06 it spliced the
run's workflow NAME and HEAD BRANCH straight into a shell line as `${{ }}`
text. Both are the fork author's to choose (a branch name may hold `$(...)`;
the name is the `name:` field of the fork's own copy of the workflow).
`self-heal.yml` had the same gap behind a `head_branch == 'main'` check that a
fork's own main satisfies, after which an agent ran with write tokens over a
log the fork author wrote. Found by the 2026-09-05 security review of the
sibling layoff tracker (its PRs #266 and #267), which shares both files.
"""
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
WORKFLOWS = os.path.join(HERE, "..", ".github", "workflows")
SAME_REPO = "github.event.workflow_run.head_repository.full_name == github.repository"
# Anywhere inside one ${{ }} expression, so `${{ x.name || 'manual proof' }}`
# (the shape main's ci-alert.yml actually carried) is caught, not only the bare
# `${{ x.name }}` form.
UNTRUSTED_IN_RUN = re.compile(
    r"\$\{\{[^}]*\bgithub\.event\.workflow_run\.(name|head_branch|display_title)\b[^}]*\}\}")


def _read(name):
    with open(os.path.join(WORKFLOWS, name), encoding="utf-8") as fh:
        return fh.read()


def _run_blocks(yml):
    """The text of every `run:` block (until the next key at a shallower indent)."""
    blocks, lines = [], yml.splitlines()
    i = 0
    while i < len(lines):
        m = re.match(r"^(\s*)run:\s*\|?\s*$", lines[i])
        if not m:
            i += 1
            continue
        indent = len(m.group(1))
        j = i + 1
        while j < len(lines) and (not lines[j].strip() or len(lines[j]) - len(lines[j].lstrip()) > indent):
            j += 1
        blocks.append("\n".join(lines[i:j]))
        i = j
    return blocks


class ListenersWithSecretsRefuseForks(unittest.TestCase):
    def test_ci_alert_admits_only_this_repository(self):
        self.assertIn(SAME_REPO, _read("ci-alert.yml"))

    def test_self_heal_admits_only_this_repository(self):
        self.assertIn(SAME_REPO, _read("self-heal.yml"))

    def test_no_untrusted_workflow_run_field_is_spliced_into_a_shell(self):
        seen = 0
        for name in sorted(os.listdir(WORKFLOWS)):
            if not name.endswith(".yml"):
                continue
            yml = _read(name)
            if "workflow_run:" not in yml:
                continue
            seen += 1
            for block in _run_blocks(yml):
                self.assertFalse(
                    UNTRUSTED_IN_RUN.search(block),
                    f"{name}: a workflow_run name or branch is interpolated into a run: "
                    "block; pass it through env: and quote it")
        self.assertGreaterEqual(seen, 2, "expected ci-alert.yml and self-heal.yml to listen on workflow_run")

    def test_the_scanner_catches_the_original_line(self):
        block = ('        run: |\n'
                 '          python3 ci_alert.py \\\n'
                 '            --workflow "${{ github.event.workflow_run.name }}"\n'
                 '      - name: next\n')
        self.assertTrue(UNTRUSTED_IN_RUN.search(_run_blocks(block)[0]))
        fallback = block.replace("workflow_run.name }}", "workflow_run.name || 'manual proof' }}")
        self.assertTrue(UNTRUSTED_IN_RUN.search(_run_blocks(fallback)[0]))
        harmless = block.replace("workflow_run.name", "workflow_run.id")
        self.assertFalse(UNTRUSTED_IN_RUN.search(_run_blocks(harmless)[0]))
        env_only = ('        env:\n'
                    '          RUN_WORKFLOW: ${{ github.event.workflow_run.name }}\n'
                    '        run: |\n'
                    '          python3 ci_alert.py --workflow "$RUN_WORKFLOW"\n')
        self.assertFalse(any(UNTRUSTED_IN_RUN.search(b) for b in _run_blocks(env_only)))


if __name__ == "__main__":
    unittest.main()
