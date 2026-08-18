"""
lambda_vpc.py
-------------
AWS Lambda handler for the VPC / Networking Agent.

Deploy as: bedrock-vpc-agent  (AGENT_KEY=vpc)

Event format (from Bedrock action groups):
    {
      "actionGroup": "VPC-Actions",
      "function":    "create_vpc",
      "parameters":  [{"name": "vpc_name", "value": "prod-vpc"}, ...]
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


# ── VPC Functions ─────────────────────────────────────────────────────────────

def handle_vpc(function_name: str, params: dict) -> dict:
    ec2 = boto3.client("ec2", region_name=AWS_REGION)

    if function_name == "describe_vpcs":
        filters = []
        if "vpc_id" in params:
            filters = [{"Name": "vpc-id", "Values": [params["vpc_id"]]}]
        try:
            resp = ec2.describe_vpcs(Filters=filters)
            vpcs = []
            for v in resp.get("Vpcs", []):
                name = next(
                    (t["Value"] for t in v.get("Tags", []) if t["Key"] == "Name"), "-"
                )
                vpcs.append({
                    "vpc_id": v["VpcId"],
                    "cidr":   v["CidrBlock"],
                    "state":  v["State"],
                    "name":   name,
                    "is_default": v.get("IsDefault", False),
                })
            return ok({"status": "success", "vpc_count": len(vpcs), "vpcs": vpcs})
        except ClientError as e:
            return err(str(e))

    elif function_name == "create_vpc":
        cidr = params.get("cidr", "10.0.0.0/16")
        name = params["vpc_name"]

        # Handle boolean from Bedrock (may be string "true"/"false" or Python bool)
        enable_flow_logs_raw = params.get("enable_flow_logs", False)
        enable_flow_logs = (
            enable_flow_logs_raw
            if isinstance(enable_flow_logs_raw, bool)
            else str(enable_flow_logs_raw).lower() == "true"
        )
        flow_logs_role_arn = params.get("flow_logs_role_arn")

        # Dependency signal: Flow Logs need an IAM role first
        if enable_flow_logs and not flow_logs_role_arn:
            return ok({
                "status":      "pending",
                "requires_iam": True,
                "action":      "create_vpc_flow_logs_role",
                "message": (
                    "Cannot enable VPC Flow Logs without an IAM delivery role. "
                    "Ask the IAM Agent to create_vpc_flow_logs_role first, "
                    "then retry create_vpc with the returned flow_logs_role_arn."
                ),
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
            ec2.create_tags(Resources=[igw_id], Tags=[{"Key": "Name", "Value": f"{name}-igw"}])

            # Subnets (2 AZs, public + private each)
            azs = ec2.describe_availability_zones(
                Filters=[{"Name": "state", "Values": ["available"]}]
            )
            az_names = [az["ZoneName"] for az in azs["AvailabilityZones"][:2]]
            subnets: dict = {"public": [], "private": []}

            for i, az in enumerate(az_names):
                pub_cidr  = f"10.0.{i}.0/24"
                priv_cidr = f"10.0.{i + 10}.0/24"
                pub  = ec2.create_subnet(VpcId=vpc_id, CidrBlock=pub_cidr,  AvailabilityZone=az)
                priv = ec2.create_subnet(VpcId=vpc_id, CidrBlock=priv_cidr, AvailabilityZone=az)
                pub_id  = pub["Subnet"]["SubnetId"]
                priv_id = priv["Subnet"]["SubnetId"]
                # Tag subnets
                ec2.create_tags(Resources=[pub_id],  Tags=[{"Key": "Name", "Value": f"{name}-public-{i+1}"}])
                ec2.create_tags(Resources=[priv_id], Tags=[{"Key": "Name", "Value": f"{name}-private-{i+1}"}])
                # Enable auto-assign public IP on public subnets
                ec2.modify_subnet_attribute(SubnetId=pub_id, MapPublicIpOnLaunch={"Value": True})
                subnets["public"].append(pub_id)
                subnets["private"].append(priv_id)

            # Route table for public subnets
            pub_rt = ec2.create_route_table(VpcId=vpc_id)
            pub_rt_id = pub_rt["RouteTable"]["RouteTableId"]
            ec2.create_route(RouteTableId=pub_rt_id, DestinationCidrBlock="0.0.0.0/0",
                             GatewayId=igw_id)
            ec2.create_tags(Resources=[pub_rt_id], Tags=[{"Key": "Name", "Value": f"{name}-public-rt"}])
            for subnet_id in subnets["public"]:
                ec2.associate_route_table(RouteTableId=pub_rt_id, SubnetId=subnet_id)

            result = {
                "status":  "created",
                "vpc_id":  vpc_id,
                "cidr":    cidr,
                "igw_id":  igw_id,
                "subnets": subnets,
                "public_route_table": pub_rt_id,
            }

            # VPC Flow Logs (only when role is provided)
            if enable_flow_logs and flow_logs_role_arn:
                try:
                    ec2.create_flow_logs(
                        ResourceIds=[vpc_id],
                        ResourceType="VPC",
                        TrafficType="ALL",
                        LogDestinationType="cloud-watch-logs",
                        LogGroupName=f"/aws/vpc/flowlogs/{name}",
                        DeliverLogsPermissionArn=flow_logs_role_arn,
                    )
                    result["flow_logs"] = "enabled"
                    result["flow_logs_log_group"] = f"/aws/vpc/flowlogs/{name}"
                except ClientError as fl_err:
                    # Don't fail the whole VPC creation for flow logs
                    result["flow_logs"] = f"failed: {fl_err}"

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
            ec2.create_tags(Resources=[sg_id], Tags=[{"Key": "Name", "Value": params["group_name"]}])

            if "ingress_rules" in params:
                rules_raw = params["ingress_rules"]
                rules = json.loads(rules_raw) if isinstance(rules_raw, str) else rules_raw
                perms = [{
                    "IpProtocol": r.get("protocol", "tcp"),
                    "FromPort":   int(r["port"]),
                    "ToPort":     int(r.get("to_port", r["port"])),
                    "IpRanges":   [{"CidrIp": r.get("cidr", "0.0.0.0/0"),
                                    "Description": r.get("description", "")}],
                } for r in rules]
                ec2.authorize_security_group_ingress(GroupId=sg_id, IpPermissions=perms)

            return ok({
                "status":            "created",
                "security_group_id": sg_id,
                "name":              params["group_name"],
                "vpc_id":            params["vpc_id"],
            })
        except ClientError as e:
            return err(str(e))

    elif function_name == "create_nat_gateway":
        try:
            eip = ec2.allocate_address(Domain="vpc")
            nat = ec2.create_nat_gateway(
                SubnetId=params["subnet_id"],
                AllocationId=eip["AllocationId"],
            )
            nat_id = nat["NatGateway"]["NatGatewayId"]
            ec2.create_tags(Resources=[nat_id], Tags=[{"Key": "Name", "Value": f"nat-{params['subnet_id']}"}])
            return ok({
                "status":         "creating",
                "nat_gateway_id": nat_id,
                "eip":            eip["PublicIp"],
                "subnet_id":      params["subnet_id"],
                "note": "NAT Gateway takes ~1-2 minutes to become available.",
            })
        except ClientError as e:
            return err(str(e))

    return err(f"Unknown VPC function: {function_name}")


# ── Handler Map ───────────────────────────────────────────────────────────────

HANDLER_MAP = {"vpc": handle_vpc}


# ── Lambda Entry Point ────────────────────────────────────────────────────────

def lambda_handler(event: dict, context) -> dict:
    """
    Dispatch incoming Bedrock action-group invocations to the VPC handler.
    Env var: AGENT_KEY=vpc
    """
    global event_ref
    event_ref = event

    log.info("Event: %s", json.dumps(event, default=str))

    agent_key = os.environ.get("AGENT_KEY", "vpc")
    handler = HANDLER_MAP.get(agent_key)

    if not handler:
        return err(f"No handler registered for AGENT_KEY={agent_key!r}")

    function_name = event.get("function", "")
    params = get_params(event)

    return handler(function_name, params)
