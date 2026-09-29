"""In-memory and persisted register of pending start-up checklist items."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..domain.records import utcnow
from ..store.filestore import FileStore
from ..store.keys import KEY_HEALTH
from .items import HealthItem, Severity, capabilities
from .restriction import RestrictionError, RestrictionErrorCode


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    """Persisted checklist so a service restart remembers sign-offs."""

    items: tuple[HealthItem, ...] = ()
    checked_at: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [item.to_dict() for item in self.items],
            "checkedAt": self.checked_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "HealthSnapshot":
        entries = raw.get("items", ())
        items: list[HealthItem] = []
        for entry in entries if isinstance(entries, list) else ():
            if not isinstance(entry, dict):
                continue
            try:
                severity = Severity(str(entry.get("severity", Severity.BLOCKING.value)))
            except ValueError:
                severity = Severity.BLOCKING
            items.append(
                HealthItem(
                    id=str(entry.get("id", "")),
                    subsystem=str(entry.get("subsystem", "")),
                    severity=severity,
                    expected=str(entry.get("expected", "")),
                    actual=str(entry.get("actual", "")),
                    detail=str(entry.get("detail", "")),
                    basis=str(entry.get("basis", "field")),
                    confirmed=bool(entry.get("confirmed", False)),
                )
            )
        checked = raw.get("checkedAt")
        return cls(
            items=tuple(item for item in items if item.id),
            checked_at=(
                datetime.fromisoformat(checked)
                if isinstance(checked, str)
                else utcnow()
            ),
        )


class HealthRegistry:
    """Holds the checklist and the capabilities its pending items hold back."""

    def __init__(self) -> None:
        self._items: dict[str, HealthItem] = {}
        self._checked_at: datetime = utcnow()
        #: Items ever signed off; a later re-evaluation cannot reopen them.
        self._confirmed_ids: set[str] = set()

    # -- rebuild from a fresh evaluation ------------------------------------

    def rebuild(self, items: tuple[HealthItem, ...]) -> frozenset[str]:
        """Replace the checklist, preserving prior sign-offs.

        Returns the ids that dropped out of the new checklist while still
        pending: the operator supplied readings that now match, so the line
        can adopt the residual state for those items automatically.
        """

        previously_pending = {
            item.id for item in self._items.values() if not item.confirmed
        }
        carried: dict[str, HealthItem] = {}
        for item in items:
            if item.id in self._confirmed_ids:
                item = HealthItem(
                    id=item.id,
                    subsystem=item.subsystem,
                    severity=item.severity,
                    expected=item.expected,
                    actual=item.actual,
                    detail=item.detail,
                    basis=item.basis,
                    confirmed=True,
                )
            carried[item.id] = item
        resolved = frozenset(previously_pending - set(carried))
        self._items = carried
        # Forget sign-offs for items that no longer appear in the checklist,
        # so a genuinely new discrepancy after a later boot is not suppressed.
        self._confirmed_ids &= set(carried)
        self._checked_at = utcnow()
        return resolved

    # -- operator sign-off --------------------------------------------------

    def confirm(self, item_id: str) -> HealthItem:
        item = self._items.get(item_id)
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
        confirmed = HealthItem(
            id=item.id,
            subsystem=item.subsystem,
            severity=item.severity,
            expected=item.expected,
            actual=item.actual,
            detail=item.detail,
            basis=item.basis,
            confirmed=True,
        )
        self._items[item_id] = confirmed
        self._confirmed_ids.add(item_id)
        self._checked_at = utcnow()
        return confirmed

    # -- projections --------------------------------------------------------

    def all(self) -> tuple[HealthItem, ...]:
        return tuple(self._items.values())

    def get(self, item_id: str) -> HealthItem | None:
        return self._items.get(item_id)

    def pending(self) -> tuple[HealthItem, ...]:
        return tuple(item for item in self._items.values() if not item.confirmed)

    def blocking_pending(self) -> tuple[HealthItem, ...]:
        return tuple(
            item
            for item in self._items.values()
            if not item.confirmed and item.severity is Severity.BLOCKING
        )

    def advisory_pending(self) -> tuple[HealthItem, ...]:
        return tuple(
            item
            for item in self._items.values()
            if not item.confirmed and item.severity is Severity.ADVISORY
        )

    def restricted(self) -> bool:
        return bool(self.blocking_pending())

    def held_capabilities(self) -> frozenset[str]:
        held: set[str] = set()
        for item in self.blocking_pending():
            held.update(capabilities(item.id))
        return frozenset(held)

    def require(self, *needed: str) -> None:
        """Refuse a command when any capability it needs is still held back."""

        held = self.held_capabilities()
        blocked = tuple(cap for cap in needed if cap in held)
        if blocked:
            pending = tuple(item.id for item in self.blocking_pending())
            raise RestrictionError(
                RestrictionErrorCode.PENDING,
                f"capabilities awaiting field confirmation: {', '.join(blocked)}",
                pending,
            )

    def checked_at(self) -> datetime:
        return self._checked_at

    def view(self) -> dict[str, object]:
        return {
            "restricted": self.restricted(),
            "heldCapabilities": sorted(self.held_capabilities()),
            "checkedAt": self._checked_at.isoformat(),
            "pending": [item.to_dict() for item in self.pending()],
            "items": [item.to_dict() for item in self.all()],
        }

    # -- persistence --------------------------------------------------------

    def snapshot(self) -> HealthSnapshot:
        return HealthSnapshot(items=self.all(), checked_at=self._checked_at)

    def save(self, store: FileStore) -> None:
        payload = json.dumps(self.snapshot().to_dict(), ensure_ascii=False).encode(
            "utf-8"
        )
        store.save(KEY_HEALTH, payload)

    def load(self, store: FileStore) -> None:
        raw = store.load(KEY_HEALTH)
        if raw is None or not raw.strip():
            return
        try:
            snapshot = HealthSnapshot.from_dict(json.loads(raw.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        self._items = {item.id: item for item in snapshot.items}
        self._confirmed_ids = {
            item.id for item in snapshot.items if item.confirmed
        }
        self._checked_at = snapshot.checked_at
