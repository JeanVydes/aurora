import os
from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_apigateway as apigw,
    aws_dynamodb as ddb,
    aws_lambda as _lambda,
)
from constructs import Construct


class AppStack(Stack):
    """
    CDK Stack for the Ephemeral Code Execution IaaS (Firecracker-style MicroVM Control Plane).
    Defines:
      - DynamoDB Single-Table (PK, SK, PAY_PER_REQUEST)
      - Lambda Layer for shared dependencies
      - Python 3.11 Lambda functions with AWS_ENDPOINT_URL and REDIS_HOST=redis
      - API Gateway (LambdaRestApi) with CfnOutput ApiUrl
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        # 1. Tabla DynamoDB — Single Table Design (PK, SK, PAY_PER_REQUEST)
        #    - GSI1: lista de ejecuciones por cuenta (sparse, INCLUDE projection)
        #    - TTL: las ejecuciones expiran automáticamente (ephemeral by default)
        #    - PITR + deletion protection: nunca se pierde información por accidente
        self.table = ddb.Table(
            self,
            "AppTable",
            table_name="EphemeralSandboxTable",
            partition_key=ddb.Attribute(
                name="PK",
                type=ddb.AttributeType.STRING,
            ),
            sort_key=ddb.Attribute(
                name="SK",
                type=ddb.AttributeType.STRING,
            ),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="ttl",
            point_in_time_recovery=True,
            deletion_protection=True,
            encryption=ddb.TableEncryption.AWS_MANAGED,
            removal_policy=RemovalPolicy.RETAIN,
        )

        # GSI1 — access pattern "listar ejecuciones recientes de una cuenta"
        # Sparse: solo los items EXEC# llevan GSI1PK/GSI1SK.
        # INCLUDE projection: solo los atributos que el dashboard/API necesitan.
        self.table.add_global_secondary_index(
            index_name="GSI1",
            partition_key=ddb.Attribute(
                name="GSI1PK",
                type=ddb.AttributeType.STRING,
            ),
            sort_key=ddb.Attribute(
                name="GSI1SK",
                type=ddb.AttributeType.STRING,
            ),
            projection_type=ddb.ProjectionType.INCLUDE,
            non_key_attributes=[
                "execution_id",
                "status",
                "duration",
                "exit_code",
                "memory_limit_mb",
                "runtime",
                "created_at",
                "account_id",
                "tier",
                "telemetry",
            ],
        )

        # 2. Lambda Layer — Dependencias compartidas
        deps_layer = _lambda.LayerVersion(
            self,
            "DependenciasLayer",
            code=_lambda.Code.from_asset(os.path.join(base_dir, "layers", "dependencias")),
            compatible_runtimes=[_lambda.Runtime.PYTHON_3_11],
            description="Dependencias para Lambdas (redis, PyJWT, boto3)",
        )

        common_env = {
            "TABLE_NAME": self.table.table_name,
            "AWS_ENDPOINT_URL": os.environ.get("AWS_ENDPOINT_URL", "http://emulador:4566"),
            "REDIS_HOST": os.environ.get("REDIS_HOST", "redis"),
            "REDIS_PORT": os.environ.get("REDIS_PORT", "6379"),
            # Placeholder for local dev only — provide JWT_SECRET in production.
            "JWT_SECRET": os.environ.get("JWT_SECRET", "dev-only-insecure-jwt-secret-do-not-use-in-prod"),
            "CACHE_TTL_SECONDS": "300",
        }

        # 3. Lambda Handler Principal — Control Plane & Ephemeral Sandbox Runner
        self.api_handler = _lambda.Function(
            self,
            "ApiHandler",
            runtime=_lambda.Runtime.PYTHON_3_11,
            handler="handler.main",
            code=_lambda.Code.from_asset(os.path.join(base_dir, "lambdas", "api")),
            layers=[deps_layer],
            timeout=Duration.seconds(60),
            memory_size=256,
            environment=common_env,
        )

        # 4. Lambda Handler de Health Check
        self.health_handler = _lambda.Function(
            self,
            "HealthHandler",
            runtime=_lambda.Runtime.PYTHON_3_11,
            handler="handler.main",
            code=_lambda.Code.from_asset(os.path.join(base_dir, "lambdas", "health")),
            layers=[deps_layer],
            timeout=Duration.seconds(10),
            memory_size=128,
            environment=common_env,
        )

        self.table.grant_read_write_data(self.api_handler)
        self.table.grant_read_data(self.health_handler)

        # 5. API Gateway — LambdaRestApi
        self.api = apigw.LambdaRestApi(
            self,
            "Api",
            handler=self.api_handler,
            proxy=True,
            deploy_options=apigw.StageOptions(stage_name="prod"),
        )

        # Output ApiUrl (como se documenta en el flujo de cdklocal deploy)
        CfnOutput(
            self,
            "ApiUrl",
            value=self.api.url,
            description="URL base del API Gateway desplegado en LocalStack/MiniStack",
        )


ProyectoFinalStack = AppStack
