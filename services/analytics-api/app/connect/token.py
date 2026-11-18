"""A customer admin's token, held for one setup run and never anywhere else (threat T1).

The wrapper cannot be printed, formatted, pickled, copied or serialised into a record by accident, expires on a hard
clock, and is wiped when the run ends. Python cannot scrub a string from memory, so the guarantee is the one that can be
tested: no reference is kept by this code after the run, and every path that could carry it out is closed.
"""

from __future__ import annotations

import time
from collections.abc import Callable

MAX_TTL_SECONDS = 300.0


class TokenUnavailable(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class AdminToken:
    __slots__ = ("_value", "_expires_at", "_clock")

    def __init__(self, value: str, *, ttl_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        if not value:
            raise ValueError("a token is required")
        if not 0 < ttl_seconds <= MAX_TTL_SECONDS:
            raise ValueError(f"a token may live at most {MAX_TTL_SECONDS:.0f} seconds")
        self._value: str | None = value
        self._clock = clock
        self._expires_at = clock() + ttl_seconds

    def reveal(self) -> str:
        """The value, for the one call that signs in. Raises once expired or wiped."""
        if self._value is None:
            raise TokenUnavailable("token_wiped")
        if self._clock() >= self._expires_at:
            self.wipe()
            raise TokenUnavailable("token_expired")
        return self._value

    @property
    def usable(self) -> bool:
        return self._value is not None and self._clock() < self._expires_at

    def wipe(self) -> None:
        self._value = None

    def __repr__(self) -> str:
        return "AdminToken(redacted)"

    __str__ = __repr__

    def __format__(self, spec: str) -> str:
        return "AdminToken(redacted)"

    def __reduce__(self):
        raise TypeError("an admin token cannot be serialised")

    def __copy__(self):
        raise TypeError("an admin token cannot be copied")

    def __deepcopy__(self, memo):
        raise TypeError("an admin token cannot be copied")
