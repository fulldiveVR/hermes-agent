"""Regression tests for the lossy ``gateway run --replace`` restart path.

Production root cause A1 (vm210 / roomcord): a new gateway started with
``--replace`` SIGTERMs the old gateway and then waited only a hardcoded
~10s before escalating to SIGKILL — but the old gateway's graceful drain
is provisioned for ``restart_drain_timeout`` (60s by default).  A reply
that takes longer than 10s to flush was hard-killed mid-generation and,
because the outbound reply is a single in-memory POST, permanently lost.

These tests pin the corrected behaviour:

1. The incoming ``--replace`` gateway grants the old gateway up to its full
   ``restart_drain_timeout`` (plus a little headroom, capped) to exit
   gracefully before SIGKILL — not a fixed ~10s.

2. Slow shutdown-notify sends cannot starve the ``mark_resume_pending``
   step: even if the notification broadcast hangs, ``stop()`` still flags
   the to-be-interrupted in-flight session as ``resume_pending`` so it is
   recoverable on the next start.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

import gateway.run as gateway_run
from gateway.restart import DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT
from tests.gateway.restart_test_helpers import make_restart_runner


# ---------------------------------------------------------------------------
# 1. Incoming --replace kill-wait budget is aligned with restart_drain_timeout
# ---------------------------------------------------------------------------


def test_replace_kill_wait_matches_drain_timeout(monkeypatch):
    """The wait-for-old-gateway-to-exit budget must cover the old gateway's
    full drain window, not a hardcoded ~10s.  An in-flight turn lasting
    between 10s and ``restart_drain_timeout`` must be allowed to flush."""
    monkeypatch.setenv("HERMES_RESTART_DRAIN_TIMEOUT", "60")

    budget = gateway_run._replace_kill_wait_seconds()

    # Must at least cover the full drain window ...
    assert budget >= 60.0
    # ... and be materially larger than the old hardcoded 10s.
    assert budget > 10.0


def test_replace_kill_wait_scales_with_drain_timeout(monkeypatch):
    """A larger configured drain window yields a larger kill-wait budget."""
    monkeypatch.setenv("HERMES_RESTART_DRAIN_TIMEOUT", "30")
    small = gateway_run._replace_kill_wait_seconds()

    monkeypatch.setenv("HERMES_RESTART_DRAIN_TIMEOUT", "90")
    large = gateway_run._replace_kill_wait_seconds()

    assert small >= 30.0
    assert large >= 90.0
    assert large > small


def test_replace_kill_wait_has_sane_cap(monkeypatch):
    """A pathological drain timeout must not make the incoming gateway wait
    forever — there is a hard upper bound."""
    monkeypatch.setenv("HERMES_RESTART_DRAIN_TIMEOUT", "1000000")
    budget = gateway_run._replace_kill_wait_seconds()
    assert budget <= gateway_run._REPLACE_KILL_WAIT_CAP
    assert budget < 1000000.0


def test_replace_kill_wait_default_covers_drain(monkeypatch):
    """With no override, the budget still covers the shipped default drain."""
    monkeypatch.delenv("HERMES_RESTART_DRAIN_TIMEOUT", raising=False)
    budget = gateway_run._replace_kill_wait_seconds()
    assert budget >= DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT


# ---------------------------------------------------------------------------
# 2. Slow shutdown-notify cannot starve the resume_pending marker write
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slow_notify_does_not_starve_resume_pending_marker():
    """If the pre-drain shutdown-notify broadcast hangs, ``stop()`` must
    still reach ``mark_resume_pending`` for the in-flight session (so the
    to-be-killed reply is recoverable) and must not block indefinitely."""
    runner, adapter = make_restart_runner()
    runner._restart_drain_timeout = 0.05

    session_key = "agent:main:telegram:dm:A"
    running_agent = MagicMock()
    # Let the forced interrupt clear the agent so stop()'s post-interrupt
    # settle loop exits promptly (keeps the test fast + deterministic).
    running_agent.interrupt.side_effect = lambda *_a, **_k: runner._running_agents.clear()
    runner._running_agents = {session_key: running_agent}

    session_store = MagicMock()
    session_store.mark_resume_pending = MagicMock(return_value=True)
    runner.session_store = session_store

    # Simulate a pathologically slow / wedged notification broadcast.
    async def _hang_notify():
        await asyncio.sleep(30)

    runner._notify_active_sessions_of_shutdown = _hang_notify

    with patch("gateway.status.remove_pid_file"), patch(
        "gateway.status.write_runtime_status"
    ):
        # If notify is unbounded, stop() blocks ~30s and this times out.
        await asyncio.wait_for(runner.stop(), timeout=5)

    calls = session_store.mark_resume_pending.call_args_list
    marked = {args[0][0] for args in calls}
    assert marked == {session_key}
