import decimal
import hashlib
import json
import os
from typing import Any, Dict, Optional, Tuple

try:
    import redis
except ImportError:  # pragma: no cover
    redis = None  # type: ignore


DEFAULT_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", "300"))

# Module-level injectable client for unit testing (e.g., fakeredis)
_redis_client_override: Any = None


class DecimalEncoder(json.JSONEncoder):
    """JSON encoder that converts DynamoDB Decimal instances into int or float."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, decimal.Decimal):
            if obj % 1 == 0:
                return int(obj)
            return float(obj)
        return super().default(obj)


def dumps_json(data: Any) -> str:
    return json.dumps(data, cls=DecimalEncoder)


def set_redis_client(client: Any) -> None:
    """Inject a custom Redis/FakeRedis client (used by unit tests)."""
    global _redis_client_override
    _redis_client_override = client


def get_redis_client(redis_client: Any = None) -> Any:
    """Return the active Redis client instance connected to REDIS_HOST:6379."""
    if redis_client is not None:
        return redis_client
    if _redis_client_override is not None:
        return _redis_client_override
    if redis is None:
        raise RuntimeError("redis-py is not installed")
    host = os.environ.get("REDIS_HOST", "redis")
    port = int(os.environ.get("REDIS_PORT", "6379"))
    return redis.Redis(host=host, port=port, decode_responses=True, socket_connect_timeout=2)


def account_cache_key(account_id: str) -> str:
    return f"account:{account_id}"


def execution_cache_key(execution_id: str) -> str:
    return f"exec:{execution_id}"


def quota_cache_key(account_id: str, period: str) -> str:
    return f"quota:{account_id}:{period}"


def jwt_session_cache_key(token: str) -> str:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:24]
    return f"jwt:{digest}"


def cache_set_with_ttl(
    key: str,
    payload: Dict[str, Any],
    ttl: int = DEFAULT_TTL_SECONDS,
    redis_client: Any = None,
) -> bool:
    """
    Store a JSON dictionary in Redis with EXPIRE (TTL) via SETEX / EXPIRE.
    Strips internal transient keys such as '_fuente' and 'password_hash' before caching.
    """
    r = get_redis_client(redis_client)
    clean_payload = {k: v for k, v in payload.items() if k not in ("_fuente", "password_hash")}
    serialized = dumps_json(clean_payload)
    r.setex(key, int(ttl), serialized)
    return True


def cache_get(key: str, redis_client: Any = None) -> Optional[Dict[str, Any]]:
    """Retrieve a JSON dictionary from Redis if present."""
    r = get_redis_client(redis_client)
    raw = r.get(key)
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return json.loads(raw)


def invalidate_cache(key: str, redis_client: Any = None) -> None:
    """Delete a key from Redis."""
    r = get_redis_client(redis_client)
    r.delete(key)


# ============================================================================
# WRITE-THROUGH PATTERN WITH EXPIRE (TTL)
# Simultaneously writes to DynamoDB Single-Table and updates Redis with EXPIRE.
# ============================================================================

def write_through_account(
    table: Any,
    account_item: Dict[str, Any],
    redis_client: Any = None,
    ttl: int = DEFAULT_TTL_SECONDS,
) -> Dict[str, Any]:
    """
    Write-Through pattern for ACCOUNT#<id>:
    1. Persists `account_item` in DynamoDB Single-Table.
    2. Immediately writes sanitized account metadata & tier to Redis (`account:<id>`) with EXPIRE (TTL).
    """
    table.put_item(Item=account_item)
    account_id = str(account_item["account_id"])
    sanitized = {k: v for k, v in account_item.items() if k != "password_hash"}
    try:
        cache_set_with_ttl(account_cache_key(account_id), sanitized, ttl=ttl, redis_client=redis_client)
    except Exception:
        pass
    result = json.loads(dumps_json(sanitized))
    result["_fuente"] = "dynamodb"
    return result


def write_through_quota(
    quota_status: Dict[str, Any],
    redis_client: Any = None,
    ttl: int = DEFAULT_TTL_SECONDS,
) -> Dict[str, Any]:
    """
    Write-Through pattern for QUOTA#<account_id>#<YYYY-MM>:
    Synchronizes the updated monthly quota state into Redis (`quota:<account_id>:<YYYY-MM>`) with EXPIRE (TTL).
    """
    account_id = str(quota_status["account_id"])
    period = str(quota_status["period"])
    key = quota_cache_key(account_id, period)
    try:
        cache_set_with_ttl(key, quota_status, ttl=ttl, redis_client=redis_client)
    except Exception:
        pass
    return quota_status


def write_through_execution(
    table: Any,
    exec_item: Dict[str, Any],
    ddb_converter: Any = None,
    redis_client: Any = None,
    ttl: int = DEFAULT_TTL_SECONDS,
    populate_cache: bool = False,
) -> Dict[str, Any]:
    """
    Persist EXEC#<execution_id> in DynamoDB Single-Table.
    When `populate_cache=True`, applies Write-Through to immediately store `exec:<execution_id>`
    in Redis with EXPIRE (TTL). When `populate_cache=False` (default for GET /executions/{id}
    Cache-Aside demonstration), invalidates any stale key so the first GET populates Redis on read.
    """
    ddb_payload = ddb_converter(exec_item) if ddb_converter else exec_item
    table.put_item(Item=ddb_payload)
    execution_id = str(exec_item["execution_id"])
    key = execution_cache_key(execution_id)
    try:
        if populate_cache:
            cache_set_with_ttl(key, exec_item, ttl=ttl, redis_client=redis_client)
        else:
            invalidate_cache(key, redis_client=redis_client)
    except Exception:
        pass
    return exec_item


def write_through_jwt_session(
    token: str,
    claims: Dict[str, Any],
    redis_client: Any = None,
    ttl: int = 3600,
) -> bool:
    """
    Write-Through pattern for JWT session tracking in Redis with EXPIRE (TTL).
    """
    key = jwt_session_cache_key(token)
    try:
        return cache_set_with_ttl(key, claims, ttl=ttl, redis_client=redis_client)
    except Exception:
        return False


# ============================================================================
# CACHE-ASIDE PATTERN WITH EXPIRE (TTL)
# 1. Check Redis first (hit -> _fuente="cache")
# 2. On miss -> read from DynamoDB Single-Table, store in Redis with EXPIRE (TTL), return _fuente="dynamodb"
# ============================================================================

def get_account_cache_aside(
    account_id: str,
    table: Any,
    redis_client: Any = None,
    ttl: int = DEFAULT_TTL_SECONDS,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Cache-Aside pattern for ACCOUNT#<id>:
    1. Check Redis first (`account:<id>`). If hit -> return with `_fuente = "cache"`.
    2. On miss -> query DynamoDB `PK="ACCOUNT#<id>"`, `SK="METADATA"`.
    3. Store in Redis with EXPIRE (TTL) via `setex` and return with `_fuente = "dynamodb"`.
    """
    key = account_cache_key(account_id)
    r = get_redis_client(redis_client)

    try:
        cached = cache_get(key, redis_client=r)
        if cached is not None:
            cached["_fuente"] = "cache"
            return cached, "cache"
    except Exception:
        pass

    response = table.get_item(
        Key={
            "PK": f"ACCOUNT#{account_id}",
            "SK": "METADATA",
        }
    )
    item = response.get("Item")
    if not item:
        return None, "dynamodb"

    sanitized = {k: v for k, v in item.items() if k != "password_hash"}
    try:
        cache_set_with_ttl(key, sanitized, ttl=ttl, redis_client=r)
    except Exception:
        pass

    result = json.loads(dumps_json(sanitized))
    result["_fuente"] = "dynamodb"
    return result, "dynamodb"


def get_execution_cache_aside(
    execution_id: str,
    table: Any,
    redis_client: Any = None,
    ttl: int = DEFAULT_TTL_SECONDS,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Cache-Aside pattern for EXEC#<execution_id>:
    1. Check Redis first (`exec:<execution_id>`). If hit -> return with `_fuente = "cache"`.
    2. On miss -> read from DynamoDB `PK="EXEC#<execution_id>"`, `SK="METADATA"`.
    3. Store in Redis with EXPIRE (TTL) via `setex` and return with `_fuente = "dynamodb"`.
    """
    key = execution_cache_key(execution_id)
    r = get_redis_client(redis_client)

    try:
        cached = cache_get(key, redis_client=r)
        if cached is not None:
            cached["_fuente"] = "cache"
            return cached, "cache"
    except Exception:
        pass

    response = table.get_item(
        Key={
            "PK": f"EXEC#{execution_id}",
            "SK": "METADATA",
        }
    )
    item = response.get("Item")
    if not item:
        return None, "dynamodb"

    try:
        cache_set_with_ttl(key, item, ttl=ttl, redis_client=r)
    except Exception:
        pass

    result = json.loads(dumps_json(item))
    result["_fuente"] = "dynamodb"
    return result, "dynamodb"
