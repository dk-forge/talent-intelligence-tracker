"""Offline coverage for the collector's optional telemetry startup."""
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import sentry_init


@pytest.fixture
def startup(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.setattr(sentry_init, "_initialised", False)
    return sentry_init.init_sentry


def test_no_dsn_does_not_import_or_initialise_sdk(startup, monkeypatch):
    sdk = SimpleNamespace(init=Mock(), set_tag=Mock())
    monkeypatch.setitem(sys.modules, "sentry_sdk", sdk)
    assert startup("fixture") is False
    sdk.init.assert_not_called()


def test_initialises_once_without_pii_or_tracing(startup, monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://fixture@example.invalid/1")
    sdk = SimpleNamespace(init=Mock(), set_tag=Mock())
    monkeypatch.setitem(sys.modules, "sentry_sdk", sdk)
    assert startup("collector") is True
    assert startup("second") is True
    sdk.init.assert_called_once_with(dsn="https://fixture@example.invalid/1", environment="production", send_default_pii=False, traces_sample_rate=0.0)
    sdk.set_tag.assert_called_once_with("component", "collector")


@pytest.mark.parametrize("failure", ["import", "init", "tag"])
def test_sdk_failures_never_break_collection(startup, monkeypatch, failure):
    monkeypatch.setenv("SENTRY_DSN", "https://fixture@example.invalid/1")
    sdk = SimpleNamespace(init=Mock(), set_tag=Mock())
    if failure != "import":
        getattr(sdk, "init" if failure == "init" else "set_tag").side_effect = RuntimeError("fixture failure")
    monkeypatch.setitem(sys.modules, "sentry_sdk", None if failure == "import" else sdk)
    assert startup("collector") is False
    assert sentry_init._initialised is False
