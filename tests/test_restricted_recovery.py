"""Restricted recovery: safe posture, sign-off and line release."""

from __future__ import annotations

from pathlib import Path

import pytest

from linecontrol.domain.phase import Phase
from linecontrol.domain.records import (
    AcidSnapshot,
    FieldReport,
    LatchSnapshot,
    PumpSnapshot,
    RinseSnapshot,
    TensionSnapshot,
    TrackSnapshot,
)
from linecontrol.health.items import (
    PUMP_RUN,
    RINSE_VALVE,
    TENSION_SETPOINT,
    WELD_LATCH,
)
from linecontrol.health.restriction import RestrictionError
from linecontrol.line.machine import Line
from linecontrol.store.keys import KEY_ACID, KEY_GAIN, KEY_LATCH, KEY_PUMP, KEY_RINSE, KEY_TENSION
from linecontrol.store.snapshot import save_float, save_json


def _prepare(tmp_path: Path) -> Line:
    line = Line(tmp_path)
    line.recover()
    save_json(line.store, KEY_TENSION, TensionSnapshot(value=91.0, gain=1.15))
    save_float(line.store, KEY_GAIN, 1.15)
    save_json(line.store, KEY_PUMP, PumpSnapshot(running=True))
    save_json(line.store, KEY_RINSE, RinseSnapshot(active=True))
    save_json(line.store, KEY_ACID, AcidSnapshot(level=6.5, offset=0.75))
    save_json(line.store, KEY_LATCH, LatchSnapshot(interlock=False, alarm=False))
    save_json(line.store, "strip.track", TrackSnapshot(position=55.0, seam_id="s-1"))
    return line


def _matching_report(**overrides) -> FieldReport:
    """Field readings matching the _prepare residuals, with overrides."""

    base = dict(
        tension=91.0,
        gain=1.15,
        pump_running=True,
        rinse_active=True,
        acid_level=6.5,
        track_position=55.0,
        weld_latch=False,
        weld_alarm=False,
    )
    base.update(overrides)
    return FieldReport(**base)


def test_clean_field_report_recovers_normally(tmp_path: Path) -> None:
    _prepare(tmp_path)
    line = Line(tmp_path)
    line.stage_field_report(_matching_report())
    line.recover()
    assert line.phase is Phase.IDLE
    assert line.health.restricted() is False
    assert line.pump.running() is True
    assert line.tension.setpoint() == 91.0


def test_mismatch_parks_the_pump_in_a_safe_posture(tmp_path: Path) -> None:
    _prepare(tmp_path)
    line = Line(tmp_path)
    line.stage_field_report(_matching_report(pump_running=False))
    line.recover()
    assert line.phase is Phase.RESTRICTED
    assert "pickle" in line.health.held_capabilities()
    assert line.pump.running() is False  # safe posture, residual said running
    # Boot recovery always closes a residual open valve before lifting rolls.
    assert line.pickle.rinsing() is False


def test_restricted_line_refuses_pump_start(tmp_path: Path) -> None:
    _prepare(tmp_path)
    line = Line(tmp_path)
    line.stage_field_report(_matching_report(pump_running=False))
    line.recover()
    with pytest.raises(RestrictionError) as raised:
        line.start_acid_loop(6.0)
    assert PUMP_RUN in raised.value.items


def test_unaffected_capabilities_remain_usable(tmp_path: Path) -> None:
    _prepare(tmp_path)
    line = Line(tmp_path)
    line.stage_field_report(_matching_report(pump_running=False))
    line.recover()
    # Dryer / shear offset etc. are unrelated to the pump mismatch.
    line.set_shear_offset(2.5)
    assert line.exit.shear_offset() == 2.5
    # But the full cycle is held back.
    with pytest.raises(RestrictionError):
        from linecontrol.line.workflow import CycleInput

        line.production_cycle(
            CycleInput("C-1", 90, 5.0, 24, 300, 1.5)
        )


def test_confirm_with_field_adopts_the_field_reading(tmp_path: Path) -> None:
    _prepare(tmp_path)
    line = Line(tmp_path)
    line.stage_field_report(_matching_report(pump_running=False))
    line.recover()
    line.health_confirm(PUMP_RUN, "field")
    assert line.pump.running() is False
    assert line.phase is Phase.IDLE
    persisted, ok = line.pump.persisted()
    assert ok is True and persisted.running is False


def test_confirm_residual_adopts_the_snapshot(tmp_path: Path) -> None:
    _prepare(tmp_path)
    line = Line(tmp_path)
    line.stage_field_report(_matching_report(pump_running=False))
    line.recover()
    line.health_confirm(PUMP_RUN, "residual")
    assert line.pump.running() is True
    assert line.phase is Phase.IDLE


def test_tension_mismatch_holds_speed_permission(tmp_path: Path) -> None:
    _prepare(tmp_path)
    line = Line(tmp_path)
    line.stage_field_report(
        _matching_report(tension=40.0)
    )
    line.recover()
    assert line.phase is Phase.RESTRICTED
    assert line.tension.speed() == "standby"
    assert line.tension.speed_permitted() is False
    with pytest.raises(RestrictionError):
        line.set_strip_speed(1.5)
    line.health_confirm(TENSION_SETPOINT, "field")
    assert line.tension.setpoint() == 40.0
    assert line.tension.speed_permitted() is True
    assert line.phase is Phase.IDLE


