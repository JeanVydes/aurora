import datetime
import json
import os
import sys
import time
import uuid
from decimal import Decimal
from typing import Any, Dict, Optional

# Ensure sibling modules in `lambdas/api` can be imported regardless of cwd
_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if _CURRENT_DIR not in sys.path:
    sys.path.insert(0, _CURRENT_DIR)

import boto3
from botocore.exceptions import ClientError
from auth import (
     VALID_TIERS,
    AuthenticationError,
    authenticate_request,
    create_jwt_token,
    hash_password,
    verify_password,
)
from cache import (
    DEFAULT_TTL_SECONDS,
    account_cache_key,
    cache_set_with_ttl,
    dumps_json,
    get_account_cache_aside,
    get_execution_cache_aside,
    write_through_execution,
    write_through_jwt_session,
    write_through_quota,
)
from apikey import (
    get_api_key_meta,
    rotate_api_key,
    revoke_api_key,
)
from quota import (
    FREE_MONTHLY_QUOTA_SECONDS,
    QuotaExceededError,
    enforce_quota_and_calculate_timeout,
    get_current_period,
    get_quota_status,
    quota_pk,
    settle_quota_usage,
)
from sandbox import DEFAULT_MEMORY_LIMIT_MB, run_ephemeral_code


MAX_CODE_CHARS = 65536
EXECUTION_TTL_SECONDS = int(os.environ.get("EXECUTION_TTL_DAYS", "30")) * 86400

_table_override: Any = None


def set_dynamodb_table(table: Any) -> None:
    """Inject a DynamoDB Table resource (used by unit tests)."""
    global _table_override
    _table_override = table


def get_dynamodb_table(table: Any = None) -> Any:
    """Return the DynamoDB Single-Table resource."""
    if table is not None:
        return table
    if _table_override is not None:
        return _table_override

    table_name = os.environ.get("TABLE_NAME", "EphemeralSandboxTable")
    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    endpoint_url = os.environ.get("AWS_ENDPOINT_URL")

    kwargs: Dict[str, Any] = {"region_name": region}
    if endpoint_url:
        kwargs["endpoint_url"] = endpoint_url

    dynamodb = boto3.resource("dynamodb", **kwargs)
    return dynamodb.Table(table_name)


def _json_response(status_code: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
        },
        "body": dumps_json(body),
    }


def _parse_json_body(event: Dict[str, Any]) -> Dict[str, Any]:
    raw_body = event.get("body")
    if not raw_body:
        return {}
    if isinstance(raw_body, dict):
        return raw_body
    try:
        return json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid JSON request body") from exc


