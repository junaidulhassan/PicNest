import hashlib
import hmac
import secrets

ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 390000


def hash_password(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), ITERATIONS)
    return f"{ALGORITHM}${ITERATIONS}${salt}${digest.hex()}"


def verify_password(password, encoded):
    try:
        algorithm, iterations, salt, expected = encoded.split("$")
        if algorithm != ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), expected)
