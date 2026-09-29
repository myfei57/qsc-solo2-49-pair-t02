"""Line-level failures raised while recovery holds are pending."""

from __future__ import annotations


class HoldError(RuntimeError):
    """Base class for recovery-hold failures."""


class HoldActive(HoldError):
    """A production command was attempted with holds still pending."""

    def __init__(self, codes: list[str]) -> None:
        detail = "recovery holds pending: " + ", ".join(codes)
        super().__init__(detail)
        self.codes = tuple(codes)
        self.detail = detail

    def to_dict(self) -> dict[str, object]:
        return {"code": "hold-active", "detail": self.detail, "holds": list(self.codes)}


class UnknownHold(HoldError):
    """A confirmation referenced a hold that is not pending."""

    def __init__(self, code: str) -> None:
        detail = f"no pending hold named {code!r}"
        super().__init__(detail)
        self.hold = code
        self.detail = detail

    def to_dict(self) -> dict[str, str]:
        return {"code": "unknown-hold", "detail": self.detail}
