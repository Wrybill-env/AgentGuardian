"""
Freshness evidence model for Agent Guardian.

`verified_fresh` is the ONLY signal that authorizes *replay* recovery actions
(WAIT_AND_RETRY / CANCEL_AND_REPLAY). This module defines exactly what evidence
is required to set it, and — importantly — what evidence is NOT sufficient.

Key principle (fail closed):

    Persisted state is NOT fresh state.

A saved snapshot proves only that *some* state existed when it was written. It
does not prove the agent is still at that state, that no tool ran in between,
or that the session identity is unchanged. Therefore a persisted snapshot alone
can never authorize replay.

Evidence ladder (weakest to strongest):

  UNKNOWN          - no evidence at all.
  HISTORICAL_ONLY  - a persisted snapshot exists, nothing live corroborates it.
  OBSERVED         - Guardian has a live local observation, but no independent
                     corroboration from the agent's own protocol.
  INVALIDATED      - evidence contradicts itself (identity mismatch, a tool may
                     have executed, marker stale/inconsistent).
  CORROBORATED     - an adapter supplied protocol-appropriate evidence that
                     independently confirms continuity of the *live* session.

Only CORROBORATED authorizes replay.

What a future adapter MUST provide (see :class:`AdapterFreshnessReport`):
  * the session id it is reporting about, matching the live session;
  * confirmation the session currently exists (``live=True``);
  * an explicit statement that no tool execution outcome is uncertain;
  * a monotonic live marker (e.g. protocol ``updatedAt`` timestamp or event
    sequence number) that is >= the marker of Guardian's local observation.

If an adapter cannot supply these, replay stays blocked. Do not invent
sequence numbers or protocol guarantees the agent does not actually provide.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional


class FreshnessStatus(Enum):
    """Strength/validity of the available freshness evidence."""

    UNKNOWN = "unknown"
    HISTORICAL_ONLY = "historical_only"
    OBSERVED = "observed"
    INVALIDATED = "invalidated"
    CORROBORATED = "corroborated"


@dataclass
class AdapterFreshnessReport:
    """
    Evidence an adapter supplies about the *live* agent session.

    This is the interface a future adapter implements (via
    ``AgentAdapter.provide_freshness_report``) using a protocol-appropriate
    mechanism. The defaults are deliberately conservative: an adapter that
    constructs this without setting the fields cannot accidentally authorize
    replay.
    """

    session_id: str
    # Agent's own protocol confirms the session currently exists.
    live: bool = False
    # Monotonic marker of the session as observed live (updatedAt / sequence no.).
    live_marker: Optional[float] = None
    # Marker that Guardian's local observation corresponds to.
    observed_marker: Optional[float] = None
    # True unless the adapter can affirm no tool-execution outcome is uncertain.
    tool_execution_uncertain: bool = True
    # Human-readable provenance, e.g. "acp.listSessions", for diagnostics.
    source: str = "adapter"


@dataclass
class FreshnessEvidence:
    """Evaluated freshness verdict with its supporting provenance."""

    status: FreshnessStatus
    reason: str
    session_id: Optional[str] = None
    observed_at: Optional[float] = None
    snapshot_at: Optional[float] = None
    sources: List[str] = field(default_factory=list)
    corroborating_source: Optional[str] = None

    @property
    def authorizes_replay(self) -> bool:
        """Replay is authorized ONLY by independently corroborated freshness."""
        return self.status is FreshnessStatus.CORROBORATED

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "reason": self.reason,
            "session_id": self.session_id,
            "observed_at": self.observed_at,
            "snapshot_at": self.snapshot_at,
            "sources": list(self.sources),
            "corroborating_source": self.corroborating_source,
        }


# Turn phases from which replay can never be safe (side effects may have occurred).
_UNREPLAYABLE_PHASES = {
    "tool_executing",
    "tool_completing",
    "turn_ending",
    "turn_complete",
    "turn_error",
    "turn_cancelled",
}


def _observed_tool_risk(observed: Any) -> bool:
    """True if the observed context shows a tool that may have executed."""
    if observed is None:
        return False
    if getattr(observed, "tool_calls_started", 0):
        return True
    if getattr(observed, "_seen_tool_call_ids", None):
        return True
    phase = getattr(observed, "phase", None)
    phase_value = getattr(phase, "value", phase)
    if phase_value in _UNREPLAYABLE_PHASES:
        return True
    return False


def evaluate_freshness(
    *,
    live_session_id: Optional[str],
    observed: Optional[Any] = None,
    persisted: Optional[Any] = None,
    report: Optional[AdapterFreshnessReport] = None,
) -> FreshnessEvidence:
    """
    Decide whether replay is authorized, from all available evidence.

    Fails closed: anything ambiguous, missing, contradictory, or merely
    historical yields a non-CORROBORATED status.
    """
    sources: List[str] = []
    if persisted is not None:
        sources.append("persistence")
    if observed is not None:
        sources.append("live_observation")

    # --- Rule 1: identity consistency (any disagreement invalidates) ---
    ids = set()
    if live_session_id:
        ids.add(live_session_id)
    for src in (observed, persisted):
        sid = getattr(src, "session_id", None) if src is not None else None
        if sid:
            ids.add(sid)
    if report is not None and report.session_id:
        ids.add(report.session_id)
    if len(ids) > 1:
        return FreshnessEvidence(
            status=FreshnessStatus.INVALIDATED,
            reason=f"Session identity mismatch across evidence sources: {sorted(ids)}",
            session_id=live_session_id,
            sources=sources,
        )

    session_id = next(iter(ids), None)

    # --- Rule 2: a possible prior tool execution voids replay ---
    if _observed_tool_risk(observed):
        return FreshnessEvidence(
            status=FreshnessStatus.INVALIDATED,
            reason="Observed state shows a tool that may have executed; replay is unsafe",
            session_id=session_id,
            observed_at=getattr(observed, "last_event_timestamp", None),
            sources=sources,
        )

    # --- Rule 3: no corroboration => never fresh ---
    if report is None:
        if observed is None and persisted is None:
            return FreshnessEvidence(
                status=FreshnessStatus.UNKNOWN,
                reason="No persistence, observation, or corroboration available",
                session_id=session_id,
            )
        if observed is None:
            return FreshnessEvidence(
                status=FreshnessStatus.HISTORICAL_ONLY,
                reason="Only a persisted snapshot exists; no live corroboration",
                session_id=session_id,
                snapshot_at=getattr(persisted, "last_activity", None),
                sources=sources,
            )
        return FreshnessEvidence(
            status=FreshnessStatus.OBSERVED,
            reason="Live observation present but not independently corroborated",
            session_id=session_id,
            observed_at=getattr(observed, "last_event_timestamp", None),
            sources=sources,
        )

    # --- Rule 4: validate the adapter's corroboration claim ---
    if not report.live:
        return FreshnessEvidence(
            status=FreshnessStatus.INVALIDATED,
            reason="Adapter did not confirm the session is currently live",
            session_id=session_id,
            sources=sources + ["adapter"],
        )
    if report.tool_execution_uncertain:
        return FreshnessEvidence(
            status=FreshnessStatus.INVALIDATED,
            reason="Adapter reports an uncertain tool-execution outcome",
            session_id=session_id,
            sources=sources + ["adapter"],
        )
    if report.live_marker is None:
        return FreshnessEvidence(
            status=FreshnessStatus.INVALIDATED,
            reason="Adapter supplied no monotonic live marker to correlate state",
            session_id=session_id,
            sources=sources + ["adapter"],
        )
    observed_marker = getattr(observed, "last_event_timestamp", None) if observed is not None else None
    if observed_marker is not None and report.live_marker < observed_marker:
        return FreshnessEvidence(
            status=FreshnessStatus.INVALIDATED,
            reason=(
                "Adapter live marker is older than the observed state "
                f"({report.live_marker} < {observed_marker})"
            ),
            session_id=session_id,
            observed_at=observed_marker,
            sources=sources + ["adapter"],
        )

    return FreshnessEvidence(
        status=FreshnessStatus.CORROBORATED,
        reason=f"Adapter corroborated live continuity via {report.source}",
        session_id=session_id,
        observed_at=observed_marker,
        sources=sources + ["adapter"],
        corroborating_source=report.source,
    )


def apply_freshness(context: Any, evidence: FreshnessEvidence) -> FreshnessEvidence:
    """
    Apply a freshness verdict to a TurnContext (sets ``verified_fresh`` and
    records the evidence for diagnostics). Returns the evidence.
    """
    if context is not None:
        context.verified_fresh = evidence.authorizes_replay
        context.freshness_evidence = evidence
    return evidence


async def corroborate_freshness(
    adapter: Any,
    *,
    live_session_id: Optional[str],
    observed: Optional[Any] = None,
    persisted: Optional[Any] = None,
) -> FreshnessEvidence:
    """
    Ask an adapter for independent freshness evidence and evaluate the result.

    Fails closed on every failure mode: a missing method, an exception, or a
    malformed response all downgrade to "no corroboration", so replay stays
    blocked. No adapter in this repository currently returns a usable report,
    so automatic replay remains blocked in practice.
    """
    report = None
    if adapter is not None:
        provider = getattr(adapter, "provide_freshness_report", None)
        if callable(provider):
            try:
                report = await provider(live_session_id, observed)
            except Exception:
                report = None
        # A malformed response must never be treated as evidence.
        if report is not None and not isinstance(report, AdapterFreshnessReport):
            report = None

    return evaluate_freshness(
        live_session_id=live_session_id,
        observed=observed,
        persisted=persisted,
        report=report,
    )
