import json
from decimal import Decimal
from handler import main as api_main
from quota import FREE_MONTHLY_QUOTA_SECONDS, get_current_period, quota_pk


def _register_user(email: str, tier: str = "FREE", account_id: str = "") -> dict:
    payload = {"email": email, "password": "secretPassword!", "tier": tier}
    if account_id:
        payload["account_id"] = account_id
    resp = api_main(
        {
            "httpMethod": "POST",
            "path": "/auth/register",
            "body": json.dumps(payload),
        }
    )
    assert resp["statusCode"] == 201
    return json.loads(resp["body"])


def test_execute_code_and_get_execution_cache_aside(aws_and_redis_env):
    """
    1. Execute Python snippet via POST /execute (JWT protected).
    2. Verify EXEC#<execution_id> is stored in DynamoDB with Firecracker microVM telemetry.
    3. Verify GET /executions/{id} returns _fuente='dynamodb' on cache miss and _fuente='cache' on hit.
    4. Verify GET /quota reflects accumulated execution seconds.
    """
    table = aws_and_redis_env["table"]
    fake_redis = aws_and_redis_env["redis"]

    user = _register_user("runner@aurora.io", tier="FREE", account_id="runner-01")
    token = user["token"]
    headers = {"Authorization": f"Bearer {token}"}

    exec_resp = api_main(
        {
            "httpMethod": "POST",
            "path": "/execute",
            "headers": headers,
            "body": json.dumps(
                {
                    "code": "import sys\nprint('Hello from Firecracker microVM')\nsys.stderr.write('diag-ok\\n')",
                    "runtime": "python3.11",
                    "timeout": 5.0,
                    "memory_limit_mb": 128,
                }
            ),
        }
    )
    assert exec_resp["statusCode"] == 200
    exec_body = json.loads(exec_resp["body"])

    execution_id = exec_body["execution_id"]
    assert exec_body["PK"] == f"EXEC#{execution_id}"
    assert exec_body["status"] == "COMPLETED"
    assert "Hello from Firecracker microVM" in exec_body["stdout"]
    assert "diag-ok" in exec_body["stderr"]
    assert exec_body["duration"] > 0

    # Verify Firecracker microVM lifecycle telemetry stored in DynamoDB
    telemetry = exec_body["telemetry"]
    assert telemetry["vm_id"].startswith("fc-vm-")
    assert telemetry["boot_time_ms"] > 0
    assert telemetry["teardown_time_ms"] > 0
    assert telemetry["teardown_status"] == "DESTROYED"
    assert telemetry["state_transitions"] == [
        "CREATING",
        "BOOTING",
        "RUNNING",
        "TEARING_DOWN",
        "DESTROYED",
    ]

    ddb_exec = table.get_item(Key={"PK": f"EXEC#{execution_id}", "SK": "METADATA"}).get("Item")
    assert ddb_exec is not None
    assert ddb_exec["telemetry"]["vm_id"] == telemetry["vm_id"]

    # GET /executions/{id} — First call is a cache miss (_fuente == 'dynamodb'), populates Redis with TTL
    get_miss = api_main(
        {
            "httpMethod": "GET",
            "path": f"/executions/{execution_id}",
            "pathParameters": {"id": execution_id},
            "headers": headers,
        }
    )
    assert get_miss["statusCode"] == 200
    miss_body = json.loads(get_miss["body"])
    assert miss_body["_fuente"] == "dynamodb"
    assert fake_redis.ttl(f"exec:{execution_id}") > 0

    # GET /executions/{id} — Second call is a cache hit (_fuente == 'cache')
    get_hit = api_main(
        {
            "httpMethod": "GET",
            "path": f"/executions/{execution_id}",
            "pathParameters": {"id": execution_id},
            "headers": headers,
        }
    )
    assert get_hit["statusCode"] == 200
    hit_body = json.loads(get_hit["body"])
    assert hit_body["_fuente"] == "cache"
    assert hit_body["execution_id"] == execution_id

    # GET /quota — Check accumulated seconds
    quota_resp = api_main(
        {
            "httpMethod": "GET",
            "path": "/quota",
            "headers": headers,
        }
    )
    assert quota_resp["statusCode"] == 200
    quota_body = json.loads(quota_resp["body"])
    assert quota_body["PK"] == quota_pk("runner-01")
    assert quota_body["tier"] == "FREE"
    assert quota_body["limit_seconds"] == 14400
    assert quota_body["accumulated_seconds"] > 0
    assert quota_body["remaining_seconds"] < 14400
    assert quota_body["_fuente"] in ("cache", "dynamodb")


