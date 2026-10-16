import base64
import hashlib
import hmac
import json
import secrets
import time

from app.config import Settings


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    iterations = 600_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256" or not 100_000 <= int(rounds) <= 2_000_000:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def issue_token(user_id: str, settings: Settings, session_id: str | None = None) -> str:
    payload = _b64(json.dumps({"sub": user_id, "exp": int(time.time()) + settings.token_lifetime_seconds,
                               "nonce": session_id or secrets.token_hex(8)}, separators=(",", ":")).encode())
    signature = _b64(hmac.new(settings.secret_key.encode(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def token_claims(token: str, settings: Settings) -> dict | None:
    try:
        if len(token) > 2048:
            return None
        payload, signature = token.split(".")
        expected = _b64(hmac.new(settings.secret_key.encode(), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(expected, signature):
            return None
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if not isinstance(data.get("exp"), int) or data["exp"] <= time.time():
            return None
        subject = data.get("sub")
        return data if isinstance(subject, str) and len(subject) == 36 else None
    except (ValueError, TypeError, KeyError, UnicodeError):
        return None


def token_subject(token: str, settings: Settings) -> str | None:
    claims = token_claims(token, settings)
    return claims["sub"] if claims else None
