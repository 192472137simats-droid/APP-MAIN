"""Accounts and sessions.

Deliberately small and dependency-free: scrypt from hashlib for password
hashing, secrets for tokens, and the existing SQLite layer for storage. No
bcrypt, passlib or JWT library - none would earn its place here.

Sessions are opaque server-side tokens rather than signed cookies, so logging
out and revoking a stolen session actually work.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from .. import db

SESSION_COOKIE = "ralm_session"
SESSION_DAYS = 30

# scrypt parameters. n=2**14 keeps a login to a few hundred milliseconds on a
# small Railway instance - slow enough to hurt offline cracking, fast enough
# that logging in does not feel broken.
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_KEY_LEN = 64

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 200

# Intentionally permissive: validating email syntax precisely is a losing game,
# and all that matters here is that it looks like an address.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthError(Exception):
    """Raised when a credential is malformed, wrong, or already taken."""


def _hash(password: str, salt: str) -> str:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=bytes.fromhex(salt),
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_KEY_LEN,
    ).hex()


def validate_credentials(email: str, password: str) -> str:
    """Check an email/password pair, returning the normalised email."""
    email = (email or "").strip().lower()
    if not _EMAIL.match(email):
        raise AuthError("That does not look like an email address.")
    if len(email) > 320:
        raise AuthError("That email address is too long.")
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise AuthError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > MAX_PASSWORD_LENGTH:
        # scrypt over an unbounded input is a cheap way to burn server CPU.
        raise AuthError(f"Password must be at most {MAX_PASSWORD_LENGTH} characters.")
    return email


def register(email: str, password: str) -> dict[str, Any]:
    """Create an account. The first account inherits any pre-auth data."""
    email = validate_credentials(email, password)
    if db.get_user_by_email(email):
        raise AuthError("An account with that email already exists.")

    first_account = db.count_users() == 0
    salt = secrets.token_hex(16)
    user_id = db.create_user(email, _hash(password, salt), salt)

    claimed = db.claim_unowned_rows(user_id) if first_account else {}
    return {"id": user_id, "email": email, "claimed": claimed}


def authenticate(email: str, password: str) -> dict[str, Any]:
    """Verify a login. The failure message never says which half was wrong."""
    email = (email or "").strip().lower()
    user = db.get_user_by_email(email)
    if user is None:
        # Hash anyway so a missing account and a wrong password cost similar
        # time, and the response cannot be used to enumerate registered emails.
        _hash(password or "", secrets.token_hex(16))
        raise AuthError("Email or password is incorrect.")

    candidate = _hash(password or "", user["salt"])
    if not hmac.compare_digest(candidate, user["password_hash"]):
        raise AuthError("Email or password is incorrect.")

    return {"id": user["id"], "email": user["email"]}


def start_session(user_id: str) -> str:
    expires = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    token = secrets.token_urlsafe(32)
    db.create_session(user_id, token, expires.isoformat(timespec="seconds"))
    return token


def end_session(token: str) -> None:
    if token:
        db.delete_session(token)


def user_for_token(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    user = db.get_session_user(token)
    return {"id": user["id"], "email": user["email"]} if user else None
