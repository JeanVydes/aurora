import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any, Dict, Optional

try:
    import jwt as pyjwt
except ImportError:  # pragma: no cover
    pyjwt = None  # type: ignore


# No default secret — JWT_SECRET MUST come from the environment (stack/tests set it).
VALID_TIERS = ("FREE", "PRO")


class AuthenticationError(Exception):
    """Raised when JWT authentication fails or credentials are invalid."""

    def __init__(self, message: str, status_code: int = 401) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def get_jwt_secret(secret: Optional[str] = None) -> str:
    resolved = secret or os.environ.get("JWT_SECRET", "")
    if not resolved:
        raise RuntimeError("JWT_SECRET environment variable is required (refusing to sign/verify with a default secret)")
    return resolved


def hash_password(password: str, salt: Optional[str] = None) -> str:
    """Hash a plaintext password using PBKDF2-HMAC-SHA256."""
    if not salt:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        100_000,
    )
    return f"pbkdf2_sha256${salt}${dk.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a plaintext password against a stored PBKDF2-HMAC-SHA256 hash."""
    try:
        scheme, salt, _ = stored_hash.split("$", 2)
        if scheme != "pbkdf2_sha256":
            return False
        candidate = hash_password(password, salt=salt)
        return hmac.compare_digest(candidate, stored_hash)
    except Exception:
        return False


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode((segment + padding).encode("ascii"))


def create_jwt_token(
    account_id: str,
    email: str,
    tier: str,
    secret: Optional[str] = None,
    expires_in: int = 3600,
) -> str:
    """Create a signed HS256 JWT token containing account claims."""
    jwt_secret = get_jwt_secret(secret)
    now = int(time.time())
    payload = {
        "sub": account_id,
        "account_id": account_id,
        "email": email,
        "tier": tier.upper(),
        "iat": now,
        "exp": now + int(expires_in),
    }
    if pyjwt is not None:
        token = pyjwt.encode(payload, jwt_secret, algorithm="HS256")
        return token if isinstance(token, str) else token.decode("utf-8")

    # Standard-library HS256 fallback
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = f"{_b64url_encode(json.dumps(header, separators=(',', ':')).encode())}.{_b64url_encode(json.dumps(payload, separators=(',', ':')).encode())}"
    signature = hmac.new(jwt_secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url_encode(signature)}"


def verify_jwt_token(token: str, secret: Optional[str] = None) -> Dict[str, Any]:
    """Verify and decode a signed HS256 JWT token."""
    if not token or not isinstance(token, str):
        raise AuthenticationError("Missing or malformed JWT token")

    jwt_secret = get_jwt_secret(secret)
    if pyjwt is not None:
        try:
            claims = pyjwt.decode(token, jwt_secret, algorithms=["HS256"])
            return dict(claims)
        except pyjwt.ExpiredSignatureError as exc:
            raise AuthenticationError("JWT token has expired") from exc
        except pyjwt.InvalidTokenError as exc:
            raise AuthenticationError("Invalid JWT token") from exc

    # Standard-library HS256 fallback
    try:
        parts = token.split(".")
        if len(parts) != 3:
            raise AuthenticationError("Invalid JWT structure")
        signing_input = f"{parts[0]}.{parts[1]}"
        expected_sig = hmac.new(
            jwt_secret.encode("utf-8"),
            signing_input.encode("ascii"),
            hashlib.sha256,
        ).digest()
        actual_sig = _b64url_decode(parts[2])
        if not hmac.compare_digest(expected_sig, actual_sig):
            raise AuthenticationError("Invalid JWT signature")
        payload = json.loads(_b64url_decode(parts[1]).decode("utf-8"))
        if int(payload.get("exp", 0)) < int(time.time()):
            raise AuthenticationError("JWT token has expired")
        return payload
    except AuthenticationError:
        raise
    except Exception as exc:
        raise AuthenticationError("Invalid JWT token") from exc


def extract_bearer_token(event: Dict[str, Any]) -> Optional[str]:
    """Extract Bearer token from API Gateway event headers."""
    headers = event.get("headers") or {}
    if not isinstance(headers, dict):
        return None
    for k, v in headers.items():
        if k.lower() == "authorization" and isinstance(v, str):
            v_strip = v.strip()
            if v_strip.lower().startswith("bearer "):
                return v_strip[7:].strip()
            return v_strip
    return None


def extract_api_key(event: Dict[str, Any]) -> Optional[str]:
    """Extract the X-API-Key header from an API Gateway event."""
    headers = event.get("headers") or {}
    if not isinstance(headers, dict):
        return None
    for k, v in headers.items():
        if k.lower() == "x-api-key" and isinstance(v, str):
            return v.strip() or None
    return None


def authenticate_request(
    event: Dict[str, Any],
    secret: Optional[str] = None,
    table: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Authenticate an incoming API Gateway event.

    Priority:
      1. Authorization: Bearer <HS256 JWT>  -> decodes claims
      2. X-API-Key: <key>                    -> resolves account via DynamoDB APIKEYLOOKUP<hash>
    """
    token = extract_bearer_token(event)
    if token:
        return verify_jwt_token(token, secret=secret)

    api_key = extract_api_key(event)
    if api_key:
        if table is None:
            raise AuthenticationError("API key authentication is not available in this context")
        from apikey import lookup_account_by_api_key, get_api_key_meta, touch_api_key

        account_id = lookup_account_by_api_key(table, api_key)
        if not account_id:
            raise AuthenticationError("Invalid API key")
        meta = get_api_key_meta(table, account_id)
        if not meta:
            raise AuthenticationError("Invalid API key")
        # Account metadata (email/tier) is read lazily from ACCOUNT#<id> by the
        # cache-aside path; the token only needs to identify the account.
        touch_api_key(table, account_id)
        return {
            "sub": account_id,
            "account_id": account_id,
            "email": "",
            "auth": "api_key",
            "api_key_id": meta.get("api_key_id"),
        }

    raise AuthenticationError("Authorization Bearer token or X-API-Key header is required")
