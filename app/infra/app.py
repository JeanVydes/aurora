#!/usr/bin/env python3
import os
import sys

# Ensure app root is on sys.path when running `cdklocal` from /app
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aws_cdk as cdk
from infra.stack import AppStack

app = cdk.App()

AppStack(
    app,
    "ProyectoFinalStack",
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT", "000000000000"),
        region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
    ),
)

app.synth()
