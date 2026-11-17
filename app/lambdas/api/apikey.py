import hashlib
import secrets
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional


API_KEY_PREFIX = "aurora_ak_"
_LOOKUP_PREFIX = "APIKEYLOOKUP#"
_META_PREFIX = "APIKEY#"


def generate_api_key() -> str:
    """Generate a new plaintext API key (shown to the caller exactly once)."""
    return f"{API_KEY_PREFIX}{secrets.token_hex(24)}"


def hash_api_key(key: str) -> str:
    """Deterministic SHA-256 of the plaintext key — only the hash is stored."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_api_key_meta(table: Any, account_id: str) -> Optional[Dict[str, Any]]:
    """Return the stored (hashed) API-key metadata for an account, or None."""
    response = table.get_item(
        Key={
            "PK": f"{_META_PREFIX}{account_id}",
            "SK": "METADATA",
        },
        ConsistentRead=True,
    )
    return response.get("Item")


def _to_metadata(account_id: str, api_key_id: str, prefix: str, key_hash: str, now_iso: str) -> Dict[str, Any]:
    return {
        "PK": f"{_META_PREFIX}{account_id}",
        "SK": "METADATA",
        "entity_type": "API_KEY",
        "account_id": account_id,
        "api_key_id": api_key_id,
        "api_key_prefix": prefix,
        "api_key_hash": key_hash,
        "created_at": now_iso,
        "updated_at": now_iso,
        "last_used_at": None,
    }


def rotate_api_key(table: Any, account_id: str) -> Dict[str, Any]:
    """
    Create (or replace) the account's API key.

    Steps (all inside one transaction):
      1. Delete the previous LOOKUP item, if any.
      2. Put the new META item (hashed key + display prefix).
      3. Put the new LOOKUP item (hashed key -> account_id).
    The plaintext key is returned exactly once; it is never stored.
    """
    existing = get_api_key_meta(table, account_id)
    key = generate_api_key()
    key_hash = hash_api_key(key)
    api_key_id = f"ak_{secrets.token_hex(6)}"
    prefix = f"{API_KEY_PREFIX}{key[len(API_KEY_PREFIX):][:8]}"
    now_iso = _now_iso()

    meta = _to_metadata(account_id, api_key_id, prefix, key_hash, now_iso)
    lookup = {
        "PK": f"{_LOOKUP_PREFIX}{key_hash}",
        "SK": "LOOKUP",
        "entity_type": "API_KEY_LOOKUP",
        "account_id": account_id,
        "api_key_id": api_key_id,
        "created_at": now_iso,
    }

    table_name = table.table_name
    transact_items = []
    if existing and existing.get("api_key_hash"):
        transact_items.append(
            {
                "Delete": {
                    "TableName": table_name,
                    "Key": {
                        "PK": f"{_LOOKUP_PREFIX}{existing['api_key_hash']}",
                        "SK": "LOOKUP",
                    },
                }
            }
        )
    transact_items.append(
        {"Put": {"TableName": table_name, "Item": meta}}
    )
    transact_items.append(
        {"Put": {"TableName": table_name, "Item": lookup, "ConditionExpression": "attribute_not_exists(PK)"}}
    )

    from botocore.exceptions import ClientError

    try:
        table.meta.client.transact_write_items(TransactItems=transact_items)
    except ClientError as exc:
        # Extremely unlikely: hash collision. Retry once with a fresh key.
        code = exc.response.get("Error", {}).get("Code", "")
        message = exc.response.get("Error", {}).get("Message", "")
        if code == "TransactionCanceledException" and "ConditionalCheckFailed" in message:
            key = generate_api_key()
            key_hash = hash_api_key(key)
            meta["api_key_hash"] = key_hash
            meta["api_key_prefix"] = f"{API_KEY_PREFIX}{key[len(API_KEY_PREFIX):][:8]}"
            lookup = {
                "PK": f"{_LOOKUP_PREFIX}{key_hash}",
                "SK": "LOOKUP",
                "entity_type": "API_KEY_LOOKUP",
                "account_id": account_id,
                "api_key_id": api_key_id,
                "created_at": now_iso,
            }
            transact_items = [item for item in transact_items if "Delete" not in item]
            transact_items.append({"Put": {"TableName": table_name, "Item": lookup, "ConditionExpression": "attribute_not_exists(PK)"}})
            table.meta.client.transact_write_items(TransactItems=transact_items)
        else:
            raise

    return {
        "api_key": key,
        "api_key_id": api_key_id,
        "api_key_prefix": meta["api_key_prefix"],
        "created_at": now_iso,
    }


def revoke_api_key(table: Any, account_id: str) -> bool:
    """Delete the account's API key and its LOOKUP item. Returns True if one existed."""
    existing = get_api_key_meta(table, account_id)
    if not existing:
        return False

    table_name = table.table_name
    items = [
        {"Delete": {"TableName": table_name, "Key": {"PK": f"{_META_PREFIX}{account_id}", "SK": "METADATA"}}},
    ]
    if existing.get("api_key_hash"):
        items.append(
            {
                "Delete": {
                    "TableName": table_name,
                    "Key": {"PK": f"{_LOOKUP_PREFIX}{existing['api_key_hash']}", "SK": "LOOKUP"},
                }
            }
        )
    table.meta.client.transact_write_items(TransactItems=items)
    return True


def lookup_account_by_api_key(table: Any, key: str) -> Optional[str]:
    """Resolve an api key to an account_id (hashed, O(1) lookup) or None."""
    if not key or not key.startswith(API_KEY_PREFIX):
        return None
    key_hash = hash_api_key(key)
    response = table.get_item(
        Key={
            "PK": f"{_LOOKUP_PREFIX}{key_hash}",
            "SK": "LOOKUP",
        },
        ConsistentRead=True,
    )
    item = response.get("Item")
    return item.get("account_id") if item else None


def touch_api_key(table: Any, account_id: str) -> None:
    """Best-effort last_used_at bookkeeping (fire-and-forget on hot paths)."""
    try:
        table.update_item(
            Key={
                "PK": f"{_META_PREFIX}{account_id}",
                "SK": "METADATA",
            },
            UpdateExpression="SET last_used_at = :now",
            ExpressionAttributeValues={":now": _now_iso()},
        )
    except Exception:
        pass
