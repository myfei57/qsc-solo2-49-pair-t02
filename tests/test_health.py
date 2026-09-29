"""Start-up health check, pending holds and the restricted line mode."""

from __future__ import annotations

from pathlib import Path

import pytest

from linecontrol.console.app import create_app
from linecontrol.domain.kinds import EventKind
from linecontrol.domain.phase import Phase
from linecontrol.domain.records import (
    AcidSnapshot,
    LatchSnapshot,
    PumpSnapshot,
    RinseSnapshot,
    TensionSnapshot,
    TrackSnapshot,
    WeldRecord,
)
from linecontrol.line.errors import HoldActive, UnknownHold
from linecontrol.line.machine import Line
from linecontrol.line.workflow import CycleInput
from linecontrol.store.keys import (
    KEY_ACID,
    KEY_GAIN,
    KEY_LATCH,
    KEY_PUMP,
    KEY_RINSE,
    KEY_TENSION,
    KEY_TRACK,
    KEY_WELD,
)
from linecontrol.store.snapshot import save_float, save_json

WEB_ROOT = Path(__file__).resolve().parents[1] / "web"


def restart(tmp_path: Path) -> Line:
    line = Line(tmp_path)
    line.recover()
    return line


def test_clean_recovery_raises_no_holds(tmp_path: Path) -> None:
    line = restart(tmp_path)
    assert line.holds() == ()
    assert line.health().checked > 0
    assert line.health().ok is True
    assert line.phase is Phase.IDLE


def test_pump_running_snapshot_holds_the_line(tmp_path: Path) -> None:
    seed = restart(tmp_path)
    save_json(seed.store, KEY_PUMP, PumpSnapshot(running=True))

    line = restart(tmp_path)

    assert line.phase is Phase.RESTRICTED
    assert [hold.code for hold in line.holds()] == ["pump-running"]
    with pytest.raises(HoldActive):
        line.load_coil("C-1", 40.0)

    remaining = line.confirm_hold("pump-running", note="现场确认泵已停")

    assert remaining == ()
    assert line.phase is Phase.IDLE
    line.load_coil("C-1", 40.0)


def test_rinse_open_is_corrected_without_a_hold(tmp_path: Path) -> None:
    seed = restart(tmp_path)
    save_json(seed.store, KEY_RINSE, RinseSnapshot(active=True))

    line = restart(tmp_path)

    assert line.holds() == ()
    assert line.phase is Phase.IDLE
    findings = line.health().findings
    assert [finding.code for finding in findings] == ["rinse-open"]
    assert findings[0].auto is True


def test_weld_latch_holds_the_line(tmp_path: Path) -> None:
    seed = restart(tmp_path)
    save_json(seed.store, KEY_LATCH, LatchSnapshot(interlock=True, alarm=True))

    line = restart(tmp_path)

    assert [hold.code for hold in line.holds()] == ["weld-latch"]
    assert line.phase is Phase.RESTRICTED


def test_gain_divergence_holds_the_line(tmp_path: Path) -> None:
    seed = restart(tmp_path)
    save_json(seed.store, KEY_TENSION, TensionSnapshot(value=91.0, gain=1.3))
    save_float(seed.store, KEY_GAIN, 1.0)

    line = restart(tmp_path)

    assert [hold.code for hold in line.holds()] == ["gain-divergence"]


def test_track_behind_weld_holds_the_line(tmp_path: Path) -> None:
    seed = restart(tmp_path)
    save_json(seed.store, KEY_WELD, WeldRecord(seam_id="s-9", position=100.0))
    save_json(seed.store, KEY_TRACK, TrackSnapshot(position=40.0, seam_id="s-9"))

    line = restart(tmp_path)

    assert [hold.code for hold in line.holds()] == ["track-behind-weld"]


def test_track_seam_mismatch_holds_the_line(tmp_path: Path) -> None:
    seed = restart(tmp_path)
    save_json(seed.store, KEY_WELD, WeldRecord(seam_id="s-9", position=40.0))
    save_json(seed.store, KEY_TRACK, TrackSnapshot(position=100.0, seam_id="s-1"))

    line = restart(tmp_path)

    assert [hold.code for hold in line.holds()] == ["track-seam-mismatch"]


def test_low_acid_with_running_pump_raises_two_holds(tmp_path: Path) -> None:
    seed = restart(tmp_path)
    save_json(seed.store, KEY_PUMP, PumpSnapshot(running=True))
    save_json(seed.store, KEY_ACID, AcidSnapshot(level=1.0))

    line = restart(tmp_path)

    assert {hold.code for hold in line.holds()} == {"pump-running", "acid-level-low"}
    line.confirm_hold("pump-running")
    assert line.phase is Phase.RESTRICTED
    line.confirm_hold("acid-level-low")
    assert line.phase is Phase.IDLE


def test_confirm_unknown_hold_raises(tmp_path: Path) -> None:
    line = restart(tmp_path)
    with pytest.raises(UnknownHold):
        line.confirm_hold("pump-running")


def test_hold_events_are_audited(tmp_path: Path) -> None:
    seed = restart(tmp_path)
    save_json(seed.store, KEY_PUMP, PumpSnapshot(running=True))

    line = restart(tmp_path)
    line.confirm_hold("pump-running", note="现场确认泵已停")

    messages = [
        event.message for event in line.audit.events() if event.kind is EventKind.HOLD
    ]
    assert messages == [
        "raised pump-running",
        "confirmed pump-running (现场确认泵已停)",
        "released",
    ]


def test_production_cycle_blocked_until_confirmed(tmp_path: Path) -> None:
    seed = restart(tmp_path)
    save_json(seed.store, KEY_PUMP, PumpSnapshot(running=True))
    line = restart(tmp_path)
    payload = CycleInput(
        coil_id="C-1",
        target=90.0,
        level=4.5,
        voltage=24.0,
        current=300.0,
        speed=1.4,
    )

    with pytest.raises(HoldActive):
        line.production_cycle(payload)

    line.confirm_hold("pump-running")
    line.production_cycle(payload)
    assert line.phase is Phase.IDLE


def test_holds_api(tmp_path: Path) -> None:
    seed = restart(tmp_path)
    save_json(seed.store, KEY_PUMP, PumpSnapshot(running=True))
    line = restart(tmp_path)
    client = create_app(line, WEB_ROOT).test_client()

    response = client.get("/api/recovery/holds")
    assert response.status_code == 200
    body = response.get_json()
    assert body["restricted"] is True
    assert [hold["code"] for hold in body["holds"]] == ["pump-running"]
    assert body["health"]["ok"] is False

    response = client.post("/api/pump/start?level=4.5")
    assert response.status_code == 409
    assert response.get_json()["detail"]["code"] == "hold-active"

    assert client.post("/api/recovery/confirm").status_code == 400
    assert client.post("/api/recovery/confirm?code=nope").status_code == 409

    response = client.post("/api/recovery/confirm?code=pump-running")
    assert response.status_code == 200
    assert response.get_json()["phase"] == "idle"

    body = client.get("/api/recovery/holds").get_json()
    assert body["restricted"] is False
    assert body["holds"] == []
    assert client.get("/api/state").get_json()["state"]["restricted"] is False
