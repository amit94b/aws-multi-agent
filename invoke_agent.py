"""
invoke_agent.py
---------------
Runtime client — sends tasks to the Super Agent and streams the response.

Usage:
    # Interactive session
    python invoke_agent.py

    # Single task from command line
    python invoke_agent.py --task "Create a VPC called prod-vpc with flow logs enabled"

    # JSON output (useful for CI/CD pipelines)
    python invoke_agent.py --task "List all S3 buckets" --json

    # Non-interactive (pipe input)
    echo "List all S3 buckets" | python invoke_agent.py --stdin

    # Run demo tasks
    python invoke_agent.py --demo

Prerequisites:
    agent_ids.json must exist (run setup_agents.py first)
"""

from __future__ import annotations

import argparse
import boto3
import json
import logging
import os
import sys
import time
import uuid
from botocore.exceptions import ClientError
from config import AWS_REGION

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

IDS_FILE = "agent_ids.json"

# ── Retry config ──────────────────────────────────────────────────────────────
MAX_RETRIES    = 3
RETRY_BASE_SEC = 2   # exponential backoff: 2, 4, 8 seconds


def load_ids(ids_file: str = IDS_FILE) -> dict:
    if not os.path.exists(ids_file):
        log.error("'%s' not found. Run setup_agents.py first.", ids_file)
        sys.exit(1)
    with open(ids_file) as f:
        return json.load(f)


def get_runtime_client(region: str | None = None) -> boto3.client:
    return boto3.client("bedrock-agent-runtime", region_name=region or AWS_REGION)


# ── Dependency-aware task routing ─────────────────────────────────────────────

def detect_dependency(task: str) -> list[str]:
    """
    Heuristic: flag tasks that will require a multi-step dependency chain
    so we can print a heads-up to the user.
    The Super Agent handles actual sequencing; this is just for display.
    """
    task_lower = task.lower()
    hints = []
    if any(kw in task_lower for kw in ["vpc", "subnet", "security group", "nat"]):
        hints.append("VPC Agent")
        if "flow log" in task_lower:
            hints.append("IAM Agent (required for Flow Logs role → then VPC Agent)")
    if any(kw in task_lower for kw in ["ec2", "instance", "launch", "compute", "asg", "auto scaling"]):
        hints.append("Compute Agent")
        hints.append("VPC Agent (subnet required) + IAM Agent (instance profile required)")
    if any(kw in task_lower for kw in ["s3", "bucket", "storage", "lifecycle"]):
        hints.append("S3 Agent")
    if any(kw in task_lower for kw in ["role", "policy", "permission", "iam"]):
        hints.append("IAM Agent")
    if any(kw in task_lower for kw in ["alarm", "cloudwatch", "cloudtrail", "log", "metric", "monitor", "dashboard"]):
        hints.append("Observability Agent")
    return hints


# ── Stream invocation with retry ──────────────────────────────────────────────

def invoke(
    task: str,
    agent_id: str,
    alias_id: str,
    session_id: str | None = None,
    json_output: bool = False,
    runtime_client=None,
) -> str:
    """
    Invoke the Super Agent and stream its response.
    Returns the final text response.
    Retries up to MAX_RETRIES times on throttling errors.
    """
    session_id = session_id or str(uuid.uuid4())
    runtime = runtime_client or get_runtime_client()

    # Print routing hints (unless JSON mode)
    if not json_output:
        hints = detect_dependency(task)
        if hints:
            print("\n[Routing analysis]")
            for h in hints:
                print(f"  → {h}")
            print()
        print(f"[Session: {session_id}]")
        print("─" * 60)

    full_response: list[str] = []
    trace_steps:   list[str] = []
    result_meta:   dict = {"session_id": session_id, "traces": []}

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = runtime.invoke_agent(
                agentId=agent_id,
                agentAliasId=alias_id,
                sessionId=session_id,
                inputText=task,
                enableTrace=True,
            )

            for event in resp["completion"]:

                # ── Agent answer chunk ──
                if "chunk" in event:
                    text = event["chunk"]["bytes"].decode("utf-8")
                    if not json_output:
                        print(text, end="", flush=True)
                    full_response.append(text)

                # ── Trace events ──
                elif "trace" in event:
                    trace = event["trace"].get("trace", {})

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
                                inp        = collab.get("input", {}).get("text", "")
                                display_inp = (inp[:120] + "…") if len(inp) > 120 else inp
                                if not json_output:
                                    print(f"\n  ↳ [Delegating to {agent_name}]: {display_inp}\n")
                                trace_steps.append(f"Delegated to {agent_name}")
                                result_meta["traces"].append({
                                    "type": "delegation",
                                    "agent": agent_name,
                                    "input": inp[:400],
                                })

                            if "actionGroupInvocationInput" in inv:
                                ag = inv["actionGroupInvocationInput"]
                                fn = ag.get("function", ag.get("actionGroupName", "?"))
                                if not json_output:
                                    print(f"\n  ⚙  [Tool: {fn}]\n")
                                result_meta["traces"].append({"type": "tool", "function": fn})

                        if "observation" in orch:
                            obs = orch["observation"]
                            if "agentCollaboratorObservation" in obs:
                                collab_obs = obs["agentCollaboratorObservation"]
                                agent_name = collab_obs.get("agentCollaboratorName", "sub-agent")
                                out        = collab_obs.get("output", {}).get("text", "")
                                display_out = (out[:300] + "…") if len(out) > 300 else out
                                if not json_output:
                                    print(f"\n  ✅ [{agent_name} responded]: {display_out}\n")
                                result_meta["traces"].append({
                                    "type": "response",
                                    "agent": agent_name,
                                    "output": out[:800],
                                })

                # ── Guardrail events ──
                elif "guardrailTrace" in event:
                    if not json_output:
                        print("\n[Guardrail triggered — request blocked or modified]")

            # Success — break retry loop
            break

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "ThrottlingException" and attempt < MAX_RETRIES:
                wait = RETRY_BASE_SEC ** attempt
                log.warning("ThrottlingException — retrying in %ds (attempt %d/%d)", wait, attempt, MAX_RETRIES)
                time.sleep(wait)
                continue
            if not json_output:
                print(f"\n❌ AWS Error: {e}", file=sys.stderr)
            else:
                print(json.dumps({"error": str(e), "session_id": session_id}))
            return ""

    final_text = "".join(full_response)

    if json_output:
        print(json.dumps({
            "response":   final_text,
            "session_id": session_id,
            "traces":     result_meta["traces"],
        }, indent=2))
    else:
        print("\n" + "─" * 60)
        if trace_steps:
            print("\n[Execution trace]")
            for step in trace_steps:
                print(f"  {step}")

    return final_text


