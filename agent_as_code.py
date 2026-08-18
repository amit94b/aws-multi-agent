"""
agent_as_code.py — 1-Click Agent-as-Code (AaaC) Provisioning & Teardown Engine
-----------------------------------------------------------------------------
Autonomous, end-to-end infrastructure-as-code pipeline for the AWS Bedrock
Multi-Agent Cloud Infrastructure System.

Features:
  • 1-Click Deploy: IAM Roles → 7 Specialist Lambdas → Bedrock Agents & Supervisor → agent_ids.json
  • 1-Click Destroy: Clean teardown of Bedrock agents, action groups, aliases, Lambdas, and state
  • 1-Click Status: Comprehensive health check and diagnostic report
  • 1-Click Redeploy: Clean teardown followed by fresh deployment from scratch

Usage:
  python agent_as_code.py            # 1-Click Deploy
  python agent_as_code.py --deploy   # 1-Click Deploy
  python agent_as_code.py --destroy  # 1-Click Clean Teardown
  python agent_as_code.py --redeploy # 1-Click Wipe & Rebuild
  python agent_as_code.py --status   # Diagnostic Health Check
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, Callable

import boto3
from botocore.exceptions import ClientError

from config import (
    AWS_ACCOUNT_ID,
    AWS_REGION,
    BEDROCK_AGENT_ROLE_ARN,
    BEDROCK_MODEL_ID,
    LAMBDA_ARNS,
    AGENT_INSTRUCTIONS,
)
from setup_agents import SCHEMA_MAP

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger("AgentAsCode")

IDS_FILE = "agent_ids.json"
BASE_DIR = Path(__file__).parent

SPECIALIST_SPECS = [
    {"key": "s3",            "name": "S3StorageAgent",     "function_name": "bedrock-s3-agent",            "handler_file": "lambda_s3.py"},
    {"key": "iam",           "name": "IAMAgent",           "function_name": "bedrock-iam-agent",           "handler_file": "lambda_iam.py"},
    {"key": "observability", "name": "ObservabilityAgent", "function_name": "bedrock-observability-agent", "handler_file": "lambda_observability.py"},
    {"key": "compute",       "name": "ComputeAgent",       "function_name": "bedrock-compute-agent",       "handler_file": "lambda_compute.py"},
    {"key": "vpc",           "name": "VPCAgent",           "function_name": "bedrock-vpc-agent",           "handler_file": "lambda_vpc.py"},
    {"key": "database",      "name": "DatabaseAgent",      "function_name": "bedrock-database-agent",      "handler_file": "lambda_database.py"},
    {"key": "finops",        "name": "FinOpsAgent",        "function_name": "bedrock-finops-agent",        "handler_file": "lambda_finops.py"},
]

SUPER_AGENT_NAME = "CloudInfraSuperAgent"


# ── AWS Client Factory ────────────────────────────────────────────────────────

def get_clients(region: str = AWS_REGION) -> dict[str, Any]:
    return {
        "iam":     boto3.client("iam", region_name=region),
        "lambda":  boto3.client("lambda", region_name=region),
        "bedrock": boto3.client("bedrock-agent", region_name=region),
        "sts":     boto3.client("sts", region_name=region),
    }


def resolve_account_id(sts_client: Any) -> str:
    global AWS_ACCOUNT_ID
    if AWS_ACCOUNT_ID and AWS_ACCOUNT_ID != "***":
        return AWS_ACCOUNT_ID
    try:
        acct = sts_client.get_caller_identity()["Account"]
        AWS_ACCOUNT_ID = acct
        return acct
    except Exception as e:
        log.warning("Could not automatically resolve AWS Account ID via STS: %s", e)
        return AWS_ACCOUNT_ID or "***"


# ── IAM Automation ────────────────────────────────────────────────────────────

def ensure_bedrock_agent_role(iam_client: Any, account_id: str) -> str:
    """Ensure the IAM execution role for Bedrock agents exists with necessary trust & policies."""
    role_name = "BedrockAgentRole"
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "bedrock.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }],
    }

    try:
        resp = iam_client.get_role(RoleName=role_name)
        role_arn = resp["Role"]["Arn"]
        log.info("  ✓ Bedrock IAM Role exists: %s", role_arn)
    except iam_client.exceptions.NoSuchEntityException:
        log.info("  Creating Bedrock IAM Role: %s...", role_name)
        resp = iam_client.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Execution role for Amazon Bedrock Multi-Agent System",
        )
        role_arn = resp["Role"]["Arn"]
        log.info("  ✓ Created Bedrock IAM Role: %s", role_arn)

    # Attach required AWS managed policies
    for policy_arn in [
        "arn:aws:iam::aws:policy/AmazonBedrockFullAccess",
        "arn:aws:iam::aws:policy/service-role/AWSLambdaRole",
    ]:
        try:
            iam_client.attach_role_policy(RoleName=role_name, PolicyArn=policy_arn)
        except Exception:
            pass

    return role_arn


def ensure_lambda_execution_role(iam_client: Any, account_id: str) -> str:
    """Ensure the IAM execution role for specialist Lambda functions exists with domain permissions."""
    role_name = "BedrockAgentLambdaRole"
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }],
    }

    try:
        resp = iam_client.get_role(RoleName=role_name)
        role_arn = resp["Role"]["Arn"]
        log.info("  ✓ Lambda IAM Role exists: %s", role_arn)
    except iam_client.exceptions.NoSuchEntityException:
        log.info("  Creating Lambda IAM Execution Role: %s...", role_name)
        resp = iam_client.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Execution role for Bedrock Specialist Agent Lambdas",
        )
        role_arn = resp["Role"]["Arn"]
        log.info("  ✓ Created Lambda IAM Role: %s", role_arn)

    # Attach basic execution policy
    try:
        iam_client.attach_role_policy(
            RoleName=role_name,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
        )
    except Exception:
        pass

    # Put inline comprehensive policy for all 7 specialist domains
    specialist_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "S3Management",
                "Effect": "Allow",
                "Action": ["s3:*"],
                "Resource": "*",
            },
            {
                "Sid": "IAMManagement",
                "Effect": "Allow",
                "Action": [
                    "iam:CreateRole",
                    "iam:AttachRolePolicy",
                    "iam:PutRolePolicy",
                    "iam:GetRole",
                    "iam:ListRoles",
                    "iam:PassRole",
                ],
                "Resource": "*",
            },
            {
                "Sid": "ObservabilityManagement",
                "Effect": "Allow",
                "Action": [
                    "cloudwatch:*",
                    "logs:*",
                    "cloudtrail:*",
                ],
                "Resource": "*",
            },
            {
                "Sid": "ComputeAndNetworking",
                "Effect": "Allow",
                "Action": [
                    "ec2:*",
                    "autoscaling:*",
                ],
                "Resource": "*",
            },
            {
                "Sid": "DatabaseManagement",
                "Effect": "Allow",
                "Action": [
                    "rds:*",
                    "dynamodb:*",
                ],
                "Resource": "*",
            },
            {
                "Sid": "FinOpsAndPricing",
                "Effect": "Allow",
                "Action": [
                    "ce:GetCostForecast",
                    "ce:GetCostAndUsage",
                    "pricing:GetProducts",
                    "pricing:DescribeServices",
                ],
                "Resource": "*",
            },
        ],
    }

    try:
        iam_client.put_role_policy(
            RoleName=role_name,
            PolicyName="SpecialistAgentDomainPermissions",
            PolicyDocument=json.dumps(specialist_policy),
        )
    except Exception as e:
        log.warning("Could not update inline policy for %s: %s", role_name, e)

    return role_arn


# ── Lambda Automation ─────────────────────────────────────────────────────────

def build_lambda_zip_bytes(handler_file: str) -> bytes:
    """Build an in-memory zip archive containing the Lambda handler file."""
    file_path = BASE_DIR / handler_file
    if not file_path.exists():
        file_path = BASE_DIR / "lambda_handlers.py"
    if not file_path.exists():
        raise FileNotFoundError(f"Could not find handler file: {handler_file}")

    content = file_path.read_text(encoding="utf-8")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Write as both original filename and lambda_function.py for maximum compatibility
        zf.writestr(handler_file, content)
        zf.writestr("lambda_function.py", content)
        zf.writestr("lambda_handlers.py", content)
    return buf.getvalue()


def deploy_lambda_function(
    lambda_client: Any,
    spec: dict,
    role_arn: str,
    region: str = AWS_REGION,
    progress_callback: Callable[[str], None] | None = None,
) -> str:
    """Create or update a specialist Lambda function."""
    key = spec["key"]
    fn_name = spec["function_name"]
    handler_file = spec["handler_file"]
    module_name = handler_file.replace(".py", "")

    msg = f"Packaging & deploying Lambda: {fn_name}..."
    if progress_callback: progress_callback(msg)
    log.info("  Deploying Lambda [%s] -> %s", key, fn_name)

    zip_bytes = build_lambda_zip_bytes(handler_file)

    env_vars = {
        "Variables": {
            "AGENT_KEY": key,
            "AWS_REGION": region,
        }
    }

    # Attempt to retrieve existing function
    try:
        resp = lambda_client.get_function(FunctionName=fn_name)
        # Function exists, update code & configuration
        lambda_client.update_function_code(
            FunctionName=fn_name,
            ZipFile=zip_bytes,
        )
        time.sleep(1)
        lambda_client.update_function_configuration(
            FunctionName=fn_name,
            Role=role_arn,
            Handler=f"{module_name}.lambda_handler",
            Timeout=60,
            MemorySize=256,
            Environment=env_vars,
        )
        fn_arn = resp["Configuration"]["FunctionArn"]
        log.info("  ✓ Updated Lambda: %s", fn_arn)
        return fn_arn
    except lambda_client.exceptions.ResourceNotFoundException:
        # Create function
        for attempt in range(6):
            try:
                resp = lambda_client.create_function(
                    FunctionName=fn_name,
                    Runtime="python3.12",
                    Role=role_arn,
                    Handler=f"{module_name}.lambda_handler",
                    Code={"ZipFile": zip_bytes},
                    Description=f"Bedrock Specialist Agent Lambda for {key.upper()}",
                    Timeout=60,
                    MemorySize=256,
                    Environment=env_vars,
                )
                fn_arn = resp["FunctionArn"]
                log.info("  ✓ Created Lambda: %s", fn_arn)
                return fn_arn
            except ClientError as e:
                if "The role defined for the function cannot be assumed" in str(e) and attempt < 5:
                    log.info("  Waiting for IAM role propagation... (attempt %d/5)", attempt + 1)
                    time.sleep(5)
                    continue
                raise e


# ── Bedrock Automation ────────────────────────────────────────────────────────

def find_agent_by_name(bedrock_client: Any, agent_name: str) -> str | None:
    paginator = bedrock_client.get_paginator("list_agents")
    for page in paginator.paginate():
        for summary in page.get("agentSummaries", []):
            if summary["agentName"] == agent_name:
                return summary["agentId"]
    return None


def get_or_create_agent_alias(bedrock_client: Any, agent_id: str) -> str:
    resp = bedrock_client.list_agent_aliases(agentId=agent_id)
    for summary in resp.get("agentAliasSummaries", []):
        if summary["agentAliasName"] == "live":
            return summary["agentAliasId"]
    alias = bedrock_client.create_agent_alias(agentId=agent_id, agentAliasName="live")
    return alias["agentAlias"]["agentAliasId"]


def wait_for_agent_status(bedrock_client: Any, agent_id: str, desired_status: str = "NOT_PREPARED") -> None:
    for _ in range(40):
        resp = bedrock_client.get_agent(agentId=agent_id)
        status = resp["agent"]["agentStatus"]
        if status == desired_status:
            return
        if status in ("FAILED", "DELETING"):
            raise RuntimeError(f"Agent {agent_id} reached invalid status: {status}")
        time.sleep(4)
    raise TimeoutError(f"Agent {agent_id} did not reach status {desired_status}")


def prepare_and_alias(bedrock_client: Any, agent_id: str) -> str:
    bedrock_client.prepare_agent(agentId=agent_id)
    wait_for_agent_status(bedrock_client, agent_id, desired_status="PREPARED")
    return get_or_create_agent_alias(bedrock_client, agent_id)


def grant_bedrock_lambda_permission(lambda_client: Any, function_name: str, agent_id: str, region: str, account_id: str) -> None:
    agent_arn = f"arn:aws:bedrock:{region}:{account_id}:agent/{agent_id}"
    stmt_id = f"bedrock-agent-{agent_id[:8]}"
    try:
        lambda_client.add_permission(
            FunctionName=function_name,
            StatementId=stmt_id,
            Action="lambda:InvokeFunction",
            Principal="bedrock.amazonaws.com",
            SourceArn=agent_arn,
        )
        log.info("  ✓ Granted Lambda invocation permission for agent %s", agent_id[:8])
    except lambda_client.exceptions.ResourceConflictException:
        pass


def deploy_specialist_agent(
    bedrock_client: Any,
    lambda_client: Any,
    spec: dict,
    bedrock_role_arn: str,
    lambda_arn: str,
    region: str,
    account_id: str,
    progress_callback: Callable[[str], None] | None = None,
) -> dict[str, str]:
    key = spec["key"]
    name = spec["name"]
    fn_name = spec["function_name"]

    msg = f"Configuring Specialist Bedrock Agent: {name}..."
    if progress_callback: progress_callback(msg)
    log.info("  Configuring Agent: %s (%s)", name, key)

    agent_id = find_agent_by_name(bedrock_client, name)
    if agent_id:
        log.info("  Found existing agent: %s (%s)", name, agent_id)
        bedrock_client.update_agent(
            agentId=agent_id,
            agentName=name,
            foundationModel=BEDROCK_MODEL_ID,
            instruction=AGENT_INSTRUCTIONS[key],
            agentResourceRoleArn=bedrock_role_arn,
            idleSessionTTLInSeconds=1800,
        )
        wait_for_agent_status(bedrock_client, agent_id, "NOT_PREPARED")
    else:
        log.info("  Creating agent: %s...", name)
        agent = bedrock_client.create_agent(
            agentName=name,
            foundationModel=BEDROCK_MODEL_ID,
            instruction=AGENT_INSTRUCTIONS[key],
            agentResourceRoleArn=bedrock_role_arn,
            description=f"Specialist agent for {name}",
            idleSessionTTLInSeconds=1800,
        )
        agent_id = agent["agent"]["agentId"]
        wait_for_agent_status(bedrock_client, agent_id, "NOT_PREPARED")

    grant_bedrock_lambda_permission(lambda_client, fn_name, agent_id, region, account_id)

    # Configure Action Group
    ag_name = f"{key.upper()}-Actions"
    existing_ags = set()
    try:
        resp = bedrock_client.list_agent_action_groups(agentId=agent_id, agentVersion="DRAFT")
        existing_ags = {ag["actionGroupName"] for ag in resp.get("actionGroupSummaries", [])}
    except Exception:
        pass

    schema_funcs = SCHEMA_MAP[key]()
    action_group_schema = {
        "functions": [
            {
                "name": fn["name"],
                "description": fn["description"],
                "parameters": {
                    param: {
                        "type": spec_param["type"],
                        "description": spec_param["description"],
                        "required": spec_param.get("required", False),
                    }
                    for param, spec_param in fn.get("parameters", {}).items()
                },
            }
            for fn in schema_funcs
        ]
    }

    if ag_name not in existing_ags:
        bedrock_client.create_agent_action_group(
            agentId=agent_id,
            agentVersion="DRAFT",
            actionGroupName=ag_name,
            actionGroupExecutor={"lambda": lambda_arn},
            functionSchema=action_group_schema,
        )
    else:
        # Get AG ID to update
        for ag in resp.get("actionGroupSummaries", []):
            if ag["actionGroupName"] == ag_name:
                bedrock_client.update_agent_action_group(
                    agentId=agent_id,
                    agentVersion="DRAFT",
                    actionGroupId=ag["actionGroupId"],
                    actionGroupName=ag_name,
                    actionGroupExecutor={"lambda": lambda_arn},
                    functionSchema=action_group_schema,
                )
                break

    alias_id = prepare_and_alias(bedrock_client, agent_id)
    log.info("  ✓ Agent %s ready (ID: %s, Alias: %s)", name, agent_id, alias_id)
    return {"agent_id": agent_id, "alias_id": alias_id}


def deploy_super_agent(
    bedrock_client: Any,
    sub_agents: dict[str, dict[str, str]],
    bedrock_role_arn: str,
    region: str,
    account_id: str,
    progress_callback: Callable[[str], None] | None = None,
) -> dict[str, str]:
    """Create/Update the Supervisor agent and wire all 7 specialists as collaborators."""
    msg = f"Orchestrating Supervisor Agent: {SUPER_AGENT_NAME}..."
    if progress_callback: progress_callback(msg)
    log.info("  Orchestrating Supervisor: %s", SUPER_AGENT_NAME)

    agent_id = find_agent_by_name(bedrock_client, SUPER_AGENT_NAME)
    if agent_id:
        log.info("  Updating existing Supervisor: %s (%s)", SUPER_AGENT_NAME, agent_id)
        bedrock_client.update_agent(
            agentId=agent_id,
            agentName=SUPER_AGENT_NAME,
            foundationModel=BEDROCK_MODEL_ID,
            instruction=AGENT_INSTRUCTIONS["super"],
            agentResourceRoleArn=bedrock_role_arn,
            agentCollaboration="SUPERVISOR",
            idleSessionTTLInSeconds=3600,
        )
        wait_for_agent_status(bedrock_client, agent_id, "NOT_PREPARED")
    else:
        log.info("  Creating Supervisor: %s...", SUPER_AGENT_NAME)
        agent = bedrock_client.create_agent(
            agentName=SUPER_AGENT_NAME,
            foundationModel=BEDROCK_MODEL_ID,
            instruction=AGENT_INSTRUCTIONS["super"],
            agentResourceRoleArn=bedrock_role_arn,
            description="Supervisor agent that orchestrates seven specialist cloud agents",
            idleSessionTTLInSeconds=3600,
            agentCollaboration="SUPERVISOR",
        )
        agent_id = agent["agent"]["agentId"]
        wait_for_agent_status(bedrock_client, agent_id, "NOT_PREPARED")

    # Wire Collaborators
    collab_map = {spec["key"]: spec["name"] for spec in SPECIALIST_SPECS}
    existing_collabs = set()
    try:
        resp = bedrock_client.list_agent_collaborators(agentId=agent_id, agentVersion="DRAFT")
        existing_collabs = {c["collaboratorName"] for c in resp.get("agentCollaboratorSummaries", [])}
    except Exception:
        pass

    for key, collab_name in collab_map.items():
        if collab_name in existing_collabs:
            continue
        sub_info = sub_agents[key]
        alias_arn = f"arn:aws:bedrock:{region}:{account_id}:agent-alias/{sub_info['agent_id']}/{sub_info['alias_id']}"
        log.info("  Associating collaborator: %s", collab_name)
        bedrock_client.associate_agent_collaborator(
            agentId=agent_id,
            agentVersion="DRAFT",
            agentDescriptor={"aliasArn": alias_arn},
            collaboratorName=collab_name,
            collaborationInstruction=(
                f"Delegate tasks that require {collab_name.replace('Agent','').strip()} "
                "expertise to this agent. Always wait for its response before proceeding."
            ),
            relayConversationHistory="TO_COLLABORATOR",
        )

    alias_id = prepare_and_alias(bedrock_client, agent_id)
    log.info("  ✓ Super Agent ready (ID: %s, Alias: %s)", agent_id, alias_id)
    return {"agent_id": agent_id, "alias_id": alias_id}


# ── 1-Click Lifecycle Orchestration ───────────────────────────────────────────

def deploy_all(progress_callback: Callable[[str], None] | None = None) -> dict[str, Any]:
    """
    1-Click complete deployment:
    1. IAM roles (Bedrock Agent Role & Lambda Execution Role)
    2. 7 Specialist Lambda functions
    3. 7 Specialist Bedrock Agents + Action Groups + Schemas
    4. 1 Super Agent Supervisor + Collaborator Wiring
    5. State Output -> agent_ids.json
    """
    start_time = time.time()
    log.info("=" * 60)
    log.info("🚀 STARTING 1-CLICK AGENT-AS-CODE (AaaC) DEPLOYMENT")
    log.info("=" * 60)

    clients = get_clients()
    account_id = resolve_account_id(clients["sts"])
    region = AWS_REGION

    log.info("Target Environment: AWS Account [%s] · Region [%s]", account_id, region)
    if progress_callback: progress_callback(f"Connected to AWS Account: {account_id} ({region})")

    # Step 1: IAM
    if progress_callback: progress_callback("Step 1/4: Provisioning IAM Roles...")
    bedrock_role_arn = ensure_bedrock_agent_role(clients["iam"], account_id)
    lambda_role_arn  = ensure_lambda_execution_role(clients["iam"], account_id)

    # Step 2: Lambdas
    if progress_callback: progress_callback("Step 2/4: Deploying 7 Specialist Lambdas...")
    lambda_arns: dict[str, str] = {}
    for spec in SPECIALIST_SPECS:
        fn_arn = deploy_lambda_function(
            clients["lambda"],
            spec,
            lambda_role_arn,
            region,
            progress_callback=progress_callback,
        )
        lambda_arns[spec["key"]] = fn_arn

    # Step 3: Specialist Agents
    if progress_callback: progress_callback("Step 3/4: Provisioning Bedrock Specialist Agents...")
    sub_agents: dict[str, dict[str, str]] = {}
    for spec in SPECIALIST_SPECS:
        key = spec["key"]
        fn_arn = lambda_arns[key]
        sub_agents[key] = deploy_specialist_agent(
            clients["bedrock"],
            clients["lambda"],
            spec,
            bedrock_role_arn,
            fn_arn,
            region,
            account_id,
            progress_callback=progress_callback,
        )

    # Step 4: Supervisor
    if progress_callback: progress_callback("Step 4/4: Provisioning Supervisor Agent...")
    super_ids = deploy_super_agent(
        clients["bedrock"],
        sub_agents,
        bedrock_role_arn,
        region,
        account_id,
        progress_callback=progress_callback,
    )

    # Save IDs
    output_state = {
        "super_agent": super_ids,
        "sub_agents":  sub_agents,
        "region":      region,
        "account_id":  account_id,
        "deployed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    ids_path = BASE_DIR / IDS_FILE
    with open(ids_path, "w", encoding="utf-8") as f:
        json.dump(output_state, f, indent=2)

    elapsed = round(time.time() - start_time, 1)
    log.info("=" * 60)
    log.info("✅ 1-CLICK DEPLOYMENT COMPLETE (Elapsed: %ss)", elapsed)
    log.info("State saved to %s", ids_path)
    log.info("=" * 60)

    if progress_callback: progress_callback(f"Deployment successfully completed in {elapsed}s!")
    return output_state


def destroy_all(progress_callback: Callable[[str], None] | None = None) -> dict[str, Any]:
    """
    1-Click clean teardown:
    1. Disassociate Collaborators from Supervisor
    2. Delete Supervisor Agent
    3. Delete 7 Specialist Bedrock Agents, Aliases, Action Groups
    4. Delete 7 Specialist Lambda Functions
    5. Clean up agent_ids.json
    """
    log.info("=" * 60)
    log.info("🧹 STARTING 1-CLICK AGENT-AS-CODE (AaaC) TEARDOWN")
    log.info("=" * 60)

    clients = get_clients()
    report = {"deleted_agents": [], "deleted_lambdas": [], "errors": []}

    # 1. Delete Super Agent
    if progress_callback: progress_callback("Cleaning up Supervisor Agent...")
    super_id = find_agent_by_name(clients["bedrock"], SUPER_AGENT_NAME)
    if super_id:
        try:
            log.info("  Deleting Supervisor: %s (%s)", SUPER_AGENT_NAME, super_id)
            clients["bedrock"].delete_agent(agentId=super_id, skipResourceInUseCheck=True)
            report["deleted_agents"].append(SUPER_AGENT_NAME)
            log.info("  ✓ Deleted %s", SUPER_AGENT_NAME)
        except Exception as e:
            log.warning("Could not delete %s: %s", SUPER_AGENT_NAME, e)
            report["errors"].append(f"Supervisor: {e}")

    # 2. Delete Specialist Agents
    if progress_callback: progress_callback("Cleaning up Specialist Bedrock Agents...")
    for spec in SPECIALIST_SPECS:
        name = spec["name"]
        aid = find_agent_by_name(clients["bedrock"], name)
        if aid:
            try:
                log.info("  Deleting Specialist: %s (%s)", name, aid)
                clients["bedrock"].delete_agent(agentId=aid, skipResourceInUseCheck=True)
                report["deleted_agents"].append(name)
                log.info("  ✓ Deleted %s", name)
            except Exception as e:
                log.warning("Could not delete %s: %s", name, e)
                report["errors"].append(f"Agent {name}: {e}")

    # 3. Delete Lambdas
    if progress_callback: progress_callback("Cleaning up Specialist Lambda Functions...")
    for spec in SPECIALIST_SPECS:
        fn_name = spec["function_name"]
        try:
            log.info("  Deleting Lambda: %s", fn_name)
            clients["lambda"].delete_function(FunctionName=fn_name)
            report["deleted_lambdas"].append(fn_name)
            log.info("  ✓ Deleted Lambda %s", fn_name)
        except clients["lambda"].exceptions.ResourceNotFoundException:
            pass
        except Exception as e:
            log.warning("Could not delete Lambda %s: %s", fn_name, e)
            report["errors"].append(f"Lambda {fn_name}: {e}")

    # 4. Clean local state
    ids_path = BASE_DIR / IDS_FILE
    if ids_path.exists():
        try:
            ids_path.unlink()
            log.info("  ✓ Removed %s", IDS_FILE)
        except Exception:
            pass

    log.info("=" * 60)
    log.info("✅ 1-CLICK CLEAN TEARDOWN COMPLETE")
    log.info("Summary: %d agents removed, %d lambdas removed", len(report["deleted_agents"]), len(report["deleted_lambdas"]))
    log.info("=" * 60)

    if progress_callback: progress_callback("Teardown complete! Environment is clean.")
    return report


def status_all() -> dict[str, Any]:
    """Check health and diagnostic status of all multi-agent cloud resources."""
    log.info("=" * 60)
    log.info("🔍 AGENT-AS-CODE (AaaC) SYSTEM DIAGNOSTICS")
    log.info("=" * 60)

    clients = get_clients()
    account_id = resolve_account_id(clients["sts"])
    region = AWS_REGION

    status_data: dict[str, Any] = {
        "account_id": account_id,
        "region": region,
        "iam_roles": {},
        "lambdas": {},
        "bedrock_agents": {},
    }

    # Check IAM
    for role_name in ["BedrockAgentRole", "BedrockAgentLambdaRole"]:
        try:
            resp = clients["iam"].get_role(RoleName=role_name)
            status_data["iam_roles"][role_name] = {"exists": True, "arn": resp["Role"]["Arn"]}
            log.info("  IAM Role [%s]: ACTIVE", role_name)
        except Exception:
            status_data["iam_roles"][role_name] = {"exists": False}
            log.info("  IAM Role [%s]: NOT FOUND", role_name)

    # Check Lambdas
    for spec in SPECIALIST_SPECS:
        fn_name = spec["function_name"]
        try:
            resp = clients["lambda"].get_function(FunctionName=fn_name)
            status_data["lambdas"][fn_name] = {
                "exists": True,
                "runtime": resp["Configuration"]["Runtime"],
                "last_modified": resp["Configuration"]["LastModified"],
            }
            log.info("  Lambda [%s]: ACTIVE (%s)", fn_name, resp["Configuration"]["Runtime"])
        except Exception:
            status_data["lambdas"][fn_name] = {"exists": False}
            log.info("  Lambda [%s]: NOT FOUND", fn_name)

    # Check Bedrock Agents
    super_id = find_agent_by_name(clients["bedrock"], SUPER_AGENT_NAME)
    status_data["bedrock_agents"][SUPER_AGENT_NAME] = {"exists": bool(super_id), "id": super_id}
    log.info("  Bedrock Supervisor [%s]: %s", SUPER_AGENT_NAME, super_id or "NOT FOUND")

    for spec in SPECIALIST_SPECS:
        name = spec["name"]
        aid = find_agent_by_name(clients["bedrock"], name)
        status_data["bedrock_agents"][name] = {"exists": bool(aid), "id": aid}
        log.info("  Bedrock Specialist [%s]: %s", name, aid or "NOT FOUND")

    return status_data


def redeploy(progress_callback: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Clean teardown followed by a fresh 1-click deploy."""
    destroy_all(progress_callback=progress_callback)
    time.sleep(3)
    return deploy_all(progress_callback=progress_callback)


# ── CLI Interface ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Agent-as-Code (AaaC): 1-Click Multi-Agent Deployment & Lifecycle Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--deploy",   action="store_true", help="1-Click deploy all IAM, Lambdas, and Bedrock agents (default)")
    group.add_argument("--destroy",  action="store_true", help="1-Click clean teardown of all resources")
    group.add_argument("--redeploy", action="store_true", help="Clean teardown then fresh 1-Click deploy")
    group.add_argument("--status",   action="store_true", help="Health check and resource diagnostics")

    args = parser.parse_args()

    if args.destroy:
        destroy_all()
    elif args.redeploy:
        redeploy()
    elif args.status:
        status = status_all()
        print("\n" + json.dumps(status, indent=2))
    else:
        # Default action is deploy
        deploy_all()


if __name__ == "__main__":
    main()
