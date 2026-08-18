"""
lambda_observability.py
-----------------------
AWS Lambda handler for the Observability Agent.

Deploy as: bedrock-observability-agent  (AGENT_KEY=observability)

Event format (from Bedrock action groups):
    {
      "actionGroup": "OBSERVABILITY-Actions",
      "function":    "create_alarm",
      "parameters":  [{"name": "alarm_name", "value": "high-cpu"}, ...]
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


# ── Observability Functions ───────────────────────────────────────────────────

def handle_observability(function_name: str, params: dict) -> dict:
    cw   = boto3.client("cloudwatch",  region_name=AWS_REGION)
    logs = boto3.client("logs",        region_name=AWS_REGION)
    ct   = boto3.client("cloudtrail",  region_name=AWS_REGION)

    if function_name == "create_alarm":
        try:
            kwargs: dict = {
                "AlarmName":          params["alarm_name"],
                "MetricName":         params["metric"],
                "Namespace":          params["namespace"],
                "Threshold":          float(params["threshold"]),
                "ComparisonOperator": "GreaterThanThreshold",
                "EvaluationPeriods":  2,
                "Period":             300,
                "Statistic":          "Average",
                "TreatMissingData":   "notBreaching",
                "AlarmDescription":   f"Auto-created by Observability Agent — {params['metric']} > {params['threshold']}",
            }
            if "dimension_name" in params and "dimension_value" in params:
                kwargs["Dimensions"] = [{
                    "Name":  params["dimension_name"],
                    "Value": params["dimension_value"],
                }]
            if "sns_topic_arn" in params:
                kwargs["AlarmActions"] = [params["sns_topic_arn"]]
                kwargs["OKActions"]    = [params["sns_topic_arn"]]
            cw.put_metric_alarm(**kwargs)
            return ok({
                "status":    "created",
                "alarm":     params["alarm_name"],
                "metric":    params["metric"],
                "namespace": params["namespace"],
                "threshold": float(params["threshold"]),
            })
        except ClientError as e:
            return err(str(e))

    elif function_name == "create_log_group":
        group     = params["log_group_name"]
        retention = int(params.get("retention_days", 90))
        try:
            logs.create_log_group(logGroupName=group)
            logs.put_retention_policy(logGroupName=group, retentionInDays=retention)
            return ok({
                "status":         "created",
                "log_group":      group,
                "retention_days": retention,
            })
        except ClientError as e:
            if "ResourceAlreadyExistsException" in str(e):
                # Still set retention in case it was never set
                try:
                    logs.put_retention_policy(logGroupName=group, retentionInDays=retention)
                except Exception:
                    pass
                return ok({"status": "exists", "log_group": group, "retention_days": retention})
            return err(str(e))

    elif function_name == "enable_cloudtrail":
        try:
            resp = ct.create_trail(
                Name=params["trail_name"],
                S3BucketName=params["s3_bucket"],
                IsMultiRegionTrail=True,
                IncludeGlobalServiceEvents=True,
                EnableLogFileValidation=True,
            )
            ct.start_logging(Name=resp["TrailARN"])
            return ok({
                "status":    "enabled",
                "trail_arn": resp["TrailARN"],
                "trail_name": params["trail_name"],
                "s3_bucket": params["s3_bucket"],
                "multi_region": True,
            })
        except ClientError as e:
            if "TrailAlreadyExistsException" in str(e):
                # Get the existing trail ARN
                try:
                    trails = ct.describe_trails(trailNameList=[params["trail_name"]])
                    if trails["trailList"]:
                        arn = trails["trailList"][0]["TrailARN"]
                        ct.start_logging(Name=arn)
                        return ok({"status": "already_exists", "trail_arn": arn})
                except Exception:
                    pass
            return err(str(e))

    elif function_name == "create_dashboard":
        widgets = []
        for res_id in params["resource_ids"].split(","):
            res_id = res_id.strip()
            widgets.append({
                "type": "metric",
                "width": 12,
                "height": 6,
                "properties": {
                    "metrics": [
                        ["AWS/EC2", "CPUUtilization",    "InstanceId", res_id],
                        ["AWS/EC2", "NetworkIn",         "InstanceId", res_id],
                        ["AWS/EC2", "NetworkOut",        "InstanceId", res_id],
                    ],
                    "title":  f"Instance {res_id}",
                    "period": 300,
                    "stat":   "Average",
                    "view":   "timeSeries",
                },
            })
        try:
            cw.put_dashboard(
                DashboardName=params["dashboard_name"],
                DashboardBody=json.dumps({"widgets": widgets}),
            )
            return ok({
                "status":    "created",
                "dashboard": params["dashboard_name"],
                "widgets":   len(widgets),
            })
        except ClientError as e:
            return err(str(e))

    return err(f"Unknown Observability function: {function_name}")


# ── Handler Map ───────────────────────────────────────────────────────────────

HANDLER_MAP = {"observability": handle_observability}


# ── Lambda Entry Point ────────────────────────────────────────────────────────

def lambda_handler(event: dict, context) -> dict:
    """
    Dispatch incoming Bedrock action-group invocations to the Observability handler.
    Env var: AGENT_KEY=observability
    """
    global event_ref
    event_ref = event

    log.info("Event: %s", json.dumps(event, default=str))

    agent_key = os.environ.get("AGENT_KEY", "observability")
    handler = HANDLER_MAP.get(agent_key)

    if not handler:
        return err(f"No handler registered for AGENT_KEY={agent_key!r}")

    function_name = event.get("function", "")
    params = get_params(event)

    return handler(function_name, params)
