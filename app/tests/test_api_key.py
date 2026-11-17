import json

from handler import main as api_main






def _register(email="agent@aurora.io"):
    resp = api_main(
        {
            "httpMethod": "POST",
            "path": "/auth/register",
            "body": json.dumps(
                {"email": email, "password": "Password123!", "tier": "FREE"}
            ),
        }
    )
    assert resp["statusCode"] == 201
    return json.loads(resp["body"])








def test_api_key_lifecycle(aws_and_redis_env):
    user = _register()
    headers = {"Authorization": f"Bearer {user['token']}"}

    # No key initially
    resp = api_main({"httpMethod": "GET", "path": "/api-key", "headers": headers})
    body = json.loads(resp["body"])
    assert resp["statusCode"] == 200
    assert body["has_key"] is False

    # Agent calls without a key -> 401
    resp = api_main({"httpMethod": "GET", "path": "/quota"})
    assert resp["statusCode"] == 401

    # Rotate -> plaintext key is returned once
    resp = api_main({"httpMethod": "POST", "path": "/api-key/rotate", "headers": headers})
    body = json.loads(resp["body"])
    assert resp["statusCode"] == 200
    key = body["api_key"]
    assert key.startswith("aurora_ak_")
    assert len(key) > 40

    # Plaintext is never stored/returned again
    resp = api_main({"httpMethod": "GET", "path": "/api-key", "headers": headers})
    body = json.loads(resp["body"])
    assert body["has_key"] is True
    assert "api_key" not in body
    assert body["api_key_prefix"].startswith("aurora_ak_")

    # Agent can authenticate with X-API-Key
    agent_headers = {"X-API-Key": key}
    resp = api_main({"httpMethod": "GET", "path": "/quota", "headers": agent_headers})
    assert resp["statusCode"] == 200
    quota = json.loads(resp["body"])
    assert quota["account_id"] == user["account_id"]

    # Agent can execute code with the key and no session
    exec_resp = api_main(
        {
            "httpMethod": "POST",
            "path": "/execute",
            "headers": agent_headers,
            "body": json.dumps({"code": "print(40 + 2)", "timeout": 5, "memory_limit_mb": 128}),
        }
    )
    assert exec_resp["statusCode"] == 200
    execution = json.loads(exec_resp["body"])
    assert execution["status"] == "COMPLETED"
    assert "42" in execution["stdout"]

    # Rotate again -> old key stops working, new key works
    resp = api_main({"httpMethod": "POST", "path": "/api-key/rotate", "headers": headers})
    new_key = json.loads(resp["body"])["api_key"]
    resp = api_main({"httpMethod": "GET", "path": "/quota", "headers": {"X-API-Key": key}})
    assert resp["statusCode"] == 401
    resp = api_main({"httpMethod": "GET", "path": "/quota", "headers": {"X-API-Key": new_key}})
    assert resp["statusCode"] == 200

    # Revoke -> even the fresh key stops working
    resp = api_main({"httpMethod": "DELETE", "path": "/api-key", "headers": headers})
    assert json.loads(resp["body"])["revoked"] is True
    resp = api_main({"httpMethod": "GET", "path": "/quota", "headers": {"X-API-Key": new_key}})
    assert resp["statusCode"] == 401


def test_rotate_requires_auth(aws_and_redis_env):
    resp = api_main({"httpMethod": "POST", "path": "/api-key/rotate"})
    assert resp["statusCode"] == 401


def test_api_key_is_account_scoped(aws_and_redis_env):
    alice = _register("alice@agent.io")
    bob = _register("bob@agent.io")
    key_a = json.loads(
        api_main(
            {
                "httpMethod": "POST",
                "path": "/api-key/rotate",
                "headers": {"Authorization": f"Bearer {alice['token']}"},
            }
        )["body"]
    )["api_key"]

    # Bob cannot read Alice's executions (ownership via api key)
    exec_resp = api_main(
        {
            "httpMethod": "POST",
            "path": "/execute",
            "headers": {"X-API-Key": key_a},
            "body": json.dumps({"code": "print('hello')", "timeout": 5}),
        }
    )
    execution_id = json.loads(exec_resp["body"])["execution_id"]
    resp = api_main(
        {
            "httpMethod": "GET",
            "path": f"/executions/{execution_id}",
            "pathParameters": {"id": execution_id},
            "headers": {"Authorization": f"Bearer {bob['token']}"},
        }
    )
    assert resp["statusCode"] == 403
    # But Alice can
    resp = api_main(
        {
            "httpMethod": "GET",
            "path": f"/executions/{execution_id}",
            "pathParameters": {"id": execution_id},
            "headers": {"X-API-Key": key_a},
        }
    )
    assert resp["statusCode"] == 200
