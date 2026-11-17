import datetime
from decimal import Decimal
from typing import Any, Dict, Optional

from botocore.exceptions import ClientError


# FREE accounts are capped at 4 hours (14,400 seconds) per month; PRO accounts have unlimited execution time.
FREE_MONTHLY_QUOTA_SECONDS = 14400.0
DEFAULT_REQUEST_TIMEOUT_SECONDS = 5.0
MAX_SINGLE_EXECUTION_TIMEOUT_SECONDS = 300.0


class QuotaExceededError(Exception):
    """Raised when a FREE account has exhausted its monthly 14,400s execution quota."""

    def __init__(self, message: str, quota_details: Dict[str, Any]) -> None:
        super().__init__(message)
        self.message = message
        self.quota_details = quota_details
        self.status_code = 429


class QuotaContentionError(QuotaExceededError):
    """Raised when concurrent updates to the quota item keep failing after retries."""


def get_current_period(now: Optional[datetime.datetime] = None) -> str:
    """Return current billing period in YYYY-MM format (UTC)."""
    dt = now or datetime.datetime.now(datetime.timezone.utc)
    return dt.strftime("%Y-%m")


def quota_pk(account_id: str, period: Optional[str] = None) -> str:
    """Build Single-Table PK: QUOTA#<account_id>#<YYYY-MM>."""
    ym = period or get_current_period()
    return f"QUOTA#{account_id}#{ym}"


def _get_item_consistent(table: Any, pk: str, sk: str) -> Optional[Dict[str, Any]]:
    return (
        table.get_item(Key={"PK": pk, "SK": sk}, ConsistentRead=True)
        .get("Item")
    )


