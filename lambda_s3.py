"""
lambda_s3.py
-------------
AWS Lambda handler for the S3 Storage Agent.

Deploy as: bedrock-s3-agent  (AGENT_KEY=s3)

Event format (from Bedrock action groups):
    {
      "actionGroup": "S3-Actions",
      "function":    "create_bucket",
      "parameters":  [{"name": "bucket_name", "value": "my-bucket"}, ...]
    }
"""

import json
import logging
import os
import boto3
from botocore.exceptions import ClientError

log = logging.getLogger()
log.setLevel(logging.INFO)

AWS_REGION = os.environ.get("AWS_REGION", "eu-west-1")

event_ref: dict = {}


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_params(event: dict) -> dict:
    """Convert Bedrock's parameter list into a plain dict."""
    return {p["name"]: p["value"] for p in event.get("parameters", [])}


def ok(body: dict) -> dict:
    """Wrap a success response for Bedrock."""
    return {
        "actionGroup": event_ref["actionGroup"],
        "function":    event_ref["function"],
        "functionResponse": {
            "responseBody": {
                "TEXT": {"body": json.dumps(body, default=str)}
            }
        },
    }


def err(message: str) -> dict:
    return ok({"status": "error", "message": message})


# ── S3 Functions ──────────────────────────────────────────────────────────────

def handle_s3(function_name: str, params: dict) -> dict:
    s3 = boto3.client("s3", region_name=AWS_REGION)

    if function_name == "list_buckets":
        try:
            resp = s3.list_buckets()
            buckets = [
                {"name": b["Name"], "created": str(b["CreationDate"])}
                for b in resp.get("Buckets", [])
            ]
            return ok({"status": "success", "bucket_count": len(buckets), "buckets": buckets})
        except Exception as e:
            return err(str(e))

    elif function_name == "create_bucket":
        bucket_name = params["bucket_name"]
        region = params.get("region", AWS_REGION)
        try:
            # AWS S3 quirk: us-east-1 must NOT pass LocationConstraint
            if region == "us-east-1":
                s3.create_bucket(Bucket=bucket_name)
            else:
                s3.create_bucket(
                    Bucket=bucket_name,
                    CreateBucketConfiguration={"LocationConstraint": region},
                )
            # Block all public access by default
            s3.put_public_access_block(
                Bucket=bucket_name,
                PublicAccessBlockConfiguration={
                    "BlockPublicAcls":      True,
                    "IgnorePublicAcls":     True,
                    "BlockPublicPolicy":    True,
                    "RestrictPublicBuckets": True,
                },
            )
            # Enable versioning by default
            versioning_param = params.get("versioning", True)
            versioning_enabled = (
                versioning_param
                if isinstance(versioning_param, bool)
                else str(versioning_param).lower() != "false"
            )
            if versioning_enabled:
                s3.put_bucket_versioning(
                    Bucket=bucket_name,
                    VersioningConfiguration={"Status": "Enabled"},
                )
            # Default lifecycle expiry
            expiry = int(params.get("lifecycle_days", 90))
            if expiry > 0:
                s3.put_bucket_lifecycle_configuration(
                    Bucket=bucket_name,
                    LifecycleConfiguration={
                        "Rules": [{
                            "ID": "auto-expiry",
                            "Status": "Enabled",
                            "Filter": {"Prefix": ""},
                            "Expiration": {"Days": expiry},
                        }]
                    },
                )
            return ok({
                "status": "created",
                "bucket": bucket_name,
                "region": region,
                "versioning": versioning_enabled,
                "lifecycle_days": expiry,
            })
        except ClientError as e:
            return err(str(e))

    elif function_name == "set_lifecycle_policy":
        bucket = params["bucket_name"]
        expiry = int(params["expiry_days"])
        rules = [{
            "ID": "expiry",
            "Status": "Enabled",
            "Filter": {"Prefix": ""},
            "Expiration": {"Days": expiry},
        }]
        if "transition_days" in params:
            rules[0]["Transitions"] = [{
                "Days": int(params["transition_days"]),
                "StorageClass": "STANDARD_IA",
            }]
        try:
            s3.put_bucket_lifecycle_configuration(
                Bucket=bucket,
                LifecycleConfiguration={"Rules": rules},
            )
            return ok({"status": "updated", "bucket": bucket, "expiry_days": expiry})
        except ClientError as e:
            return err(str(e))

    elif function_name == "delete_bucket":
        bucket_name = params["bucket_name"]
        # Require explicit confirmation
        confirmed = params.get("confirmed", False)
        if not (confirmed is True or str(confirmed).lower() == "true"):
            return err("Deletion requires confirmed=true. This action is irreversible.")
        try:
            # Empty all objects (including versions) before deleting
            paginator = s3.get_paginator("list_object_versions")
            for page in paginator.paginate(Bucket=bucket_name):
                objects_to_delete = []
                for v in page.get("Versions", []):
                    objects_to_delete.append({"Key": v["Key"], "VersionId": v["VersionId"]})
                for m in page.get("DeleteMarkers", []):
                    objects_to_delete.append({"Key": m["Key"], "VersionId": m["VersionId"]})
                if objects_to_delete:
                    s3.delete_objects(Bucket=bucket_name, Delete={"Objects": objects_to_delete})
            s3.delete_bucket(Bucket=bucket_name)
            return ok({"status": "deleted", "bucket": bucket_name})
        except ClientError as e:
            return err(str(e))

    return err(f"Unknown S3 function: {function_name}")


# ── Handler Map ───────────────────────────────────────────────────────────────

HANDLER_MAP = {"s3": handle_s3}


# ── Lambda Entry Point ────────────────────────────────────────────────────────

def lambda_handler(event: dict, context) -> dict:
    """
    Dispatch incoming Bedrock action-group invocations to the S3 handler.
    Env var: AGENT_KEY=s3
    """
    global event_ref
    event_ref = event

    log.info("Event: %s", json.dumps(event, default=str))

    agent_key = os.environ.get("AGENT_KEY", "s3")
    handler = HANDLER_MAP.get(agent_key)

    if not handler:
        return err(f"No handler registered for AGENT_KEY={agent_key!r}")

    function_name = event.get("function", "")
    params = get_params(event)

    return handler(function_name, params)