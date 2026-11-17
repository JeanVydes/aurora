from decimal import Decimal
from cache import (
    account_cache_key,
    execution_cache_key,
    get_account_cache_aside,
    get_execution_cache_aside,
    invalidate_cache,
    jwt_session_cache_key,
    quota_cache_key,
    write_through_account,
    write_through_execution,
    write_through_jwt_session,
    write_through_quota,
)


def test_account_cache_aside_and_ttl(aws_and_redis_env):
    """
    Verify Cache-Aside pattern with EXPIRE (TTL) for ACCOUNT#<id>:
      1. Cache miss reads from DynamoDB (_fuente == 'dynamodb') and populates Redis with TTL.
      2. Cache hit reads from Redis (_fuente == 'cache') and strips password_hash.
    """
    table = aws_and_redis_env["table"]
    fake_redis = aws_and_redis_env["redis"]

    table.put_item(
        Item={
            "PK": "ACCOUNT#acc-100",
            "SK": "METADATA",
            "account_id": "acc-100",
            "email": "dev@example.com",
            "tier": "FREE",
            "password_hash": "pbkdf2_sha256$salt$secret",
        }
    )

    # 1. First lookup -> Miss in Redis, reads from DynamoDB
    acc_miss, fuente_miss = get_account_cache_aside("acc-100", table=table, redis_client=fake_redis, ttl=180)
    assert fuente_miss == "dynamodb"
    assert acc_miss is not None
    assert acc_miss["_fuente"] == "dynamodb"
    assert acc_miss["account_id"] == "acc-100"
    assert "password_hash" not in acc_miss

    # Verify Redis key exists and has a positive EXPIRE TTL
    key = account_cache_key("acc-100")
    ttl_remaining = fake_redis.ttl(key)
    assert 0 < ttl_remaining <= 180

    # 2. Second lookup -> Hit in Redis
    acc_hit, fuente_hit = get_account_cache_aside("acc-100", table=table, redis_client=fake_redis, ttl=180)
    assert fuente_hit == "cache"
    assert acc_hit is not None
    assert acc_hit["_fuente"] == "cache"
    assert acc_hit["email"] == "dev@example.com"


def test_execution_cache_aside_and_invalidation(aws_and_redis_env):
    """
    Verify Cache-Aside pattern with EXPIRE (TTL) for EXEC#<execution_id>.
    """
    table = aws_and_redis_env["table"]
    fake_redis = aws_and_redis_env["redis"]

    table.put_item(
        Item={
            "PK": "EXEC#exec-999",
            "SK": "METADATA",
            "execution_id": "exec-999",
            "account_id": "acc-100",
            "code": "print(42)",
            "runtime": "python3.11",
            "duration": Decimal("0.025"),
            "status": "COMPLETED",
            "stdout": "42\n",
            "stderr": "",
        }
    )

    # 1. Miss -> DynamoDB
    exec_miss, fuente_miss = get_execution_cache_aside("exec-999", table=table, redis_client=fake_redis, ttl=300)
    assert fuente_miss == "dynamodb"
    assert exec_miss is not None
    assert exec_miss["_fuente"] == "dynamodb"
    assert exec_miss["stdout"] == "42\n"

    key = execution_cache_key("exec-999")
    assert fake_redis.ttl(key) > 0

    # 2. Hit -> Redis cache
    exec_hit, fuente_hit = get_execution_cache_aside("exec-999", table=table, redis_client=fake_redis, ttl=300)
    assert fuente_hit == "cache"
    assert exec_hit is not None
    assert exec_hit["_fuente"] == "cache"
    assert exec_hit["duration"] == 0.025

    # 3. Invalidate key -> next read goes back to DynamoDB
    invalidate_cache(key, redis_client=fake_redis)
    assert fake_redis.get(key) is None
    _, fuente_after_inv = get_execution_cache_aside("exec-999", table=table, redis_client=fake_redis, ttl=300)
    assert fuente_after_inv == "dynamodb"


def test_write_through_patterns_with_expire_ttl(aws_and_redis_env):
    """
    Verify Write-Through pattern simultaneously writes to DynamoDB Single-Table
    and populates Redis with EXPIRE (TTL) for accounts, quotas, JWT sessions, and executions.
    """
    table = aws_and_redis_env["table"]
    fake_redis = aws_and_redis_env["redis"]

    # 1. Write-Through Account
    account_item = {
        "PK": "ACCOUNT#wt-200",
        "SK": "METADATA",
        "account_id": "wt-200",
        "email": "wt@aurora.io",
        "tier": "PRO",
        "password_hash": "pbkdf2_sha256$salt$secret",
    }
    write_through_account(table, account_item, redis_client=fake_redis, ttl=240)

    # Both DynamoDB and Redis must immediately have the item, and Redis must have TTL set
    assert table.get_item(Key={"PK": "ACCOUNT#wt-200", "SK": "METADATA"}).get("Item") is not None
    assert 0 < fake_redis.ttl(account_cache_key("wt-200")) <= 240
    cached_acc, fuente = get_account_cache_aside("wt-200", table=table, redis_client=fake_redis)
    assert fuente == "cache"
    assert cached_acc["tier"] == "PRO"

    # 2. Write-Through Quota
    quota_payload = {
        "PK": "QUOTA#wt-200#2026-11",
        "SK": "SUMMARY",
        "account_id": "wt-200",
        "period": "2026-11",
        "tier": "PRO",
        "accumulated_seconds": 12.5,
        "limit_seconds": None,
        "execution_count": 2,
    }
    write_through_quota(quota_payload, redis_client=fake_redis, ttl=240)
    assert 0 < fake_redis.ttl(quota_cache_key("wt-200", "2026-11")) <= 240

    # 3. Write-Through Execution (with populate_cache=True)
    exec_item = {
        "PK": "EXEC#exec-wt-01",
        "SK": "METADATA",
        "execution_id": "exec-wt-01",
        "account_id": "wt-200",
        "code": "print('write-through')",
        "runtime": "python3.11",
        "duration": Decimal("0.015"),
        "status": "COMPLETED",
        "stdout": "write-through\n",
        "stderr": "",
    }
    write_through_execution(
        table,
        exec_item,
        redis_client=fake_redis,
        ttl=240,
        populate_cache=True,
    )
    assert 0 < fake_redis.ttl(execution_cache_key("exec-wt-01")) <= 240
    cached_exec, exec_fuente = get_execution_cache_aside("exec-wt-01", table=table, redis_client=fake_redis)
    assert exec_fuente == "cache"
    assert cached_exec["stdout"] == "write-through\n"

    # 4. Write-Through JWT Session
    token = "eyJhbGciOiJIUzI1NiJ9.dummy.sig"
    write_through_jwt_session(token, {"account_id": "wt-200", "tier": "PRO"}, redis_client=fake_redis, ttl=600)
    assert 0 < fake_redis.ttl(jwt_session_cache_key(token)) <= 600
