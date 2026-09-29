"""Raised when an operator command touches a capability still pending sign-off."""

from __future__ import annotations

from enum import Enum


class RestrictionErrorCode(str, Enum):
    PENDING = "pending-confirmation"
    UNKNOWN_ITEM = "unknown-item"


class RestrictionError(RuntimeError):
    """Maps to a 409 response with the pending checklist items attached."""

    def __init__(
        self,
        code: RestrictionErrorCode,
        detail: str,
        items: tuple[str, ...] = (),
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.items = tuple(items)

    def to_dict(self) -> dict[str, object]:
        return {"code": self.code.value, "detail": self.detail, "items": list(self.items)}
