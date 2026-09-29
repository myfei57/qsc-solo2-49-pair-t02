"""Start-up recovery: health check, safe posture, then residual rebuild."""

from __future__ import annotations

from ..domain.events import Event
from ..domain.kinds import EventKind
from ..domain.phase import Phase
from ..domain.records import (
    LatchSnapshot,
    PumpSnapshot,
    TrackSnapshot,
)
from ..health.items import (
    ACID_LEVEL,
    PUMP_RINSE,
    PUMP_RUN,
    RINSE_TRACK,
    RINSE_VALVE,
    SEAM_TRACK,
    TENSION_GAIN_CONFLICT,
    TENSION_SETPOINT,
    WELD_LATCH,
    WELD_TENSION,
    evaluate,
)
from ..store.recovery import Recovery, collect

#: Item ids whose pending state keeps the tension roller at its defaults.
_TENSION_HELD = (TENSION_SETPOINT, TENSION_GAIN_CONFLICT)
#: Item ids whose pending state keeps the pump stopped.
_PUMP_HELD = (PUMP_RUN, PUMP_RINSE)
#: Item ids whose pending state keeps the rinse valve closed.
_RINSE_HELD = (RINSE_VALVE, PUMP_RINSE, RINSE_TRACK)
#: Item ids whose pending state keeps the strip track un-restored.
_TRACK_HELD = (SEAM_TRACK, RINSE_TRACK)


class RecoveryOps:
    """Mixed into :class:`~linecontrol.line.machine.Line`."""

    def recover(self) -> Recovery:
        """Run the health check, then rebuild every controller.

        Residual snapshots that agree with the field are restored normally.
        A mismatched or unreported item is parked in a safe posture and listed
        for operator confirmation instead; the line comes up restricted and is
        released once every blocking item is signed off. With no field report
        staged the residual state is trusted, preserving a blind restart.
        """

        self.set_phase(Phase.RECOVERING)
        state = collect(self.store)
        self.health.load(self.store)
        self.health.rebuild(evaluate(state, self.staged_field()))
        self._apply_recovery(state)
        self.health.save(self.store)
        for item in self.health.pending():
            self.bus.publish(
                Event(kind=EventKind.RECOVER, source="line", message=f"health:{item.id}")
            )
        self._refresh_recovery_phase()
        return state

    # -- bulk restore on boot ------------------------------------------------

    def _posture(self, state: Recovery) -> dict[str, object]:
        """Live safe postures keyed the same way as the residual checks."""

        pending = {item.id for item in self.health.pending()}
        return {
            "pump_running": state.pump.running
            and not any(item_id in pending for item_id in _PUMP_HELD),
            "rinse_active": state.rinse.active
            and not any(item_id in pending for item_id in _RINSE_HELD),
        }

    def _apply_recovery(self, state: Recovery) -> None:
        """Restore residual state for items that do not need confirmation."""

        pending = {item.id for item in self.health.pending()}
        posture = self._posture(state)

        if TENSION_GAIN_CONFLICT not in pending and state.gain > 0:
            self.tension.recalibrate(state.gain)
        if any(item_id in pending for item_id in _TENSION_HELD):
            self.tension.hold()
        else:
            self.tension.restore_tension(state.tension)

        if WELD_LATCH in pending:
            # Unknown latch state: weld stays interlocked until a human looks.
            self.weld.restore_latch(LatchSnapshot(interlock=True, alarm=True))
        else:
            self.weld.restore_latch(state.latch)
        self.weld.restore_tension(state.tension)

        if ACID_LEVEL not in pending:
            self.pickle.restore_acid(state.acid)
        self.pump.restore(PumpSnapshot(running=bool(posture["pump_running"])))
        self.pump.restore_run(state.run_seconds)
        self.pickle.restore_rinse(bool(posture["rinse_active"]))
        if any(item_id in pending for item_id in _TRACK_HELD):
            self.strip.restore_track(TrackSnapshot())
        else:
            self.strip.restore_track(state.track)
        self.audit.restore()
        self.audit.flush()
        # Recovery always closes a residual open valve before lifting the
        # rolls; the closure is persisted so a restart does not re-discover
        # the pre-restart valve state.
        if posture["rinse_active"]:
            self.pickle.rinse_stop()
        self.exit.recover_lift()

    # -- per-item posture changes after a sign-off or a fresh field report --

    def _hold_item(self, item_id: str) -> None:
        """Force one item into its safe posture while it stays pending."""

        if item_id in _TENSION_HELD:
            self.tension.hold()
        elif item_id == WELD_LATCH:
            self.weld.restore_latch(LatchSnapshot(interlock=True, alarm=True))
        elif item_id in _PUMP_HELD:
            self.pump.commit_running(False)
        if item_id in _RINSE_HELD:
            self.pickle.commit_rinse(False)
        if item_id in _TRACK_HELD:
            self.strip.restore_track(TrackSnapshot())

    def _adopt_residual(self, state: Recovery, item_id: str) -> None:
        """Apply the residual posture for one item after sign-off."""

        if item_id in _TENSION_HELD:
            if item_id == TENSION_GAIN_CONFLICT:
                self.tension.recalibrate(state.gain)
            self.tension.restore_tension(state.tension)
        elif item_id == WELD_LATCH:
            self.weld.restore_latch(state.latch)
        elif item_id == ACID_LEVEL:
            self.pickle.restore_acid(state.acid)
        elif item_id == PUMP_RUN:
            self.pump.commit_running(state.pump.running)
        elif item_id == RINSE_VALVE:
            self.pickle.commit_rinse(state.rinse.active)
        elif item_id == PUMP_RINSE:
            # Residual says the pump ran without a rinse; the operator keeps
            # the pump stopped and the valve closed until a manual acid start.
            self.pump.commit_running(False)
            self.pickle.commit_rinse(False)
        elif item_id in _TRACK_HELD:
            self.strip.restore_track(state.track)
        elif item_id == WELD_TENSION:
            self.weld.restore_tension(state.tension)

    def _refresh_recovery_phase(self) -> None:
        # Phase switches only during recovery; a running production line is
        # never yanked into restricted mode by a checklist update.
        if self.phase not in (Phase.RECOVERING, Phase.RESTRICTED, Phase.IDLE):
            return
        if self.health.restricted():
            self.set_phase(Phase.RESTRICTED)
            self.bus.publish(
                Event(
                    kind=EventKind.RECOVER,
                    source="line",
                    message="restricted",
                    payload=float(len(self.health.blocking_pending())),
                )
            )
        elif self.phase in (Phase.RESTRICTED, Phase.RECOVERING):
            self.set_phase(Phase.IDLE)
            self.bus.publish(
                Event(kind=EventKind.RECOVER, source="line", message="released")
            )
