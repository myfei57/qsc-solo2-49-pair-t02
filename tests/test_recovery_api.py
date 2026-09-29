"""Recovery health check HTTP endpoints."""

from __future__ import annotations

from pathlib import Path

from linecontrol.console.app import create_app
from linecontrol.domain.records import (
    LatchSnapshot,
    PumpSnapshot,
    RinseSnapshot,
    TensionSnapshot,
)
from linecontrol.line.machine import Line
from linecontrol.store.keys import KEY_LATCH, KEY_PUMP, KEY_RINSE, KEY_TENSION
from linecontrol.store.snapshot import save_float, save_json

WEB_ROOT = Path(__file__).resolve().parents[1] / "web"


def _line_with_residuals(tmp_path: Path) -> Line:
    line = Line(tmp_path)
    line.recover()
    save_json(line.store, KEY_TENSION, TensionSnapshot(value=90.0, gain=1.1))
    save_float(line.store, "tension.gain", 1.1)
    save_json(line.store, KEY_PUMP, PumpSnapshot(running=True))
    save_json(line.store, KEY_RINSE, RinseSnapshot(active=True))
    save_json(line.store, KEY_LATCH, LatchSnapshot(interlock=False, alarm=False))
    return line


def test_health_endpoint_reports_clean_boot(tmp_path: Path) -> None:
    client = create_app(Line(tmp_path), WEB_ROOT).test_client()
    body = client.get("/api/recovery/health").get_json()
    assert body["ok"] is True
    assert body["health"]["restricted"] is False


def _matching() -> dict[str, str]:
    return {
        "tension": "90.0",
        "gain": "1.1",
        "pumpRunning": "true",
        "rinseActive": "true",
        "weldLatch": "false",
        "weldAlarm": "false",
    }


def test_field_report_lists_mismatch_and_restricts(tmp_path: Path) -> None:
    line = _line_with_residuals(tmp_path)
    client = create_app(line, WEB_ROOT).test_client()
    params = _matching()
    params["pumpRunning"] = "false"
    response = client.post("/api/recovery/field", query_string=params)
    assert response.status_code == 200
    health = response.get_json()["health"]
    assert health["restricted"] is True
    assert "pickle" in health["heldCapabilities"]
    assert [item["id"] for item in health["pending"]] == ["pump-run"]
    state = client.get("/api/state").get_json()["state"]
    assert state["phase"] == "restricted"


def test_restricted_command_is_conflict(tmp_path: Path) -> None:
    line = _line_with_residuals(tmp_path)
    client = create_app(line, WEB_ROOT).test_client()
    params = _matching()
    params["pumpRunning"] = "false"
    client.post("/api/recovery/field", query_string=params)
    response = client.post("/api/pump/start?level=6.0")
    assert response.status_code == 409
    body = response.get_json()
    assert body["detail"]["code"] == "pending-confirmation"
    assert "pump-run" in body["detail"]["items"]


def test_confirm_releases_the_line(tmp_path: Path) -> None:
    line = _line_with_residuals(tmp_path)
    client = create_app(line, WEB_ROOT).test_client()
    params = _matching()
    params["pumpRunning"] = "false"
    client.post("/api/recovery/field", query_string=params)
    response = client.post("/api/recovery/confirm?item=pump-run&basis=residual")
    assert response.status_code == 200
    assert response.get_json()["health"]["restricted"] is False
    assert client.get("/api/state").get_json()["state"]["phase"] == "idle"


def test_confirm_unknown_item_is_conflict(tmp_path: Path) -> None:
    line = _line_with_residuals(tmp_path)
    client = create_app(line, WEB_ROOT).test_client()
    response = client.post("/api/recovery/confirm?item=nope")
    assert response.status_code == 409


def test_confirm_requires_item_parameter(tmp_path: Path) -> None:
    client = create_app(Line(tmp_path), WEB_ROOT).test_client()
    assert client.post("/api/recovery/confirm").status_code == 400


def test_bad_bool_parameter_is_a_bad_request(tmp_path: Path) -> None:
    client = create_app(Line(tmp_path), WEB_ROOT).test_client()
    response = client.post("/api/recovery/field?pumpRunning=maybe")
    assert response.status_code == 400


def test_clear_field_report(tmp_path: Path) -> None:
    line = _line_with_residuals(tmp_path)
    client = create_app(line, WEB_ROOT).test_client()
    client.post("/api/recovery/field?pumpRunning=false&rinseActive=true")
    response = client.delete("/api/recovery/field")
    assert response.status_code == 200
    assert response.get_json()["cleared"] is True
