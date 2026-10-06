"""Injectable time sources for platform services.

All platform services depend on a ``Clock`` — a zero-argument callable that
returns the current ``datetime``. Injecting a deterministic clock keeps the
behaviour of schedulers, token expiry, and quota windows fully testable without
touching the system clock or sleeping in tests.

Two ready-to-use implementations are provided:

  * :class:`SystemClock` — production default, returns ``datetime.utcnow()``.
  * :class:`TestClock` — mutable clock whose ``now`` attribute is advanced
    explicitly from tests; calling the instance returns the current ``now``.

The module also publishes a :data:`Clock` type alias so service signatures can
declare the dependency without importing a concrete class.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta


class SystemClock:
    """Callable clock returning ``datetime.utcnow()`` on each call."""

    def __call__(self) -> datetime:
        return datetime.utcnow()


class TestClock:
    """Mutable deterministic clock for tests.

    The clock starts at ``start`` (defaults to a stable epoch) and only advances
    when the test calls :meth:`advance` or assigns :attr:`now`. Calling the
    instance returns the current ``now`` unchanged, mirroring :class:`SystemClock`.
    """

    # Sentinel that prevents pytest from collecting this class as a test suite
    # just because its name starts with "Test".
    __test__ = False

    def __init__(self, start: datetime | None = None) -> None:
        # Stable deterministic default; avoids depending on wall-clock at import time.
        self.now: datetime = start if start is not None else datetime(2026, 1, 1, 0, 0, 0)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> datetime:
        """Advance the clock by a ``timedelta`` expressed in keyword units.

        Accepts the same units as :class:`datetime.timedelta` (``seconds``,
        ``minutes``, ``hours``, ``days``, ...). Returns the new ``now`` value.
        """
        self.now = self.now + timedelta(**kwargs)
        return self.now

    def set(self, value: datetime) -> datetime:
        """Set the clock to an absolute timestamp."""
        self.now = value
        return self.now


# Type alias used in service signatures: any zero-arg callable returning a datetime.
Clock = Callable[[], datetime]
