"""Pure evaluation of residual snapshots against the start-up field report.

A *blocking* item keeps one or more line capabilities in a safe posture until
an operator confirms it. An *advisory* item is shown on the same checklist but
never blocks production, because it cannot put equipment in an unsafe state.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..domain.records import FieldReport
from ..store.keys import (
    KEY_ACID,
    KEY_GAIN,
    KEY_LATCH,
    KEY_PUMP,
    KEY_RINSE,
    KEY_TENSION,
    KEY_TRACK,
)
from ..store.recovery import Recovery

#: Numeric agreement bands for the field comparison.
TENSION_TOL = 1.0
LEVEL_TOL = 0.2
TRACK_TOL = 1.0
GAIN_TOL = 0.05


class Severity(str, Enum):
    BLOCKING = "blocking"
    ADVISORY = "advisory"


#: Item identifiers, grouped by subsystem.
TENSION_SETPOINT = "tension-setpoint"
TENSION_GAIN_DRIFT = "tension-gain-drift"
TENSION_GAIN_CONFLICT = "tension-gain-conflict"
PUMP_RUN = "pump-run"
RINSE_VALVE = "rinse-valve"
PUMP_RINSE = "pump-rinse"
ACID_LEVEL = "acid-level"
SEAM_TRACK = "seam-track"
RINSE_TRACK = "rinse-track"
WELD_LATCH = "weld-latch"
WELD_TENSION = "weld-tension"

#: Operator-facing labels for the confirmation checklist.
LABELS: dict[str, str] = {
    TENSION_SETPOINT: "张力辊设定值",
    TENSION_GAIN_DRIFT: "张力增益偏差",
    TENSION_GAIN_CONFLICT: "张力设定/增益落盘不一致",
    PUMP_RUN: "酸泵运行状态",
    RINSE_VALVE: "喷淋阀状态",
    PUMP_RINSE: "酸泵运行但喷淋关闭",
    ACID_LEVEL: "槽体液位",
    SEAM_TRACK: "焊缝跟踪点",
    RINSE_TRACK: "喷淋开启但无焊缝跟踪点",
    WELD_LATCH: "焊接联锁/过流告警",
    WELD_TENSION: "最近焊缝的张力参数",
}

#: Capabilities held back while an item is still pending confirmation.
CAPABILITIES: dict[str, frozenset[str]] = {
    TENSION_SETPOINT: frozenset({"weld", "strip"}),
    TENSION_GAIN_DRIFT: frozenset(),
    TENSION_GAIN_CONFLICT: frozenset({"weld", "strip"}),
    PUMP_RUN: frozenset({"pickle"}),
    RINSE_VALVE: frozenset({"pickle"}),
    PUMP_RINSE: frozenset({"pickle"}),
    ACID_LEVEL: frozenset({"pickle"}),
    SEAM_TRACK: frozenset({"weld", "strip"}),
    RINSE_TRACK: frozenset({"weld", "pickle", "strip"}),
    WELD_LATCH: frozenset({"weld"}),
    WELD_TENSION: frozenset(),
}

#: Capability identifiers accepted on the confirmation endpoint.
BLOCKING = ("weld", "pickle", "strip")

NOT_REPORTED = "现场未上报"


@dataclass(frozen=True, slots=True)
class HealthItem:
    """One entry of the start-up confirmation checklist."""

    id: str
    subsystem: str
    severity: Severity
    expected: str
    actual: str
    detail: str
    basis: str = "field"
    confirmed: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "subsystem": self.subsystem,
            "label": describe(self.id),
            "severity": self.severity.value,
            "expected": self.expected,
            "actual": self.actual,
            "detail": self.detail,
            "basis": self.basis,
            "confirmed": self.confirmed,
        }


def describe(item_id: str) -> str:
    return LABELS.get(item_id, item_id)


def capabilities(item_id: str) -> frozenset[str]:
    return CAPABILITIES.get(item_id, frozenset())


def _number(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _switch(value: bool, on: str, off: str) -> str:
    return on if value else off


def _mismatch(
    item_id: str,
    subsystem: str,
    severity: Severity,
    expected: str,
    actual: str,
    detail: str,
    basis: str,
) -> HealthItem:
    return HealthItem(
        id=item_id,
        subsystem=subsystem,
        severity=severity,
        expected=expected,
        actual=actual,
        detail=detail,
        basis=basis,
    )


def _field_item(
    item_id: str,
    subsystem: str,
    severity: Severity,
    expected: str,
    field_value: object | None,
    format_field,
    detail: str,
) -> HealthItem | None:
    """Compare one residual value with its field reading.

    A missing field reading becomes a blocking item of its own: the operator
    has to look at the equipment and confirm the residual state manually.
    """

    if field_value is None:
        return _mismatch(
            item_id,
            subsystem,
            Severity.BLOCKING,
            expected,
            NOT_REPORTED,
            f"{detail}；现场无读数，需人工确认",
            "field",
        )
    actual = format_field(field_value)
    if actual != expected:
        return _mismatch(
            item_id, subsystem, severity, expected, actual, detail, "field"
        )
    return None


def _numeric_item(
    item_id: str,
    subsystem: str,
    residual: float,
    field_value: float | None,
    tolerance: float,
    unit: str,
    detail: str,
    severity: Severity = Severity.BLOCKING,
) -> HealthItem | None:
    """Compare a numeric residual with its field reading within a band."""

    residual_text = _number(residual)
    if field_value is None:
        return _mismatch(
            item_id,
            subsystem,
            Severity.BLOCKING,
            residual_text + unit,
            NOT_REPORTED,
            f"{detail}；现场无读数，需人工确认",
            "field",
        )
    if abs(field_value - residual) > tolerance:
        return _mismatch(
            item_id,
            subsystem,
            severity,
            residual_text + unit,
            _number(field_value) + unit,
            detail,
            "field",
        )
    return None


def evaluate(state: Recovery, field: FieldReport | None) -> tuple[HealthItem, ...]:
    """Build the checklist from residual snapshots plus any field readings."""

    items: list[HealthItem] = []
    loaded = set(state.loaded)

    pump_residual_running = KEY_PUMP in loaded and state.pump.running
    rinse_residual_active = KEY_RINSE in loaded and state.rinse.active

    # -- internal consistency: residual snapshots against each other --------
    # These checks only fire without a field report. When field readings are
    # present, the same contradiction surfaces through the field-backed items
    # (an operator only signs one checklist entry per real discrepancy).
    if field is None:
        if KEY_TENSION in loaded and KEY_GAIN in loaded and state.gain > 0:
            if abs(state.tension.gain - state.gain) > GAIN_TOL:
                items.append(
                    _mismatch(
                        TENSION_GAIN_CONFLICT,
                        "tension",
                        Severity.BLOCKING,
                        f"设定文件增益 {_number(state.gain)}",
                        f"张力文件增益 {_number(state.tension.gain)}",
                        "两份张力落盘互相矛盾，禁止以任一份直接恢复",
                        "internal",
                    )
                )
        if pump_residual_running and not rinse_residual_active:
            items.append(
                _mismatch(
                    PUMP_RINSE,
                    "pickle",
                    Severity.BLOCKING,
                    "酸泵运行时喷淋应开启",
                    "酸泵运行、喷淋关闭",
                    "残留状态自相矛盾：无喷淋的酸循环",
                    "internal",
                )
            )
        if rinse_residual_active and KEY_TRACK in loaded and state.track.position <= 0:
            items.append(
                _mismatch(
                    RINSE_TRACK,
                    "strip",
                    Severity.BLOCKING,
                    "喷淋开启时应存在焊缝跟踪点",
                    f"跟踪点 {_number(state.track.position)}、焊缝 "
                    f"{state.track.seam_id or '空'}",
                    "残留状态自相矛盾：喷淋中却没有带头跟踪",
                    "internal",
                )
            )

    # -- residual versus field ---------------------------------------------
    if field is not None:
        tension_item = None
        if KEY_TENSION in loaded and state.tension.value > 0:
            residual = state.tension.value
            tension_item = _numeric_item(
                TENSION_SETPOINT,
                "tension",
                residual,
                field.tension,
                TENSION_TOL,
                "",
                f"残留张力设定 {_number(residual)} 与现场不一致",
            )
            if tension_item is not None:
                items.append(tension_item)
            if field.gain is not None and state.gain > 0:
                if abs(field.gain - state.gain) > GAIN_TOL:
                    items.append(
                        _mismatch(
                            TENSION_GAIN_DRIFT,
                            "tension",
                            Severity.ADVISORY,
                            f"落盘增益 {_number(state.gain)}",
                            f"现场增益 {_number(field.gain)}",
                            "增益偏差超过标定噪声带，建议重新标定",
                            "field",
                        )
                    )
        # Advisory: the latest seam's tension is redundant while the blocking
        # setpoint item already covers the same reading.
        if state.weld.seam_id and state.weld.tension > 0 and field.tension is None:
            items.append(
                _mismatch(
                    WELD_TENSION,
                    "weld",
                    Severity.ADVISORY,
                    f"焊缝 {state.weld.seam_id} 张力 "
                    f"{_number(state.weld.tension)}",
                    NOT_REPORTED,
                    "最近一条焊缝的张力现场无读数，仅作提示",
                    "field",
                )
            )
        elif (
            state.weld.seam_id
            and state.weld.tension > 0
            and field.tension is not None
            and tension_item is None
            and abs(field.tension - state.weld.tension) > TENSION_TOL
        ):
            items.append(
                _mismatch(
                    WELD_TENSION,
                    "weld",
                    Severity.ADVISORY,
                    f"落盘 {_number(state.weld.tension)}",
                    f"现场 {_number(field.tension)}",
                    f"焊缝 {state.weld.seam_id} 焊接张力与落盘不符",
                    "field",
                )
            )
        if KEY_PUMP in loaded:
            pump_item = _field_item(
                PUMP_RUN,
                "pump",
                Severity.BLOCKING,
                _switch(state.pump.running, "运行", "停止"),
                field.pump_running,
                lambda v: _switch(v, "运行", "停止"),
                "酸泵启停状态与残留不一致",
            )
            if pump_item is not None:
                items.append(pump_item)
        if KEY_RINSE in loaded and state.rinse.active:
            rinse_item = _field_item(
                RINSE_VALVE,
                "pickle",
                Severity.BLOCKING,
                _switch(state.rinse.active, "开", "关"),
                field.rinse_active,
                lambda v: _switch(v, "开", "关"),
                "喷淋阀位与残留不一致",
            )
            if rinse_item is not None:
                items.append(rinse_item)
        if KEY_ACID in loaded:
            acid_item = _numeric_item(
                ACID_LEVEL,
                "pickle",
                state.acid.level,
                field.acid_level,
                LEVEL_TOL,
                "",
                "槽体液位与残留不一致",
            )
            if acid_item is not None:
                items.append(acid_item)
        if KEY_TRACK in loaded:
            track_item = _numeric_item(
                SEAM_TRACK,
                "strip",
                state.track.position,
                field.track_position,
                TRACK_TOL,
                "",
                "焊缝跟踪点与残留不一致",
            )
            if track_item is not None:
                items.append(track_item)
        if KEY_LATCH in loaded:
            residual_latch = _switch(state.latch.interlock, "锁定", "释放")
            if field.weld_latch is None:
                items.append(
                    _mismatch(
                        WELD_LATCH,
                        "weld",
                        Severity.BLOCKING,
                        f"联锁{residual_latch}",
                        NOT_REPORTED,
                        "焊接联锁状态现场无读数，需人工确认",
                        "field",
                    )
                )
            elif (
                field.weld_latch != state.latch.interlock
                or (field.weld_alarm is not None and field.weld_alarm != state.latch.alarm)
            ):
                actual = f"联锁{_switch(field.weld_latch, '锁定', '释放')}"
                if field.weld_alarm is not None:
                    actual += f"、告警{_switch(field.weld_alarm, '有', '无')}"
                items.append(
                    _mismatch(
                        WELD_LATCH,
                        "weld",
                        Severity.BLOCKING,
                        f"联锁{residual_latch}、告警"
                        f"{_switch(state.latch.alarm, '有', '无')}",
                        actual,
                        "焊接联锁或过流告警与残留不一致",
                        "field",
                    )
                )

    return tuple(items)


def items_view(items: tuple[HealthItem, ...] | list[HealthItem]) -> list[dict]:
    return [item.to_dict() for item in items]
