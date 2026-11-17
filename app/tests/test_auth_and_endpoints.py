import json
from handler import main as api_main
from lambdas.health.handler import main as health_main


def test_health_endpoint(aws_and_redis_env):
    """GET /health returns 200 OK in both ApiHandler and HealthHandler."""
    resp_api = api_main({"httpMethod": "GET", "path": "/health"})
    assert resp_api["statusCode"] == 200
    body_api = json.loads(resp_api["body"])
    assert body_api["status"] == "healthy"
    assert body_api["runtime"] == "python3.11"

    resp_health = health_main({"httpMethod": "GET", "path": "/health"})
    assert resp_health["statusCode"] == 200
    body_health = json.loads(resp_health["body"])
    assert body_health["status"] == "healthy"


def test_register_and_login_flow(aws_and_redis_env):
    """
    POST /auth/register creates ACCOUNT#<id> and QUOTA#<account_id>#<YYYY-MM> in DynamoDB,
    caches account metadata in Redis, and POST /auth/login validates credentials and issues JWT.
    """
    table = aws_and_redis_env["table"]

    # 1. Register FREE account
    reg_event = {
        "httpMethod": "POST",
        "path": "/auth/register",
        "body": json.dumps(
            {
                "email": "alice@aurora.io",
                "password": "StrongPassword123",
                "tier": "FREE",
                "account_id": "alice-01",
            }
        ),
    }
    reg_resp = api_main(reg_event)
    assert reg_resp["statusCode"] == 201
    reg_body = json.loads(reg_resp["body"])
    assert reg_body["PK"] == "ACCOUNT#alice-01"
    assert reg_body["tier"] == "FREE"
    assert reg_body["token"]
    assert reg_body["_fuente"] == "dynamodb"

    # Verify Single-Table item ACCOUNT#alice-01 exists in DynamoDB
    ddb_account = table.get_item(Key={"PK": "ACCOUNT#alice-01", "SK": "METADATA"}).get("Item")
    assert ddb_account is not None
    assert ddb_account["email"] == "alice@aurora.io"
    assert ddb_account["tier"] == "FREE"

    # 2. Duplicate registration returns 409 Conflict
    dup_resp = api_main(reg_event)
    assert dup_resp["statusCode"] == 409

    # 3. Login with valid credentials returns 200 and JWT
    login_resp = api_main(
        {
            "httpMethod": "POST",
            "path": "/auth/login",
            "body": json.dumps({"email": "alice@aurora.io", "password": "StrongPassword123"}),
        }
    )
    assert login_resp["statusCode"] == 200
    login_body = json.loads(login_resp["body"])
    assert login_body["account_id"] == "alice-01"
    assert login_body["token"]

    # 4. Login with wrong password returns 401
    bad_login = api_main(
        {
            "httpMethod": "POST",
            "path": "/auth/login",
            "body": json.dumps({"email": "alice@aurora.io", "password": "WrongPassword"}),
        }
    )
    assert bad_login["statusCode"] == 401


def test_protected_endpoints_require_jwt(aws_and_redis_env):
    """POST /execute and GET /quota reject unauthenticated or invalid JWT requests with 401."""
    no_auth_exec = api_main(
        {
            "httpMethod": "POST",
            "path": "/execute",
            "body": json.dumps({"code": "print('hi')"}),
        }
    )
    assert no_auth_exec["statusCode"] == 401

    bad_auth_quota = api_main(
        {
            "httpMethod": "GET",
            "path": "/quota",
            "headers": {"Authorization": "Bearer invalid.jwt.token"},
        }
    )
    assert bad_auth_quota["statusCode"] == 401


def test_refresh_and_dummy_subscription_upgrade(aws_and_redis_env):
    """
    Verify POST /auth/refresh and POST /subscription/upgrade (dummy instant upgrade to PRO, no paywall).
    """
    reg_resp = api_main(
        {
            "httpMethod": "POST",
            "path": "/auth/register",
            "body": json.dumps(
                {
                    "email": "upgrader@aurora.io",
                    "password": "Password123",
                    "tier": "FREE",
                    "account_id": "upg-01",
                }
            ),
        }
    )
    assert reg_resp["statusCode"] == 201
    token = json.loads(reg_resp["body"])["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Refresh token
    ref_resp = api_main({"httpMethod": "POST", "path": "/auth/refresh", "headers": headers})
    assert ref_resp["statusCode"] == 200
    ref_body = json.loads(ref_resp["body"])
    assert ref_body["tier"] == "FREE"
    assert ref_body["token"]

    # 2. Dummy subscription purchase -> instant PRO upgrade without paywall
    upg_resp = api_main(
        {
            "httpMethod": "POST",
            "path": "/subscription/upgrade",
            "headers": headers,
            "body": json.dumps({"tier": "PRO"}),
        }
    )
    assert upg_resp["statusCode"] == 200
    upg_body = json.loads(upg_resp["body"])
    assert upg_body["tier"] == "PRO"
    assert upg_body["quota"]["unlimited"] is True
    assert upg_body["token"]

