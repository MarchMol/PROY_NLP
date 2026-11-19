"""Sesión firmada (HMAC) en cookie. El rol de cada consulta sale de aquí, nunca del texto.

Formato: `<rol>.<expira_epoch>.<hmac_sha256>`. Sin cookie válida, el rol es `jugador`.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from marioqa.index.access import ROLE_RANK, Role

COOKIE = "marioqa_session"


class SessionSigner:
    def __init__(self, secret: str | None, ttl_s: int) -> None:
        self._key = (secret or secrets.token_hex(32)).encode()
        self.ttl_s = ttl_s

    def _sig(self, payload: str) -> str:
        return hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()

    def issue(self, role: Role, now: float | None = None) -> str:
        payload = f"{role}.{int((now or time.time()) + self.ttl_s)}"
        return f"{payload}.{self._sig(payload)}"

    def role(self, cookie: str | None, now: float | None = None) -> Role:
        if not cookie or cookie.count(".") != 2:
            return "jugador"
        role, exp, sig = cookie.split(".")
        payload = f"{role}.{exp}"
        if not hmac.compare_digest(sig, self._sig(payload)):
            return "jugador"
        if role not in ROLE_RANK or not exp.isdigit() or int(exp) < (now or time.time()):
            return "jugador"
        return role  # type: ignore[return-value]


def check_admin_token(given: str, expected: str | None) -> bool:
    return bool(expected) and hmac.compare_digest(given.encode(), (expected or "").encode())
