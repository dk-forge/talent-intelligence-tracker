"""A subset run must not be able to disagree with a full run.

CLAUDE.md, "Test gotcha": never stub a real module into `sys.modules` — the
fake persists and shadows the real module for every test loaded afterwards, so
tests pass alone and fail in the suite. On 2026-08-14 the same defect arrived
the other way round. Three files carried

    sys.modules.setdefault("requests", types.ModuleType("requests"))

which is a no-op whenever an earlier file already imported the real library.
In `pytest -q` one always does, so the suite was green. Hand pytest four files
and the setdefault WINS: the bare ModuleType has no `RequestException`, and
every module that subclasses it at import time (`collectors/national_press.py`)
blows up. 35 failures in a subset, zero in the full run, and nothing about the
change under review had caused any of it.

Two guards, because either alone is escapable:

* the structural one reads the test tree and refuses a module-scope mutation of
  `sys.modules`, which is the only kind that can outlive the file doing it;
* the runtime one asserts the loaded third-party modules are the real ones, so
  a stub installed some way this parser does not model is still caught — in
  whatever selection of files happens to be running.

Function-local stubbing with a try/finally is untouched by both: it is scoped,
and `tests/test_identity.py` legitimately does it to prove a missing library is
a blank column rather than a crash.
"""

from __future__ import annotations

import ast
import sys
from importlib.machinery import PathFinder
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent

# Third-party modules the suite imports, which a stub could plausibly shadow.
# A name absent from the environment is skipped, not failed: these files are
# also run by plain unittest on a machine with no dependencies installed.
REAL_MODULES = {
    "requests": ("RequestException", "Session", "get"),
}


_SCOPED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _module_level_statements(tree: ast.Module):
    """Every node that executes on import, and not one that does not.

    Descends through `if`/`try`/`with`/loops, and stops dead at a function or
    class definition: a body that only runs when something calls it is scoped
    by construction, and `tests/test_identity.py` rightly pops `requests` in a
    try/finally to prove a missing library is a blank column rather than a
    crash.
    """
    stack = list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, _SCOPED):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _is_sys_modules(node: ast.expr) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == "modules"
            and isinstance(node.value, ast.Name) and node.value.id == "sys")


def _mutations(tree: ast.Module) -> list[tuple[int, str]]:
    found = []
    for sub in _module_level_statements(tree):
        if isinstance(sub, ast.Assign):
            for target in sub.targets:
                if isinstance(target, ast.Subscript) and _is_sys_modules(target.value):
                    found.append((sub.lineno, "sys.modules[...] = ..."))
        elif isinstance(sub, ast.Call):
            func = sub.func
            if (isinstance(func, ast.Attribute)
                    and func.attr in {"setdefault", "pop", "update", "clear",
                                      "__setitem__", "__delitem__"}
                    and _is_sys_modules(func.value)):
                found.append((sub.lineno, f"sys.modules.{func.attr}(...)"))
        elif isinstance(sub, ast.Delete):
            for target in sub.targets:
                if isinstance(target, ast.Subscript) and _is_sys_modules(target.value):
                    found.append((sub.lineno, "del sys.modules[...]"))
    return sorted(set(found))


TEST_FILES = sorted(p for p in TESTS.glob("test_*.py"))


@pytest.mark.parametrize("path", TEST_FILES, ids=lambda p: p.name)
def test_no_test_file_mutates_sys_modules_at_import_time(path: Path):
    """Import-time `sys.modules` surgery outlives the file that wrote it.

    Whether it takes effect depends on collection order, so it makes the same
    commit pass or fail according to which files were selected. Use
    `tests/requests_stub.py` (real library first) or `monkeypatch.setattr` on
    the attribute you actually need to change.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offences = _mutations(tree)
    assert not offences, (
        f"{path.name} mutates sys.modules at module scope: "
        + "; ".join(f"line {line}: {what}" for line, what in offences)
        + " — see tests/requests_stub.py"
    )


@pytest.mark.parametrize("name", sorted(REAL_MODULES))
def test_the_loaded_module_is_the_real_one(name: str):
    """Belt to the parser's braces: whatever is loaded must be the library."""
    # PathFinder searches sys.path and ignores sys.modules, so a stub cannot
    # answer this question on the real library's behalf.
    if PathFinder().find_spec(name) is None:
        pytest.skip(f"{name} is not installed in this environment")
    module = sys.modules.get(name)
    if module is None:
        pytest.skip(f"{name} has not been imported by this selection of tests")
    missing = [attr for attr in REAL_MODULES[name] if not hasattr(module, attr)]
    assert not missing, (
        f"the {name!r} in sys.modules is a stub (missing {missing}) while the "
        f"real library is installed — some test file shadowed it, and every "
        f"module imported after it sees the fake"
    )
