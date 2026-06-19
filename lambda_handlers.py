"""
lambda_handlers.py
------------------
AWS Lambda handler code for all five specialist agents.

Deploy this file (or individual functions) as Lambda functions.
Each function is identified by its function name convention:
    bedrock-s3-agent, bedrock-iam-agent, etc.

You can deploy ONE dispatcher Lambda (set AGENT_KEY env var per function)
or deploy five separate functions — both patterns work.

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

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")


# ── Parameter helpers ─────────────────────────────────────────────────────────

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


def err(msg: str) -> dict:
    return ok({"status": "error", "message": msg})


# We store event context here so ok() / err() can access it
event_ref: dict = {}


# ── S3 handler ────────────────────────────────────────────────────────────────

def handle_s3(function_name: str, params: dict) -> dict:
    s3 = boto3.client("s3", region_name=AWS_REGION)

    if function_name == "create_bucket":
        bucket = params["bucket_name"]
        region = params.get("region", AWS_REGION)
        try:
            if region == "us-east-1":
                s3.create_bucket(Bucket=bucket)
            else:
                s3.create_bucket(
                    Bucket=bucket,
                    CreateBucketConfiguration={"LocationConstraint": region},
                )
            # Block public access
            s3.put_public_access_block(
                Bucket=bucket,
                PublicAccessBlockConfiguration={
                    "BlockPublicAcls": True,
                    "IgnorePublicAcls": True,
                    "BlockPublicPolicy": True,
                    "RestrictPublicBuckets": True,
                },
            )
            # Versioning
            if params.get("versioning", "true").lower() != "false":
                s3.put_bucket_versioning(
                    Bucket=bucket,
                    VersioningConfiguration={"Status": "Enabled"},
                )
            # Default lifecycle
            expiry = int(params.get("lifecycle_days", 90))
            if expiry > 0:
                s3.put_bucket_lifecycle_configuration(
                    Bucket=bucket,
                    LifecycleConfiguration={
                        "Rules": [{
                            "ID": "auto-expiry",
                            "Status": "Enabled",
                            "Filter": {"Prefix": ""},
                            "Expiration": {"Days": expiry},
                        }]
                    },
                )
            return ok({"status": "created", "bucket": bucket, "region": region, "versioning": True})
        except ClientError as e:
            return err(str(e))

    elif function_name == "set_lifecycle_policy":
        bucket = params["bucket_name"]
        expiry = int(params["expiry_days"])
        rules = [{"ID": "expiry", "Status": "Enabled", "Filter": {"Prefix": ""},
                  "Expiration": {"Days": expiry}}]
        if "transition_days" in params:
            rules[0]["Transitions"] = [{"Days": int(params["transition_days"]),
                                         "StorageClass": "STANDARD_IA"}]
        try:
            s3.put_bucket_lifecycle_configuration(Bucket=bucket,
                                                  LifecycleConfiguration={"Rules": rules})
            return ok({"status": "updated", "bucket": bucket, "expiry_days": expiry})
        except ClientError as e:
            return err(str(e))

    elif function_name == "list_buckets":
        resp = s3.list_buckets()
        buckets = [{"name": b["Name"], "created": b["CreationDate"]} for b in resp["Buckets"]]
        return ok({"bucket_count": len(buckets), "buckets": buckets})

    elif function_name == "delete_bucket":
        if params.get("confirmed", "false").lower() != "true":
            return err("Deletion requires confirmed=true")
        bucket = params["bucket_name"]
        try:
            # Empty bucket first
            paginator = s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket):
                for obj in page.get("Contents", []):
                    s3.delete_object(Bucket=bucket, Key=obj["Key"])
            s3.delete_bucket(Bucket=bucket)
            return ok({"status": "deleted", "bucket": bucket})
        except ClientError as e:
            return err(str(e))

    return err(f"Unknown S3 function: {function_name}")


# ── IAM handler ───────────────────────────────────────────────────────────────

def handle_iam(function_name: str, params: dict) -> dict:
    iam = boto3.client("iam", region_name=AWS_REGION)

    if function_name == "create_role":
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
            return ok({"status": "created", "role_arn": resp["Role"]["Arn"],
                       "role_name": role_name})
        except ClientError as e:
            return err(str(e))

    elif function_name == "attach_policy":
        try:
            iam.attach_role_policy(RoleName=params["role_name"],
                                   PolicyArn=params["policy_arn"])
            return ok({"status": "attached", "role": params["role_name"],
                       "policy": params["policy_arn"]})
        except ClientError as e:
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
            return ok({"status": "created", "role": params["role_name"],
                       "policy_name": params["policy_name"]})
        except ClientError as e:
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
            resp = iam.create_role(RoleName=role_name,
                                   AssumeRolePolicyDocument=json.dumps(trust),
                                   Description="VPC Flow Logs delivery role")
            role_arn = resp["Role"]["Arn"]
            iam.put_role_policy(RoleName=role_name, PolicyName="FlowLogsPolicy",
                                PolicyDocument=json.dumps(policy))
            return ok({"status": "granted", "role_arn": role_arn,
                       "role_name": role_name,
                       "message": "VPC Flow Logs role ready"})
        except ClientError as e:
            if "EntityAlreadyExists" in str(e):
                resp = iam.get_role(RoleName=role_name)
                return ok({"status": "granted", "role_arn": resp["Role"]["Arn"],
                           "role_name": role_name, "message": "Role already existed"})
            return err(str(e))

    elif function_name == "list_roles":
        prefix = params.get("prefix", "")
        resp = iam.list_roles(PathPrefix="/")
        roles = [{"name": r["RoleName"], "arn": r["Arn"]}
                 for r in resp["Roles"] if r["RoleName"].startswith(prefix)]
        return ok({"role_count": len(roles), "roles": roles})

    return err(f"Unknown IAM function: {function_name}")


# ── Observability handler ─────────────────────────────────────────────────────

def handle_observability(function_name: str, params: dict) -> dict:
    cw = boto3.client("cloudwatch", region_name=AWS_REGION)
    logs = boto3.client("logs", region_name=AWS_REGION)
    ct = boto3.client("cloudtrail", region_name=AWS_REGION)

    if function_name == "create_alarm":
        try:
            kwargs = {
                "AlarmName":          params["alarm_name"],
                "MetricName":         params["metric"],
                "Namespace":          params["namespace"],
                "Threshold":          float(params["threshold"]),
                "ComparisonOperator": "GreaterThanThreshold",
                "EvaluationPeriods":  2,
                "Period":             300,
                "Statistic":          "Average",
                "TreatMissingData":   "notBreaching",
            }
            if "dimension_name" in params:
                kwargs["Dimensions"] = [{"Name": params["dimension_name"],
                                          "Value": params["dimension_value"]}]
            if "sns_topic_arn" in params:
                kwargs["AlarmActions"] = [params["sns_topic_arn"]]
            cw.put_metric_alarm(**kwargs)
            return ok({"status": "created", "alarm": params["alarm_name"]})
        except ClientError as e:
            return err(str(e))

    elif function_name == "create_log_group":
        group = params["log_group_name"]
        retention = int(params.get("retention_days", 90))
        try:
            logs.create_log_group(logGroupName=group)
            logs.put_retention_policy(logGroupName=group, retentionInDays=retention)
            return ok({"status": "created", "log_group": group, "retention_days": retention})
        except logs.exceptions.ResourceAlreadyExistsException:
            return ok({"status": "exists", "log_group": group})
        except ClientError as e:
            return err(str(e))

    elif function_name == "enable_cloudtrail":
        try:
            resp = ct.create_trail(Name=params["trail_name"],
                                   S3BucketName=params["s3_bucket"],
                                   IsMultiRegionTrail=True,
                                   IncludeGlobalServiceEvents=True)
            ct.start_logging(Name=resp["TrailARN"])
            return ok({"status": "enabled", "trail_arn": resp["TrailARN"]})
        except ClientError as e:
            return err(str(e))

    elif function_name == "create_dashboard":
        widgets = []
        for res_id in params["resource_ids"].split(","):
            res_id = res_id.strip()
            widgets.append({
                "type": "metric",
                "properties": {
                    "metrics": [["AWS/EC2", "CPUUtilization", "InstanceId", res_id]],
                    "title": f"CPU - {res_id}", "period": 300,
                },
            })
        try:
            cw.put_dashboard(DashboardName=params["dashboard_name"],
                             DashboardBody=json.dumps({"widgets": widgets}))
            return ok({"status": "created", "dashboard": params["dashboard_name"]})
        except ClientError as e:
            return err(str(e))

    return err(f"Unknown Observability function: {function_name}")


# ── Compute handler ───────────────────────────────────────────────────────────

def handle_compute(function_name: str, params: dict) -> dict:
    ec2 = boto3.client("ec2", region_name=AWS_REGION)

    if function_name == "launch_ec2":
        ami = params.get("ami_id")
        if ami == "latest-al2":
            resp = ec2.describe_images(
                Filters=[{"Name": "name", "Values": ["amzn2-ami-hvm-*-x86_64-gp2"]},
                         {"Name": "owner-alias", "Values": ["amazon"]}],
                Owners=["amazon"],
            )
            ami = sorted(resp["Images"], key=lambda x: x["CreationDate"], reverse=True)[0]["ImageId"]

        kwargs = {
            "ImageId":      ami,
            "InstanceType": params["instance_type"],
            "SubnetId":     params["subnet_id"],
            "SecurityGroupIds": [params["security_group_id"]],
            "MinCount": 1, "MaxCount": 1,
            "Monitoring": {"Enabled": True},
        }
        if "key_name" in params:
            kwargs["KeyName"] = params["key_name"]
        if "instance_profile" in params:
            kwargs["IamInstanceProfile"] = {"Arn": params["instance_profile"]}
        if "tags" in params:
            tag_specs = [{"ResourceType": "instance",
                          "Tags": [{"Key": k, "Value": v}
                                   for k, v in json.loads(params["tags"]).items()]}]
            kwargs["TagSpecifications"] = tag_specs

        try:
            resp = ec2.run_instances(**kwargs)
            instance = resp["Instances"][0]
            return ok({
                "status": "launched",
                "instance_id": instance["InstanceId"],
                "state": instance["State"]["Name"],
                "private_ip": instance.get("PrivateIpAddress"),
                "ami": ami,
            })
        except ClientError as e:
            return err(str(e))

    elif function_name == "describe_instances":
        filters = [{"Name": "instance-state-name", "Values": ["running", "pending"]}]
        if "tag_key" in params:
            filters.append({"Name": f"tag:{params['tag_key']}", "Values": [params["tag_value"]]})
        resp = ec2.describe_instances(Filters=filters)
        instances = []
        for r in resp["Reservations"]:
            for i in r["Instances"]:
                instances.append({
                    "id": i["InstanceId"], "type": i["InstanceType"],
                    "state": i["State"]["Name"],
                    "private_ip": i.get("PrivateIpAddress"),
                })
        return ok({"instance_count": len(instances), "instances": instances})

    elif function_name == "stop_instance":
        ids = [i.strip() for i in params["instance_ids"].split(",")]
        try:
            ec2.stop_instances(InstanceIds=ids)
            return ok({"status": "stopping", "instance_ids": ids})
        except ClientError as e:
            return err(str(e))

    return err(f"Unknown Compute function: {function_name}")


# ── VPC handler ───────────────────────────────────────────────────────────────

def handle_vpc(function_name: str, params: dict) -> dict:
    ec2 = boto3.client("ec2", region_name=AWS_REGION)

    if function_name == "create_vpc":
        cidr = params.get("cidr", "10.0.0.0/16")
        name = params["vpc_name"]
        enable_flow_logs = params.get("enable_flow_logs", "true").lower() == "true"
        flow_logs_role_arn = params.get("flow_logs_role_arn")

        if enable_flow_logs and not flow_logs_role_arn:
            # Signal to supervisor that IAM dependency must be resolved first
            return ok({
                "status": "pending",
                "requires_iam": True,
                "action": "create_vpc_flow_logs_role",
                "message": "Cannot enable VPC Flow Logs without an IAM delivery role. "
                           "Please ask the IAM Agent to create_vpc_flow_logs_role first.",
            })

        try:
            vpc = ec2.create_vpc(CidrBlock=cidr)
            vpc_id = vpc["Vpc"]["VpcId"]
            ec2.create_tags(Resources=[vpc_id], Tags=[{"Key": "Name", "Value": name}])
            ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={"Value": True})
            ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsSupport={"Value": True})

            # Internet Gateway
            igw = ec2.create_internet_gateway()
            igw_id = igw["InternetGateway"]["InternetGatewayId"]
            ec2.attach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)

            # Subnets (2 AZs, public + private each)
            azs = ec2.describe_availability_zones(Filters=[{"Name": "state", "Values": ["available"]}])
            az_names = [az["ZoneName"] for az in azs["AvailabilityZones"][:2]]
            subnet_ids = {"public": [], "private": []}
            octets = [10, 0]
            for i, az in enumerate(az_names):
                pub_cidr = f"{octets[0]}.{octets[1]}.{i}.0/24"
                priv_cidr = f"{octets[0]}.{octets[1]}.{i+10}.0/24"
                pub = ec2.create_subnet(VpcId=vpc_id, CidrBlock=pub_cidr, AvailabilityZone=az)
                priv = ec2.create_subnet(VpcId=vpc_id, CidrBlock=priv_cidr, AvailabilityZone=az)
                subnet_ids["public"].append(pub["Subnet"]["SubnetId"])
                subnet_ids["private"].append(priv["Subnet"]["SubnetId"])

            result = {
                "status": "created",
                "vpc_id": vpc_id,
                "cidr": cidr,
                "igw_id": igw_id,
                "subnets": subnet_ids,
            }

            # Flow Logs (if role provided)
            if enable_flow_logs and flow_logs_role_arn:
                ec2.create_flow_logs(
                    ResourceIds=[vpc_id],
                    ResourceType="VPC",
                    TrafficType="ALL",
                    LogDestinationType="cloud-watch-logs",
                    LogGroupName=f"/aws/vpc/flowlogs/{name}",
                    DeliverLogsPermissionArn=flow_logs_role_arn,
                )
                result["flow_logs"] = "enabled"

            return ok(result)
        except ClientError as e:
            return err(str(e))

    elif function_name == "create_security_group":
        try:
            resp = ec2.create_security_group(
                GroupName=params["group_name"],
                Description=params["description"],
                VpcId=params["vpc_id"],
            )
            sg_id = resp["GroupId"]
            if "ingress_rules" in params:
                rules = json.loads(params["ingress_rules"])
                perms = [{"IpProtocol": r.get("protocol", "tcp"),
                          "FromPort": int(r["port"]), "ToPort": int(r["port"]),
                          "IpRanges": [{"CidrIp": r.get("cidr", "0.0.0.0/0")}]}
                         for r in rules]
                ec2.authorize_security_group_ingress(GroupId=sg_id, IpPermissions=perms)
            return ok({"status": "created", "security_group_id": sg_id,
                       "name": params["group_name"]})
        except ClientError as e:
            return err(str(e))

    elif function_name == "create_nat_gateway":
        try:
            eip = ec2.allocate_address(Domain="vpc")
            nat = ec2.create_nat_gateway(SubnetId=params["subnet_id"],
                                          AllocationId=eip["AllocationId"])
            return ok({"status": "creating",
                       "nat_gateway_id": nat["NatGateway"]["NatGatewayId"],
                       "eip": eip["PublicIp"]})
        except ClientError as e:
            return err(str(e))

    elif function_name == "describe_vpcs":
        filters = []
        if "vpc_id" in params:
            filters = [{"Name": "vpc-id", "Values": [params["vpc_id"]]}]
        resp = ec2.describe_vpcs(Filters=filters)
        vpcs = [{"vpc_id": v["VpcId"], "cidr": v["CidrBlock"],
                 "state": v["State"],
                 "name": next((t["Value"] for t in v.get("Tags", []) if t["Key"] == "Name"), "-")}
                for v in resp["Vpcs"]]
        return ok({"vpc_count": len(vpcs), "vpcs": vpcs})

    return err(f"Unknown VPC function: {function_name}")


# ── Lambda entry point ────────────────────────────────────────────────────────

HANDLER_MAP = {
    "s3":            handle_s3,
    "iam":           handle_iam,
    "observability": handle_observability,
    "compute":       handle_compute,
    "vpc":           handle_vpc,
}


def lambda_handler(event: dict, context) -> dict:
    """
    Dispatch incoming Bedrock action-group invocations to the correct handler.
    Set the AGENT_KEY environment variable in each Lambda to one of:
        s3 | iam | observability | compute | vpc
    """
    global event_ref
    event_ref = event

    log.info("Event: %s", json.dumps(event, default=str))

    agent_key = os.environ.get("AGENT_KEY", "")
    function_name = event.get("function", "")
    params = get_params(event)

    handler = HANDLER_MAP.get(agent_key)
    if not handler:
        return err(f"No handler registered for AGENT_KEY={agent_key!r}")

    return handler(function_name, params)

