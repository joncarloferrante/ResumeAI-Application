import base64
import hashlib
import hmac
import os


def hash_password(password: str, salt: bytes | None = None) -> str:
    if salt is None:
        salt = os.urandom(16)

    password_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        310000,
    )

    return (
        "pbkdf2_sha256$310000$"
        f"{base64.urlsafe_b64encode(salt).decode('utf-8')}$"
        f"{base64.urlsafe_b64encode(password_hash).decode('utf-8')}"
    )


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations, salt, expected_hash = stored_hash.split("$", 3)
    except ValueError:
        return False

    if algorithm != "pbkdf2_sha256":
        return False

    candidate_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        base64.urlsafe_b64decode(salt.encode("utf-8")),
        int(iterations),
    )

    return hmac.compare_digest(
        base64.urlsafe_b64encode(candidate_hash).decode("utf-8"),
        expected_hash,
    )