def test_free_tier_quota_rejection_http_429_and_strict_timeout_cap(aws_and_redis_env):
    """
    1. If a FREE account has only 0.3s remaining in its monthly 14,400s quota and requests a 10s timeout,
       the sandbox strictly caps the execution timeout to 0.3s and terminates a sleeping script with status='TIMEOUT'.
    2. Once the FREE account reaches 14,400s (4 hours), subsequent POST /execute requests are rejected with HTTP 429.
    """
    table = aws_and_redis_env["table"]
    user = _register_user("free-limit@aurora.io", tier="FREE", account_id="free-cap-01")
    headers = {"Authorization": f"Bearer {user['token']}"}
    period = get_current_period()

    # Set accumulated_seconds to 14,399.7s (only 0.3s left out of 14,400s)
    table.update_item(
        Key={"PK": quota_pk("free-cap-01", period), "SK": "SUMMARY"},
        UpdateExpression="SET accumulated_seconds = :val",
        ExpressionAttributeValues={":val": Decimal("14399.7")},
    )

    # Request a 10s timeout on a script that sleeps 5s -> must be capped at 0.3s and time out!
    timeout_resp = api_main(
        {
            "httpMethod": "POST",
            "path": "/execute",
            "headers": headers,
            "body": json.dumps(
                {
                    "code": "import time\ntime.sleep(5)\nprint('never reached')",
                    "timeout": 10.0,
                }
            ),
        }
    )
    assert timeout_resp["statusCode"] == 200
    timeout_body = json.loads(timeout_resp["body"])
    assert timeout_body["status"] == "TIMEOUT"
    assert timeout_body["timeout_enforced"] == 0.3
    assert timeout_body["duration"] == 0.3
    assert timeout_body["quota"]["accumulated_seconds"] >= FREE_MONTHLY_QUOTA_SECONDS
    assert timeout_body["quota"]["quota_exceeded"] is True

    # Next execution attempt must be rejected with HTTP 429 Too Many Requests
    rejected_resp = api_main(
        {
            "httpMethod": "POST",
            "path": "/execute",
            "headers": headers,
            "body": json.dumps({"code": "print('blocked by quota')"}),
        }
    )
    assert rejected_resp["statusCode"] == 429
    rejected_body = json.loads(rejected_resp["body"])
    assert "429" in str(rejected_resp["statusCode"])
    assert rejected_body["quota"]["limit_seconds"] == 14400
    assert rejected_body["quota"]["remaining_seconds"] == 0.0


def test_pro_tier_unlimited_execution_quota(aws_and_redis_env):
    """
    PRO accounts have unlimited monthly execution time and are not rejected after 14,400 seconds.
    """
    table = aws_and_redis_env["table"]
    user = _register_user("enterprise@aurora.io", tier="PRO", account_id="pro-01")
    headers = {"Authorization": f"Bearer {user['token']}"}
    period = get_current_period()

    # Simulate 50,000 seconds (> 4 hours) already accumulated this month
    table.update_item(
        Key={"PK": quota_pk("pro-01", period), "SK": "SUMMARY"},
        UpdateExpression="SET accumulated_seconds = :val",
        ExpressionAttributeValues={":val": Decimal("50000.0")},
    )

    resp = api_main(
        {
            "httpMethod": "POST",
            "path": "/execute",
            "headers": headers,
            "body": json.dumps({"code": "print('PRO tier unlimited execution')"}),
        }
    )
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["status"] == "COMPLETED"
    assert body["quota"]["tier"] == "PRO"
    assert body["quota"]["unlimited"] is True
    assert body["quota"]["limit_seconds"] is None
    assert body["quota"]["quota_exceeded"] is False
    assert body["quota"]["accumulated_seconds"] > 50000.0


def test_sandbox_memory_limit_enforcement(aws_and_redis_env):
    """
    Verify the Ephemeral Sandbox Runner detects and terminates executions that exceed memory_limit_mb.
    """
    user = _register_user("memtest@aurora.io", tier="PRO", account_id="mem-01")
    headers = {"Authorization": f"Bearer {user['token']}"}

    # Allocate ~32MB while memory_limit_mb is capped at 16MB
    resp = api_main(
        {
            "httpMethod": "POST",
            "path": "/execute",
            "headers": headers,
            "body": json.dumps(
                {
                    "code": "data = bytearray(32 * 1024 * 1024)\nprint(len(data))",
                    "memory_limit_mb": 16,
                    "timeout": 5.0,
                }
            ),
        }
    )
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["status"] == "MEMORY_LIMIT_EXCEEDED"
    assert "MemoryError" in body["stderr"]

    # Same allocation, but the MemoryError is swallowed by the snippet (exit code 0) —
    # the sandbox must still report MEMORY_LIMIT_EXCEEDED, not COMPLETED.
    resp2 = api_main(
        {
            "httpMethod": "POST",
            "path": "/execute",
            "headers": headers,
            "body": json.dumps(
                {
                    "code": "import sys\ntry:\n    data = bytearray(32 * 1024 * 1024)\nexcept MemoryError:\n    sys.stderr.write('MemoryError: oom\\n')\nprint('swallowed')",
                    "memory_limit_mb": 16,
                    "timeout": 5.0,
                }
            ),
        }
    )
    assert resp2["statusCode"] == 200
    body2 = json.loads(resp2["body"])
    assert body2["status"] == "MEMORY_LIMIT_EXCEEDED"
    assert body2["exit_code"] == 0
