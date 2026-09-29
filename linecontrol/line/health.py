"""Field-report upload and operator sign-off of recovery checklist items."""

from __future__ import annotations

from ..domain.events import Event
from ..domain.kinds import EventKind
from ..domain.phase import Phase
from ..domain.records import FieldReport, TensionSnapshot
from ..health.items import (
    ACID_LEVEL,
    PUMP_RUN,
    RINSE_TRACK,
    RINSE_VALVE,
    SEAM_TRACK,
    TENSION_GAIN_CONFLICT,
    TENSION_GAIN_DRIFT,
    TENSION_SETPOINT,
    WELD_LATCH,
    WELD_TENSION,
    evaluate,
)
from ..health.restriction import RestrictionError, RestrictionErrorCode
from ..store.keys import KEY_FIELD
from ..store.recovery import Recovery, collect
from ..store.snapshot import save_json

#: Basis strings accepted on the confirmation endpoint.
BASIS_RESIDUAL = "residual"
BASIS_FIELD = "field"
BASIS_SAFE = "safe"

#: Phases in which recovery sign-off may still touch equipment posture.
_RECOVERY_PHASES = frozenset(
    {Phase.IDLE, Phase.RECOVERING, Phase.RESTRICTED}
)

#: Items that may be resolved by trusting a field-side reading.
_FIELD_CAPABLE = frozenset(
    {
        TENSION_SETPOINT,
        TENSION_GAIN_CONFLICT,
        TENSION_GAIN_DRIFT,
        PUMP_RUN,
        RINSE_VALVE,
        ACID_LEVEL,
        SEAM_TRACK,
        RINSE_TRACK,
        WELD_LATCH,
        WELD_TENSION,
    }
)


class HealthOps:
    """Mixed into :class:`~linecontrol.line.machine.Line`."""

    def staged_field(self) -> FieldReport | None:
        from ..store.snapshot import load_json

        report, ok = load_json(self.store, KEY_FIELD, FieldReport)
        return report if ok else None

    def stage_field_report(self, report: FieldReport) -> None:
        save_json(self.store, KEY_FIELD, report)

    def clear_field_report(self) -> bool:
        return self.store.delete(KEY_FIELD)

    def accept_field_report(self, report: FieldReport) -> dict[str, object]:
        """Persist a field report and re-run the health check.

        Items the new readings clear are resolved automatically by adopting
        the residual posture; newly discovered items are parked in their safe
        postures. The line stays restricted while any blocking item remains.
        Once the line is running production the report is only stored; the
        posture re-application happens on the next recovery boot instead.
        """

        self.stage_field_report(report)
        state = collect(self.store)
        self.health.rebuild(evaluate(state, report))
        if self.phase in _RECOVERY_PHASES:
            # Re-apply the full recovery posture so resolved items are
            # restored from disk and disputed items fall back to safe state.
            self._apply_recovery(state)
        self.health.save(self.store)
        self._refresh_recovery_phase()
        return self.health.view()

    def health_items(self) -> list[dict[str, object]]:
        return [item.to_dict() for item in self.health.all()]

    def health_view(self) -> dict[str, object]:
        view = self.health.view()
        field = self.staged_field()
        view["fieldReport"] = field.to_dict() if field is not None else None
        return view

    def health_confirm(self, item_id: str, basis: str = BASIS_RESIDUAL) -> dict[str, object]:
        """Sign off one checklist item and adopt the chosen posture."""

        if self.phase is Phase.FAULT:
            raise RestrictionError(
                RestrictionErrorCode.PENDING,
                "line is in fault; clear the fault before confirming items",
            )
        if self.phase not in _RECOVERY_PHASES:
            raise RestrictionError(
                RestrictionErrorCode.PENDING,
                f"recovery sign-off unavailable while the line is {self.phase.value}",
            )
        item = self.health.get(item_id)
        if item is None:
            raise RestrictionError(
                RestrictionErrorCode.UNKNOWN_ITEM,
                f"unknown health item: {item_id}",
            )
        if item.confirmed:
            raise RestrictionError(
                RestrictionErrorCode.PENDING,
                f"health item already confirmed: {item_id}",
            )

        state = collect(self.store)
        field = self.staged_field()
        self._resolve_item(state, field, item_id, basis)
        self.health.confirm(item_id)
        self.health.save(self.store)
        self.bus.publish(
            Event(
                kind=EventKind.RECOVER,
                source="line",
                message=f"confirmed:{item_id}",
            )
        )
        self._refresh_recovery_phase()
        return self.health.view()

    # -- resolution per item/basis -------------------------------------------

    def _resolve_item(
        self,
        state: Recovery,
        field: FieldReport | None,
        item_id: str,
        basis: str,
    ) -> None:
        if basis == BASIS_RESIDUAL:
            self._adopt_residual(state, item_id)
            return
        if basis == BASIS_SAFE:
            self._hold_item(item_id)
            return
        if basis != BASIS_FIELD:
            raise RestrictionError(
                RestrictionErrorCode.PENDING,
                f"unknown confirmation basis: {basis}",
            )
        if item_id not in _FIELD_CAPABLE:
            raise RestrictionError(
                RestrictionErrorCode.PENDING,
                f"item {item_id} has no field-side basis; use residual or safe",
            )
        if field is None:
            raise RestrictionError(
                RestrictionErrorCode.PENDING,
                "no field report on file; upload field readings first",
            )
        self._adopt_field(state, field, item_id)

    def _adopt_field(self, state, field: FieldReport, item_id: str) -> None:
        if item_id == TENSION_SETPOINT:
            self._require_reading(item_id, field.tension)
            self.tension.commit_setpoint(field.tension)
        elif item_id == TENSION_GAIN_DRIFT:
            self._require_reading(item_id, field.gain)
            self.tension.recalibrate(field.gain)
        elif item_id == TENSION_GAIN_CONFLICT:
            self._require_reading(item_id, field.gain)
            self.tension.recalibrate(field.gain)
            if field.tension is not None:
                self.tension.commit_setpoint(field.tension)
        elif item_id == PUMP_RUN:
            self._require_reading(item_id, field.pump_running)
            self.pump.commit_running(field.pump_running)
        elif item_id == RINSE_VALVE:
            self._require_reading(item_id, field.rinse_active)
            self.pickle.commit_rinse(field.rinse_active)
        elif item_id == ACID_LEVEL:
            self._require_reading(item_id, field.acid_level)
            self.pickle.commit_level(field.acid_level)
        elif item_id == SEAM_TRACK:
            self._require_reading(item_id, field.track_position)
            self.strip.track_refresh(field.track_position, state.track.seam_id)
        elif item_id == RINSE_TRACK:
            self._require_reading(item_id, field.track_position)
            self.strip.track_refresh(field.track_position, state.track.seam_id)
        elif item_id == WELD_LATCH:
            self._require_reading(item_id, field.weld_latch)
            if field.weld_latch:
                self.weld.alarm()
            else:
                self.weld.alarm_clear()
        elif item_id == WELD_TENSION:
            self._require_reading(item_id, field.tension)
            self.weld.restore_tension(TensionSnapshot(value=field.tension))

    @staticmethod
    def _require_reading(item_id: str, value: object | None) -> None:
        if value is None:
            raise RestrictionError(
                RestrictionErrorCode.PENDING,
                f"field report has no reading for {item_id}",
            )
