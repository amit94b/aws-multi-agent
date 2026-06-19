"""
invoke_agent.py
---------------
Runtime client — sends tasks to the Super Agent and streams the response.

Usage:
    # Interactive session
    python invoke_agent.py

    # Single task from command line
    python invoke_agent.py --task "Create a VPC called prod-vpc with flow logs enabled"

    # Non-interactive (pipe input)
    echo "List all S3 buckets" | python invoke_agent.py --stdin

Prerequisites:
    agent_ids.json must exist (run setup_agents.py first)
"""

import argparse
import boto3
import json
import logging
import os
import sys
import uuid
from config import AWS_REGION

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

runtime = boto3.client("bedrock-agent-runtime", region_name=AWS_REGION)

IDS_FILE = "agent_ids.json"


def load_ids() -> dict:
    if not os.path.exists(IDS_FILE):
        log.error("'%s' not found. Run setup_agents.py first.", IDS_FILE)
        sys.exit(1)
    with open(IDS_FILE) as f:
        return json.load(f)


# ── Dependency-aware task routing ─────────────────────────────────────────────

def detect_dependency(task: str) -> list[str]:
    """
    Heuristic: flag tasks that will require a multi-step dependency chain
    so we can print a heads-up to the user.
    The Super Agent handles actual sequencing; this is just for display.
    """
    task_lower = task.lower()
    hints = []
    if any(kw in task_lower for kw in ["vpc", "subnet", "security group"]):
        hints.append("VPC Agent")
        if "flow log" in task_lower:
            hints.append("IAM Agent (required for Flow Logs role → then VPC Agent)")
    if any(kw in task_lower for kw in ["ec2", "instance", "launch", "compute", "asg"]):
        hints.append("Compute Agent")
        hints.append("VPC Agent (subnet required) + IAM Agent (instance profile required)")
    if any(kw in task_lower for kw in ["s3", "bucket", "storage"]):
        hints.append("S3 Agent")
    if any(kw in task_lower for kw in ["role", "policy", "permission", "iam"]):
        hints.append("IAM Agent")
    if any(kw in task_lower for kw in ["alarm", "cloudwatch", "cloudtrail", "log", "metric", "monitor"]):
        hints.append("Observability Agent")
    return hints


# ── Stream invocation ─────────────────────────────────────────────────────────

def invoke(task: str, agent_id: str, alias_id: str, session_id: str | None = None) -> str:
    """
    Invoke the Super Agent and stream its response.
    Returns the final text response.
    """
    session_id = session_id or str(uuid.uuid4())

    # Print routing hints
    hints = detect_dependency(task)
    if hints:
        print("\n[Routing analysis]")
        for h in hints:
            print(f"  → {h}")
        print()

    print(f"[Session: {session_id}]")
    print("─" * 60)

    resp = runtime.invoke_agent(
        agentId=agent_id,
        agentAliasId=alias_id,
        sessionId=session_id,
        inputText=task,
        enableTrace=True,
    )

    full_response = []
    trace_steps = []

    for event in resp["completion"]:

        # ── Agent answer chunk ──
        if "chunk" in event:
            text = event["chunk"]["bytes"].decode("utf-8")
            print(text, end="", flush=True)
            full_response.append(text)

        # ── Trace events (shows routing, tool calls, collaborator invocations) ──
        elif "trace" in event:
            trace = event["trace"].get("trace", {})

            # Orchestration steps
            if "orchestrationTrace" in trace:
                orch = trace["orchestrationTrace"]

                if "rationale" in orch:
                    reason = orch["rationale"].get("text", "")
                    if reason:
                        trace_steps.append(f"[Reasoning] {reason[:200]}")

                if "invocationInput" in orch:
                    inv = orch["invocationInput"]
                    if "agentCollaboratorInvocationInput" in inv:
                        collab = inv["agentCollaboratorInvocationInput"]
                        agent_name = collab.get("agentCollaboratorName", "sub-agent")
                        inp = collab.get("input", {}).get("text", "")
                        print(f"\n  ↳ [Delegating to {agent_name}]: {inp[:120]}...\n")
                        trace_steps.append(f"Delegated to {agent_name}")

                    if "actionGroupInvocationInput" in inv:
                        ag = inv["actionGroupInvocationInput"]
                        fn = ag.get("function", ag.get("actionGroupName", "?"))
                        print(f"\n  ⚙  [Tool: {fn}]\n")

                if "observation" in orch:
                    obs = orch["observation"]
                    if "agentCollaboratorObservation" in obs:
                        collab_obs = obs["agentCollaboratorObservation"]
                        agent_name = collab_obs.get("agentCollaboratorName", "sub-agent")
                        out = collab_obs.get("output", {}).get("text", "")[:300]
                        print(f"\n  ✅ [{agent_name} responded]: {out}...\n")

        # ── Guardrail events ──
        elif "guardrailTrace" in event:
            print("\n[Guardrail triggered — request blocked or modified]")

    print("\n" + "─" * 60)

    if trace_steps:
        print("\n[Execution trace]")
        for step in trace_steps:
            print(f"  {step}")

    return "".join(full_response)


