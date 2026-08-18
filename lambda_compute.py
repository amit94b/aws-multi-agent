"""
lambda_compute.py
-----------------
AWS Lambda handler for the Compute Agent.

Deploy as: bedrock-compute-agent  (AGENT_KEY=compute)

Event format (from Bedrock action groups):
    {
      "actionGroup": "COMPUTE-Actions",
      "function":    "launch_ec2",
      "parameters":  [{"name": "instance_type", "value": "t3.medium"}, ...]
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


# ── Compute Functions ─────────────────────────────────────────────────────────

def handle_compute(function_name: str, params: dict) -> dict:
    ec2 = boto3.client("ec2", region_name=AWS_REGION)

    if function_name == "describe_instances":
        filters = [{"Name": "instance-state-name", "Values": ["running", "pending"]}]
        if "tag_key" in params and "tag_value" in params:
            filters.append({"Name": f"tag:{params['tag_key']}", "Values": [params["tag_value"]]})
        try:
            resp = ec2.describe_instances(Filters=filters)
            instances = []
            for r in resp.get("Reservations", []):
                for i in r.get("Instances", []):
                    instances.append({
                        "id":         i["InstanceId"],
                        "type":       i["InstanceType"],
                        "state":      i["State"]["Name"],
                        "private_ip": i.get("PrivateIpAddress"),
                        "az":         i.get("Placement", {}).get("AvailabilityZone"),
                        "tags":       {t["Key"]: t["Value"] for t in i.get("Tags", [])},
                    })
            return ok({"status": "success", "instance_count": len(instances), "instances": instances})
        except ClientError as e:
            return err(str(e))

    elif function_name == "launch_ec2":
        ami = params.get("ami_id")
        try:
            if ami == "latest-al2":
                imgs = ec2.describe_images(
                    Filters=[{"Name": "name", "Values": ["amzn2-ami-hvm-*-x86_64-gp2"]}],
                    Owners=["amazon"],
                )
                if not imgs["Images"]:
                    return err("No Amazon Linux 2 AMIs found in this region")
                ami = sorted(imgs["Images"], key=lambda x: x["CreationDate"], reverse=True)[0]["ImageId"]

            kwargs: dict = {
                "ImageId":          ami,
                "InstanceType":     params["instance_type"],
                "SubnetId":         params["subnet_id"],
                "SecurityGroupIds": [params["security_group_id"]],
                "MinCount": 1,
                "MaxCount": 1,
                "Monitoring": {"Enabled": True},
            }
            if "key_name" in params:
                kwargs["KeyName"] = params["key_name"]
            if "instance_profile" in params:
                profile = params["instance_profile"]
                # Accept either ARN or name
                if profile.startswith("arn:"):
                    kwargs["IamInstanceProfile"] = {"Arn": profile}
                else:
                    kwargs["IamInstanceProfile"] = {"Name": profile}
            if "tags" in params:
                tag_dict = json.loads(params["tags"]) if isinstance(params["tags"], str) else params["tags"]
                kwargs["TagSpecifications"] = [{
                    "ResourceType": "instance",
                    "Tags": [{"Key": k, "Value": v} for k, v in tag_dict.items()],
                }]

            resp = ec2.run_instances(**kwargs)
            inst = resp["Instances"][0]
            return ok({
                "status":      "launched",
                "instance_id": inst["InstanceId"],
                "state":       inst["State"]["Name"],
                "private_ip":  inst.get("PrivateIpAddress"),
                "ami":         ami,
                "type":        params["instance_type"],
            })
        except ClientError as e:
            return err(str(e))

    elif function_name == "stop_instance":
        ids = [i.strip() for i in params["instance_ids"].split(",")]
        try:
            ec2.stop_instances(InstanceIds=ids)
            return ok({"status": "stopping", "instance_ids": ids})
        except ClientError as e:
            return err(str(e))

    elif function_name == "create_asg":
        asg_client = boto3.client("autoscaling", region_name=AWS_REGION)
        try:
            lt_name = params["asg_name"] + "-lt"
            ec2.create_launch_template(
                LaunchTemplateName=lt_name,
                LaunchTemplateData={
                    "ImageId":      params["ami_id"],
                    "InstanceType": params["instance_type"],
                    "Monitoring":   {"Enabled": True},
                },
            )
            # Bedrock passes all values as strings — always cast to int
            min_size = int(params["min_size"])
            max_size = int(params["max_size"])
            desired  = int(params.get("desired", min_size))
            asg_client.create_auto_scaling_group(
                AutoScalingGroupName=params["asg_name"],
                LaunchTemplate={"LaunchTemplateName": lt_name, "Version": "$Latest"},
                MinSize=min_size,
                MaxSize=max_size,
                DesiredCapacity=desired,
                VPCZoneIdentifier=params["subnet_ids"],
            )
            return ok({
                "status":   "created",
                "asg":      params["asg_name"],
                "min":      min_size,
                "max":      max_size,
                "desired":  desired,
            })
        except ClientError as e:
            return err(str(e))

    return err(f"Unknown Compute function: {function_name}")


# ── Handler Map ───────────────────────────────────────────────────────────────

HANDLER_MAP = {"compute": handle_compute}


# ── Lambda Entry Point ────────────────────────────────────────────────────────

def lambda_handler(event: dict, context) -> dict:
    """
    Dispatch incoming Bedrock action-group invocations to the Compute handler.
    Env var: AGENT_KEY=compute
    """
    global event_ref
    event_ref = event

    log.info("Event: %s", json.dumps(event, default=str))

    agent_key = os.environ.get("AGENT_KEY", "compute")
    handler = HANDLER_MAP.get(agent_key)

    if not handler:
        return err(f"No handler registered for AGENT_KEY={agent_key!r}")

    function_name = event.get("function", "")
    params = get_params(event)

    return handler(function_name, params)