# ── Interactive REPL ──────────────────────────────────────────────────────────

def interactive_session(agent_id: str, alias_id: str, runtime_client) -> None:
    session_id = str(uuid.uuid4())
    print("=" * 60)
    print("  AWS Cloud Infrastructure Super Agent")
    print("  Interactive Mode — Powered by Amazon Bedrock")
    print("=" * 60)
    print("Commands: 'new' (new session) | 'exit' / Ctrl-C to quit")
    print("─" * 60)

    while True:
        try:
            task = input("\n> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye.")
            break

        if not task:
            continue
        if task.lower() in ("exit", "quit", "q"):
            break
        if task.lower() == "new":
            session_id = str(uuid.uuid4())
            print(f"[New session: {session_id}]")
            continue
        if task.lower() in ("help", "?"):
            print("""
Available commands:
  new      Start a new conversation session
  exit     Quit
  help     Show this help

Sample tasks:
  List all S3 buckets
  Create a VPC called dev-vpc with flow logs
  Create an IAM role for Lambda with S3 read access
  Launch a t3.medium EC2 in subnet-xxx with security group sg-xxx
  Create a CloudWatch CPU alarm for instance i-xxx at 80%
            """)
            continue

        print()
        invoke(task, agent_id, alias_id, session_id, runtime_client=runtime_client)


# ── Demo tasks ────────────────────────────────────────────────────────────────

DEMO_TASKS = [
    # IAM only
    "Create an IAM role called AppServerRole that EC2 can assume, "
    "and attach the AmazonS3ReadOnlyAccess policy to it.",

    # S3 only
    "Create an S3 bucket called my-app-logs-demo with versioning enabled "
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
    parser = argparse.ArgumentParser(
        description="Invoke the AWS Cloud Infrastructure Super Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python invoke_agent.py
  python invoke_agent.py --task "Create a VPC called prod-vpc"
  python invoke_agent.py --task "List all S3 buckets" --json
  python invoke_agent.py --demo
  echo "List running EC2 instances" | python invoke_agent.py --stdin
        """,
    )
    parser.add_argument("--task",     help="Single task to execute")
    parser.add_argument("--demo",     action="store_true", help="Run all demo tasks sequentially")
    parser.add_argument("--stdin",    action="store_true", help="Read task from stdin")
    parser.add_argument("--json",     action="store_true", help="Output response as JSON (useful for CI/CD)")
    parser.add_argument("--ids-file", default=IDS_FILE,   help="Path to agent_ids.json")
    parser.add_argument("--region",   default=None,       help="AWS region override")
    args = parser.parse_args()

    ids    = load_ids(args.ids_file)
    region = args.region or ids.get("region") or AWS_REGION
    runtime = get_runtime_client(region)

    agent_id = ids["super_agent"]["agent_id"]
    alias_id = ids["super_agent"]["alias_id"]

    if args.task:
        invoke(args.task, agent_id, alias_id, json_output=args.json, runtime_client=runtime)

    elif args.stdin:
        task = sys.stdin.read().strip()
        if task:
            invoke(task, agent_id, alias_id, json_output=args.json, runtime_client=runtime)

    elif args.demo:
        for i, task in enumerate(DEMO_TASKS, 1):
            print(f"\n{'='*60}")
            print(f"  DEMO TASK {i}/{len(DEMO_TASKS)}")
            print(f"{'='*60}")
            print(f"  {task[:80]}{'…' if len(task) > 80 else ''}")
            print()
            invoke(task, agent_id, alias_id, json_output=args.json, runtime_client=runtime)
        print("\nAll demo tasks complete.")

    else:
        interactive_session(agent_id, alias_id, runtime)


if __name__ == "__main__":
    main()
