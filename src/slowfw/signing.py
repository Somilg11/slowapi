"""HMAC-signed, optionally time-limited payloads for cookies and tokens.

Sessions and "remember me" cookies need tamper detection, not encryption, and
the standard library already has everything required.  Values are signed with
HMAC-SHA256, compared in constant time, and namespaced by a salt so a session
cookie can never be replayed as a password-reset token.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import typing as t

from .exceptions import SlowAPIError

__all__ = ["BadSignature", "SignatureExpired", "Signer"]


class BadSignature(SlowAPIError):
    """The payload was modified, truncated, or signed with another key."""


class SignatureExpired(BadSignature):
    """The signature was valid but older than ``max_age``."""


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


class Signer:
    """Signs and verifies JSON payloads.

    :param secret: The application secret.  Never hard-code it; read it from
        the environment and rotate it by passing old keys in ``fallbacks``.
    :param salt: Namespace for this signer's tokens.
    :param fallbacks: Previous secrets accepted for verification only, so a key
        rotation does not log every user out.
    """

    def __init__(
        self,
        secret: str | bytes,
        *,
        salt: str = "slowfw.session",
        fallbacks: t.Sequence[str | bytes] = (),
    ) -> None:
        if not secret:
            raise SlowAPIError("Signer requires a non-empty secret")
        self.salt = salt
        self._keys = [self._derive(secret)] + [self._derive(k) for k in fallbacks]

    def _derive(self, secret: str | bytes) -> bytes:
        raw = secret.encode() if isinstance(secret, str) else secret
        return hashlib.sha256(self.salt.encode() + b"|" + raw).digest()

    def _signature(self, payload: bytes, key: bytes) -> str:
        return _b64encode(hmac.new(key, payload, hashlib.sha256).digest())

    def sign(self, data: t.Any) -> str:
        """Return ``<payload>.<timestamp>.<signature>``."""
        body = _b64encode(json.dumps(data, separators=(",", ":")).encode())
        stamp = _b64encode(str(int(time.time())).encode())
        message = f"{body}.{stamp}".encode()
        return f"{body}.{stamp}.{self._signature(message, self._keys[0])}"

    def unsign(self, token: str, *, max_age: int | None = None) -> t.Any:
        """Verify ``token`` and return the payload, or raise."""
        try:
            body, stamp, signature = token.split(".")
        except ValueError:
            raise BadSignature("Malformed token") from None

        message = f"{body}.{stamp}".encode()
        if not any(
            hmac.compare_digest(signature, self._signature(message, key)) for key in self._keys
        ):
            raise BadSignature("Signature does not match")

        try:
            issued = int(_b64decode(stamp).decode())
        except (ValueError, UnicodeDecodeError):
            raise BadSignature("Malformed timestamp") from None

        if max_age is not None and time.time() - issued > max_age:
            raise SignatureExpired(f"Token is older than {max_age}s")

        try:
            return json.loads(_b64decode(body))
        except (ValueError, UnicodeDecodeError):
            raise BadSignature("Malformed payload") from None

    def safe_unsign(
        self, token: str | None, *, max_age: int | None = None, default: t.Any = None
    ) -> t.Any:
        """Like :meth:`unsign` but returns ``default`` instead of raising."""
        if not token:
            return default
        try:
            return self.unsign(token, max_age=max_age)
        except BadSignature:
            return default
