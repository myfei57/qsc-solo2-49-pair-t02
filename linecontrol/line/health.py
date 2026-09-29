"""Start-up health check over the recovered snapshots.

The check compares the residual state read back from the store with the
state the field can plausibly be in after a restart. Every mismatch becomes
a :class:`Finding`; findings that recovery cannot correct on its own are
raised as holds and keep the line in restricted mode until an operator
confirms them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..pickle.state import MIN_LEVEL
from ..store.recovery import Recovery
from ..tension.state import GAIN_TOLERANCE


@dataclass(frozen=True, slots=True)
class Finding:
    """One mismatch between the residual state and the field."""

    code: str
    area: str
    message: str
    expected: str
    actual: str
    auto: bool = False  # recovery already corrected it; listed for audit only

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "area": self.area,
            "message": self.message,
            "expected": self.expected,
            "actual": self.actual,
            "auto": self.auto,
        }


@dataclass(frozen=True, slots=True)
class HealthReport:
    """Outcome of the start-up health check."""

    checked: int = 0
    findings: tuple[Finding, ...] = ()

    @property
    def holds(self) -> tuple[Finding, ...]:
        """Findings recovery could not correct; they block production."""

        return tuple(finding for finding in self.findings if not finding.auto)

    @property
    def ok(self) -> bool:
        return not self.holds

    def to_dict(self) -> dict[str, object]:
        return {
            "checked": self.checked,
            "ok": self.ok,
            "findings": [finding.to_dict() for finding in self.findings],
        }


def _pump_running(state: Recovery) -> Finding | None:
    if not state.pump.running:
        return None
    return Finding(
        code="pump-running",
        area="pump",
        message="pump snapshot says running; the field pump is stopped after a restart",
        expected="stopped",
        actual="running",
    )


def _rinse_open(state: Recovery) -> Finding | None:
    if not state.rinse.active:
        return None
    return Finding(
        code="rinse-open",
        area="pickle",
        message="rinse valve snapshot was open; recovery closed it",
        expected="closed",
        actual="open",
        auto=True,
    )


def _weld_latch(state: Recovery) -> Finding | None:
    if not (state.latch.interlock or state.latch.alarm):
        return None
    return Finding(
        code="weld-latch",
        area="weld",
        message="overcurrent latch still engaged; inspect the welder before clearing",
        expected="interlock clear",
        actual=f"interlock={state.latch.interlock} alarm={state.latch.alarm}",
    )


def _gain_divergence(state: Recovery) -> Finding | None:
    if state.gain <= 0 or state.tension.gain <= 0:
        return None
    if abs(state.gain - state.tension.gain) <= GAIN_TOLERANCE:
        return None
    return Finding(
        code="gain-divergence",
        area="tension",
        message="standalone gain and tension snapshot gain disagree",
        expected=f"gain {state.gain:.3f}",
        actual=f"snapshot gain {state.tension.gain:.3f}",
    )


def _track_weld(state: Recovery) -> Finding | None:
    if (
        state.weld.seam_id
        and state.track.seam_id
        and state.track.seam_id != state.weld.seam_id
    ):
        return Finding(
            code="track-seam-mismatch",
            area="strip",
            message="tracked seam differs from the last weld record",
            expected=f"seam {state.weld.seam_id}",
            actual=f"seam {state.track.seam_id}",
        )
    if state.weld.position > 0 and state.track.position < state.weld.position:
        return Finding(
            code="track-behind-weld",
            area="strip",
            message="track point sits behind the last weld position",
            expected=f"track >= {state.weld.position:.1f}",
            actual=f"track {state.track.position:.1f}",
        )
    return None


def _acid_level(state: Recovery) -> Finding | None:
    if not state.pump.running or state.acid.level <= 0:
        return None
    if state.acid.level >= MIN_LEVEL:
        return None
    return Finding(
        code="acid-level-low",
        area="pickle",
        message="pump claims running with the tank below the minimum level",
        expected=f"level >= {MIN_LEVEL:.2f}",
        actual=f"level {state.acid.level:.2f}",
    )


#: Checks run during start-up recovery, in report order.
CHECKS: tuple[Callable[[Recovery], "Finding | None"], ...] = (
    _pump_running,
    _rinse_open,
    _weld_latch,
    _gain_divergence,
    _track_weld,
    _acid_level,
)


def inspect(state: Recovery) -> HealthReport:
    """Run every check over the recovered snapshots."""

    findings: list[Finding] = []
    for check in CHECKS:
        finding = check(state)
        if finding is not None:
            findings.append(finding)
    return HealthReport(checked=len(CHECKS), findings=tuple(findings))
