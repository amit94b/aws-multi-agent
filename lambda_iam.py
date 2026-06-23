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
# IAM Functions
# ------------------------------------------------------------------

def handle_iam(function_name, params):

    iam = boto3.client("iam")

    if function_name == "list_roles":

        prefix = params.get("prefix", "")
        try:
            resp = iam.list_roles()
            roles = [
                {"name": r["RoleName"], "arn": r["Arn"]}
                for r in resp.get("Roles", [])
                if r["RoleName"].startswith(prefix)
            ]
            return ok({"status": "success", "role_count": len(roles), "roles": roles})
        except Exception as e:
            return err(str(e))

    elif function_name == "create_role":

        role_name = params["role_name"]
        service = params["trusted_service"]
        trust = {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": service},
                "Action": "sts:AssumeRole",
            }],
        }
        try:
            resp = iam.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(trust),
                Description=params.get("description", f"Role for {service}"),
            )
            return ok({"status": "created", "role_arn": resp["Role"]["Arn"], "role_name": role_name})
        except Exception as e:
            return err(str(e))

    elif function_name == "attach_policy":

        try:
            iam.attach_role_policy(
                RoleName=params["role_name"],
                PolicyArn=params["policy_arn"],
            )
            return ok({"status": "attached", "role": params["role_name"], "policy": params["policy_arn"]})
        except Exception as e:
            return err(str(e))

    elif function_name == "create_inline_policy":

        actions = [a.strip() for a in params["actions"].split(",")]
        resources = [r.strip() for r in params["resources"].split(",")]
        policy_doc = {
            "Version": "2012-10-17",
            "Statement": [{"Effect": "Allow", "Action": actions, "Resource": resources}],
        }
        try:
            iam.put_role_policy(
                RoleName=params["role_name"],
                PolicyName=params["policy_name"],
                PolicyDocument=json.dumps(policy_doc),
            )
            return ok({"status": "created", "role": params["role_name"], "policy_name": params["policy_name"]})
        except Exception as e:
            return err(str(e))

    elif function_name == "create_vpc_flow_logs_role":

        role_name = params.get("role_name", "VPCFlowLogsDeliveryRole")
        trust = {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "vpc-flow-logs.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }],
        }
        policy = {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Action": ["logs:CreateLogGroup", "logs:CreateLogStream",
                           "logs:PutLogEvents", "logs:DescribeLogGroups",
                           "logs:DescribeLogStreams"],
                "Resource": "*",
            }],
        }
        try:
            resp = iam.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(trust),
                Description="VPC Flow Logs delivery role",
            )
            role_arn = resp["Role"]["Arn"]
            iam.put_role_policy(
                RoleName=role_name,
                PolicyName="FlowLogsPolicy",
                PolicyDocument=json.dumps(policy),
            )
            return ok({"status": "granted", "role_arn": role_arn, "role_name": role_name})
        except Exception as e:
            if "EntityAlreadyExists" in str(e):
                resp = iam.get_role(RoleName=role_name)
                return ok({"status": "granted", "role_arn": resp["Role"]["Arn"], "role_name": role_name})
            return err(str(e))

    return err(f"Unknown IAM function: {function_name}")

# ------------------------------------------------------------------
# Handler Map
# ------------------------------------------------------------------

HANDLER_MAP = {"iam": handle_iam}

# ------------------------------------------------------------------
# Lambda Entry Point
# ------------------------------------------------------------------

def lambda_handler(event, context):

    global event_ref
    event_ref = event

    log.info(json.dumps(event))

    agent_key = os.environ.get("AGENT_KEY", "iam")
    handler = HANDLER_MAP.get(agent_key)

    if not handler:
        return err(f"No handler registered for AGENT_KEY={agent_key}")

    function_name = event["function"]
    params = get_params(event)

    return handler(function_name, params)
