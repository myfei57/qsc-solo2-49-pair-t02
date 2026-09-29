"""Pending-confirmation list raised by the start-up health check."""

from __future__ import annotations

from ..domain.events import Event
from ..domain.kinds import EventKind
from ..domain.phase import Phase
from .errors import HoldActive, UnknownHold
from .health import Finding, HealthReport


class HoldOps:
    """Mixed into :class:`~linecontrol.line.machine.Line`."""

    def holds(self) -> tuple[Finding, ...]:
        """Health-check findings still waiting for operator confirmation."""

        return tuple(self._holds)

    def health(self) -> HealthReport:
        """The report produced by the most recent start-up health check."""

        return self._health

    def confirm_hold(self, code: str, note: str = "") -> tuple[Finding, ...]:
        """Acknowledge one hold; release the line once the list is empty."""

        hold = next((item for item in self._holds if item.code == code), None)
        if hold is None:
            raise UnknownHold(code)
        self._holds.remove(hold)
        message = f"confirmed {code}"
        if note:
            message += f" ({note})"
        self.bus.publish(Event(kind=EventKind.HOLD, source="line", message=message))
        if not self._holds:
            self.set_phase(Phase.IDLE)
            self.bus.publish(Event(kind=EventKind.HOLD, source="line", message="released"))
        return self.holds()

    def _apply_health(self, report: HealthReport) -> None:
        self._health = report
        self._holds = list(report.holds)
        for hold in self._holds:
            self.bus.publish(
                Event(kind=EventKind.HOLD, source="line", message=f"raised {hold.code}")
            )

    def _require_ready(self) -> None:
        """Refuse production commands while any hold is still pending."""

        if self._holds:
            raise HoldActive([hold.code for hold in self._holds])
