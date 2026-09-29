"""Persisted and in-flight process records."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .events import utcnow


def _stamp(raw: Any) -> datetime:
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str):
        return datetime.fromisoformat(raw)
    return utcnow()


def _number(raw: Any) -> float | None:
    """Coerce an optional JSON number; non-numeric or missing values stay None."""

    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value


def _flag(raw: Any) -> bool | None:
    """Coerce an optional JSON boolean; other types stay None."""

    if isinstance(raw, bool):
        return raw
    return None


@dataclass(frozen=True, slots=True)
class Confirm:
    """Outcome of a strip head alignment check."""

    ok: bool = False
    position: float = 0.0
    at: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "position": self.position, "at": self.at.isoformat()}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Confirm":
        return cls(
            ok=bool(raw.get("ok", False)),
            position=float(raw.get("position", 0.0)),
            at=_stamp(raw.get("at")),
        )


@dataclass(frozen=True, slots=True)
class WeldRecord:
    """A completed weld seam."""

    seam_id: str = ""
    position: float = 0.0
    tension: float = 0.0
    heat_input: float = 0.0
    at: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seam_id": self.seam_id,
            "position": self.position,
            "tension": self.tension,
            "heat_input": self.heat_input,
            "at": self.at.isoformat(),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "WeldRecord":
        return cls(
            seam_id=str(raw.get("seam_id", "")),
            position=float(raw.get("position", 0.0)),
            tension=float(raw.get("tension", 0.0)),
            heat_input=float(raw.get("heat_input", 0.0)),
            at=_stamp(raw.get("at")),
        )


@dataclass(frozen=True, slots=True)
class TensionSnapshot:
    value: float = 0.0
    gain: float = 1.0
    saved: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "gain": self.gain, "saved": self.saved.isoformat()}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TensionSnapshot":
        return cls(
            value=float(raw.get("value", 0.0)),
            gain=float(raw.get("gain", 1.0)),
            saved=_stamp(raw.get("saved")),
        )


@dataclass(frozen=True, slots=True)
class AcidSnapshot:
    level: float = 0.0
    offset: float = 0.0
    saved: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {"level": self.level, "offset": self.offset, "saved": self.saved.isoformat()}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AcidSnapshot":
        return cls(
            level=float(raw.get("level", 0.0)),
            offset=float(raw.get("offset", 0.0)),
            saved=_stamp(raw.get("saved")),
        )


@dataclass(frozen=True, slots=True)
class PumpSnapshot:
    running: bool = False
    saved: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {"running": self.running, "saved": self.saved.isoformat()}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PumpSnapshot":
        return cls(running=bool(raw.get("running", False)), saved=_stamp(raw.get("saved")))


@dataclass(frozen=True, slots=True)
class TrackSnapshot:
    position: float = 0.0
    seam_id: str = ""
    saved: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "position": self.position,
            "seam_id": self.seam_id,
            "saved": self.saved.isoformat(),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TrackSnapshot":
        return cls(
            position=float(raw.get("position", 0.0)),
            seam_id=str(raw.get("seam_id", "")),
            saved=_stamp(raw.get("saved")),
        )


@dataclass(frozen=True, slots=True)
class RinseSnapshot:
    active: bool = False
    saved: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {"active": self.active, "saved": self.saved.isoformat()}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RinseSnapshot":
        return cls(active=bool(raw.get("active", False)), saved=_stamp(raw.get("saved")))


@dataclass(frozen=True, slots=True)
class LatchSnapshot:
    interlock: bool = False
    alarm: bool = False
    saved: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "interlock": self.interlock,
            "alarm": self.alarm,
            "saved": self.saved.isoformat(),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "LatchSnapshot":
        return cls(
            interlock=bool(raw.get("interlock", False)),
            alarm=bool(raw.get("alarm", False)),
            saved=_stamp(raw.get("saved")),
        )


@dataclass(frozen=True, slots=True)
class FieldReport:
    """Field-side readings handed to the line at start-up.

    Every value is optional: a missing reading means the subsystem did not
    report, and the health check treats that as an item the operator must
    confirm before the line is released.
    """

    tension: float | None = None
    gain: float | None = None
    pump_running: bool | None = None
    rinse_active: bool | None = None
    acid_level: float | None = None
    track_position: float | None = None
    weld_latch: bool | None = None
    weld_alarm: bool | None = None
    reported_at: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tension": self.tension,
            "gain": self.gain,
            "pumpRunning": self.pump_running,
            "rinseActive": self.rinse_active,
            "acidLevel": self.acid_level,
            "trackPosition": self.track_position,
            "weldLatch": self.weld_latch,
            "weldAlarm": self.weld_alarm,
            "reportedAt": self.reported_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "FieldReport":
        return cls(
            tension=_number(raw.get("tension")),
            gain=_number(raw.get("gain")),
            pump_running=_flag(raw.get("pumpRunning")),
            rinse_active=_flag(raw.get("rinseActive")),
            acid_level=_number(raw.get("acidLevel")),
            track_position=_number(raw.get("trackPosition")),
            weld_latch=_flag(raw.get("weldLatch")),
            weld_alarm=_flag(raw.get("weldAlarm")),
            reported_at=_stamp(raw.get("reportedAt")),
        )