def get_quota_status(
    account_id: str,
    tier: str,
    table: Any,
    period: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Read QUOTA#<account_id>#<YYYY-MM> from the DynamoDB Single-Table and compute remaining quota.
    """
    ym = period or get_current_period()
    pk = quota_pk(account_id, ym)
    normalized_tier = (tier or "FREE").upper()

    item = _get_item_consistent(table, pk, "SUMMARY") or {}
    accumulated = float(item.get("accumulated_seconds", Decimal("0")))
    execution_count = int(item.get("execution_count", 0))

    if normalized_tier == "PRO":
        return {
            "PK": pk,
            "SK": "SUMMARY",
            "account_id": account_id,
            "period": ym,
            "tier": "PRO",
            "accumulated_seconds": round(accumulated, 4),
            "limit_seconds": None,
            "remaining_seconds": None,
            "unlimited": True,
            "quota_exceeded": False,
            "execution_count": execution_count,
        }

    remaining = max(0.0, round(FREE_MONTHLY_QUOTA_SECONDS - accumulated, 4))
    exceeded = accumulated >= FREE_MONTHLY_QUOTA_SECONDS or remaining <= 0.0
    return {
        "PK": pk,
        "SK": "SUMMARY",
        "account_id": account_id,
        "period": ym,
        "tier": "FREE",
        "accumulated_seconds": round(accumulated, 4),
        "limit_seconds": int(FREE_MONTHLY_QUOTA_SECONDS),
        "remaining_seconds": remaining,
        "unlimited": False,
        "quota_exceeded": exceeded,
        "execution_count": execution_count,
    }


def _raise_exhausted(account_id: str, period: str, status: Dict[str, Any]) -> None:
    raise QuotaExceededError(
        message=(
            f"Monthly execution quota of {int(FREE_MONTHLY_QUOTA_SECONDS)} seconds "
            f"(4 hours) exceeded for FREE account '{account_id}' in period {period}."
        ),
        quota_details=status,
    )


def enforce_quota_and_calculate_timeout(
    account_id: str,
    tier: str,
    requested_timeout: Optional[float],
    table: Any,
    period: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Atomically RESERVE the caller's quota before executing.

    FREE accounts are capped at 4 hours (14,400s) per month. PRO accounts are unlimited.
      1. Read the quota item (strongly consistent) to compute the strict timeout:
         effective = min(requested, remaining).
      2. Reserve `effective` seconds in a single conditional update
         (attribute_not_exists(accumulated_seconds) OR accumulated_seconds = :old),
         so concurrent requests can never overspend the quota. Retried with a
         fresh read on contention; raises HTTP 429 when exhausted.
    The unused remainder is refunded after execution by `settle_quota_usage`.
    """
    ym = period or get_current_period()
    pk = quota_pk(account_id, ym)
    normalized_tier = (tier or "FREE").upper()

    req_timeout = (
        float(requested_timeout)
        if requested_timeout is not None
        else DEFAULT_REQUEST_TIMEOUT_SECONDS
    )
    if req_timeout <= 0 or req_timeout != req_timeout:  # NaN guard
        req_timeout = DEFAULT_REQUEST_TIMEOUT_SECONDS
    req_timeout = min(req_timeout, MAX_SINGLE_EXECUTION_TIMEOUT_SECONDS)

    if normalized_tier == "PRO":
        status = get_quota_status(account_id=account_id, tier="PRO", table=table, period=ym)
        return {
            "effective_timeout": req_timeout,
            "requested_timeout": req_timeout,
            "quota_status": status,
            "reserved_seconds": 0.0,
        }

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for _attempt in range(3):
        item = _get_item_consistent(table, pk, "SUMMARY") or {}
        old_accumulated = float(item.get("accumulated_seconds", Decimal("0")))
        execution_count = int(item.get("execution_count", 0))
        if old_accumulated >= FREE_MONTHLY_QUOTA_SECONDS:
            _raise_exhausted(account_id, ym, get_quota_status(account_id, "FREE", table, ym))
        effective = round(min(req_timeout, FREE_MONTHLY_QUOTA_SECONDS - old_accumulated), 4)
        try:
            table.update_item(
                Key={"PK": pk, "SK": "SUMMARY"},
                UpdateExpression=(
                    "SET entity_type = :etype, account_id = :acc, #per = :per, tier = :tier, updated_at = :now "
                    "ADD accumulated_seconds :reserve, execution_count :inc"
                ),
                ConditionExpression=(
                    "attribute_not_exists(accumulated_seconds) OR accumulated_seconds = :old"
                ),
                ExpressionAttributeNames={"#per": "period"},
                ExpressionAttributeValues={
                    ":etype": "QUOTA",
                    ":acc": account_id,
                    ":per": ym,
                    ":tier": normalized_tier,
                    ":now": now_iso,
                    ":reserve": Decimal(str(effective)),
                    ":inc": Decimal("1"),
                    ":old": Decimal(str(round(old_accumulated, 6))),
                },
            )
            break
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                continue  # another writer touched the item; re-read and retry
            raise
    else:
        raise QuotaContentionError(
            "Your execution could not be admitted due to concurrent usage updates; please retry.",
            quota_details=get_quota_status(account_id, "FREE", table, ym),
        )

    status = get_quota_status(account_id=account_id, tier="FREE", table=table, period=ym)
    return {
        "effective_timeout": effective,
        "requested_timeout": round(req_timeout, 4),
        "quota_status": status,
        "reserved_seconds": effective,
    }


def settle_quota_usage(
    account_id: str,
    tier: str,
    reserved_seconds: float,
    duration_seconds: float,
    table: Any,
    period: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Settle the reservation after execution.
      - FREE: refund the unused reserved seconds (reserved - actual duration).
      - PRO : unlimited — only track consumed seconds and the count.
    """
    ym = period or get_current_period()
    pk = quota_pk(account_id, ym)
    normalized_tier = (tier or "FREE").upper()
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    if normalized_tier == "PRO":
        dur_dec = Decimal(str(round(max(0.0, float(duration_seconds)), 4)))
        table.update_item(
            Key={"PK": pk, "SK": "SUMMARY"},
            UpdateExpression=(
                "SET entity_type = :etype, account_id = :acc, #per = :per, tier = :tier, updated_at = :now "
                "ADD accumulated_seconds :dur, execution_count :inc"
            ),
            ExpressionAttributeNames={"#per": "period"},
            ExpressionAttributeValues={
                ":etype": "QUOTA",
                ":acc": account_id,
                ":per": ym,
                ":tier": normalized_tier,
                ":now": now_iso,
                ":dur": dur_dec,
                ":inc": Decimal("1"),
            },
        )
        return get_quota_status(account_id=account_id, tier="PRO", table=table, period=ym)

    delta = round(max(0.0, float(reserved_seconds)) - round(max(0.0, float(duration_seconds)), 4), 4)
    if delta <= 0.0:
        return get_quota_status(account_id=account_id, tier="FREE", table=table, period=ym)

    refund_dec = Decimal(str(-delta))  # ADD supports negative numbers -> subtract
    table.update_item(
        Key={"PK": pk, "SK": "SUMMARY"},
        UpdateExpression="SET updated_at = :now ADD accumulated_seconds :refund",
        ExpressionAttributeValues={":refund": refund_dec, ":now": now_iso},
    )
    return get_quota_status(account_id=account_id, tier="FREE", table=table, period=ym)
