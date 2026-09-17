"""Sentry initialisation for the collector entry point.

A no-op unless SENTRY_DSN is set, so a checkout or a workflow with no Sentry
secret behaves exactly as it did before this module existed. Never raises -
an SDK import or init failure must not take a collect run down with it.

No PII, no tracing by default: this pipeline stores public company/hiring
data, not personal data, but request bodies and local variables can still
leak into breadcrumbs, so `send_default_pii` stays False and
`traces_sample_rate` stays 0.0.
"""
from __future__ import annotations

import os

_initialised = False


def init_sentry(component: str | None = None) -> bool:
    """Initialise Sentry once per process if SENTRY_DSN is set. Returns True
    if it initialised (or already had), False if it's a no-op. Never raises."""
    global _initialised
    if _initialised:
        return True
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return False
    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=dsn,
            environment="production",
            send_default_pii=False,
            traces_sample_rate=0.0,
        )
        if component:
            sentry_sdk.set_tag("component", component)
        _initialised = True
        return True
    except Exception:  # noqa: BLE001 - Sentry must never break the job it watches
        return False
