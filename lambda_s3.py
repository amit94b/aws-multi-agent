import json
import logging
import os
import boto3
from botocore.exceptions import ClientError

log = logging.getLogger()
log.setLevel(logging.INFO)

AWS_REGION = os.environ.get("AWS_REGION", "eu-west-1")

event_ref = {}

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def get_params(event):
    return {p["name"]: p["value"] for p in event.get("parameters", [])}

def ok(body):
    return {
        "actionGroup": event_ref["actionGroup"],
        "function": event_ref["function"],
        "functionResponse": {
            "responseBody": {
                "TEXT": {
                    "body": json.dumps(body, default=str)
                }
            }
        }
    }

def err(message):
    return ok({
        "status": "error",
        "message": message
    })

# ------------------------------------------------------------------
# S3 Functions
# ------------------------------------------------------------------

def handle_s3(function_name, params):

    s3 = boto3.client("s3")

    if function_name == "list_buckets":

        try:
            resp = s3.list_buckets()

            buckets = []

            for b in resp.get("Buckets", []):
                buckets.append({
                    "name": b["Name"],
                    "created": str(b["CreationDate"])
                })

            return ok({
                "status": "success",
                "bucket_count": len(buckets),
                "buckets": buckets
            })

        except Exception as e:
            return err(str(e))

    elif function_name == "create_bucket":

        bucket_name = params["bucket_name"]

        try:

            s3.create_bucket(
                Bucket=bucket_name,
                CreateBucketConfiguration={
                    "LocationConstraint": AWS_REGION
                }
            )

            return ok({
                "status": "created",
                "bucket": bucket_name
            })

        except Exception as e:
            return err(str(e))

    elif function_name == "delete_bucket":

        bucket_name = params["bucket_name"]

        try:

            s3.delete_bucket(
                Bucket=bucket_name
            )

            return ok({
                "status": "deleted",
                "bucket": bucket_name
            })

        except Exception as e:
            return err(str(e))

    return err(f"Unknown S3 function: {function_name}")

# ------------------------------------------------------------------
# Handler Map
# ------------------------------------------------------------------

HANDLER_MAP = {
    "s3": handle_s3
}

# ------------------------------------------------------------------
# Lambda Entry Point
# ------------------------------------------------------------------

def lambda_handler(event, context):

    global event_ref
    event_ref = event

    log.info(json.dumps(event))

    agent_key = os.environ.get("AGENT_KEY", "s3")

    handler = HANDLER_MAP.get(agent_key)

    if not handler:
        return err(
            f"No handler registered for AGENT_KEY={agent_key}"
        )

    function_name = event["function"]

    params = get_params(event)

    return handler(function_name, params)