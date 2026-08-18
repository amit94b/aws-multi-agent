"""
streamlit_app.py — AWS DevOps Command Center
--------------------------------------------
A premium Streamlit UI for the AWS Bedrock Multi-Agent System.

Run:
    pip install streamlit boto3
    streamlit run streamlit_app.py

Prerequisites:
    agent_ids.json must exist (run setup_agents.py first)
    AWS credentials must be configured (env vars or ~/.aws/credentials)
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path

import boto3
import streamlit as st
from botocore.exceptions import ClientError, NoCredentialsError

# ── Page config (must be the very first Streamlit call) ──────────────────────
st.set_page_config(
    page_title="AWS DevOps Command Center",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Constants ─────────────────────────────────────────────────────────────────
IDS_FILE = Path(__file__).parent / "agent_ids.json"

AGENT_META = {
    "s3":            {"label": "S3 Storage",    "icon": "🪣", "color": "#FF9900"},
    "iam":           {"label": "IAM",            "icon": "🔐", "color": "#DD344C"},
    "observability": {"label": "Observability",  "icon": "📊", "color": "#7AA116"},
    "compute":       {"label": "Compute",        "icon": "💻", "color": "#1A9C3E"},
    "vpc":           {"label": "VPC / Network",  "icon": "🌐", "color": "#0073BB"},
    "database":      {"label": "Database",       "icon": "🗄️", "color": "#336791"},
    "finops":        {"label": "FinOps",         "icon": "💰", "color": "#1A9C3E"},
    "super":         {"label": "Super Agent",    "icon": "🤖", "color": "#FF9900"},
}

QUICK_TASKS = {
    "🪣 List all S3 buckets":
        "List all S3 buckets in the account with their regions and sizes.",
    "🔐 Create IAM role for Lambda":
        "Create an IAM role called LambdaExecutionRole that Lambda can assume with AmazonS3ReadOnlyAccess attached.",
    "🌐 Create a new VPC":
        "Create a VPC called dev-vpc (10.0.0.0/16) with public and private subnets across two AZs.",
    "🌐 VPC + Flow Logs":
        "Create a VPC called prod-vpc with VPC Flow Logs enabled. Handle any IAM role dependencies automatically.",
    "💻 Describe running instances":
        "List all running EC2 instances with their types, private IPs, AZs, and states.",
    "📊 Create CPU alarm":
        "Create a CloudWatch alarm called high-cpu-alarm for metric CPUUtilization in namespace AWS/EC2 with threshold 80.",
    "📋 Create CloudWatch log group":
        "Create a CloudWatch log group called /app/backend with 30-day retention.",
    "☁️ Full environment setup":
        "Full DevOps environment: create a VPC called staging-env, launch a t3.medium Amazon Linux 2 instance, "
        "set up a CloudWatch CPU alarm at 75%, create a log group /app/staging, and give me a full summary report.",
    "🔐 EC2 instance profile":
        "Create an IAM role called EC2AppRole that EC2 can assume, then attach AmazonS3ReadOnlyAccess and AmazonDynamoDBReadOnlyAccess to it.",
    "🗄️ Create DynamoDB table":
        "Create a DynamoDB table called Users with partition key UserId of type S and PAY_PER_REQUEST billing mode.",
    "💰 Cost forecast":
        "What is the estimated cost forecast for this month based on current usage?",
}


# ── Premium CSS ───────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* ── Import Google Fonts ── */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

/* ── Root variables ── */
:root {
    --bg-primary:    #0a0e1a;
    --bg-secondary:  #0f1520;
    --bg-card:       #131929;
    --bg-hover:      #1a2235;
    --border:        #1e2d45;
    --border-active: #2a4070;
    --aws-orange:    #FF9900;
    --aws-blue:      #146eb4;
    --aws-dark:      #0f2847;
    --text-primary:  #e8edf5;
    --text-secondary:#8b9ab4;
    --text-muted:    #4a5568;
    --success:       #22c55e;
    --warning:       #f59e0b;
    --error:         #ef4444;
    --info:          #3b82f6;
    --purple:        #8b5cf6;
    --radius:        10px;
    --radius-lg:     16px;
    --shadow:        0 4px 24px rgba(0,0,0,0.4);
    --shadow-sm:     0 2px 8px rgba(0,0,0,0.3);
}

/* ── Global reset & base ── */
html, body, [data-testid="stAppViewContainer"] {
    background: var(--bg-primary) !important;
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    color: var(--text-primary);
}

/* ── Sidebar ── */
[data-testid="stSidebar"] {
    background: var(--bg-secondary) !important;
    border-right: 1px solid var(--border) !important;
}
[data-testid="stSidebar"] .stMarkdown { color: var(--text-primary); }

/* ── Remove default Streamlit padding ── */
.block-container {
    padding-top: 1.5rem !important;
    padding-bottom: 1rem !important;
    max-width: 1400px !important;
}

/* ── Header bar ── */
.header-bar {
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 16px 20px;
    background: linear-gradient(135deg, var(--bg-card) 0%, #0f1b2d 100%);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    margin-bottom: 20px;
    position: relative;
    overflow: hidden;
}
.header-bar::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 2px;
    background: linear-gradient(90deg, var(--aws-orange), var(--aws-blue), var(--aws-orange));
    background-size: 200% 100%;
    animation: shimmer 3s linear infinite;
}
@keyframes shimmer {
    0% { background-position: 200% 0; }
    100% { background-position: -200% 0; }
}
.header-logo {
    width: 44px; height: 44px;
    background: linear-gradient(135deg, var(--aws-orange), #cc7a00);
    border-radius: 10px;
    display: flex; align-items: center; justify-content: center;
    font-size: 22px;
    flex-shrink: 0;
    box-shadow: 0 4px 12px rgba(255,153,0,0.3);
}
.header-title {
    font-size: 1.3rem;
    font-weight: 700;
    color: var(--text-primary);
    letter-spacing: -0.02em;
}
.header-subtitle {
    font-size: 0.78rem;
    color: var(--text-secondary);
    margin-top: 1px;
}
.header-badge {
    margin-left: auto;
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 0.72rem;
    color: var(--aws-orange);
    background: rgba(255,153,0,0.08);
    border: 1px solid rgba(255,153,0,0.2);
    border-radius: 20px;
    padding: 4px 10px;
}

/* ── Metric cards ── */
.metrics-row {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 12px;
    margin-bottom: 20px;
}
.metric-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 14px 16px;
    text-align: center;
    transition: border-color 0.2s, transform 0.2s;
}
.metric-card:hover {
    border-color: var(--border-active);
    transform: translateY(-1px);
}
.metric-icon {
    font-size: 1.4rem;
    margin-bottom: 4px;
}
.metric-value {
    font-size: 1.6rem;
    font-weight: 700;
    color: var(--text-primary);
    font-family: 'JetBrains Mono', monospace;
    line-height: 1;
}
.metric-label {
    font-size: 0.7rem;
    color: var(--text-secondary);
    margin-top: 4px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
}

/* ── Chat bubbles ── */
.user-msg {
    display: flex;
    justify-content: flex-end;
    margin: 10px 0 6px 0;
}
.user-bubble {
    background: linear-gradient(135deg, #1a3a6b, #1e4080);
    border: 1px solid #2563eb;
    border-radius: 16px 16px 4px 16px;
    padding: 12px 16px;
    max-width: 80%;
    color: var(--text-primary);
    font-size: 0.9rem;
    line-height: 1.6;
    box-shadow: var(--shadow-sm);
}
.user-meta {
    font-size: 0.68rem;
    color: var(--text-muted);
    text-align: right;
    margin-bottom: 4px;
    margin-right: 2px;
}
.agent-msg {
    margin: 6px 0 10px 0;
}
.agent-meta {
    font-size: 0.68rem;
    color: var(--text-muted);
    margin-bottom: 4px;
    display: flex;
    align-items: center;
    gap: 5px;
}
.agent-bubble {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 4px 16px 16px 16px;
    padding: 14px 18px;
    color: var(--text-primary);
    font-size: 0.9rem;
    line-height: 1.7;
    box-shadow: var(--shadow-sm);
    max-width: 90%;
}
.agent-bubble code {
    background: rgba(0,0,0,0.3);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 1px 5px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.82rem;
    color: #a78bfa;
}
.agent-bubble pre {
    background: #060d1a;
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px;
    overflow-x: auto;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.82rem;
    color: #a5f3fc;
    margin: 8px 0;
}

/* ── Trace / activity cards ── */
.trace-card {
    background: rgba(0,0,0,0.2);
    border: 1px solid var(--border);
    border-left: 3px solid var(--border-active);
    border-radius: 6px;
    padding: 7px 12px;
    margin: 4px 0;
    font-size: 0.78rem;
    color: var(--text-secondary);
    font-family: 'JetBrains Mono', monospace;
    transition: background 0.15s;
}
.trace-card:hover { background: rgba(0,0,0,0.35); }
.trace-card.delegation { border-left-color: var(--warning); }
.trace-card.response   { border-left-color: var(--success); }
.trace-card.tool       { border-left-color: var(--purple); }
.trace-card.routing    { border-left-color: var(--info); }
.trace-card.error      { border-left-color: var(--error); }

/* ── Agent status pills ── */
.agent-pill {
    display: flex;
    align-items: center;
    gap: 8px;
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 8px 12px;
    margin: 4px 0;
    font-size: 0.82rem;
    color: var(--text-primary);
    transition: border-color 0.2s;
}
.agent-pill:hover { border-color: var(--border-active); }
.agent-pill .status-dot { width: 7px; height: 7px; border-radius: 50%; margin-left: auto; flex-shrink: 0; }

/* ── Quick task buttons ── */
.stButton > button {
    width: 100%;
    text-align: left !important;
    background: var(--bg-card) !important;
    color: var(--text-secondary) !important;
    border: 1px solid var(--border) !important;
    border-radius: 8px !important;
    font-size: 0.8rem !important;
    padding: 7px 10px !important;
    margin: 2px 0 !important;
    transition: all 0.15s !important;
    font-family: 'Inter', sans-serif !important;
}
.stButton > button:hover {
    background: var(--bg-hover) !important;
    border-color: var(--aws-orange) !important;
    color: var(--text-primary) !important;
    transform: translateX(2px);
}

/* ── Session badge ── */
.session-badge {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 7px 10px;
    font-size: 0.72rem;
    color: var(--text-muted);
    font-family: 'JetBrains Mono', monospace;
    word-break: break-all;
}

/* ── Thinking animation ── */
.thinking-bar {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 10px 14px;
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 8px;
    color: var(--text-secondary);
    font-size: 0.82rem;
}
.thinking-dots { display: flex; gap: 4px; }
.thinking-dot {
    width: 6px; height: 6px;
    background: var(--aws-orange);
    border-radius: 50%;
    animation: pulse-dot 1.2s ease-in-out infinite;
}
.thinking-dot:nth-child(2) { animation-delay: 0.2s; }
.thinking-dot:nth-child(3) { animation-delay: 0.4s; }
@keyframes pulse-dot {
    0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; }
    40% { transform: scale(1); opacity: 1; }
}

/* ── Empty state ── */
.empty-state {
    text-align: center;
    padding: 60px 20px;
}
.empty-icon { font-size: 3.5rem; opacity: 0.5; }
.empty-title {
    font-size: 1.1rem;
    color: var(--text-secondary);
    margin-top: 16px;
    font-weight: 500;
}
.empty-hint {
    font-size: 0.82rem;
    color: var(--text-muted);
    margin-top: 8px;
    line-height: 1.6;
}

/* ── Progress bar for agent calls ── */
.bar-track {
    height: 5px;
    background: var(--border);
    border-radius: 3px;
    margin-top: 3px;
    overflow: hidden;
}
.bar-fill {
    height: 100%;
    border-radius: 3px;
    transition: width 0.4s ease;
}

/* ── Section dividers ── */
.section-header {
    font-size: 0.72rem;
    font-weight: 600;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin: 16px 0 8px 0;
    padding-bottom: 6px;
    border-bottom: 1px solid var(--border);
}

/* ── Streamlit overrides ── */
[data-testid="stTextArea"] textarea {
    background: var(--bg-card) !important;
    border: 1px solid var(--border) !important;
    border-radius: 10px !important;
    color: var(--text-primary) !important;
    font-family: 'Inter', sans-serif !important;
    font-size: 0.9rem !important;
    transition: border-color 0.2s !important;
}
[data-testid="stTextArea"] textarea:focus {
    border-color: var(--aws-orange) !important;
    box-shadow: 0 0 0 3px rgba(255,153,0,0.1) !important;
}
[data-testid="stForm"] {
    border: none !important;
    padding: 0 !important;
}
/* Primary send button */
[data-testid="stFormSubmitButton"] > button[kind="primary"] {
    background: linear-gradient(135deg, var(--aws-orange), #cc7a00) !important;
    color: #000 !important;
    font-weight: 600 !important;
    border: none !important;
    border-radius: 10px !important;
    font-size: 0.9rem !important;
    padding: 10px !important;
    transition: all 0.2s !important;
    letter-spacing: 0.01em !important;
}
[data-testid="stFormSubmitButton"] > button[kind="primary"]:hover {
    background: linear-gradient(135deg, #ffaa1a, #e68a00) !important;
    transform: translateY(-1px);
    box-shadow: 0 4px 16px rgba(255,153,0,0.35) !important;
}

/* Scrollbar */
::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: var(--bg-primary); }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--border-active); }

/* Sidebar section title */
.sidebar-section {
    font-size: 0.68rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--text-muted);
    margin: 16px 0 8px 0;
    padding-left: 2px;
}

/* Expander override */
[data-testid="stExpander"] {
    background: var(--bg-card) !important;
    border: 1px solid var(--border) !important;
    border-radius: 8px !important;
}
</style>
""", unsafe_allow_html=True)


