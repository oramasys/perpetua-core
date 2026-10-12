"""Neutral delivery contracts and failure types for perpetua-core."""
from __future__ import annotations


class CriticalDeliveryFailed(RuntimeError):
    """Neutral Core-side failure type. Carries a sanitized, stable cause code.

    Core has NO knowledge of Oramasys terminal policy -- it just raises this;
    Oramasys's observer catches it, records run/attempt evidence via the
    EXISTING UnknownDisposition type, and maps it to stop + delivery uncertainty.
    """

    def __init__(self, cause_code: str, message: str | None = None) -> None:
        """Keep the stable cause code beside a message that never echoes sink internals."""
        super().__init__(message or f"Critical delivery failed: {cause_code}")
        self.cause_code = cause_code
