"""PIN hashing and brute-force lockout."""
import hashlib
import hmac
import os
import time
from collections.abc import Callable

PIN_PATTERN = r"^\d{4,8}$"

_N, _R, _P = 2**14, 8, 1


def _scrypt(pin: str, salt: bytes) -> bytes:
    return hashlib.scrypt(pin.encode(), salt=salt, n=_N, r=_R, p=_P)


def hash_pin(pin: str) -> str:
    salt = os.urandom(16)
    return f"scrypt${salt.hex()}${_scrypt(pin, salt).hex()}"


def verify_pin(pin: str, hashed: str) -> bool:
    try:
        scheme, salt_hex, hash_hex = hashed.split("$")
        if scheme != "scrypt":
            return False
        expected = bytes.fromhex(hash_hex)
        actual = _scrypt(pin, bytes.fromhex(salt_hex))
    except ValueError:
        return False
    return hmac.compare_digest(actual, expected)


class PinLockout:
    def __init__(self, max_attempts: int = 5, lock_seconds: float = 300,
                 clock: Callable[[], float] = time.monotonic):
        self._max = max_attempts
        self._seconds = lock_seconds
        self._clock = clock
        self._failures = 0
        self._locked_until: float | None = None

    def is_locked(self) -> bool:
        if self._locked_until is None:
            return False
        if self._clock() >= self._locked_until:
            self._locked_until = None
            self._failures = 0
            return False
        return True

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self._max:
            self._locked_until = self._clock() + self._seconds

    def reset(self) -> None:
        self._failures = 0
        self._locked_until = None