def _convert_floats_to_decimal(obj: Any) -> Any:
    """Recursively convert float values into Decimal for DynamoDB storage."""
    if isinstance(obj, float):
        return Decimal(str(round(obj, 6)))
    if isinstance(obj, dict):
        return {k: _convert_floats_to_decimal(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_convert_floats_to_decimal(v) for v in obj]
    return obj


def handle_health(event: Dict[str, Any]) -> Dict[str, Any]:
    """GET /health"""
    return _json_response(
        200,
        {
            "status": "healthy",
            "service": "ephemeral-code-execution-iaas",
            "control_plane": "firecracker-microvm",
            "runtime": "python3.11",
            "table_name": os.environ.get("TABLE_NAME", "EphemeralSandboxTable"),
            "redis_host": os.environ.get("REDIS_HOST", "redis"),
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        },
    )


def handle_register(event: Dict[str, Any], table: Optional[Any] = None) -> Dict[str, Any]:
    """
    POST /auth/register
    Atomically (TransactWriteItems) creates:
      - ACCOUNT#<id> (SK="METADATA", tier="FREE"|"PRO", credentials)
      - EMAIL#<email> (SK="LOOKUP", maps email -> account_id)
      - QUOTA#<account_id>#<YYYY-MM> (SK="SUMMARY", accumulated_seconds=0)
    Conditions (attribute_not_exists) enforce email/account_id uniqueness and
    prevent overwriting an existing account. Cache is populated AFTER commit.
    """
    ddb_table = get_dynamodb_table(table)
    try:
        body = _parse_json_body(event)
    except ValueError as exc:
        return _json_response(400, {"error": str(exc)})

    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    tier = (body.get("tier") or "FREE").strip().upper()
    username = (body.get("username") or (email.split("@")[0] if "@" in email else "tenant")).strip()
    custom_account_id = (body.get("account_id") or "").strip()

    if not email or "@" not in email:
        return _json_response(400, {"error": "A valid 'email' is required"})
    if not password or len(password) < 4:
        return _json_response(400, {"error": "'password' must be at least 4 characters"})
    if tier not in VALID_TIERS:
        return _json_response(400, {"error": f"'tier' must be one of {VALID_TIERS}"})

    account_id = custom_account_id or f"acc-{uuid.uuid4().hex[:10]}"
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    pwd_hash = hash_password(password)

    account_item = {
        "PK": f"ACCOUNT#{account_id}",
        "SK": "METADATA",
        "entity_type": "ACCOUNT",
        "account_id": account_id,
        "email": email,
        "username": username,
        "tier": tier,
        "password_hash": pwd_hash,
        "created_at": now_iso,
    }
    email_item = {
        "PK": f"EMAIL#{email}",
        "SK": "LOOKUP",
        "entity_type": "EMAIL_LOOKUP",
        "account_id": account_id,
        "email": email,
        "tier": tier,
        "created_at": now_iso,
    }

    period = get_current_period()
    quota_item = {
        "PK": quota_pk(account_id, period),
        "SK": "SUMMARY",
        "entity_type": "QUOTA",
        "account_id": account_id,
        "period": period,
        "tier": tier,
        "accumulated_seconds": Decimal("0"),
        "execution_count": Decimal("0"),
        "updated_at": now_iso,
    }
    if tier == "FREE":
        quota_item["limit_seconds"] = Decimal(str(int(FREE_MONTHLY_QUOTA_SECONDS)))
    # PRO: no limit_seconds attribute (unlimited)

    table_name = ddb_table.table_name
    # NOTE: boto3 resource's meta.client serializes high-level Python values
    # automatically (Do NOT pass AttributeValue-formatted items here).
    try:
        ddb_table.meta.client.transact_write_items(
            TransactItems=[
                {
                    "Put": {
                        "TableName": table_name,
                        "Item": account_item,
                        "ConditionExpression": "attribute_not_exists(PK)",
                    }
                },
                {
                    "Put": {
                        "TableName": table_name,
                        "Item": email_item,
                        "ConditionExpression": "attribute_not_exists(PK)",
                    }
                },
                {
                    "Put": {
                        "TableName": table_name,
                        "Item": quota_item,
                        "ConditionExpression": "attribute_not_exists(PK)",
                    }
                },
            ]
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        message = exc.response.get("Error", {}).get("Message", "")
        is_condition_failure = (
            code == "ConditionalCheckFailedException"
            or (code == "TransactionCanceledException" and "ConditionalCheckFailed" in message)
        )
        if is_condition_failure:
            return _json_response(
                409,
                {"error": f"Account with email '{email}' or account_id '{account_id}' already exists"},
            )
        raise

    # Write-Through cache AFTER the transaction committed (best-effort, never blocks register)
    public_account = {k: v for k, v in account_item.items() if k != "password_hash"}
    try:
        cache_set_with_ttl(account_cache_key(account_id), public_account, ttl=DEFAULT_TTL_SECONDS)
    except Exception:
        pass

    write_through_quota(
        {
            "PK": quota_pk(account_id, period),
            "SK": "SUMMARY",
            "account_id": account_id,
            "period": period,
            "tier": tier,
            "accumulated_seconds": 0.0,
            "limit_seconds": int(FREE_MONTHLY_QUOTA_SECONDS) if tier == "FREE" else None,
            "execution_count": 0,
        },
        ttl=DEFAULT_TTL_SECONDS,
    )

    token = create_jwt_token(account_id=account_id, email=email, tier=tier)
    write_through_jwt_session(
        token,
        {"account_id": account_id, "email": email, "tier": tier},
        ttl=3600,
    )
    return _json_response(
        201,
        {
            "message": "Account registered successfully",
            "PK": f"ACCOUNT#{account_id}",
            "SK": "METADATA",
            "account_id": account_id,
            "email": email,
            "username": username,
            "tier": tier,
            "token": token,
            "_fuente": "dynamodb",
        },
    )


def handle_login(event: Dict[str, Any], table: Optional[Any] = None) -> Dict[str, Any]:
    """
    POST /auth/login
    Authenticates credentials against ACCOUNT#<id> in DynamoDB and returns a signed JWT.
    """
    ddb_table = get_dynamodb_table(table)
    try:
        body = _parse_json_body(event)
    except ValueError as exc:
        return _json_response(400, {"error": str(exc)})

    email = (body.get("email") or "").strip().lower()
    account_id = (body.get("account_id") or "").strip()
    password = body.get("password") or ""

    if not password or (not email and not account_id):
        return _json_response(400, {"error": "'email' (or 'account_id') and 'password' are required"})

    if not account_id:
        lookup = ddb_table.get_item(
            Key={
                "PK": f"EMAIL#{email}",
                "SK": "LOOKUP",
            }
        ).get("Item")
        if not lookup:
            return _json_response(401, {"error": "Invalid email or password"})
        account_id = lookup["account_id"]

    account_item = ddb_table.get_item(
        Key={
            "PK": f"ACCOUNT#{account_id}",
            "SK": "METADATA",
        }
    ).get("Item")

    if not account_item or not verify_password(password, account_item.get("password_hash", "")):
        return _json_response(401, {"error": "Invalid email or password"})

    tier = account_item.get("tier", "FREE")
    user_email = account_item.get("email", email)
    token = create_jwt_token(account_id=account_id, email=user_email, tier=tier)

    # Refresh account metadata in Redis with EXPIRE (TTL)
    public_account = {k: v for k, v in account_item.items() if k != "password_hash"}
    try:
        cache_set_with_ttl(account_cache_key(account_id), public_account, ttl=DEFAULT_TTL_SECONDS)
    except Exception:
        pass

    return _json_response(
        200,
        {
            "message": "Login successful",
            "account_id": account_id,
            "email": user_email,
            "tier": tier,
            "token": token,
            "_fuente": "dynamodb",
        },
    )


def handle_execute(event: Dict[str, Any], table: Optional[Any] = None) -> Dict[str, Any]:
    """
    POST /execute (JWT protected)
    1. Validates Bearer JWT token.
    2. Loads ACCOUNT#<id> via Cache-Aside (`_fuente`: "cache" | "dynamodb").
    3. Enforces monthly execution quota (`QUOTA#<account_id>#<YYYY-MM>`):
       - FREE capped at 14,400s/month (HTTP 429 if exceeded)
       - PRO unlimited
       - Strict timeout capped at user's remaining monthly quota.
    4. Runs code snippet in isolated Ephemeral Sandbox Runner with memory limits & microVM telemetry.
    5. Persists `EXEC#<execution_id>` and updates `QUOTA#<account_id>#<YYYY-MM>` in DynamoDB.
    """
    ddb_table = get_dynamodb_table(table)
    try:
        claims = authenticate_request(event, table=ddb_table)
    except AuthenticationError as exc:
        return _json_response(exc.status_code, {"error": exc.message})

    try:
        body = _parse_json_body(event)
    except ValueError as exc:
        return _json_response(400, {"error": str(exc)})

    code = body.get("code")
    if not code or not isinstance(code, str) or not code.strip():
        return _json_response(400, {"error": "Non-empty 'code' snippet is required"})
    if len(code) > MAX_CODE_CHARS:
        return _json_response(413, {"error": f"'code' exceeds the {MAX_CODE_CHARS} character limit"})

    runtime = body.get("runtime", "python3.11")
    requested_timeout = body.get("timeout")
    memory_limit_mb = int(body.get("memory_limit_mb", DEFAULT_MEMORY_LIMIT_MB))

    account_id = claims["account_id"]

    # Retrieve account metadata via Cache-Aside
    account_data, account_fuente = get_account_cache_aside(account_id=account_id, table=ddb_table)
    tier = (account_data.get("tier") if account_data else claims.get("tier", "FREE")).upper()

    # Enforce monthly execution quota & calculate strict timeout capped at remaining quota
    try:
        enforcement = enforce_quota_and_calculate_timeout(
            account_id=account_id,
            tier=tier,
            requested_timeout=requested_timeout,
            table=ddb_table,
        )
    except QuotaExceededError as exc:
        return _json_response(
            429,
            {
                "error": "Too Many Requests — Monthly execution quota exceeded",
                "message": exc.message,
                "quota": exc.quota_details,
            },
        )

    effective_timeout = enforcement["effective_timeout"]
    execution_id = body.get("execution_id") or f"exec-{uuid.uuid4().hex[:12]}"

    # Execute inside the ephemeral sandbox runner
    try:
        sandbox_result = run_ephemeral_code(
            code=code,
            runtime=runtime,
            timeout_seconds=effective_timeout,
            memory_limit_mb=memory_limit_mb,
            execution_id=execution_id,
        )
    except ValueError as exc:
        return _json_response(400, {"error": str(exc)})

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    exec_item = {
        "PK": f"EXEC#{execution_id}",
        "SK": "METADATA",
        "entity_type": "EXECUTION",
        "execution_id": execution_id,
        "account_id": account_id,
        "tier": tier,
        "code": code,
        "runtime": sandbox_result["runtime"],
        "duration": sandbox_result["duration"],
        "timeout_enforced": sandbox_result["timeout_enforced"],
        "memory_limit_mb": sandbox_result["memory_limit_mb"],
        "status": sandbox_result["status"],
        "exit_code": sandbox_result["exit_code"],
        "stdout": sandbox_result["stdout"],
        "stderr": sandbox_result["stderr"],
        "telemetry": sandbox_result["telemetry"],
        "created_at": now_iso,
        # GSI1 — listado por cuenta sin duplicar el item
        "GSI1PK": f"ACCOUNT#{account_id}",
        "GSI1SK": now_iso,
        # TTL — las ejecuciones expiran automáticamente
        "ttl": int(time.time()) + EXECUTION_TTL_SECONDS,
    }

    # Persist EXEC#<execution_id> in DynamoDB Single-Table & apply Write-Through / Cache-Aside policy
    write_through_execution(
        ddb_table,
        exec_item,
        ddb_converter=_convert_floats_to_decimal,
        ttl=DEFAULT_TTL_SECONDS,
        populate_cache=bool(body.get("write_through", False)),
    )

    # Settle the quota reservation made BEFORE the execution ran.
    # FREE: refund unused reserved seconds. PRO: count + accumulate duration.
    updated_quota = settle_quota_usage(
        account_id=account_id,
        tier=tier,
        reserved_seconds=enforcement.get("reserved_seconds", 0.0),
        duration_seconds=sandbox_result["duration"],
        table=ddb_table,
    )
    write_through_quota(updated_quota, ttl=DEFAULT_TTL_SECONDS)

    response_payload = dict(exec_item)
    response_payload["quota"] = updated_quota
    response_payload["account_fuente"] = account_fuente
    response_payload["_fuente"] = "dynamodb"
    return _json_response(200, response_payload)


def handle_get_execution(
    event: Dict[str, Any],
    execution_id: str,
    table: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    GET /executions/{id}
    Uses Cache-Aside with EXPIRE (TTL) for execution results (`_fuente`: "cache" | "dynamodb").
    """
    ddb_table = get_dynamodb_table(table)
    try:
        claims = authenticate_request(event, table=ddb_table)
    except AuthenticationError as exc:
        return _json_response(exc.status_code, {"error": exc.message})

    if not execution_id:
        return _json_response(400, {"error": "Missing execution_id path parameter"})

    exec_data, fuente = get_execution_cache_aside(execution_id=execution_id, table=ddb_table)
    if not exec_data:
        return _json_response(404, {"error": f"Execution '{execution_id}' not found"})

    # Ownership check — executions are private to the owning account
    if exec_data.get("account_id") != claims.get("account_id"):
        return _json_response(403, {"error": "Execution belongs to a different account"})

    exec_data["_fuente"] = fuente
    return _json_response(200, exec_data)


def handle_get_quota(event: Dict[str, Any], table: Optional[Any] = None) -> Dict[str, Any]:
    """
    GET /quota
    Returns the caller's monthly execution quota (`QUOTA#<account_id>#<YYYY-MM>`)
    and account metadata resolved via Cache-Aside (`_fuente`: "cache" | "dynamodb").
    """
    ddb_table = get_dynamodb_table(table)
    try:
        claims = authenticate_request(event, table=ddb_table)
    except AuthenticationError as exc:
        return _json_response(exc.status_code, {"error": exc.message})

    account_id = claims["account_id"]
    account_data, account_fuente = get_account_cache_aside(account_id=account_id, table=ddb_table)
    tier = (account_data.get("tier") if account_data else claims.get("tier", "FREE")).upper()

    query_params = event.get("queryStringParameters") or {}
    period = query_params.get("period") if isinstance(query_params, dict) else None

    quota_status = get_quota_status(
        account_id=account_id,
        tier=tier,
        table=ddb_table,
        period=period,
    )
    quota_status["account"] = account_data or {
        "PK": f"ACCOUNT#{account_id}",
        "account_id": account_id,
        "email": claims.get("email"),
        "tier": tier,
        "_fuente": account_fuente,
    }
    quota_status["_fuente"] = account_fuente
    return _json_response(200, quota_status)


def handle_refresh(event: Dict[str, Any], table: Optional[Any] = None) -> Dict[str, Any]:
    """
    POST /auth/refresh
    Validates current Bearer JWT, reads latest account metadata via Cache-Aside,
    and issues a refreshed JWT token.
    """
    ddb_table = get_dynamodb_table(table)
    try:
        claims = authenticate_request(event, table=ddb_table)
    except AuthenticationError as exc:
        return _json_response(exc.status_code, {"error": exc.message})

    account_id = claims["account_id"]
    account_data, fuente = get_account_cache_aside(account_id=account_id, table=ddb_table)
    tier = (account_data.get("tier") if account_data else claims.get("tier", "FREE")).upper()
    email = (account_data.get("email") if account_data else claims.get("email", "")) or ""

    new_token = create_jwt_token(account_id=account_id, email=email, tier=tier)
    return _json_response(
        200,
        {
            "message": "Token refreshed",
            "account_id": account_id,
            "email": email,
            "tier": tier,
            "token": new_token,
            "_fuente": fuente,
        },
    )


def handle_subscription_upgrade(event: Dict[str, Any], table: Optional[Any] = None) -> Dict[str, Any]:
    """
    POST /subscription/upgrade
    Instant dummy subscription purchase (no paywall).
    Upgrades (or switches) the authenticated account's tier (default 'PRO') in DynamoDB
    Single-Table (`ACCOUNT#<id>` and `QUOTA#<account_id>#<YYYY-MM>`), updates Redis cache,
    and returns a freshly signed JWT with the updated tier.
    """
    ddb_table = get_dynamodb_table(table)
    try:
        claims = authenticate_request(event, table=ddb_table)
    except AuthenticationError as exc:
        return _json_response(exc.status_code, {"error": exc.message})

    try:
        body = _parse_json_body(event)
    except ValueError as exc:
        return _json_response(400, {"error": str(exc)})

    target_tier = (body.get("tier") or "PRO").strip().upper()
    if target_tier not in VALID_TIERS:
        return _json_response(400, {"error": f"'tier' must be one of {VALID_TIERS}"})

    account_id = claims["account_id"]
    email = claims.get("email", "")
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # Update ACCOUNT#<id> in DynamoDB
    ddb_table.update_item(
        Key={"PK": f"ACCOUNT#{account_id}", "SK": "METADATA"},
        UpdateExpression="SET tier = :tier, updated_at = :now",
        ExpressionAttributeValues={":tier": target_tier, ":now": now_iso},
    )

    # Update current period QUOTA#<account_id>#<YYYY-MM>
    period = get_current_period()
    ddb_table.update_item(
        Key={"PK": quota_pk(account_id, period), "SK": "SUMMARY"},
        UpdateExpression="SET tier = :tier, updated_at = :now",
        ExpressionAttributeValues={":tier": target_tier, ":now": now_iso},
    )

    # Keep EMAIL#LOOKUP consistent (tier denormalized there)
    if email:
        try:
            ddb_table.update_item(
                Key={"PK": f"EMAIL#{email}", "SK": "LOOKUP"},
                UpdateExpression="SET tier = :tier, updated_at = :now",
                ExpressionAttributeValues={":tier": target_tier, ":now": now_iso},
            )
        except ClientError:
            pass

    updated_account = ddb_table.get_item(
        Key={"PK": f"ACCOUNT#{account_id}", "SK": "METADATA"}
    ).get("Item") or {
        "PK": f"ACCOUNT#{account_id}",
        "SK": "METADATA",
        "account_id": account_id,
        "email": email,
        "tier": target_tier,
    }
    public_account = {k: v for k, v in updated_account.items() if k != "password_hash"}
    try:
        cache_set_with_ttl(account_cache_key(account_id), public_account, ttl=DEFAULT_TTL_SECONDS)
    except Exception:
        pass

    new_token = create_jwt_token(
        account_id=account_id,
        email=public_account.get("email", email),
        tier=target_tier,
    )
    quota_status = get_quota_status(account_id=account_id, tier=target_tier, table=ddb_table, period=period)

    return _json_response(
        200,
        {
            "message": f"Subscription activated: {target_tier} tier granted immediately (dummy purchase)",
            "account_id": account_id,
            "email": public_account.get("email", email),
            "tier": target_tier,
            "token": new_token,
            "quota": quota_status,
            "_fuente": "dynamodb",
        },
    )


def handle_get_api_key(event: Dict[str, Any], table: Optional[Any] = None) -> Dict[str, Any]:
    """
    GET /api-key
    Returns metadata about the caller's API key (never the key itself), so agents
    can confirm a key exists and when it was created/used.
    """
    ddb_table = get_dynamodb_table(table)
    try:
        claims = authenticate_request(event, table=ddb_table)
    except AuthenticationError as exc:
        return _json_response(exc.status_code, {"error": exc.message})

    account_id = claims["account_id"]
    meta = get_api_key_meta(ddb_table, account_id)
    if not meta:
        return _json_response(200, {
            "has_key": False,
            "api_key_id": None,
            "api_key_prefix": None,
            "created_at": None,
            "last_used_at": None,
        })

    return _json_response(200, {
        "has_key": True,
        "api_key_id": meta.get("api_key_id"),
        "api_key_prefix": meta.get("api_key_prefix"),
        "created_at": meta.get("created_at"),
        "last_used_at": meta.get("last_used_at"),
    })


def handle_rotate_api_key(event: Dict[str, Any], table: Optional[Any] = None) -> Dict[str, Any]:
    """
    POST /api-key/rotate
    Creates (or replaces) the caller's API key. The plaintext key is returned
    exactly once and is never stored — only its SHA-256 hash is persisted.
    """
    ddb_table = get_dynamodb_table(table)
    try:
        claims = authenticate_request(event, table=ddb_table)
    except AuthenticationError as exc:
        return _json_response(exc.status_code, {"error": exc.message})

    account_id = claims["account_id"]
    result = rotate_api_key(ddb_table, account_id)
    return _json_response(200, {
        "message": "API key rotated. Store it somewhere safe — it will not be shown again.",
        **result,
    })


def handle_revoke_api_key(event: Dict[str, Any], table: Optional[Any] = None) -> Dict[str, Any]:
    """
    DELETE /api-key
    Revokes the caller's API key (agents using it will receive 401 afterwards).
    """
    ddb_table = get_dynamodb_table(table)
    try:
        claims = authenticate_request(event, table=ddb_table)
    except AuthenticationError as exc:
        return _json_response(exc.status_code, {"error": exc.message})

    account_id = claims["account_id"]
    revoked = revoke_api_key(ddb_table, account_id)
    return _json_response(200, {
        "revoked": revoked,
        "message": "API key revoked." if revoked else "No API key was set.",
    })


def handle_list_executions(event: Dict[str, Any], table: Optional[Any] = None) -> Dict[str, Any]:
    """
    GET /executions
    Returns recent executions for the authenticated account along with cost & usage
    telemetry (daily, weekly, monthly, projected).
    """
    from boto3.dynamodb.conditions import Key

    ddb_table = get_dynamodb_table(table)
    try:
        claims = authenticate_request(event, table=ddb_table)
    except AuthenticationError as exc:
        return _json_response(exc.status_code, {"error": exc.message})

    account_id = claims["account_id"]
    resp = ddb_table.query(
        IndexName="GSI1",
        KeyConditionExpression=Key("GSI1PK").eq(f"ACCOUNT#{account_id}"),
        ScanIndexForward=False,
        Limit=50,
    )
    items = resp.get("Items") or []

    return _json_response(
        200,
        {
            "account_id": account_id,
            "count": len(items),
            "executions": items,
            "_fuente": "dynamodb",
        },
    )


def main(event: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    """
    Main AWS Lambda entrypoint for API Gateway (LambdaRestApi proxy integration).
    Routes:
      - GET  /health
      - POST /auth/register
      - POST /auth/login
      - POST /auth/refresh
      - POST /subscription/upgrade
      - GET  /api-key
      - POST /api-key/rotate
      - DELETE /api-key
      - POST /execute
      - GET  /executions
      - GET  /executions/{id}
      - GET  /quota
    """
    http_method = (event.get("httpMethod") or "GET").upper()
    path = (event.get("path") or event.get("resource") or "/").rstrip("/")
    if not path:
        path = "/"

    if http_method == "OPTIONS":
        return _json_response(200, {"status": "ok"})

    if http_method == "GET" and path.endswith("/health"):
        return handle_health(event)

    if http_method == "POST" and path.endswith("/auth/register"):
        return handle_register(event)

    if http_method == "POST" and path.endswith("/auth/login"):
        return handle_login(event)

    if http_method == "POST" and path.endswith("/auth/refresh"):
        return handle_refresh(event)

    if http_method == "POST" and path.endswith("/subscription/upgrade"):
        return handle_subscription_upgrade(event)

    if http_method == "GET" and path.endswith("/api-key"):
        return handle_get_api_key(event)

    if http_method == "POST" and path.endswith("/api-key/rotate"):
        return handle_rotate_api_key(event)

    if http_method == "DELETE" and path.endswith("/api-key"):
        return handle_revoke_api_key(event)

    if http_method == "POST" and path.endswith("/execute"):
        return handle_execute(event)

    if http_method == "GET" and path.endswith("/executions"):
        return handle_list_executions(event)

    if http_method == "GET" and ("/executions/" in path or path.endswith("/executions/{id}")):
        path_params = event.get("pathParameters") or {}
        execution_id = path_params.get("id") or path_params.get("execution_id")
        if not execution_id and "/executions/" in path:
            execution_id = path.split("/executions/", 1)[1].split("/")[0]
        return handle_get_execution(event, execution_id=execution_id or "")

    if http_method == "GET" and path.endswith("/quota"):
        return handle_get_quota(event)

    return _json_response(
        404,
        {
            "error": f"Route '{http_method} {path}' not found",
            "available_endpoints": [
                "GET /health",
                "POST /auth/register",
                "POST /auth/login",
                "POST /auth/refresh",
                "POST /subscription/upgrade",
                "GET /api-key",
                "POST /api-key/rotate",
                "DELETE /api-key",
                "POST /execute",
                "GET /executions",
                "GET /executions/{id}",
                "GET /quota",
            ],
        },
    )
