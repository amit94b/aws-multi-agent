import json
import logging
import os
import boto3

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
    return ok({"status": "error", "message": message})

# ------------------------------------------------------------------
# Observability Functions
# ------------------------------------------------------------------

def handle_observability(function_name, params):

    cw = boto3.client("cloudwatch", region_name=AWS_REGION)
    logs = boto3.client("logs", region_name=AWS_REGION)
    ct = boto3.client("cloudtrail", region_name=AWS_REGION)

    if function_name == "create_alarm":

        try:
            kwargs = {
                "AlarmName": params["alarm_name"],
                "MetricName": params["metric"],
                "Namespace": params["namespace"],
                "Threshold": float(params["threshold"]),
                "ComparisonOperator": "GreaterThanThreshold",
                "EvaluationPeriods": 2,
                "Period": 300,
                "Statistic": "Average",
                "TreatMissingData": "notBreaching",
            }
            if "dimension_name" in params and "dimension_value" in params:
                kwargs["Dimensions"] = [{
                    "Name": params["dimension_name"],
                    "Value": params["dimension_value"],
                }]
            if "sns_topic_arn" in params:
                kwargs["AlarmActions"] = [params["sns_topic_arn"]]
            cw.put_metric_alarm(**kwargs)
            return ok({"status": "created", "alarm": params["alarm_name"]})
        except Exception as e:
            return err(str(e))

    elif function_name == "create_log_group":

        group = params["log_group_name"]
        retention = int(params.get("retention_days", 90))
        try:
            logs.create_log_group(logGroupName=group)
            logs.put_retention_policy(logGroupName=group, retentionInDays=retention)
            return ok({"status": "created", "log_group": group, "retention_days": retention})
        except Exception as e:
            if "ResourceAlreadyExists" in str(e):
                return ok({"status": "exists", "log_group": group})
            return err(str(e))

    elif function_name == "enable_cloudtrail":

        try:
            resp = ct.create_trail(
                Name=params["trail_name"],
                S3BucketName=params["s3_bucket"],
                IsMultiRegionTrail=True,
                IncludeGlobalServiceEvents=True,
            )
            ct.start_logging(Name=resp["TrailARN"])
            return ok({"status": "enabled", "trail_arn": resp["TrailARN"]})
        except Exception as e:
            return err(str(e))

    elif function_name == "create_dashboard":

        widgets = []
        for res_id in params["resource_ids"].split(","):
            res_id = res_id.strip()
            widgets.append({
                "type": "metric",
                "properties": {
                    "metrics": [["AWS/EC2", "CPUUtilization", "InstanceId", res_id]],
                    "title": f"CPU - {res_id}",
                    "period": 300,
                },
            })
        try:
            cw.put_dashboard(
                DashboardName=params["dashboard_name"],
                DashboardBody=json.dumps({"widgets": widgets}),
            )
            return ok({"status": "created", "dashboard": params["dashboard_name"]})
        except Exception as e:
            return err(str(e))

    return err(f"Unknown Observability function: {function_name}")

# ------------------------------------------------------------------
# Handler Map
# ------------------------------------------------------------------

HANDLER_MAP = {"observability": handle_observability}

# ------------------------------------------------------------------
# Lambda Entry Point
# ------------------------------------------------------------------

def lambda_handler(event, context):

    global event_ref
    event_ref = event

    log.info(json.dumps(event))

    agent_key = os.environ.get("AGENT_KEY", "observability")
    handler = HANDLER_MAP.get(agent_key)

    if not handler:
        return err(f"No handler registered for AGENT_KEY={agent_key}")

    function_name = event["function"]
    params = get_params(event)

    return handler(function_name, params)
