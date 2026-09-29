"""Pump run-status projection and its persisted snapshot."""

from __future__ import annotations

from ..domain.records import PumpSnapshot, utcnow
from ..store.keys import KEY_PUMP
from ..store.snapshot import load_json, save_json


class StatusOps:
    """Mixed into :class:`~linecontrol.pump.pump.Pump`."""

    def running(self) -> bool:
        return self.data.running()

    def restore(self, snapshot: PumpSnapshot) -> None:
        """Rebuild the run state read back from disk."""

        self.data.machine.force("running" if snapshot.running else "stopped")

    def commit_running(self, running: bool) -> None:
        """Adopt an operator-confirmed field state and persist it."""

        self.data.machine.force("running" if running else "stopped")
        if not running:
            self.data.started_at = None
        self._save_state()

    def persisted(self) -> tuple[PumpSnapshot, bool]:
        return load_json(self.store, KEY_PUMP, PumpSnapshot)

    def _save_state(self) -> None:
        save_json(
            self.store,
            KEY_PUMP,
            PumpSnapshot(running=self.running(), saved=utcnow()),
        )