# ── Session state ─────────────────────────────────────────────────────────────
def init_state() -> None:
    defaults = {
        "messages":      [],          # [{role, content, timestamp, traces}]
        "session_id":    str(uuid.uuid4()),
        "agent_ids":     None,
        "task_count":    0,
        "agent_calls":   {},          # {agent_key: count}
        "errors":        0,
        "region":        None,
        "pending_task":  None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


init_state()


# ── AWS clients ───────────────────────────────────────────────────────────────
@st.cache_resource(show_spinner=False)
def get_clients(region: str):
    try:
        runtime = boto3.client("bedrock-agent-runtime", region_name=region)
        mgmt    = boto3.client("bedrock-agent",         region_name=region)
        boto3.client("sts", region_name=region).get_caller_identity()
        return runtime, mgmt, None
    except NoCredentialsError:
        return None, None, "AWS credentials not found. Configure via environment variables or ~/.aws/credentials."
    except Exception as e:
        return None, None, str(e)


# ── Load agent IDs ────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def load_agent_ids(path: str):
    p = Path(path)
    if not p.exists():
        return None, f"agent_ids.json not found at {path}. Run: python setup_agents.py"
    try:
        with open(p) as f:
            return json.load(f), None
    except Exception as e:
        return None, str(e)


# ── Agent status check ────────────────────────────────────────────────────────
@st.cache_data(ttl=30, show_spinner=False)
def get_agent_status(agent_id: str, region: str) -> str:
    try:
        mgmt = boto3.client("bedrock-agent", region_name=region)
        resp = mgmt.get_agent(agentId=agent_id)
        return resp["agent"]["agentStatus"]
    except Exception:
        return "UNKNOWN"


# ── Routing hints ─────────────────────────────────────────────────────────────
def routing_hints(task: str) -> list[tuple[str, str]]:
    t = task.lower()
    hints = []
    if any(k in t for k in ["s3", "bucket", "storage", "lifecycle"]):
        hints.append(("s3", "S3 Storage Agent"))
    if any(k in t for k in ["iam", "role", "policy", "permission", "profile"]):
        hints.append(("iam", "IAM Agent"))
    if any(k in t for k in ["vpc", "subnet", "security group", "nat", "network", "cidr"]):
        hints.append(("vpc", "VPC Agent"))
        if "flow log" in t:
            hints.append(("iam", "IAM Agent (Flow Logs dependency)"))
    if any(k in t for k in ["ec2", "instance", "compute", "launch", "asg", "auto scaling", "ami"]):
        hints.append(("compute", "Compute Agent"))
    if any(k in t for k in ["alarm", "cloudwatch", "cloudtrail", "log group", "metric", "monitor", "dashboard", "observ"]):
        hints.append(("observability", "Observability Agent"))
    if any(k in t for k in ["rds", "aurora", "dynamodb", "database", "db"]):
        hints.append(("database", "Database Agent"))
    if any(k in t for k in ["cost", "price", "forecast", "budget", "billing"]):
        hints.append(("finops", "FinOps Agent"))
    # Deduplicate
    seen = {}
    for k, label in hints:
        if k not in seen:
            seen[k] = label
    return list(seen.items())


# ── Invoke agent (streaming) ──────────────────────────────────────────────────
def invoke_agent(task: str, agent_id: str, alias_id: str, runtime_client, session_id: str):
    """
    Call Bedrock invoke_agent and yield (event_type, data) tuples.
    event_type: "text" | "delegation" | "tool" | "response" | "rationale" | "error"
    """
    try:
        resp = runtime_client.invoke_agent(
            agentId=agent_id,
            agentAliasId=alias_id,
            sessionId=session_id,
            inputText=task,
            enableTrace=True,
        )
        for event in resp["completion"]:
            if "chunk" in event:
                yield "text", event["chunk"]["bytes"].decode("utf-8")

            elif "trace" in event:
                trace = event["trace"].get("trace", {})
                orch  = trace.get("orchestrationTrace", {})

                if "rationale" in orch:
                    text = orch["rationale"].get("text", "")
                    if text:
                        yield "rationale", text[:400]

                inv = orch.get("invocationInput", {})
                if "agentCollaboratorInvocationInput" in inv:
                    c = inv["agentCollaboratorInvocationInput"]
                    yield "delegation", {
                        "agent": c.get("agentCollaboratorName", "sub-agent"),
                        "input": c.get("input", {}).get("text", "")[:300],
                    }
                if "actionGroupInvocationInput" in inv:
                    ag = inv["actionGroupInvocationInput"]
                    yield "tool", ag.get("function", ag.get("actionGroupName", "?"))

                obs = orch.get("observation", {})
                if "agentCollaboratorObservation" in obs:
                    c = obs["agentCollaboratorObservation"]
                    yield "response", {
                        "agent":  c.get("agentCollaboratorName", "sub-agent"),
                        "output": c.get("output", {}).get("text", "")[:500],
                    }

    except ClientError as e:
        yield "error", str(e)
    except Exception as e:
        yield "error", str(e)


# ── Load configuration ────────────────────────────────────────────────────────
agent_ids, load_error = load_agent_ids(str(IDS_FILE))

# Determine region: prefer agent_ids.json → env var → fallback
if agent_ids:
    st.session_state.region = agent_ids.get("region", os.environ.get("AWS_REGION", "us-east-1"))
else:
    st.session_state.region = os.environ.get("AWS_REGION", "us-east-1")

region = st.session_state.region
runtime_client, mgmt_client, cred_error = get_clients(region)


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    # Brand
    st.markdown("""
    <div style="text-align:center;padding:16px 8px 8px">
        <div style="font-size:2rem">🚀</div>
        <div style="font-size:1rem;font-weight:700;color:#e8edf5;margin-top:6px">DevOps Command</div>
        <div style="font-size:0.72rem;color:#8b9ab4;margin-top:2px">AWS Bedrock Multi-Agent</div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("---")

    # ── AWS Configuration
    st.markdown('<div class="sidebar-section">⚙️ Configuration</div>', unsafe_allow_html=True)
    with st.expander("AWS Settings", expanded=False):
        region_input = st.text_input("Region", value=region, key="region_override")
        ids_path_input = st.text_input("agent_ids.json path", value=str(IDS_FILE), key="ids_path")
        if st.button("🔄 Reload Config", use_container_width=True):
            st.cache_data.clear()
            st.cache_resource.clear()
            st.rerun()

    # ── Connection status
    st.markdown('<div class="sidebar-section">🔌 Connection</div>', unsafe_allow_html=True)
    if cred_error:
        st.error(f"❌ {cred_error}", icon="⚠️")
    else:
        identity_str = ""
        try:
            identity = boto3.client("sts", region_name=region).get_caller_identity()
            acct = identity.get("Account", "")
            identity_str = f"Account: {acct}"
        except Exception:
            pass
        st.success(f"✅ Connected · {region}", icon="🔗")
        if identity_str:
            st.caption(identity_str)

    if load_error:
        st.warning(f"⚠️ {load_error}", icon="📋")

    st.markdown("---")

    # ── Agent Status Panel
    st.markdown('<div class="sidebar-section">🤖 Agent Fleet</div>', unsafe_allow_html=True)
    if agent_ids and not cred_error:
        super_id = agent_ids["super_agent"]["agent_id"]
        status   = get_agent_status(super_id, region)
        dot_color = "#22c55e" if status == "PREPARED" else "#f59e0b"
        st.markdown(
            f'<div class="agent-pill">'
            f'{AGENT_META["super"]["icon"]} '
            f'<b style="color:{AGENT_META["super"]["color"]}">{AGENT_META["super"]["label"]}</b>'
            f'<span class="status-dot" style="background:{dot_color}" title="{status}"></span>'
            f'</div>',
            unsafe_allow_html=True,
        )
        for key, meta in AGENT_META.items():
            if key == "super":
                continue
            sub_id = agent_ids["sub_agents"].get(key, {}).get("agent_id", "")
            if sub_id:
                sub_status = get_agent_status(sub_id, region)
                dot = "#22c55e" if sub_status == "PREPARED" else "#f59e0b"
                st.markdown(
                    f'<div class="agent-pill">'
                    f'{meta["icon"]} {meta["label"]}'
                    f'<span class="status-dot" style="background:{dot}" title="{sub_status}"></span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
    else:
        for key, meta in AGENT_META.items():
            st.markdown(
                f'<div class="agent-pill">'
                f'{meta["icon"]} {meta["label"]}'
                f'<span class="status-dot" style="background:#4a5568"></span>'
                f'</div>',
                unsafe_allow_html=True,
            )

    st.markdown("---")

    # ── Session
    st.markdown('<div class="sidebar-section">🗂️ Session</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="session-badge">ID: {st.session_state.session_id[:16]}…</div>',
        unsafe_allow_html=True,
    )
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🆕 New", use_container_width=True, key="new_session_btn"):
            st.session_state.session_id = str(uuid.uuid4())
            st.toast("New session started!", icon="🆕")
    with col2:
        if st.button("🗑️ Clear", use_container_width=True, key="clear_chat_btn"):
            st.session_state.messages   = []
            st.session_state.task_count  = 0
            st.session_state.agent_calls = {}
            st.session_state.errors      = 0
            st.toast("Chat cleared", icon="🗑️")

    st.markdown("---")

    # ── Quick Tasks
    st.markdown('<div class="sidebar-section">⚡ Quick Tasks</div>', unsafe_allow_html=True)
    for label, task_text in QUICK_TASKS.items():
        if st.button(label, use_container_width=True, key=f"qt_{label[:20]}"):
            st.session_state.pending_task = task_text

    st.markdown("---")

    # ── Dependency map
    with st.expander("📋 Dependency Map", expanded=False):
        st.markdown("""
**Auto-resolved by Super Agent:**

🌐 **VPC + Flow Logs**
→ IAM creates delivery role
→ VPC Agent receives role ARN

💻 **EC2 launch**
→ VPC provides subnet
→ IAM provides instance profile
→ Compute launches instance

☁️ **New service**
→ Resources provisioned
→ Observability adds alarms
        """)

    # ── Export
    with st.expander("💾 Export Chat", expanded=False):
        if st.session_state.messages:
            export_data = [
                {"role": m["role"], "content": m["content"], "timestamp": m.get("timestamp", "")}
                for m in st.session_state.messages
            ]
            st.download_button(
                "⬇️ Download JSON",
                data=json.dumps(export_data, indent=2),
                file_name=f"devops-chat-{st.session_state.session_id[:8]}.json",
                mime="application/json",
                use_container_width=True,
            )
        else:
            st.caption("No messages to export yet.")


# ── Main Panel ────────────────────────────────────────────────────────────────

# ── Header
st.markdown("""
<div class="header-bar">
    <div class="header-logo">🚀</div>
    <div>
        <div class="header-title">AWS DevOps Command Center</div>
        <div class="header-subtitle">Supervisor + 5 Specialist Agents · Amazon Bedrock</div>
    </div>
    <div class="header-badge">
        <span style="width:6px;height:6px;background:#22c55e;border-radius:50%;animation:pulse-dot 2s infinite"></span>
        Live
    </div>
</div>
""", unsafe_allow_html=True)

# ── Metrics row
total_calls = sum(st.session_state.agent_calls.values())
exchanges   = len([m for m in st.session_state.messages if m["role"] == "user"])
st.markdown(
    f"""
    <div class="metrics-row">
        <div class="metric-card">
            <div class="metric-icon">📤</div>
            <div class="metric-value">{st.session_state.task_count}</div>
            <div class="metric-label">Tasks Sent</div>
        </div>
        <div class="metric-card">
            <div class="metric-icon">🤖</div>
            <div class="metric-value">{total_calls}</div>
            <div class="metric-label">Agent Calls</div>
        </div>
        <div class="metric-card">
            <div class="metric-icon">💬</div>
            <div class="metric-value">{exchanges}</div>
            <div class="metric-label">Exchanges</div>
        </div>
        <div class="metric-card">
            <div class="metric-icon">❌</div>
            <div class="metric-value" style="color:{'#ef4444' if st.session_state.errors else '#e8edf5'}">{st.session_state.errors}</div>
            <div class="metric-label">Errors</div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# ── Two-column layout
chat_col, trace_col = st.columns([3, 1], gap="medium")


# ── Chat column ───────────────────────────────────────────────────────────────
with chat_col:
    # ── Chat history
    chat_container = st.container(height=520)
    with chat_container:
        if not st.session_state.messages:
            st.markdown(
                """
                <div class="empty-state">
                    <div class="empty-icon">🚀</div>
                    <div class="empty-title">AWS DevOps Command Center</div>
                    <div class="empty-hint">
                        Ask anything about your AWS infrastructure.<br>
                        Use the quick tasks panel on the left, or type your own request below.
                        <br><br>
                        <b>Examples:</b><br>
                        "Create a VPC with flow logs"<br>
                        "List all running EC2 instances"<br>
                        "Set up a full staging environment"
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            for msg in st.session_state.messages:
                ts = msg.get("timestamp", "")
                if msg["role"] == "user":
                    st.markdown(
                        f'<div class="user-meta">You · {ts}</div>'
                        f'<div class="user-msg"><div class="user-bubble">{msg["content"]}</div></div>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        f'<div class="agent-meta">🤖 Super Agent · {ts}</div>',
                        unsafe_allow_html=True,
                    )
                    st.markdown(
                        f'<div class="agent-msg"><div class="agent-bubble">{msg["content"]}</div></div>',
                        unsafe_allow_html=True,
                    )

    # ── Input area
    st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

    # Consume pending quick task
    pending = st.session_state.pop("pending_task", None)

    with st.form("chat_form", clear_on_submit=True):
        user_input = st.text_area(
            "Task",
            value=pending or "",
            placeholder="e.g. Create a VPC called prod-vpc with flow logs, or list all S3 buckets…",
            height=90,
            label_visibility="collapsed",
            key="task_input",
        )
        send_col, hint_col = st.columns([2, 3])
        with send_col:
            submitted = st.form_submit_button(
                "▶ Send to Super Agent",
                use_container_width=True,
                type="primary",
            )
        with hint_col:
            st.markdown(
                '<div style="font-size:0.75rem;color:#4a5568;padding-top:8px;line-height:1.4">'
                '💡 Tip: The Super Agent automatically delegates to specialist sub-agents and resolves dependencies.'
                '</div>',
                unsafe_allow_html=True,
            )

    # ── Handle submission
    if submitted and user_input.strip():
        task = user_input.strip()
        now  = datetime.now().strftime("%H:%M:%S")

        st.session_state.messages.append({
            "role":      "user",
            "content":   task,
            "timestamp": now,
        })
        st.session_state.task_count += 1

        if not agent_ids:
            st.session_state.messages.append({
                "role":      "assistant",
                "content":   "⚠️ **Agent IDs not loaded.**\n\nPlease check that `agent_ids.json` exists and is valid. Run `python setup_agents.py` to create the agents.",
                "timestamp": now,
                "traces":    [],
            })
            st.session_state.errors += 1
            st.rerun()

        elif not runtime_client:
            st.session_state.messages.append({
                "role":      "assistant",
                "content":   f"⚠️ **AWS connection error:**\n\n{cred_error}",
                "timestamp": now,
                "traces":    [],
            })
            st.session_state.errors += 1
            st.rerun()

        else:
            super_agent_id = agent_ids["super_agent"]["agent_id"]
            super_alias_id = agent_ids["super_agent"]["alias_id"]

            # Show routing hints
            hints = routing_hints(task)
            if hints:
                hint_text = " → ".join(
                    f'{AGENT_META[k]["icon"]} {label}' for k, label in hints
                )
                st.markdown(
                    f'<div class="trace-card routing">🔍 Routing: {hint_text}</div>',
                    unsafe_allow_html=True,
                )

            # Stream live response
            response_parts: list[str] = []
            traces:          list[dict] = []
            response_ph = st.empty()
            trace_ph    = st.empty()

            with st.spinner(""):
                trace_ph.markdown(
                    '<div class="thinking-bar">'
                    '<div class="thinking-dots">'
                    '<div class="thinking-dot"></div>'
                    '<div class="thinking-dot"></div>'
                    '<div class="thinking-dot"></div>'
                    '</div>'
                    'Super Agent is thinking…'
                    '</div>',
                    unsafe_allow_html=True,
                )

                for event_type, data in invoke_agent(
                    task, super_agent_id, super_alias_id,
                    runtime_client, st.session_state.session_id
                ):
                    if event_type == "text":
                        response_parts.append(data)
                        current = "".join(response_parts)
                        response_ph.markdown(
                            f'<div class="agent-bubble">{current}▌</div>',
                            unsafe_allow_html=True,
                        )

                    elif event_type == "delegation":
                        agent_key = next(
                            (k for k, m in AGENT_META.items()
                             if m["label"].lower().replace(" / ", " ").replace(" ", "")
                                in data["agent"].lower().replace(" ", "")),
                            "super"
                        )
                        st.session_state.agent_calls[agent_key] = (
                            st.session_state.agent_calls.get(agent_key, 0) + 1
                        )
                        traces.append({"type": "delegation", "data": data})
                        inp_preview = data["input"][:160] + ("…" if len(data["input"]) > 160 else "")
                        trace_ph.markdown(
                            f'<div class="trace-card delegation">'
                            f'↳ Delegating to <b>{data["agent"]}</b><br>'
                            f'<span style="color:#6b7280;font-size:0.75rem">{inp_preview}</span>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )

                    elif event_type == "tool":
                        traces.append({"type": "tool", "data": data})
                        trace_ph.markdown(
                            f'<div class="trace-card tool">⚙️ Calling tool: <b>{data}</b></div>',
                            unsafe_allow_html=True,
                        )

                    elif event_type == "response":
                        traces.append({"type": "response", "data": data})
                        out_preview = data["output"][:200] + ("…" if len(data["output"]) > 200 else "")
                        trace_ph.markdown(
                            f'<div class="trace-card response">'
                            f'✅ <b>{data["agent"]}</b> responded<br>'
                            f'<span style="color:#6b7280;font-size:0.75rem">{out_preview}</span>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )

                    elif event_type == "error":
                        st.session_state.errors += 1
                        traces.append({"type": "error", "data": data})
                        response_ph.error(f"❌ {data}")
                        break

            response_ph.empty()
            trace_ph.empty()

            final_response = "".join(response_parts)
            st.session_state.messages.append({
                "role":      "assistant",
                "content":   final_response or "_(No response received from the agent.)_",
                "timestamp": datetime.now().strftime("%H:%M:%S"),
                "traces":    traces,
            })

            st.rerun()


# ── Trace / Activity column ───────────────────────────────────────────────────
with trace_col:
    st.markdown('<div class="section-header">🔍 Activity</div>', unsafe_allow_html=True)

    # ── Agent call breakdown
    if st.session_state.agent_calls:
        total = sum(st.session_state.agent_calls.values()) or 1
        for key, count in sorted(st.session_state.agent_calls.items(), key=lambda x: -x[1]):
            meta = AGENT_META.get(key, {"icon": "?", "label": key, "color": "#888"})
            pct  = int(count / total * 100)
            st.markdown(
                f'<div style="margin:6px 0">'
                f'<div style="display:flex;justify-content:space-between;font-size:0.75rem;color:#8b9ab4;margin-bottom:3px">'
                f'<span>{meta["icon"]} {meta["label"]}</span>'
                f'<span style="font-family:monospace">{count} ({pct}%)</span>'
                f'</div>'
                f'<div class="bar-track">'
                f'<div class="bar-fill" style="width:{pct}%;background:{meta["color"]}"></div>'
                f'</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        st.markdown("<hr style='border-color:#1e2d45;margin:12px 0'>", unsafe_allow_html=True)

    # ── Latest traces from most recent message
    last_traces: list[dict] = []
    for msg in reversed(st.session_state.messages):
        if msg["role"] == "assistant" and msg.get("traces"):
            last_traces = msg["traces"]
            break

    if last_traces:
        st.markdown('<div class="sidebar-section">Last Exchange</div>', unsafe_allow_html=True)
        for t in last_traces:
            if t["type"] == "delegation":
                d = t["data"]
                inp_p = d["input"][:100] + ("…" if len(d["input"]) > 100 else "")
                st.markdown(
                    f'<div class="trace-card delegation">'
                    f'↳ <b>{d["agent"]}</b><br>'
                    f'<span style="color:#6b7280;font-size:0.72rem">{inp_p}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            elif t["type"] == "tool":
                st.markdown(
                    f'<div class="trace-card tool">⚙️ {t["data"]}</div>',
                    unsafe_allow_html=True,
                )
            elif t["type"] == "response":
                d = t["data"]
                out_p = d["output"][:120] + ("…" if len(d["output"]) > 120 else "")
                st.markdown(
                    f'<div class="trace-card response">'
                    f'✅ <b>{d["agent"]}</b><br>'
                    f'<span style="color:#6b7280;font-size:0.72rem">{out_p}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            elif t["type"] == "error":
                st.markdown(
                    f'<div class="trace-card error">❌ {t["data"][:180]}</div>',
                    unsafe_allow_html=True,
                )
    else:
        st.markdown(
            '<div style="color:#4a5568;font-size:0.8rem;text-align:center;padding:24px 0;line-height:1.6">'
            '🔍<br>Routing traces &<br>agent responses<br>appear here'
            '</div>',
            unsafe_allow_html=True,
        )

    st.markdown("<hr style='border-color:#1e2d45;margin:12px 0'>", unsafe_allow_html=True)

    # ── Dependency map reference
    with st.expander("📋 Dependency Map", expanded=False):
        st.markdown("""
**Auto-resolved by Super Agent:**

🌐 VPC + Flow Logs  
→ IAM creates role  
→ VPC receives ARN

💻 EC2 launch  
→ VPC provides subnet  
→ IAM provides profile  
→ Compute launches

☁️ New service  
→ Resources created  
→ Observability adds alarms
        """)

    # ── Help & Tips
    with st.expander("💡 Tips", expanded=False):
        st.markdown("""
**Writing good tasks:**
- Be specific about resource names
- Mention regions if different from default
- For complex tasks, describe the full desired end state

**Dependency tasks:**
- "VPC with flow logs" → automatically fetches IAM role first
- "Full environment" → orchestrates VPC + IAM + EC2 + Observability

**Useful commands:**
```
python setup_agents.py    # (re)create agents
python invoke_agent.py    # CLI mode
python invoke_agent.py --demo   # run demo tasks
```
        """)
