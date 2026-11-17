import datetime
import json
import os


def main(event, context=None):
    """
    GET /health — Health check endpoint for the Ephemeral Code Execution IaaS.
    """
    body = {
        "status": "healthy",
        "service": "ephemeral-code-execution-iaas",
        "control_plane": "firecracker-microvm",
        "runtime": "python3.11",
        "table_name": os.environ.get("TABLE_NAME", "EphemeralSandboxTable"),
        "redis_host": os.environ.get("REDIS_HOST", "redis"),
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body),
    }