def test_unknown_latch_is_kept_interlocked(tmp_path: Path) -> None:
    _prepare(tmp_path)
    line = Line(tmp_path)
    line.stage_field_report(_matching_report(weld_latch=True, weld_alarm=True))
    line.recover()
    assert line.weld.interlock_ok() is False
    assert "weld" in line.health.held_capabilities()
    with pytest.raises(RestrictionError):
        line.weld_and_speedup()

    # Field says the interlock really is latched: trusting the field keeps
    # the welder locked even though the checklist item is signed off.
    line.health_confirm(WELD_LATCH, "field")
    assert line.weld.interlock_ok() is False

    # If the operator inspects the machine and trusts the residual snapshot
    # instead, the residual basis releases the interlock.
    inspected_dir = tmp_path / "inspected"
    _prepare(inspected_dir)
    inspected = Line(inspected_dir)
    inspected.stage_field_report(_matching_report(weld_latch=True, weld_alarm=True))
    inspected.recover()
    inspected.health_confirm(WELD_LATCH, "residual")
    assert inspected.weld.interlock_ok() is True
    assert inspected.phase is Phase.IDLE


def test_rinse_mismatch_blocks_until_confirmed(tmp_path: Path) -> None:
    _prepare(tmp_path)
    line = Line(tmp_path)
    line.stage_field_report(_matching_report(rinse_active=False))
    line.recover()
    assert RINSE_VALVE in {item.id for item in line.health.pending()}
    assert line.pickle.rinsing() is False
    line.health_confirm(RINSE_VALVE, "residual")
    assert line.pickle.rinsing() is True


def test_advisory_item_does_not_restrict(tmp_path: Path) -> None:
    _prepare(tmp_path)
    line = Line(tmp_path)
    line.stage_field_report(
        _matching_report(gain=1.5)
    )
    line.recover()
    assert line.phase is Phase.IDLE
    pending = {item.id for item in line.health.pending()}
    assert "tension-gain-drift" in pending
    line.health_confirm("tension-gain-drift", "field")
    assert line.tension.gain() == pytest.approx(1.5)


def test_confirming_without_a_reading_is_rejected(tmp_path: Path) -> None:
    _prepare(tmp_path)
    line = Line(tmp_path)
    line.stage_field_report(FieldReport())  # nothing reported
    line.recover()
    with pytest.raises(RestrictionError):
        line.health_confirm(PUMP_RUN, "field")
    # Residual / safe bases remain available.
    line.health_confirm(PUMP_RUN, "safe")


def test_follow_up_field_report_resolves_matching_items(tmp_path: Path) -> None:
    _prepare(tmp_path)
    line = Line(tmp_path)
    line.stage_field_report(_matching_report(pump_running=False))
    line.recover()
    assert line.phase is Phase.RESTRICTED
    view = line.accept_field_report(
        _matching_report()
    )
    assert view["restricted"] is False
    assert line.pump.running() is True
    assert line.phase is Phase.IDLE


def test_newly_disagreed_item_reopens_restricted_mode(tmp_path: Path) -> None:
    _prepare(tmp_path)
    line = Line(tmp_path)
    line.stage_field_report(_matching_report())
    line.recover()
    assert line.phase is Phase.IDLE
    line.accept_field_report(_matching_report(tension=40.0))
    assert line.phase is Phase.RESTRICTED
    assert line.tension.speed() == "standby"


def test_restart_remembers_confirmed_items(tmp_path: Path) -> None:
    _prepare(tmp_path)
    line = Line(tmp_path)
    line.stage_field_report(_matching_report(pump_running=False))
    line.recover()
    line.health_confirm(PUMP_RUN, "residual")
    assert line.phase is Phase.IDLE

    restarted = Line(tmp_path)
    restarted.recover()
    assert restarted.phase is Phase.IDLE
    item = next(i for i in restarted.health.all() if i.id == PUMP_RUN)
    assert item.confirmed is True
    assert restarted.pump.running() is True


def test_sign_off_is_refused_during_a_production_phase(tmp_path: Path) -> None:
    line = _prepare(tmp_path)
    line.stage_field_report(_matching_report(pump_running=False))
    line.recover()
    assert line.phase is Phase.RESTRICTED
    line.set_phase(Phase.ACID_LOOP)
    with pytest.raises(RestrictionError):
        line.health_confirm(PUMP_RUN, "residual")


def test_track_field_confirmation_keeps_the_residual_seam(tmp_path: Path) -> None:
    line = _prepare(tmp_path)
    line.stage_field_report(_matching_report(track_position=80.0))
    line.recover()
    assert line.strip.track_position() == 0.0
    assert line.strip.seam_id() == ""
    line.health_confirm("seam-track", "field")
    assert line.strip.track_position() == pytest.approx(80.0)
    # The residual seam id is preserved; only the position follows the field.
    assert line.strip.seam_id() == "s-1"
    assert line.phase is Phase.IDLE


def test_safe_basis_keeps_the_pump_stopped_and_releases(tmp_path: Path) -> None:
    line = _prepare(tmp_path)
    line.stage_field_report(_matching_report(pump_running=False))
    line.recover()
    line.health_confirm(PUMP_RUN, "safe")
    assert line.pump.running() is False
    assert line.phase is Phase.IDLE


def test_duplicate_confirmation_is_rejected(tmp_path: Path) -> None:
    line = _prepare(tmp_path)
    line.stage_field_report(_matching_report(pump_running=False))
    line.recover()
    line.health_confirm(PUMP_RUN, "safe")
    with pytest.raises(RestrictionError):
        line.health_confirm(PUMP_RUN, "safe")


def test_field_basis_requires_the_reading(tmp_path: Path) -> None:
    line = _prepare(tmp_path)
    # Acid level disputed, but no field report carries a level reading.
    line.stage_field_report(_matching_report(acid_level=None))
    line.recover()
    assert "acid-level" in {item.id for item in line.health.pending()}
    with pytest.raises(RestrictionError):
        line.health_confirm("acid-level", "field")
    # Residual basis still works.
    line.health_confirm("acid-level", "residual")