# ── Interactive REPL ──────────────────────────────────────────────────────────

def interactive_session(agent_id: str, alias_id: str) -> None:
    session_id = str(uuid.uuid4())
    print("=" * 60)
    print("Cloud Infrastructure Super Agent — Interactive Mode")
    print("Type 'exit' or Ctrl-C to quit | Type 'new' for a new session")
    print("=" * 60)

    while True:
        try:
            task = input("\n> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye.")
            break

        if not task:
            continue
        if task.lower() == "exit":
            break
        if task.lower() == "new":
            session_id = str(uuid.uuid4())
            print(f"[New session: {session_id}]")
            continue

        print()
        invoke(task, agent_id, alias_id, session_id)


# ── Demo tasks ────────────────────────────────────────────────────────────────

DEMO_TASKS = [
    # IAM only
    "Create an IAM role called AppServerRole that EC2 can assume, "
    "and attach the AmazonS3ReadOnlyAccess policy to it.",

    # S3 only
    "Create an S3 bucket called my-app-logs-2024 with versioning enabled "
    "and a 30-day lifecycle expiry.",

    # Dependency: VPC → IAM first
    "Create a VPC called prod-vpc (10.10.0.0/16) with VPC Flow Logs enabled.",

    # Multi-domain: VPC + IAM + Compute + Observability
    "Full environment setup: create a VPC called dev-env-vpc, "
    "launch a t3.medium EC2 instance (Amazon Linux 2) in it, "
    "create a CloudWatch CPU alarm at 80%, "
    "and confirm the setup with a summary report.",
]


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Invoke the Cloud Infrastructure Super Agent")
    parser.add_argument("--task", help="Single task to execute")
    parser.add_argument("--demo", action="store_true", help="Run all demo tasks sequentially")
    parser.add_argument("--stdin", action="store_true", help="Read task from stdin")
    parser.add_argument("--ids-file", default=IDS_FILE)
    args = parser.parse_args()

    ids = load_ids()
    agent_id  = ids["super_agent"]["agent_id"]
    alias_id  = ids["super_agent"]["alias_id"]

    if args.task:
        invoke(args.task, agent_id, alias_id)

    elif args.stdin:
        task = sys.stdin.read().strip()
        invoke(task, agent_id, alias_id)

    elif args.demo:
        for i, task in enumerate(DEMO_TASKS, 1):
            print(f"\n{'='*60}")
            print(f"DEMO TASK {i}/{len(DEMO_TASKS)}")
            print(f"{'='*60}")
            invoke(task, agent_id, alias_id)
        print("\nAll demo tasks complete.")

    else:
        interactive_session(agent_id, alias_id)


if __name__ == "__main__":
    main()

