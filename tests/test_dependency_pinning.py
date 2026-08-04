"""No workflow installs a package this repo has not pinned by hash.

WHY. Every requirement here was a FLOOR (`requests>=2.32.0`) with no lockfile,
and a scheduled job resolved it fresh at run time. Twenty-odd workflows went
further and ran a bare `pip install requests` that named no file at all. Those
runners hold `TIT_API_KEY` and `OPENROUTER_API_KEY`, they run unattended twice a
day, and nobody reviews what a resolver picked. One malicious release of any
transitive dependency, and it executes with both keys, with no human in the
loop and nothing in any log that would look wrong.

So: `requirements.txt` stays the human-edited input, `requirements.lock` is the
resolved, fully hash-pinned output, and `--require-hashes` makes pip refuse
anything the lock did not vouch for, transitively. `requirements-dev.lock` is
the same thing plus pytest and scikit-learn, so a twice-daily collect run does
not pull a model-training stack, which is exactly why `tests.yml` and
`gate-classifier.yml` were appending packages to a bare install line.

REGENERATING (the ritual, also in CLAUDE.md):

    python3 -m venv /tmp/lock && /tmp/lock/bin/pip install pip-tools
    /tmp/lock/bin/pip-compile --generate-hashes --strip-extras \\
        --output-file=requirements.lock requirements.txt
    /tmp/lock/bin/pip-compile --generate-hashes --strip-extras \\
        --output-file=requirements-dev.lock requirements-dev.txt

Then READ THE DIFF. A lock refresh that nobody read is the unpinned state with
extra steps.
"""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAILWAY = ROOT
WORKFLOWS = ROOT / ".github" / "workflows"

LOCKS = ("requirements.lock", "requirements-dev.lock")


def workflow_files():
    return sorted(WORKFLOWS.glob("*.yml"))


class EveryLockIsFullyHashed(unittest.TestCase):
    def test_the_locks_exist(self):
        for name in LOCKS:
            with self.subTest(lock=name):
                self.assertTrue((RAILWAY / name).is_file(),
                                f"{name} is missing")

    def test_every_pinned_line_is_an_exact_version_with_hashes(self):
        for name in LOCKS:
            src = (RAILWAY / name).read_text()
            pins = re.findall(r"^([A-Za-z0-9_.\-]+)==([^\s\\]+)", src, re.M)
            self.assertTrue(pins, f"{name} pins nothing")
            for package, _version in pins:
                with self.subTest(lock=name, package=package):
                    block = src[src.index(f"{package}=="):]
                    block = block[:block.index("\n#") if "\n#" in block else len(block)]
                    self.assertIn("--hash=sha256:", block,
                                  f"{package} in {name} is pinned but not hashed")

    def test_no_floor_or_range_survived_into_a_lock(self):
        for name in LOCKS:
            for line in (RAILWAY / name).read_text().splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith(("#", "--hash")):
                    continue
                with self.subTest(lock=name, line=stripped):
                    self.assertNotRegex(stripped, r"(>=|<=|~=|>|<)",
                                        "a lock holds exact versions only")

    def test_the_two_locks_never_disagree_about_a_shared_package(self):
        """The dev lock is the runtime lock PLUS test/training packages.

        Two locks that pin different versions of `requests` means a job's
        behaviour depends on which lock its workflow happened to name, which is
        a worse failure than being unpinned because it looks deliberate.
        """
        def pins(name):
            return dict(re.findall(r"^([A-Za-z0-9_.\-]+)==([^\s\\]+)",
                                   (RAILWAY / name).read_text(), re.M))
        full, minimal = pins("requirements.lock"), pins("requirements-dev.lock")
        for package, version in minimal.items():
            if package in full:
                with self.subTest(package=package):
                    self.assertEqual(version, full[package],
                                     f"{package} is pinned two ways")


class NoWorkflowInstallsAnythingUnpinned(unittest.TestCase):
    def test_no_bare_pip_install_survives(self):
        """`pip install requests` in a runner holding two API keys."""
        for path in workflow_files():
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if "pip install" not in line or line.lstrip().startswith("#"):
                    continue
                with self.subTest(workflow=path.name, line=number):
                    self.assertIn("--require-hashes", line,
                                  f"{path.name}:{number} installs without "
                                  f"hash verification: {line.strip()}")
                    self.assertRegex(line, r"-r \S*requirements[\w.-]*\.lock",
                                     f"{path.name}:{number} does not install "
                                     f"from a lockfile")

    def test_no_workflow_installs_the_unlocked_requirements_txt(self):
        for path in workflow_files():
            src = path.read_text()
            with self.subTest(workflow=path.name):
                self.assertNotRegex(
                    src, r"pip install[^\n]*-r \S*requirements(-min)?\.txt",
                    "requirements.txt is the INPUT to the lock, never an "
                    "install target: it is a set of floors")

    def test_nothing_upgrades_pip_itself_from_pypi_unpinned(self):
        """`pip install --upgrade pip` is an unverified download into the same
        runner, immediately before the verified one. It defeats the point."""
        for path in workflow_files():
            with self.subTest(workflow=path.name):
                self.assertNotRegex(path.read_text(),
                                    r"pip install --upgrade pip")

    def test_the_lock_paths_the_workflows_name_actually_exist(self):
        """A typo here is a run that dies at the install step, unattended."""
        for path in workflow_files():
            for line in path.read_text().splitlines():
                if "--require-hashes" not in line:
                    continue
                match = re.search(r"-r (\S+)", line)
                self.assertIsNotNone(match, line)
                named = match.group(1).replace("$GITHUB_WORKSPACE/", "")
                with self.subTest(workflow=path.name, lock=named):
                    self.assertTrue((ROOT / named).is_file(),
                                    f"{path.name} installs {named}, which is "
                                    f"not in the repository")


class TheInputsStayHumanEdited(unittest.TestCase):
    def test_every_package_named_in_an_input_is_present_in_its_lock(self):
        """A package added to requirements.txt and never re-locked would
        silently not be installed, and the failure would be an ImportError in a
        scheduled job at 2am rather than here."""
        pairs = [("requirements.txt", "requirements.lock"),
                 ("requirements-dev.txt", "requirements-dev.lock")]
        for source, lock in pairs:
            locked = (RAILWAY / lock).read_text().lower()
            for line in (RAILWAY / source).read_text().splitlines():
                line = line.strip()
                if not line or line.startswith(("#", "-r ", "-c ")):
                    continue
                package = re.split(r"[<>=\[]", line)[0].strip().lower()
                with self.subTest(source=source, package=package):
                    self.assertRegex(
                        locked, rf"(?m)^{re.escape(package)}(\[|==)",
                        f"{package} is in {source} but not in {lock}; "
                        f"re-run pip-compile")


if __name__ == "__main__":
    unittest.main()
