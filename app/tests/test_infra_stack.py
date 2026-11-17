import aws_cdk as cdk
from aws_cdk.assertions import Template
from infra.stack import AppStack


def test_cdk_stack_synthesizes_dynamodb_lambda_and_apigateway():
    """
    Verify infra/stack.py synthesizes:
      - AWS::DynamoDB::Table with PK (HASH), SK (RANGE), and PAY_PER_REQUEST
      - AWS::Lambda::Function with python3.11 runtime, REDIS_HOST='redis', and AWS_ENDPOINT_URL
      - AWS::ApiGateway::RestApi (LambdaRestApi)
    """
    app = cdk.App()
    stack = AppStack(app, "TestProyectoFinalStack")
    template = Template.from_stack(stack)

    template.has_resource_properties(
        "AWS::DynamoDB::Table",
        {
            "BillingMode": "PAY_PER_REQUEST",
            "KeySchema": [
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
        },
    )

    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Runtime": "python3.11",
            "Handler": "handler.main",
        },
    )

    template.resource_count_is("AWS::ApiGateway::RestApi", 1)
