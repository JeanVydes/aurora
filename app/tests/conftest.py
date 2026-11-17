import os
import sys

# Import sibling modules EXACTLY as the Lambda runtime sees them: as top-level
# modules inside `lambdas/api`. Importing via the `lambdas.api.*` package path
# would create DUPLICATE module objects (different `_redis_client_override`
# globals) and defeat the injected fakeredis/moto fixtures.
API_LAMBDA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lambdas", "api")
API_LAMBDA_DIR = os.path.abspath(API_LAMBDA_DIR)
if API_LAMBDA_DIR not in sys.path:
    sys.path.insert(0, API_LAMBDA_DIR)

import boto3
import fakeredis
import pytest
from moto import mock_aws

import cache as cache_module
import handler as handler_module


@pytest.fixture(autouse=True)
def aws_and_redis_env(monkeypatch):
    """
    Set up an isolated in-memory DynamoDB Single-Table (via moto) and
    in-memory Redis instance (via fakeredis) for every unit test.
    Runs completely offline without requiring LocalStack or Redis containers.
    """
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("TABLE_NAME", "EphemeralSandboxTable")
    monkeypatch.setenv("JWT_SECRET", "unit-test-secret-key-2026-0123456789abcdef0123456789abcdef")
    monkeypatch.setenv("CACHE_TTL_SECONDS", "300")
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)

    fake_redis = fakeredis.FakeStrictRedis(decode_responses=True)
    cache_module.set_redis_client(fake_redis)

    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        table = dynamodb.create_table(
            TableName="EphemeralSandboxTable",
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        table.wait_until_exists()
        handler_module.set_dynamodb_table(table)

        yield {
            "table": table,
            "redis": fake_redis,
        }

        handler_module.set_dynamodb_table(None)
        cache_module.set_redis_client(None)
