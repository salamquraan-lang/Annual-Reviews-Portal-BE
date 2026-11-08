import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.config import get_settings
from app.models import Role

BCRYPT_ROUNDS = 12
BCRYPT_MAX_BYTES = 72

# Compared against when the email is unknown, so the response time doesn't reveal it.
_DUMMY_HASH = bcrypt.hashpw(b"dummy-password-for-timing", bcrypt.gensalt(BCRYPT_ROUNDS)).decode()


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(BCRYPT_ROUNDS)).decode()


def verify_password(plain: str, password_hash: str | None) -> bool:
    data = plain.encode()
    too_long = len(data) > BCRYPT_MAX_BYTES
    ok = bcrypt.checkpw(data[:BCRYPT_MAX_BYTES], (password_hash or _DUMMY_HASH).encode())
    return ok and password_hash is not None and not too_long


def create_access_token(user_id: int, role: Role) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "role": role.value,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_ttl_minutes),
    }
    return jwt.encode(payload, settings.jwt_access_secret, algorithm="HS256")


def decode_access_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(
            token,
            get_settings().jwt_access_secret,
            algorithms=["HS256"],
            options={"require": ["sub", "exp"]},
        )
    except jwt.PyJWTError:
        return None
    return payload if str(payload.get("sub", "")).isdigit() else None


def random_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
