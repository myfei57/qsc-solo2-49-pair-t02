"""Start-up health check: residual snapshots vs the field readings."""

from __future__ import annotations

from pathlib import Path

from linecontrol.domain.records import (
    AcidSnapshot,
    FieldReport,
    LatchSnapshot,
    PumpSnapshot,
    RinseSnapshot,
    TensionSnapshot,
    TrackSnapshot,
    WeldRecord,
)
from linecontrol.health.items import (
    ACID_LEVEL,
    PUMP_RINSE,
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
from linecontrol.line.machine import Line
from linecontrol.store.keys import (
    KEY_ACID,
    KEY_GAIN,
    KEY_LATCH,
    KEY_PUMP,
    KEY_RINSE,
    KEY_TENSION,
    KEY_TRACK,
)
from linecontrol.store.recovery import collect
from linecontrol.store.snapshot import save_float, save_json


def _snapshot_dir(tmp_path: Path, **snapshots) -> Path:
    line = Line(tmp_path)
    line.recover()
    if "tension" in snapshots:
        save_json(line.store, KEY_TENSION, snapshots["tension"])
    if "gain" in snapshots:
        save_float(line.store, KEY_GAIN, snapshots["gain"])
    if "pump" in snapshots:
        save_json(line.store, KEY_PUMP, snapshots["pump"])
    if "rinse" in snapshots:
        save_json(line.store, KEY_RINSE, snapshots["rinse"])
    if "acid" in snapshots:
        save_json(line.store, KEY_ACID, snapshots["acid"])
    if "track" in snapshots:
        save_json(line.store, KEY_TRACK, snapshots["track"])
    if "latch" in snapshots:
        save_json(line.store, KEY_LATCH, snapshots["latch"])
    return tmp_path


def test_no_field_report_means_no_items(tmp_path: Path) -> None:
    _snapshot_dir(
        tmp_path,
        tension=TensionSnapshot(value=90.0, gain=1.1),
        gain=1.1,
        pump=PumpSnapshot(running=True),
        rinse=RinseSnapshot(active=True),
    )
    assert evaluate(collect(Line(tmp_path).store), None) == ()


def test_matching_field_report_produces_no_items(tmp_path: Path) -> None:
    _snapshot_dir(
        tmp_path,
        tension=TensionSnapshot(value=90.0, gain=1.1),
        gain=1.1,
        pump=PumpSnapshot(running=False),
        rinse=RinseSnapshot(active=False),
        acid=AcidSnapshot(level=6.0, offset=0.2),
        track=TrackSnapshot(position=40.0, seam_id="s-1"),
        latch=LatchSnapshot(interlock=False, alarm=False),
    )
    state = collect(Line(tmp_path).store)
    field = FieldReport(
        tension=90.0,
        gain=1.1,
        pump_running=False,
        rinse_active=False,
        acid_level=6.05,
        track_position=40.2,
        weld_latch=False,
        weld_alarm=False,
    )
    assert evaluate(state, field) == ()


def test_disagreement_becomes_a_blocking_item(tmp_path: Path) -> None:
    _snapshot_dir(
        tmp_path,
        pump=PumpSnapshot(running=True),
        rinse=RinseSnapshot(active=True),
    )
    state = collect(Line(tmp_path).store)
    items = evaluate(state, FieldReport(pump_running=False, rinse_active=True))
    assert [item.id for item in items] == [PUMP_RUN]
    assert items[0].severity.value == "blocking"


def test_missing_field_reading_is_itself_blocking(tmp_path: Path) -> None:
    _snapshot_dir(
        tmp_path,
        pump=PumpSnapshot(running=True),
        rinse=RinseSnapshot(active=True),
    )
    items = evaluate(
        collect(Line(tmp_path).store), FieldReport(rinse_active=True)
    )
    assert [item.id for item in items] == [PUMP_RUN]
    assert items[0].actual == "现场未上报"


def test_internal_conflict_is_hidden_when_field_explains_it(tmp_path: Path) -> None:
    _snapshot_dir(
        tmp_path,
        pump=PumpSnapshot(running=True),
        rinse=RinseSnapshot(active=False),
    )
    # With field readings the contradiction surfaces only as the pump item.
    items = evaluate(
        collect(Line(tmp_path).store),
        FieldReport(pump_running=False, rinse_active=False),
    )
    assert [item.id for item in items] == [PUMP_RUN]
    # Without any field report it remains an internal blocking item.
    assert [i.id for i in evaluate(collect(Line(tmp_path).store), None)] == [
        PUMP_RINSE
    ]


def test_gain_drift_is_advisory(tmp_path: Path) -> None:
    _snapshot_dir(
        tmp_path,
        tension=TensionSnapshot(value=90.0, gain=1.1),
        gain=1.1,
    )
    items = evaluate(
        collect(Line(tmp_path).store), FieldReport(tension=90.0, gain=1.5)
    )
    assert [item.id for item in items] == [TENSION_GAIN_DRIFT]
    assert items[0].severity.value == "advisory"


def test_internal_pump_rinse_conflict_is_blocking(tmp_path: Path) -> None:
    _snapshot_dir(
        tmp_path,
        pump=PumpSnapshot(running=True),
        rinse=RinseSnapshot(active=False),
    )
    # Even without any field report the residual snapshots disagree.
    items = evaluate(collect(Line(tmp_path).store), None)
    assert [item.id for item in items] == [PUMP_RINSE]


def test_internal_tension_gain_conflict_is_blocking(tmp_path: Path) -> None:
    _snapshot_dir(
        tmp_path,
        tension=TensionSnapshot(value=90.0, gain=1.1),
        gain=1.3,
    )
    items = evaluate(collect(Line(tmp_path).store), None)
    assert [item.id for item in items] == [TENSION_GAIN_CONFLICT]


def test_rinse_active_without_track_is_blocking(tmp_path: Path) -> None:
    _snapshot_dir(
        tmp_path,
        rinse=RinseSnapshot(active=True),
        track=TrackSnapshot(position=0.0, seam_id=""),
    )
    items = evaluate(collect(Line(tmp_path).store), None)
    assert [item.id for item in items] == [RINSE_TRACK]


def test_every_field_item_kind_is_detected(tmp_path: Path) -> None:
    line = Line(tmp_path)
    line.recover()
    line.weld.persist_tension(88.0, 1.1)
    save_json(line.store, KEY_TENSION, TensionSnapshot(value=90.0, gain=1.1))
    save_float(line.store, KEY_GAIN, 1.1)
    save_json(line.store, KEY_PUMP, PumpSnapshot(running=True))
    save_json(line.store, KEY_RINSE, RinseSnapshot(active=True))
    save_json(line.store, KEY_ACID, AcidSnapshot(level=6.0))
    save_json(line.store, KEY_TRACK, TrackSnapshot(position=40.0, seam_id="s-1"))
    save_json(line.store, KEY_LATCH, LatchSnapshot(interlock=False, alarm=False))

    items = evaluate(
        collect(Line(tmp_path).store),
        FieldReport(
            tension=50.0,
            gain=1.1,
            pump_running=False,
            rinse_active=False,
            acid_level=2.0,
            track_position=99.0,
            weld_latch=True,
            weld_alarm=True,
        ),
    )
    ids = {item.id for item in items}
    assert {
        TENSION_SETPOINT,
        PUMP_RUN,
        RINSE_VALVE,
        ACID_LEVEL,
        SEAM_TRACK,
        WELD_LATCH,
    } <= ids


def test_weld_tension_advisory(tmp_path: Path) -> None:
    line = Line(tmp_path)
    line.recover()
    save_json(line.store, KEY_TENSION, TensionSnapshot(value=80.0, gain=1.0))
    save_json(
        line.store,
        "weld.record",
        WeldRecord(seam_id="s-9", position=10.0, tension=88.0, heat_input=3.0),
    )
    items = evaluate(
        collect(Line(tmp_path).store), FieldReport(tension=80.0)
    )
    assert WELD_TENSION in {item.id for item in items}
    advisory = next(item for item in items if item.id == WELD_TENSION)
    assert advisory.severity.value == "advisory"
