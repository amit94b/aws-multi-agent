vi config.py
vi invoke_agent.py
vi lambda_handlers.py
vi README.md
vi setup_agents.py
vi streamlit_app.py
passwd
yum install python*
yum install python* -y
pip install boto3 streamlit
python3 pip install boto3
python3  install boto3
python install boto3
python3
python3 install 
ls
systemctl status sshd
aws 
aws s3 ls
yum install pip
pip install boto3 stremlit
install boto3 streamlit
pip install boto3 streamlit
pip show boto3
pip install boto3 streamlit
pip shows boto3
pip show boto3
pip show streamlit_app.py 
pip show streamlit
vi config.py 
aws configure
aws config
aws login
aws
exit
yum install python*
yum install python* """
config.py — Central configuration for the AWS Bedrock Multi-Agent System.
Edit the values in this file before running setup_agents.py.
"""
import os
# ── AWS Settings ──────────────────────────────────────────────────────────────
AWS_REGION          = os.environ.get("AWS_REGION", "eu-west-1")
AWS_ACCOUNT_ID      = os.environ.get("AWS_ACCOUNT_ID", "943086490726")   # ← replace
BEDROCK_MODEL_ID    = "anthropic.claude-sonnet-4-6"     # cross-region inference
# ── IAM Role ─────────────────────────────────────────────────────────────────
# A single IAM role that all agents assume.  The role must have:
#   - AmazonBedrockFullAccess
#   - AWSLambdaRole
# Create it manually and paste the ARN here (or set the env var).
BEDROCK_AGENT_ROLE_ARN = os.environ.get(
    "BEDROCK_AGENT_ROLE_ARN",
    f"arn:aws:iam::943086490726:role/BedrockAgentRole",   # ← replace
)
# ── Lambda ARNs ───────────────────────────────────────────────────────────────
# One Lambda function per specialist agent.
# Deploy lambda_handlers.py as individual functions (or one dispatcher).
LAMBDA_ARNS = {
    "s3":            f"arn:aws:lambda:eu-west-1:943086490726:function:bedrock-s3-agent",
    "iam":           f"arn:aws:lambda:eu-west-1:943086490726:function:bedrock-iam-agent",
    "observability": f"arn:aws:lambda:eu-west-1:943086490726:function:bedrock-observability-agent",
    "compute":       f"arn:aws:lambda:eu-west-1:943086490726:function:bedrock-compute-agent",
    "vpc":           f"arn:aws:lambda:eu-west-1:943086490726:function:bedrock-vpc-agent",
}
# ── Agent Instructions ────────────────────────────────────────────────────────
AGENT_INSTRUCTIONS = {
    "s3": """\
You are the S3 Storage Agent, a specialist in Amazon S3.
You manage: bucket creation, deletion, tagging, versioning, ACLs,
lifecycle policies, replication rules, and cost reporting.

Rules:
- Always enable versioning on new buckets unless explicitly told not to.
- Apply 90-day lifecycle expiry by default on non-production buckets.
- Block all public access unless the requester explicitly overrides this.
- Before deleting a bucket, confirm it is empty.
- After every action, return a JSON summary of what changed.
""",
    "iam": """\
You are the IAM Agent, a specialist in AWS Identity and Access Management.
You manage: roles, policies, users, groups, permission boundaries,
and Service Control Policies.

Rules:
- Apply least-privilege. Never attach AdministratorAccess unless explicitly required.
- For cross-service permissions, create a new role rather than broadening an existing one.
- Always return the ARN of any resource you create.
- When asked by another agent to create a permission, confirm the principal (who needs it),
  the actions, and the resource before proceeding.
- After granting a permission, return: { "status": "granted", "role_arn": "...", "policy_arn": "..." }
""",
    "observability": """\
You are the Observability Agent, a specialist in AWS monitoring and logging.
You manage: CloudWatch dashboards, alarms, log groups, log subscriptions,
X-Ray tracing, CloudTrail, Config rules, and AWS Health.

Rules:
- Create a CloudWatch alarm for every new compute or network resource.
- Enable CloudTrail in all regions by default.
- Set log group retention to 90 days unless specified otherwise.
- Return a summary of all resources created and their ARNs.
""",
    "compute": """\
You are the Compute Agent, a specialist in AWS compute services.
You manage: EC2 instances, Auto Scaling groups, Launch Templates,
ECS clusters and services, Lambda functions, and AMI management.

Rules:
- Never launch instances in the default VPC.
- Always use the latest patched AMI for the requested OS family.
- Attach an IAM instance profile before launching an EC2 instance.
- Enable detailed monitoring on all EC2 instances.
- After launching, confirm the instance ID, state, and private IP.

IMPORTANT: If you need a VPC or subnet before launching, request it from the VPC Agent.
If you need an IAM instance profile, request it from the IAM Agent.
""",
    "vpc": """\
You are the VPC Agent, a specialist in AWS networking.
You manage: VPCs, subnets, route tables, internet gateways, NAT gateways,
VPC peering, security groups, and Network ACLs.

Rules:
- Use /16 CIDR for new VPCs and /24 for subnets by default.
- Always create at least two AZs worth of subnets (public + private).
- Enable VPC Flow Logs on every new VPC.
- Apply least-privilege security group rules (no 0.0.0.0/0 ingress on SSH).

IMPORTANT DEPENDENCY: Before enabling VPC Flow Logs, you MUST request the IAM Agent
to create a flow-logs delivery role. Wait for confirmation and the role ARN before proceeding.
Signal this as: { "requires_iam": true, "action": "create_vpc_flow_logs_role", "vpc_id": "..." }
""",
    "super": """\
You are the Cloud Infrastructure Super Agent. You coordinate five specialist agents:

  1. S3 Agent          — storage, buckets, lifecycle policies
  2. IAM Agent         — roles, policies, permissions
  3. Observability Agent — CloudWatch, alarms, logging, tracing
  4. Compute Agent     — EC2, ECS, Lambda, Auto Scaling
  5. VPC Agent         — VPCs, subnets, security groups, routing

Routing rules:
- Analyse every incoming request and decide which specialists to involve.
- For independent tasks, invoke specialists IN PARALLEL.
- For dependent tasks (e.g., VPC needs an IAM role for Flow Logs), orchestrate the
  correct sequence: resolve dependencies first, then continue.
- Always confirm a dependency is resolved before proceeding (check for "status": "granted").
- After all specialists respond, consolidate their outputs into a single structured report:
    • Executive Summary
    • Actions taken per domain
    • Resources created (with ARNs)
    • Pending items or warnings

Dependency map (check for these automatically):
  VPC Flow Logs  → needs IAM role        → call IAM Agent first
  EC2 launch     → needs VPC + IAM role  → call VPC Agent and IAM Agent first
  New service    → needs alarms          → call Observability Agent after provisioning

Never reveal internal routing steps in the final answer unless the user asks.
""",
}
yum install python* [200~"""
config.py — Central configuration for the AWS Bedrock Multi-Agent System.
Edit the values in this file before running setup_agents.py.
"""
import os
# ── AWS Settings ──────────────────────────────────────────────────────────────
AWS_REGION          = os.environ.get("AWS_REGION", "eu-west-1")
AWS_ACCOUNT_ID      = os.environ.get("AWS_ACCOUNT_ID", "943086490726")   # ← replace
BEDROCK_MODEL_ID    = "anthropic.claude-sonnet-4-6"     # cross-region inference
# ── IAM Role ─────────────────────────────────────────────────────────────────
# A single IAM role that all agents assume.  The role must have:
#   - AmazonBedrockFullAccess
#   - AWSLambdaRole
# Create it manually and paste the ARN here (or set the env var).
BEDROCK_AGENT_ROLE_ARN = os.environ.get(
    "BEDROCK_AGENT_ROLE_ARN",
    f"arn:aws:iam::943086490726:role/BedrockAgentRole",   # ← replace
)
# ── Lambda ARNs ───────────────────────────────────────────────────────────────
# One Lambda function per specialist agent.
# Deploy lambda_handlers.py as individual functions (or one dispatcher).
LAMBDA_ARNS = {
    "s3":            f"arn:aws:lambda:eu-west-1:943086490726:function:bedrock-s3-agent",
    "iam":           f"arn:aws:lambda:eu-west-1:943086490726:function:bedrock-iam-agent",
    "observability": f"arn:aws:lambda:eu-west-1:943086490726:function:bedrock-observability-agent",
    "compute":       f"arn:aws:lambda:eu-west-1:943086490726:function:bedrock-compute-agent",
    "vpc":           f"arn:aws:lambda:eu-west-1:943086490726:function:bedrock-vpc-agent",
}
# ── Agent Instructions ────────────────────────────────────────────────────────
AGENT_INSTRUCTIONS = {
    "s3": """\
You are the S3 Storage Agent, a specialist in Amazon S3.
You manage: bucket creation, deletion, tagging, versioning, ACLs,
lifecycle policies, replication rules, and cost reporting.

Rules:
- Always enable versioning on new buckets unless explicitly told not to.
- Apply 90-day lifecycle expiry by default on non-production buckets.
- Block all public access unless the requester explicitly overrides this.
- Before deleting a bucket, confirm it is empty.
- After every action, return a JSON summary of what changed.
""",
    "iam": """\
You are the IAM Agent, a specialist in AWS Identity and Access Management.
You manage: roles, policies, users, groups, permission boundaries,
and Service Control Policies.

Rules:
- Apply least-privilege. Never attach AdministratorAccess unless explicitly required.
- For cross-service permissions, create a new role rather than broadening an existing one.
- Always return the ARN of any resource you create.
- When asked by another agent to create a permission, confirm the principal (who needs it),
  the actions, and the resource before proceeding.
- After granting a permission, return: { "status": "granted", "role_arn": "...", "policy_arn": "..." }
""",
    "observability": """\
You are the Observability Agent, a specialist in AWS monitoring and logging.
You manage: CloudWatch dashboards, alarms, log groups, log subscriptions,
X-Ray tracing, CloudTrail, Config rules, and AWS Health.

Rules:
- Create a CloudWatch alarm for every new compute or network resource.
- Enable CloudTrail in all regions by default.
- Set log group retention to 90 days unless specified otherwise.
- Return a summary of all resources created and their ARNs.
""",
    "compute": """\
You are the Compute Agent, a specialist in AWS compute services.
You manage: EC2 instances, Auto Scaling groups, Launch Templates,
ECS clusters and services, Lambda functions, and AMI management.

Rules:
- Never launch instances in the default VPC.
- Always use the latest patched AMI for the requested OS family.
- Attach an IAM instance profile before launching an EC2 instance.
- Enable detailed monitoring on all EC2 instances.
- After launching, confirm the instance ID, state, and private IP.

IMPORTANT: If you need a VPC or subnet before launching, request it from the VPC Agent.
If you need an IAM instance profile, request it from the IAM Agent.
""",
    "vpc": """\
You are the VPC Agent, a specialist in AWS networking.
You manage: VPCs, subnets, route tables, internet gateways, NAT gateways,
VPC peering, security groups, and Network ACLs.

Rules:
- Use /16 CIDR for new VPCs and /24 for subnets by default.
- Always create at least two AZs worth of subnets (public + private).
- Enable VPC Flow Logs on every new VPC.
- Apply least-privilege security group rules (no 0.0.0.0/0 ingress on SSH).

IMPORTANT DEPENDENCY: Before enabling VPC Flow Logs, you MUST request the IAM Agent
to create a flow-logs delivery role. Wait for confirmation and the role ARN before proceeding.
Signal this as: { "requires_iam": true, "action": "create_vpc_flow_logs_role", "vpc_id": "..." }
""",
    "super": """\
You are the Cloud Infrastructure Super Agent. You coordinate five specialist agents:

  1. S3 Agent          — storage, buckets, lifecycle policies
  2. IAM Agent         — roles, policies, permissions
  3. Observability Agent — CloudWatch, alarms, logging, tracing
  4. Compute Agent     — EC2, ECS, Lambda, Auto Scaling
  5. VPC Agent         — VPCs, subnets, security groups, routing

Routing rules:
- Analyse every incoming request and decide which specialists to involve.
- For independent tasks, invoke specialists IN PARALLEL.
- For dependent tasks (e.g., VPC needs an IAM role for Flow Logs), orchestrate the
  correct sequence: resolve dependencies first, then continue.
- Always confirm a dependency is resolved before proceeding (check for "status": "granted").
- After all specialists respond, consolidate their outputs into a single structured report:
    • Executive Summary
    • Actions taken per domain
    • Resources created (with ARNs)
    • Pending items or warnings

Dependency map (check for these automatically):
  VPC Flow Logs  → needs IAM role        → call IAM Agent first
  EC2 launch     → needs VPC + IAM role  → call VPC Agent and IAM Agent first
  New service    → needs alarms          → call Observability Agent after provisioning

Never reveal internal routing steps in the final answer unless the user asks.
""",
}
ifconfig
vi /etc/ssh/sshd_config
cd /etc/ssh
ls
vi ssh_config
vi sshd_config.d/
cd sshd_config.d/
ls
cd //
cd  -
cd ..
vi sshd_config
systemctl restart sshd
vi sshd_config
systemctl restart sshd
clear
yum install python*
yum install python* --allowerasing
yum install python* --allowerasing --skip-broken
df -h
pip
pip install boto3
pip show boto2
pip show boto3
aws configure
aws --version
sudo yum install python3-dateutil -y
yum install python3-dateutil -y
aws --version
yum update python3-dateutil -y
python3 -m pip install python-dateutil
aws --version
 yum install python3-dateutil -y
aws remove awscli
yum remove awscli
yum install awscli*
aws configure
yum install boto3*
yum install boto*
pip install boto*
python3 -m pip install boto3
aws configure
aws
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
sudo ./aws/install
aws --version
yum remove awscli
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
sudo ./aws/install
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
sudo ./aws/install --bin-dir /usr/local/bin --install-dir /usr/local/aws-cli --update
aws 
aws configure
[200~curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"~
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
sudo ./aws/install
./aws/install --update
aws
aws --version
hash -r
/usr/local/bin/aws --version
sudo ln -s /usr/local/bin/aws /usr/bin/aws
hash -r
aws --version
aws configure
python3 setup_agents.py 
vi setup_agents.py 
python3 setup_agents.py 
vi setup_agents.py 
vi config.py 
python3 setup_agents.py 
vi setup_agents.py 
python3 setup_agents.py 
streamlit run streamlit_app.py
pip install steamlist
pip install steamlit
pip install streamlit
python3 -m venv ~/streamlit-venv
source ~/streamlit-venv/bin/activate
python -m pip install --upgrade pip
pip install streamlit boto3
streamlit run app.py --server.address 0.0.0.0 --server.port 8501
ls
streamlit run streamlit_app.py --server.address 0.0.0.0 --server.port 8501
streamlit run streamlit_app.py 
vi streamlit_app.py 
streamlit run streamlit_app.py 
vi streamlit_app.py 
streamlit run streamlit_app.py 
ls
python setup_agents.py 
streamlit run streamlit_app.py 
exit
curl http://localhost:8501
cat agent_ids.json 
python3 - <<'EOF'
import json, boto3
ids = json.load(open('/root/agent_ids.json'))
sa = ids["super_agent"]
rt = boto3.client("bedrock-agent-runtime", region_name=ids["region"])
resp = rt.invoke_agent(agentId=sa["agent_id"], agentAliasId=sa["alias_id"],
                       sessionId="t1", inputText="List all S3 buckets in my account")
got = False
for e in resp["completion"]:
    if "chunk" in e:
        got = True
        print("CHUNK:", e["chunk"]["bytes"].decode())
print("--- any chunks?", got)
EOF

streamlit run streamlit_app.py 
ls
cd streamlit-venv/
ls
cd ..
source ~/streamlit-venv/bin/activate
streamlit streamlit_app.py 
streamlit run streamlit_app.py 
cat agent_ids.json 
python3 - <<'EOF'
import json, boto3
ids = json.load(open('/root/agent_ids.json'))
region = ids.get("region")
print("region in file:", region)
c = boto3.client("bedrock-agent", region_name=region)

sa = ids["super_agent"]
print("\nsuper_agent in file:", sa)
try:
    st = c.get_agent(agentId=sa["agent_id"])["agent"]["agentStatus"]
    print("  super agent EXISTS, status:", st)
except Exception as e:
    print("  super agent MISSING:", e)

print("  aliases that actually exist for this agent:")
try:
    for a in c.list_agent_aliases(agentId=sa["agent_id"])["agentAliasSummaries"]:
        print("    ", a["agentAliasId"], a["agentAliasStatus"], a["agentAliasName"])
except Exception as e:
    print("    cannot list:", e)
EOF

[200~python3 - <<'EOF'
import json, boto3
ids = json.load(open('/root/agent_ids.json'))
sa = ids["super_agent"]
rt = boto3.client("bedrock-agent-runtime", region_name=ids["region"])
print("Invoking agent", sa["agent_id"], "alias", sa["alias_id"])
resp = rt.invoke_agent(
    agentId=sa["agent_id"],
    agentAliasId=sa["alias_id"],
    sessionId="test-session-1",
    inputText="Hi, list S3 buckets",
)
for event in resp["completion"]:
    if "chunk" in event:
        print(event["chunk"]["bytes"].decode())

python3 - <<'EOF'
import json, boto3
ids = json.load(open('/root/agent_ids.json'))
sa = ids["super_agent"]
rt = boto3.client("bedrock-agent-runtime", region_name=ids["region"])
print("Invoking agent", sa["agent_id"], "alias", sa["alias_id"])
resp = rt.invoke_agent(
    agentId=sa["agent_id"],
    agentAliasId=sa["alias_id"],
    sessionId="test-session-1",
    inputText="Hi, list S3 buckets",
)
for event in resp["completion"]:
    if "chunk" in event:
        print(event["chunk"]["bytes"].decode())
EOF

python3 - <<'EOF'
import json, boto3
ids = json.load(open('/root/agent_ids.json'))
sa = ids["super_agent"]
rt = boto3.client("bedrock-agent-runtime", region_name=ids["region"])
resp = rt.invoke_agent(agentId=sa["agent_id"], agentAliasId=sa["alias_id"],
                       sessionId="test-2", inputText="Hi, list S3 buckets")
for e in resp["completion"]:
    if "chunk" in e: print(e["chunk"]["bytes"].decode())
EOF

python3 - <<'EOF'
import json, boto3, time
ids = json.load(open('/root/agent_ids.json'))
region = ids["region"]
NEW_MODEL = "eu.amazon.nova-2-lite-v1:0"
c = boto3.client("bedrock-agent", region_name=region)

agents = [ids["super_agent"]["agent_id"]] + [v["agent_id"] for v in ids["sub_agents"].values()]
for aid in agents:
    a = c.get_agent(agentId=aid)["agent"]
    kwargs = dict(
        agentId=aid,
        agentName=a["agentName"],
        foundationModel=NEW_MODEL,
        instruction=a["instruction"],
        agentResourceRoleArn=a["agentResourceRoleArn"],
    )
    if a.get("agentCollaboration") and a["agentCollaboration"] != "DISABLED":
        kwargs["agentCollaboration"] = a["agentCollaboration"]
    c.update_agent(**kwargs)
    # wait until NOT_PREPARED then prepare
    while c.get_agent(agentId=aid)["agent"]["agentStatus"] not in ("NOT_PREPARED","PREPARED"):
        time.sleep(3)
    c.prepare_agent(agentId=aid)
    print("updated + preparing:", aid, "->", NEW_MODEL)
print("Done. Wait ~30s for PREPARED, then test invoke.")
EOF

streamlit run streamlit_app.py 
grep BEDROCK_MODEL_ID /root/config.py        # must show: eu.amazon.nova-2-lite-v1:0
grep 'agentCollaboration="SUPERVISOR"' /root/setup_agents.py   # must print a line
python3 - <<'EOF'
import json, boto3
ids = json.load(open('/root/agent_ids.json'))
c = boto3.client("bedrock-agent", region_name=ids["region"])
agents = [ids["super_agent"]["agent_id"]] + [v["agent_id"] for v in ids["sub_agents"].values()]
for aid in agents:
    try:
        c.delete_agent(agentId=aid, skipResourceInUseCheck=True)
        print("deleted", aid)
    except Exception as e:
        print("skip", aid, e)
EOF

vi config.py 
python3 setup_agents.py
python3 - <<'EOF'
import json, boto3
ids = json.load(open('/root/agent_ids.json'))
sa = ids["super_agent"]
rt = boto3.client("bedrock-agent-runtime", region_name=ids["region"])
resp = rt.invoke_agent(agentId=sa["agent_id"], agentAliasId=sa["alias_id"],
                       sessionId="test-final", inputText="List all S3 buckets")
for e in resp["completion"]:
    if "chunk" in e: print(e["chunk"]["bytes"].decode())
EOF

python3 - <<'EOF'
import json, boto3
ids = json.load(open('/root/agent_ids.json'))
c = boto3.client("bedrock-agent", region_name=ids["region"])
agents = [ids["super_agent"]["agent_id"]] + [v["agent_id"] for v in ids["sub_agents"].values()]
for aid in agents:
    try:
        c.delete_agent(agentId=aid, skipResourceInUseCheck=True)
        print("deleted", aid)
    except Exception as e:
        print("skip", aid, e)
EOF

grep BEDROCK_MODEL_ID /root/config.py
sed -i 's|"amazon.nova-lite-v1:0"|"eu.amazon.nova-lite-v1:0"|' /root/config.py
grep BEDROCK_MODEL_ID /root/config.py
aws bedrock list-inference-profiles --region eu-west-1   --query "inferenceProfileSummaries[?contains(inferenceProfileId,'nova-lite')].inferenceProfileId" --output text
aws bedrock list-inference-profiles --region eu-west-1   --query "inferenceProfileSummaries[?contains(inferenceProfileId,'nova-lite')].inferenceProfileId" --output text
python3 -c "import json,boto3;ids=json.load(open('/root/agent_ids.json'));c=boto3.client('bedrock-agent',region_name=ids['region']);[c.delete_agent(agentId=a,skipResourceInUseCheck=True) for a in [ids['super_agent']['agent_id']]+[v['agent_id'] for v in ids['sub_agents'].values()]]"
python3 setup_agents.py
streamlit run streamlit_app.py 
python3 - <<'EOF'
import json, boto3, time
ids = json.load(open('/root/agent_ids.json'))
sa = ids["super_agent"]
rt = boto3.client("bedrock-agent-runtime", region_name=ids["region"])
for attempt in range(1, 4):
    try:
        resp = rt.invoke_agent(agentId=sa["agent_id"], agentAliasId=sa["alias_id"],
                               sessionId=f"retry-{attempt}", inputText="List all S3 buckets in my account")
        for e in resp["completion"]:
            if "chunk" in e: print(e["chunk"]["bytes"].decode())
        print("--- SUCCESS on attempt", attempt)
        break
    except Exception as ex:
        print(f"attempt {attempt} failed: {ex}")



python3 - <<'EOF'
import json, boto3, time
ids = json.load(open('/root/agent_ids.json'))
sa = ids["super_agent"]
rt = boto3.client("bedrock-agent-runtime", region_name=ids["region"])
for attempt in range(1, 4):
    try:
        resp = rt.invoke_agent(agentId=sa["agent_id"], agentAliasId=sa["alias_id"],
                               sessionId=f"retry-{attempt}", inputText="List all S3 buckets in my account")
        for e in resp["completion"]:
            if "chunk" in e: print(e["chunk"]["bytes"].decode())
        print("--- SUCCESS on attempt", attempt)
        break
    except Exception as ex:
        print(f"attempt {attempt} failed: {ex}")
        time.sleep(5)
EOF

# Does the S3 Lambda exist?
aws lambda get-function --function-name bedrock-s3-agent --region eu-west-1   --query "Configuration.{name:FunctionName,state:State,runtime:Runtime}" --output table
# Check its recent logs for errors
aws logs tail /aws/lambda/bedrock-s3-agent --region eu-west-1 --since 10m
python3 - <<'EOF'
import json, boto3
ids = json.load(open('/root/agent_ids.json'))
s3 = ids["sub_agents"]["s3"]
rt = boto3.client("bedrock-agent-runtime", region_name=ids["region"])
try:
    resp = rt.invoke_agent(agentId=s3["agent_id"], agentAliasId=s3["alias_id"],
                           sessionId="s3-direct", inputText="List all S3 buckets")
    for e in resp["completion"]:
        if "chunk" in e: print(e["chunk"]["bytes"].decode())
    print("--- S3 agent works directly")
except Exception as ex:
    print("S3 agent direct FAILED:", ex)
EOF

