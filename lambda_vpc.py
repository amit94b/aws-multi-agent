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
# VPC Functions
# ------------------------------------------------------------------

def handle_vpc(function_name, params):

    ec2 = boto3.client("ec2", region_name=AWS_REGION)

    if function_name == "describe_vpcs":

        filters = []
        if "vpc_id" in params:
            filters = [{"Name": "vpc-id", "Values": [params["vpc_id"]]}]
        try:
            resp = ec2.describe_vpcs(Filters=filters)
            vpcs = []
            for v in resp.get("Vpcs", []):
                name = next((t["Value"] for t in v.get("Tags", []) if t["Key"] == "Name"), "-")
                vpcs.append({
                    "vpc_id": v["VpcId"],
                    "cidr": v["CidrBlock"],
                    "state": v["State"],
                    "name": name,
                })
            return ok({"status": "success", "vpc_count": len(vpcs), "vpcs": vpcs})
        except Exception as e:
            return err(str(e))

    elif function_name == "create_vpc":

        cidr = params.get("cidr", "10.0.0.0/16")
        name = params["vpc_name"]
        enable_flow_logs = str(params.get("enable_flow_logs", "false")).lower() == "true"
        flow_logs_role_arn = params.get("flow_logs_role_arn")

        # Dependency signal: Flow Logs need an IAM role first
        if enable_flow_logs and not flow_logs_role_arn:
            return ok({
                "status": "pending",
                "requires_iam": True,
                "action": "create_vpc_flow_logs_role",
                "message": "Cannot enable VPC Flow Logs without an IAM delivery role. "
                           "Ask the IAM Agent to create_vpc_flow_logs_role first.",
            })

        try:
            vpc = ec2.create_vpc(CidrBlock=cidr)
            vpc_id = vpc["Vpc"]["VpcId"]
            ec2.create_tags(Resources=[vpc_id], Tags=[{"Key": "Name", "Value": name}])
            ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={"Value": True})

            igw = ec2.create_internet_gateway()
            igw_id = igw["InternetGateway"]["InternetGatewayId"]
            ec2.attach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)

            azs = ec2.describe_availability_zones(
                Filters=[{"Name": "state", "Values": ["available"]}]
            )
            az_names = [az["ZoneName"] for az in azs["AvailabilityZones"][:2]]
            subnets = {"public": [], "private": []}
            for i, az in enumerate(az_names):
                pub = ec2.create_subnet(VpcId=vpc_id, CidrBlock=f"10.0.{i}.0/24", AvailabilityZone=az)
                priv = ec2.create_subnet(VpcId=vpc_id, CidrBlock=f"10.0.{i+10}.0/24", AvailabilityZone=az)
                subnets["public"].append(pub["Subnet"]["SubnetId"])
                subnets["private"].append(priv["Subnet"]["SubnetId"])

            result = {
                "status": "created",
                "vpc_id": vpc_id,
                "cidr": cidr,
                "igw_id": igw_id,
                "subnets": subnets,
            }

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
        except Exception as e:
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
                perms = [{
                    "IpProtocol": r.get("protocol", "tcp"),
                    "FromPort": int(r["port"]),
                    "ToPort": int(r["port"]),
                    "IpRanges": [{"CidrIp": r.get("cidr", "0.0.0.0/0")}],
                } for r in rules]
                ec2.authorize_security_group_ingress(GroupId=sg_id, IpPermissions=perms)
            return ok({"status": "created", "security_group_id": sg_id, "name": params["group_name"]})
        except Exception as e:
            return err(str(e))

    elif function_name == "create_nat_gateway":

        try:
            eip = ec2.allocate_address(Domain="vpc")
            nat = ec2.create_nat_gateway(
                SubnetId=params["subnet_id"],
                AllocationId=eip["AllocationId"],
            )
            return ok({
                "status": "creating",
                "nat_gateway_id": nat["NatGateway"]["NatGatewayId"],
                "eip": eip["PublicIp"],
            })
        except Exception as e:
            return err(str(e))

    return err(f"Unknown VPC function: {function_name}")

# ------------------------------------------------------------------
# Handler Map
# ------------------------------------------------------------------

HANDLER_MAP = {"vpc": handle_vpc}

# ------------------------------------------------------------------
# Lambda Entry Point
# ------------------------------------------------------------------

def lambda_handler(event, context):

    global event_ref
    event_ref = event

    log.info(json.dumps(event))

    agent_key = os.environ.get("AGENT_KEY", "vpc")
    handler = HANDLER_MAP.get(agent_key)

    if not handler:
        return err(f"No handler registered for AGENT_KEY={agent_key}")

    function_name = event["function"]
    params = get_params(event)

    return handler(function_name, params)
