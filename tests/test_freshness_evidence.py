"""
Tests for the freshness evidence model (guardian.core.freshness).

These prove the honesty rules the mission requires:
  * loading persisted state does NOT establish live freshness;
  * session identity mismatch denies replay;
  * missing / stale evidence denies replay;
  * a possible prior tool execution invalidates unsafe replay;
  * a reconnect does not restore freshness without adequate evidence;
  * a capability declaration alone does not authorize replay;
  * only protocol-appropriate, corroborated evidence authorizes replay.
"""

import pytest

from guardian.core.freshness import (
    AdapterFreshnessReport, FreshnessStatus, apply_freshness, evaluate_freshness,
)
from guardian.core.session_state import create_session
from guardian.core.types import TurnContext, TurnPhase


def _observed(**kw):
    ctx = TurnContext(session_id=kw.pop("session_id", "s1"), phase=kw.pop("phase", TurnPhase.USER_MESSAGE_SENT))
    ctx.last_event_timestamp = kw.pop("last_event_timestamp", 100.0)
    for k, v in kw.items():
        setattr(ctx, k, v)
    return ctx


def test_unknown_when_no_evidence():
    ev = evaluate_freshness(live_session_id="s1")
    assert ev.status is FreshnessStatus.UNKNOWN
    assert ev.authorizes_replay is False


def test_historical_only_from_persisted_snapshot():
    persisted = create_session("s1", "fake")
    ev = evaluate_freshness(live_session_id="s1", persisted=persisted)
    assert ev.status is FreshnessStatus.HISTORICAL_ONLY
    assert ev.authorizes_replay is False


def test_observed_without_corroboration_is_not_fresh():
    ev = evaluate_freshness(live_session_id="s1", observed=_observed())
    assert ev.status is FreshnessStatus.OBSERVED
    assert ev.authorizes_replay is False


def test_reconnect_observation_does_not_restore_freshness():
    # A freshly observed stream (as after a reconnect) still lacks independent
    # corroboration, so it must not authorize replay.
    ev = evaluate_freshness(live_session_id="s1", observed=_observed(last_event_timestamp=999.0))
    assert ev.status is FreshnessStatus.OBSERVED
    assert ev.authorizes_replay is False


def test_identity_mismatch_invalidates():
    ev = evaluate_freshness(
        live_session_id="s1", observed=_observed(session_id="s2"), persisted=create_session("s1", "fake")
    )
    assert ev.status is FreshnessStatus.INVALIDATED
    assert ev.authorizes_replay is False


def test_possible_tool_execution_invalidates():
    ev = evaluate_freshness(live_session_id="s1", observed=_observed(tool_calls_started=1))
    assert ev.status is FreshnessStatus.INVALIDATED
    assert ev.authorizes_replay is False


def test_tool_phase_invalidates():
    ev = evaluate_freshness(live_session_id="s1", observed=_observed(phase=TurnPhase.TOOL_EXECUTING))
    assert ev.status is FreshnessStatus.INVALIDATED


@pytest.mark.parametrize("report,expected", [
    (AdapterFreshnessReport(session_id="s1", live=False, live_marker=200.0, tool_execution_uncertain=False),
     FreshnessStatus.INVALIDATED),
    (AdapterFreshnessReport(session_id="s1", live=True, live_marker=200.0, tool_execution_uncertain=True),
     FreshnessStatus.INVALIDATED),
    (AdapterFreshnessReport(session_id="s1", live=True, live_marker=None, tool_execution_uncertain=False),
     FreshnessStatus.INVALIDATED),
    (AdapterFreshnessReport(session_id="s1", live=True, live_marker=50.0, tool_execution_uncertain=False),
     FreshnessStatus.INVALIDATED),  # marker older than observed
])
def test_inadequate_adapter_reports_do_not_authorize(report, expected):
    ev = evaluate_freshness(live_session_id="s1", observed=_observed(), report=report)
    assert ev.status is expected
    assert ev.authorizes_replay is False


def test_corroborated_evidence_authorizes_replay():
    report = AdapterFreshnessReport(
        session_id="s1", live=True, live_marker=200.0,
        tool_execution_uncertain=False, source="acp.listSessions",
    )
    ev = evaluate_freshness(live_session_id="s1", observed=_observed(last_event_timestamp=150.0), report=report)
    assert ev.status is FreshnessStatus.CORROBORATED
    assert ev.authorizes_replay is True
    assert ev.corroborating_source == "acp.listSessions"


def test_apply_freshness_sets_verified_fresh():
    ctx = _observed()
    apply_freshness(ctx, evaluate_freshness(live_session_id="s1", observed=ctx))
    assert ctx.verified_fresh is False
    assert ctx.freshness_evidence.status is FreshnessStatus.OBSERVED

    report = AdapterFreshnessReport(session_id="s1", live=True, live_marker=1e9,
                                    tool_execution_uncertain=False, source="test")
    apply_freshness(ctx, evaluate_freshness(live_session_id="s1", observed=ctx, report=report))
    assert ctx.verified_fresh is True


def test_freshness_evidence_serializes_without_secrets():
    ev = evaluate_freshness(live_session_id="s1", observed=_observed())
    d = ev.to_dict()
    assert set(d) == {"status", "reason", "session_id", "observed_at", "snapshot_at",
                      "sources", "corroborating_source"}


# --------------------------------------------------------------------------
# Adapter corroboration integration point
# --------------------------------------------------------------------------

class _NoEvidenceAdapter:
    """Default contract: cannot corroborate -> replay stays blocked."""

    async def provide_freshness_report(self, session_id, observed=None):
        return None


class _GoodAdapter:
    async def provide_freshness_report(self, session_id, observed=None):
        return AdapterFreshnessReport(session_id=session_id, live=True, live_marker=1e9,
                                      tool_execution_uncertain=False, source="fake.protocol")


class _RaisingAdapter:
    async def provide_freshness_report(self, session_id, observed=None):
        raise RuntimeError("protocol error")


class _MalformedAdapter:
    async def provide_freshness_report(self, session_id, observed=None):
        return {"live": True}  # not an AdapterFreshnessReport


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter,expected", [
    (_NoEvidenceAdapter(), FreshnessStatus.OBSERVED),
    (_RaisingAdapter(), FreshnessStatus.OBSERVED),
    (_MalformedAdapter(), FreshnessStatus.OBSERVED),
])
async def test_corroborate_fails_closed(adapter, expected):
    from guardian.core.freshness import corroborate_freshness

    ev = await corroborate_freshness(adapter, live_session_id="s1", observed=_observed())
    assert ev.status is expected
    assert ev.authorizes_replay is False


@pytest.mark.asyncio
async def test_corroborate_authorizes_with_protocol_evidence():
    from guardian.core.freshness import corroborate_freshness

    ev = await corroborate_freshness(_GoodAdapter(), live_session_id="s1", observed=_observed())
    assert ev.status is FreshnessStatus.CORROBORATED
    assert ev.authorizes_replay is True
