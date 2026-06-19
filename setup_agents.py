"""
setup_agents.py
---------------
One-time script to create and wire up all Bedrock agents.

Run:
    python setup_agents.py

Output:
    agent_ids.json  — save this file; it is required by invoke_agent.py

AWS prerequisites:
    1. IAM role with AmazonBedrockFullAccess + AWSLambdaRole (see config.py)
    2. Five Lambda functions deployed (see lambda_handlers.py)
    3. Bedrock model access enabled for BEDROCK_MODEL_ID in your region
"""

from __future__ import annotations

import boto3
import json
import time
import logging
from config import (
    AWS_REGION,
    BEDROCK_AGENT_ROLE_ARN,
    BEDROCK_MODEL_ID,
    LAMBDA_ARNS,
    AGENT_INSTRUCTIONS,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

client = boto3.client("bedrock-agent", region_name=AWS_REGION)
lambda_client = boto3.client("lambda", region_name=AWS_REGION)

IDS_FILE = "agent_ids.json"


# ── Action group schemas ──────────────────────────────────────────────────────

def s3_function_schema() -> list:
    return [
        {
            "name": "create_bucket",
            "description": "Create an S3 bucket with optional versioning and lifecycle policy.",
            "parameters": {
                "bucket_name": {"type": "string", "description": "Globally unique bucket name.", "required": True},
                "region":      {"type": "string", "description": "AWS region for the bucket.", "required": False},
                "versioning":  {"type": "boolean", "description": "Enable object versioning.", "required": False},
                "lifecycle_days": {"type": "integer", "description": "Days before objects expire. 0 = no expiry.", "required": False},
            },
        },
        {
            "name": "set_lifecycle_policy",
            "description": "Apply or update a lifecycle policy on an existing S3 bucket.",
            "parameters": {
                "bucket_name":    {"type": "string", "description": "Target bucket name.", "required": True},
                "expiry_days":    {"type": "integer", "description": "Days before current objects expire.", "required": True},
                "transition_days":{"type": "integer", "description": "Days before moving to S3-IA.", "required": False},
            },
        },
        {
            "name": "list_buckets",
            "description": "List all S3 buckets in the account with size and region.",
            "parameters": {},
        },
        {
            "name": "delete_bucket",
            "description": "Empty and delete an S3 bucket. Requires explicit confirmation.",
            "parameters": {
                "bucket_name": {"type": "string", "description": "Bucket to delete.", "required": True},
                "confirmed":   {"type": "boolean", "description": "Must be true to proceed.", "required": True},
            },
        },
    ]


def iam_function_schema() -> list:
    return [
        {
            "name": "create_role",
            "description": "Create an IAM role with a trust policy for the given AWS service.",
            "parameters": {
                "role_name":        {"type": "string", "description": "Name for the new role.", "required": True},
                "trusted_service":  {"type": "string", "description": "AWS service principal, e.g. ec2.amazonaws.com", "required": True},
                "description":      {"type": "string", "description": "Human-readable purpose of the role.", "required": False},
            },
        },
        {
            "name": "attach_policy",
            "description": "Attach an AWS managed or customer-managed policy to a role.",
            "parameters": {
                "role_name":  {"type": "string", "description": "Target role name.", "required": True},
                "policy_arn": {"type": "string", "description": "Full ARN of the policy to attach.", "required": True},
            },
        },
        {
            "name": "create_inline_policy",
            "description": "Create and attach a least-privilege inline policy to a role.",
            "parameters": {
                "role_name":    {"type": "string", "description": "Target role name.", "required": True},
                "policy_name":  {"type": "string", "description": "Name for the inline policy.", "required": True},
                "actions":      {"type": "string", "description": "Comma-separated IAM actions, e.g. s3:PutObject,s3:GetObject", "required": True},
                "resources":    {"type": "string", "description": "Comma-separated resource ARNs. Use * only if necessary.", "required": True},
            },
        },
        {
            "name": "create_vpc_flow_logs_role",
            "description": "Create the IAM role required for VPC Flow Logs delivery to CloudWatch Logs.",
            "parameters": {
                "role_name": {"type": "string", "description": "Name for the flow logs role.", "required": False},
            },
        },
        {
            "name": "list_roles",
            "description": "List IAM roles matching an optional name prefix.",
            "parameters": {
                "prefix": {"type": "string", "description": "Filter roles by name prefix.", "required": False},
            },
        },
    ]


def observability_function_schema() -> list:
    return [
        {
            "name": "create_dashboard",
            "description": "Create a CloudWatch dashboard for given resources.",
            "parameters": {
                "dashboard_name": {"type": "string", "description": "Dashboard name.", "required": True},
                "resource_ids":   {"type": "string", "description": "Comma-separated EC2/ECS/RDS IDs to include.", "required": True},
            },
        },
        {
            "name": "create_alarm",
            "description": "Create a CloudWatch metric alarm.",
            "parameters": {
                "alarm_name":  {"type": "string", "description": "Alarm name.", "required": True},
                "metric":      {"type": "string", "description": "CloudWatch metric name, e.g. CPUUtilization", "required": True},
                "namespace":   {"type": "string", "description": "CloudWatch namespace, e.g. AWS/EC2", "required": True},
                "threshold":   {"type": "number",  "description": "Threshold value to trigger the alarm.", "required": True},
                "dimension_name":  {"type": "string", "description": "Dimension name, e.g. InstanceId", "required": False},
                "dimension_value": {"type": "string", "description": "Dimension value, e.g. i-0abc123", "required": False},
                "sns_topic_arn":   {"type": "string", "description": "SNS topic ARN for alarm notifications.", "required": False},
            },
        },
        {
            "name": "enable_cloudtrail",
            "description": "Enable CloudTrail in all regions with S3 delivery.",
            "parameters": {
                "trail_name":  {"type": "string", "description": "Trail name.", "required": True},
                "s3_bucket":   {"type": "string", "description": "S3 bucket for log delivery.", "required": True},
            },
        },
        {
            "name": "create_log_group",
            "description": "Create a CloudWatch Log Group with a retention policy.",
            "parameters": {
                "log_group_name": {"type": "string", "description": "Log group name.", "required": True},
                "retention_days": {"type": "integer", "description": "Days to retain logs. Default 90.", "required": False},
            },
        },
    ]


def compute_function_schema() -> list:
    return [
        {
            "name": "launch_ec2",
            "description": "Launch an EC2 instance with the given configuration.",
            "parameters": {
                "instance_type":   {"type": "string", "description": "EC2 instance type, e.g. t3.medium", "required": True},
                "ami_id":          {"type": "string", "description": "AMI ID. Use 'latest-al2' for latest Amazon Linux 2.", "required": True},
                "subnet_id":       {"type": "string", "description": "Subnet ID for the instance.", "required": True},
                "security_group_id": {"type": "string", "description": "Security group ID.", "required": True},
                "instance_profile":{"type": "string", "description": "IAM instance profile ARN or name.", "required": False},
                "key_name":        {"type": "string", "description": "EC2 key pair name for SSH.", "required": False},
                "tags":            {"type": "string", "description": "JSON string of key=value tags.", "required": False},
            },
        },
        {
            "name": "create_asg",
            "description": "Create an Auto Scaling Group with a Launch Template.",
            "parameters": {
                "asg_name":       {"type": "string", "description": "Name of the ASG.", "required": True},
                "instance_type":  {"type": "string", "description": "EC2 instance type.", "required": True},
                "ami_id":         {"type": "string", "description": "AMI ID.", "required": True},
                "subnet_ids":     {"type": "string", "description": "Comma-separated subnet IDs.", "required": True},
                "min_size":       {"type": "integer", "description": "Minimum instance count.", "required": True},
                "max_size":       {"type": "integer", "description": "Maximum instance count.", "required": True},
                "desired":        {"type": "integer", "description": "Desired instance count.", "required": False},
            },
        },
        {
            "name": "describe_instances",
            "description": "Describe running EC2 instances, optionally filtered by tag.",
            "parameters": {
                "tag_key":   {"type": "string", "description": "Filter by tag key.", "required": False},
                "tag_value": {"type": "string", "description": "Filter by tag value.", "required": False},
            },
        },
        {
            "name": "stop_instance",
            "description": "Stop one or more EC2 instances.",
            "parameters": {
                "instance_ids": {"type": "string", "description": "Comma-separated instance IDs.", "required": True},
            },
        },
    ]


def vpc_function_schema() -> list:
    return [
        {
            "name": "create_vpc",
            "description": "Create a VPC with public and private subnets across two AZs.",
            "parameters": {
                "vpc_name":    {"type": "string", "description": "Name tag for the VPC.", "required": True},
                "cidr":        {"type": "string", "description": "VPC CIDR block, e.g. 10.0.0.0/16", "required": False},
                "enable_flow_logs": {"type": "boolean", "description": "Enable VPC Flow Logs to CloudWatch.", "required": False},
                "flow_logs_role_arn": {"type": "string", "description": "IAM role ARN for Flow Logs delivery. Required if enable_flow_logs=true.", "required": False},
            },
        },
        {
            "name": "create_security_group",
            "description": "Create a security group with specified ingress/egress rules.",
            "parameters": {
                "group_name":  {"type": "string", "description": "Security group name.", "required": True},
                "vpc_id":      {"type": "string", "description": "VPC to create the group in.", "required": True},
                "description": {"type": "string", "description": "Security group description.", "required": True},
                "ingress_rules": {"type": "string", "description": "JSON array of ingress rules [{port, protocol, cidr}]", "required": False},
            },
        },
        {
            "name": "create_nat_gateway",
            "description": "Create a NAT Gateway in a public subnet for private subnet egress.",
            "parameters": {
                "subnet_id": {"type": "string", "description": "Public subnet ID for the NAT Gateway.", "required": True},
                "vpc_id":    {"type": "string", "description": "VPC ID.", "required": True},
            },
        },
        {
            "name": "describe_vpcs",
            "description": "List all VPCs with their subnets and route tables.",
            "parameters": {
                "vpc_id": {"type": "string", "description": "Optional VPC ID to filter.", "required": False},
            },
        },
    ]


SCHEMA_MAP = {
    "s3":            s3_function_schema,
    "iam":           iam_function_schema,
    "observability": observability_function_schema,
    "compute":       compute_function_schema,
    "vpc":           vpc_function_schema,
}


# ── Helper functions ──────────────────────────────────────────────────────────

def find_agent_by_name(agent_name: str) -> str | None:
    """Return the agentId of an existing agent with this name, or None."""
    paginator = client.get_paginator("list_agents")
    for page in paginator.paginate():
        for summary in page.get("agentSummaries", []):
            if summary["agentName"] == agent_name:
                return summary["agentId"]
    return None


def get_or_create_alias(agent_id: str) -> str:
    """Return an existing 'live' alias id for the agent, or create one."""
    resp = client.list_agent_aliases(agentId=agent_id)
    for summary in resp.get("agentAliasSummaries", []):
        if summary["agentAliasName"] == "live":
            log.info("  Reusing existing alias: %s", summary["agentAliasId"])
            return summary["agentAliasId"]
    alias = client.create_agent_alias(agentId=agent_id, agentAliasName="live")
    alias_id = alias["agentAlias"]["agentAliasId"]
    log.info("  Alias created: %s", alias_id)
    return alias_id


def existing_action_group_names(agent_id: str) -> set:
    """Return the set of action-group names already on the agent's DRAFT version."""
    try:
        resp = client.list_agent_action_groups(agentId=agent_id, agentVersion="DRAFT")
        return {ag["actionGroupName"] for ag in resp.get("actionGroupSummaries", [])}
    except Exception:
        return set()


def existing_collaborator_names(agent_id: str) -> set:
    """Return the set of collaborator names already wired to the supervisor."""
    try:
        resp = client.list_agent_collaborators(agentId=agent_id, agentVersion="DRAFT")
        return {c["collaboratorName"] for c in resp.get("agentCollaboratorSummaries", [])}
    except Exception:
        return set()


def wait_for_agent(agent_id: str, desired_status: str = "NOT_PREPARED") -> None:
    """Poll until the agent reaches the desired status."""
    for _ in range(30):
        resp = client.get_agent(agentId=agent_id)
        status = resp["agent"]["agentStatus"]
        log.info("  Agent %s → %s", agent_id, status)
        if status == desired_status:
            return
        if status in ("FAILED", "DELETING"):
            raise RuntimeError(f"Agent {agent_id} entered status {status}")
        time.sleep(5)
    raise TimeoutError(f"Agent {agent_id} did not reach {desired_status}")


def prepare_agent(agent_id: str) -> str:
    """Prepare the agent and return (re)used 'live' alias id."""
    client.prepare_agent(agentId=agent_id)
    wait_for_agent(agent_id, desired_status="PREPARED")
    return get_or_create_alias(agent_id)


def allow_bedrock_to_invoke_lambda(function_arn: str, agent_id: str) -> None:
    """Add resource-based policy so Bedrock agent can invoke the Lambda."""
    try:
        lambda_client.add_permission(
            FunctionName=function_arn,
            StatementId=f"bedrock-agent-{agent_id[:8]}",
            Action="lambda:InvokeFunction",
            Principal="bedrock.amazonaws.com",
            SourceArn=f"arn:aws:bedrock:{AWS_REGION}::foundation-model/{BEDROCK_MODEL_ID}",
        )
    except lambda_client.exceptions.ResourceConflictException:
        pass  # Permission already exists


# ── Agent creation ────────────────────────────────────────────────────────────

def create_specialist_agent(name: str, key: str) -> dict:
    """Create one specialist agent with its action group. Returns ids dict.
    If an agent with this name already exists, reuse it (skip creation)."""

    existing_id = find_agent_by_name(name)
    if existing_id:
        log.info("⏭  %s already exists (%s) — reusing", name, existing_id)
        alias_id = get_or_create_alias(existing_id)
        return {"agent_id": existing_id, "alias_id": alias_id}

    log.info("Creating %s agent...", name)
    agent = client.create_agent(
        agentName=name,
        foundationModel=BEDROCK_MODEL_ID,
        instruction=AGENT_INSTRUCTIONS[key],
        agentResourceRoleArn=BEDROCK_AGENT_ROLE_ARN,
        description=f"Specialist agent for {name}",
        idleSessionTTLInSeconds=1800,
    )
    agent_id = agent["agent"]["agentId"]
    log.info("  Agent ID: %s", agent_id)

    wait_for_agent(agent_id, desired_status="NOT_PREPARED")

    # Action group (skip if already present)
    lambda_arn = LAMBDA_ARNS[key]
    allow_bedrock_to_invoke_lambda(lambda_arn, agent_id)

    ag_name = f"{key.upper()}-Actions"
    if ag_name not in existing_action_group_names(agent_id):
        client.create_agent_action_group(
            agentId=agent_id,
            agentVersion="DRAFT",
            actionGroupName=ag_name,
            actionGroupExecutor={"lambda": lambda_arn},
            functionSchema={
                "functions": [
                    {
                        "name": fn["name"],
                        "description": fn["description"],
                        "parameters": {
                            param: {
                                "type": spec["type"],
                                "description": spec["description"],
                                "required": spec.get("required", False),
                            }
                            for param, spec in fn.get("parameters", {}).items()
                        },
                    }
                    for fn in SCHEMA_MAP[key]()
                ]
            },
        )

    alias_id = prepare_agent(agent_id)
    return {"agent_id": agent_id, "alias_id": alias_id}


def create_super_agent(sub_agents: dict) -> dict:
    """Create the supervisor agent with all specialist agents as collaborators.
    Reuses an existing supervisor and skips collaborators already wired."""
    super_name = "CloudInfraSuperAgent"

    agent_id = find_agent_by_name(super_name)
    if agent_id:
        log.info("⏭  Super Agent already exists (%s) — reusing", agent_id)
        # Ensure collaboration is enabled (older agents may have it DISABLED)
        client.update_agent(
            agentId=agent_id,
            agentName=super_name,
            foundationModel=BEDROCK_MODEL_ID,
            instruction=AGENT_INSTRUCTIONS["super"],
            agentResourceRoleArn=BEDROCK_AGENT_ROLE_ARN,
            agentCollaboration="SUPERVISOR",
            idleSessionTTLInSeconds=3600,
        )
        wait_for_agent(agent_id, desired_status="NOT_PREPARED")
    else:
        log.info("Creating Super Agent (supervisor)...")
        agent = client.create_agent(
            agentName=super_name,
            foundationModel=BEDROCK_MODEL_ID,
            instruction=AGENT_INSTRUCTIONS["super"],
            agentResourceRoleArn=BEDROCK_AGENT_ROLE_ARN,
            description="Supervisor agent that orchestrates five specialist cloud agents",
            idleSessionTTLInSeconds=3600,
            # CRITICAL: must enable collaboration at creation, otherwise
            # associate_agent_collaborator fails with "set to 'DISABLED'".
            # Use "SUPERVISOR" (agent decides routing) or "SUPERVISOR_ROUTER".
            agentCollaboration="SUPERVISOR",
        )
        agent_id = agent["agent"]["agentId"]
        log.info("  Super Agent ID: %s", agent_id)
        wait_for_agent(agent_id, desired_status="NOT_PREPARED")

    # Wire each specialist as a collaborator (skip ones already wired)
    collab_map = {
        "s3":            "S3StorageAgent",
        "iam":           "IAMAgent",
        "observability": "ObservabilityAgent",
        "compute":       "ComputeAgent",
        "vpc":           "VPCAgent",
    }

    already_wired = existing_collaborator_names(agent_id)
    account_id = _get_account_id()

    for key, collab_name in collab_map.items():
        if collab_name in already_wired:
            log.info("  ⏭  Collaborator %s already wired — skipping", collab_name)
            continue
        ids = sub_agents[key]
        log.info("  Associating collaborator: %s", collab_name)
        client.associate_agent_collaborator(
            agentId=agent_id,
            agentVersion="DRAFT",
            agentDescriptor={
                "aliasArn": (
                    f"arn:aws:bedrock:{AWS_REGION}:{account_id}"
                    f":agent-alias/{ids['agent_id']}/{ids['alias_id']}"
                )
            },
            collaboratorName=collab_name,
            collaborationInstruction=(
                f"Delegate tasks that require {collab_name.replace('Agent','').strip()} "
                "expertise to this agent. Always wait for its response before proceeding."
            ),
            relayConversationHistory="TO_COLLABORATOR",
        )

    alias_id = prepare_agent(agent_id)
    return {"agent_id": agent_id, "alias_id": alias_id}


def _get_account_id() -> str:
    sts = boto3.client("sts")
    return sts.get_caller_identity()["Account"]


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    log.info("=" * 60)
    log.info("AWS Bedrock Multi-Agent Setup")
    log.info("=" * 60)

    sub_agents: dict = {}

    for key, name in [
        ("s3",            "S3StorageAgent"),
        ("iam",           "IAMAgent"),
        ("observability", "ObservabilityAgent"),
        ("compute",       "ComputeAgent"),
        ("vpc",           "VPCAgent"),
    ]:
        sub_agents[key] = create_specialist_agent(name, key)
        log.info("  ✅ %s ready", name)

    super_ids = create_super_agent(sub_agents)
    log.info("  ✅ Super Agent ready")

    output = {
        "super_agent": super_ids,
        "sub_agents":  sub_agents,
        "region":      AWS_REGION,
    }

    with open(IDS_FILE, "w") as f:
        json.dump(output, f, indent=2)

    log.info("=" * 60)
    log.info("Setup complete. Agent IDs saved to '%s'", IDS_FILE)
    log.info("Next: python invoke_agent.py")
    log.info("=" * 60)


if __name__ == "__main__":
    main()

