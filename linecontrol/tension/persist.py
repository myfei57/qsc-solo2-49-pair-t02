"""Reading and restoring the persisted tension snapshot."""

from __future__ import annotations

from ..domain.records import TensionSnapshot, utcnow
from ..store.keys import KEY_TENSION
from ..store.snapshot import load_json, save_json
from .state import SpeedState


class PersistOps:
    """Mixed into :class:`~linecontrol.tension.roller.Roller`."""

    def persisted_tension(self) -> tuple[TensionSnapshot, bool]:
        return load_json(self.store, KEY_TENSION, TensionSnapshot)

    def restore_tension(self, snapshot: TensionSnapshot) -> None:
        if snapshot.value <= 0:
            return
        self.data.setpoint = snapshot.value
        if snapshot.gain > 0:
            self.data.gain = snapshot.gain
        self.data.persisted = True
        self.data.machine.force(SpeedState.PERMITTED)

    def hold(self) -> None:
        """Safe posture: drop speed-up permission while an item is pending."""

        self.data.persisted = False
        self.data.setpoint = 0.0
        self.data.machine.force(SpeedState.STANDBY)

    def commit_setpoint(self, value: float) -> None:
        """Adopt a field-confirmed setpoint and make it the new residual."""

        self.data.setpoint = float(value)
        self.data.persisted = True
        self.data.machine.force(SpeedState.PERMITTED)
        save_json(
            self.store,
            KEY_TENSION,
            TensionSnapshot(
                value=float(value), gain=self.data.gain, saved=utcnow()
            ),
        )
