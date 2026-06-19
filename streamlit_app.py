"""
streamlit_app.py — AWS Bedrock Multi-Agent Cloud Infrastructure Manager
-----------------------------------------------------------------------
Run:
    pip install streamlit boto3
    streamlit run streamlit_app.py

Prerequisites:
    agent_ids.json must exist (run setup_agents.py first)
    AWS credentials must be configured (env vars or ~/.aws/credentials)
"""

import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path

import boto3
import streamlit as st
from botocore.exceptions import ClientError, NoCredentialsError

# ── Page config (must be first Streamlit call) ────────────────────────────────
st.set_page_config(
    page_title="Cloud Infrastructure Manager",
    page_icon="☁️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Constants ─────────────────────────────────────────────────────────────────
IDS_FILE = Path(__file__).parent / "agent_ids.json"
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

AGENT_META = {
    "s3":            {"label": "S3 Storage",      "icon": "🪣", "color": "#FF9900"},
    "iam":           {"label": "IAM",              "icon": "🔐", "color": "#DD344C"},
    "observability": {"label": "Observability",    "icon": "📊", "color": "#7AA116"},
    "compute":       {"label": "Compute",          "icon": "💻", "color": "#1A9C3E"},
    "vpc":           {"label": "VPC / Network",    "icon": "🌐", "color": "#0073BB"},
    "super":         {"label": "Super Agent",      "icon": "🤖", "color": "#8B5CF6"},
}

QUICK_TASKS = {
    "🪣  List all S3 buckets": "List all S3 buckets in the account with their regions and sizes.",
    "🔐  Create IAM role for EC2": "Create an IAM role called EC2AppRole that EC2 can assume with S3 read access.",
    "🌐  Create a new VPC": "Create a VPC called dev-vpc (10.0.0.0/16) with public and private subnets across two AZs.",
    "🌐  VPC + Flow Logs (IAM dep.)": "Create a VPC called prod-vpc with VPC Flow Logs enabled. Handle any IAM dependencies automatically.",
    "💻  Describe running instances": "List all running EC2 instances with their types, IPs, and states.",
    "📊  Create CPU alarm": "Create a CloudWatch alarm for high CPU on instance i-0abc1234 (threshold 80%).",
    "☁️  Full environment setup": (
        "Full environment: create a VPC called staging-env, launch a t3.medium Amazon Linux 2 "
        "instance, set up a CloudWatch CPU alarm at 75%, and give me a summary report."
    ),
}

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* ── Global ── */
[data-testid="stAppViewContainer"] { background: #0f1117; }
[data-testid="stSidebar"] { background: #1a1d27; border-right: 1px solid #2d3148; }

/* ── Chat bubbles ── */
.user-bubble {
    background: #1e3a5f;
    border: 1px solid #2563eb;
    border-radius: 12px 12px 4px 12px;
    padding: 12px 16px;
    margin: 8px 0 8px 60px;
    color: #e2e8f0;
    font-size: 0.95rem;
    line-height: 1.6;
}
.agent-bubble {
    background: #1a1d2e;
    border: 1px solid #374151;
    border-radius: 4px 12px 12px 12px;
    padding: 14px 18px;
    margin: 8px 60px 8px 0;
    color: #d1d5db;
    font-size: 0.95rem;
    line-height: 1.6;
}
.agent-bubble pre {
    background: #0d1117;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 12px;
    overflow-x: auto;
    font-size: 0.85rem;
}
.agent-bubble code {
    background: #0d1117;
    padding: 2px 5px;
    border-radius: 3px;
    font-size: 0.85rem;
}

/* ── Trace cards ── */
.trace-card {
    background: #111827;
    border: 1px solid #1f2937;
    border-left: 3px solid #6366f1;
    border-radius: 6px;
    padding: 8px 12px;
    margin: 4px 0;
    font-size: 0.82rem;
    color: #9ca3af;
    font-family: monospace;
}
.trace-card.delegation { border-left-color: #f59e0b; }
.trace-card.response   { border-left-color: #10b981; }
.trace-card.tool       { border-left-color: #8b5cf6; }

/* ── Agent status pills ── */
.agent-pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: #1f2937;
    border: 1px solid #374151;
    border-radius: 20px;
    padding: 4px 12px;
    margin: 3px 2px;
    font-size: 0.8rem;
    color: #d1d5db;
    width: 100%;
}

/* ── Session badge ── */
.session-badge {
    background: #1f2937;
    border: 1px solid #374151;
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 0.75rem;
    color: #6b7280;
    font-family: monospace;
}

/* ── Quick task buttons ── */
.stButton > button {
    width: 100%;
    text-align: left;
    background: #1f2937;
    color: #d1d5db;
    border: 1px solid #374151;
    border-radius: 6px;
    font-size: 0.82rem;
    padding: 6px 10px;
    margin: 1px 0;
}
.stButton > button:hover {
    background: #2d3748;
    border-color: #6366f1;
    color: #fff;
}

/* ── Metric cards ── */
.metric-row {
    display: flex;
    gap: 10px;
    margin: 8px 0;
}
.metric-card {
    flex: 1;
    background: #1f2937;
    border: 1px solid #374151;
    border-radius: 8px;
    padding: 12px;
    text-align: center;
}
.metric-value { font-size: 1.4rem; font-weight: 700; color: #e2e8f0; }
.metric-label { font-size: 0.72rem; color: #6b7280; margin-top: 2px; }

/* ── Scrollable chat area ── */
.chat-container {
    max-height: 62vh;
    overflow-y: auto;
    padding: 8px 0;
}
</style>
""", unsafe_allow_html=True)


# ── Session state ─────────────────────────────────────────────────────────────
def init_state() -> None:
    defaults = {
        "messages": [],          # [{role, content, timestamp, traces}]
        "session_id": str(uuid.uuid4()),
        "agent_ids": None,
        "task_count": 0,
        "agent_calls": {},       # {agent_key: count}
        "errors": 0,
        "runtime_client": None,
        "bedrock_client": None,
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
        # Validate credentials with a cheap call
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
        return None, f"agent_ids.json not found at {path}. Run setup_agents.py first."
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
def routing_hints(task: str) -> list[str]:
    t = task.lower()
    hints = []
    if any(k in t for k in ["s3", "bucket", "storage", "lifecycle"]):
        hints.append(("s3", "S3 Storage Agent"))
    if any(k in t for k in ["iam", "role", "policy", "permission"]):
        hints.append(("iam", "IAM Agent"))
    if any(k in t for k in ["vpc", "subnet", "security group", "nat", "network"]):
        hints.append(("vpc", "VPC Agent"))
        if "flow log" in t:
            hints.append(("iam", "IAM Agent (Flow Logs dependency)"))
    if any(k in t for k in ["ec2", "instance", "compute", "launch", "asg", "lambda"]):
        hints.append(("compute", "Compute Agent"))
    if any(k in t for k in ["alarm", "cloudwatch", "cloudtrail", "log", "metric", "monitor", "observ"]):
        hints.append(("observability", "Observability Agent"))
    # Deduplicate by key, preserving (key, label) order
    seen = {}
    for k, label in hints:
        if k not in seen:
            seen[k] = label
    return list(seen.items())


# ── Invoke agent (streaming) ──────────────────────────────────────────────────
def invoke_agent(task: str, agent_id: str, alias_id: str,
                 runtime_client, session_id: str):
    """
    Call Bedrock invoke_agent and yield (event_type, data) tuples.
    event_type: "text" | "delegation" | "tool" | "response" | "error"
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
                orch = trace.get("orchestrationTrace", {})

                if "rationale" in orch:
                    text = orch["rationale"].get("text", "")
                    if text:
                        yield "rationale", text[:300]

                inv = orch.get("invocationInput", {})
                if "agentCollaboratorInvocationInput" in inv:
                    c = inv["agentCollaboratorInvocationInput"]
                    yield "delegation", {
                        "agent": c.get("agentCollaboratorName", "sub-agent"),
                        "input": c.get("input", {}).get("text", "")[:200],
                    }
                if "actionGroupInvocationInput" in inv:
                    ag = inv["actionGroupInvocationInput"]
                    yield "tool", ag.get("function", ag.get("actionGroupName", "?"))

                obs = orch.get("observation", {})
                if "agentCollaboratorObservation" in obs:
                    c = obs["agentCollaboratorObservation"]
                    yield "response", {
                        "agent": c.get("agentCollaboratorName", "sub-agent"),
                        "output": c.get("output", {}).get("text", "")[:400],
                    }

    except ClientError as e:
        yield "error", str(e)
    except Exception as e:
        yield "error", str(e)


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ☁️ Cloud Agent Manager")
    st.markdown("---")

    # ── AWS config
    with st.expander("⚙️ AWS Configuration", expanded=False):
        region = st.text_input("Region", value=AWS_REGION, key="region_input")
        ids_path = st.text_input("agent_ids.json path", value=str(IDS_FILE))
        if st.button("🔄 Reload", use_container_width=True):
            st.cache_data.clear()
            st.cache_resource.clear()
            st.rerun()

    runtime_client, mgmt_client, cred_error = get_clients(
        st.session_state.get("region_input", AWS_REGION)
    )

    # ── Connection status
    st.markdown("### 🔌 Connection")
    if cred_error:
        st.error(f"❌ {cred_error}")
    else:
        st.success("✅ AWS Connected")

    agent_ids, load_error = load_agent_ids(st.session_state.get("agent_ids.json path", str(IDS_FILE)) or str(IDS_FILE))
    if load_error:
        st.warning(f"⚠️ {load_error}")
        agent_ids = None
    else:
        st.session_state.agent_ids = agent_ids

    st.markdown("---")

    # ── Agent status panel
    st.markdown("### 🤖 Agent Status")
    if agent_ids and not cred_error:
        super_id = agent_ids["super_agent"]["agent_id"]
        status = get_agent_status(super_id, st.session_state.get("region_input", AWS_REGION))
        color = "#10b981" if status == "PREPARED" else "#f59e0b"
        st.markdown(
            f'<div class="agent-pill">{AGENT_META["super"]["icon"]} '
            f'<b style="color:{AGENT_META["super"]["color"]}">Super Agent</b> '
            f'<span style="margin-left:auto;color:{color};font-size:0.75rem">● {status}</span></div>',
            unsafe_allow_html=True,
        )
        for key, meta in AGENT_META.items():
            if key == "super":
                continue
            sub_id = agent_ids["sub_agents"].get(key, {}).get("agent_id", "")
            if sub_id:
                sub_status = get_agent_status(sub_id, st.session_state.get("region_input", AWS_REGION))
                col = "#10b981" if sub_status == "PREPARED" else "#f59e0b"
                st.markdown(
                    f'<div class="agent-pill">{meta["icon"]} '
                    f'<b style="color:{meta["color"]}">{meta["label"]}</b>'
                    f'<span style="margin-left:auto;color:{col};font-size:0.75rem">● {sub_status}</span></div>',
                    unsafe_allow_html=True,
                )
    else:
        for key, meta in AGENT_META.items():
            st.markdown(
                f'<div class="agent-pill">{meta["icon"]} {meta["label"]}'
                f'<span style="margin-left:auto;color:#6b7280;font-size:0.75rem">● N/A</span></div>',
                unsafe_allow_html=True,
            )

    st.markdown("---")

    # ── Session controls
    st.markdown("### 🗂️ Session")
    st.markdown(
        f'<div class="session-badge">ID: {st.session_state.session_id[:18]}...</div>',
        unsafe_allow_html=True,
    )
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🆕 New", use_container_width=True):
            st.session_state.session_id = str(uuid.uuid4())
            st.toast("New session started", icon="🆕")
    with col2:
        if st.button("🗑️ Clear", use_container_width=True):
            st.session_state.messages = []
            st.session_state.task_count = 0
            st.session_state.agent_calls = {}
            st.session_state.errors = 0
            st.toast("Chat cleared", icon="🗑️")

    st.markdown("---")

    # ── Quick tasks
    st.markdown("### ⚡ Quick Tasks")
    for label, task_text in QUICK_TASKS.items():
        if st.button(label, use_container_width=True):
            st.session_state["pending_task"] = task_text


# ── Main panel ────────────────────────────────────────────────────────────────
st.markdown("## ☁️ Cloud Infrastructure Super Agent")

# ── Metrics row
total_calls = sum(st.session_state.agent_calls.values()) if st.session_state.agent_calls else 0
st.markdown(
    f"""
    <div class="metric-row">
        <div class="metric-card">
            <div class="metric-value">{st.session_state.task_count}</div>
            <div class="metric-label">Tasks Sent</div>
        </div>
        <div class="metric-card">
            <div class="metric-value">{total_calls}</div>
            <div class="metric-label">Agent Calls</div>
        </div>
        <div class="metric-card">
            <div class="metric-value">{len(st.session_state.messages) // 2}</div>
            <div class="metric-label">Exchanges</div>
        </div>
        <div class="metric-card">
            <div class="metric-value" style="color:#f87171">{st.session_state.errors}</div>
            <div class="metric-label">Errors</div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# ── Two-column layout: chat | trace
chat_col, trace_col = st.columns([2, 1])

# ── Chat column ───────────────────────────────────────────────────────────────
with chat_col:
    # Render history
    chat_container = st.container(height=500)
    with chat_container:
        if not st.session_state.messages:
            st.markdown(
                """
                <div style="text-align:center;padding:60px 20px;color:#4b5563">
                    <div style="font-size:3rem">☁️</div>
                    <div style="font-size:1.1rem;margin-top:12px;color:#6b7280">
                        Ask anything about your AWS infrastructure
                    </div>
                    <div style="font-size:0.85rem;margin-top:8px;color:#374151">
                        Use quick tasks on the left or type your own request below
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
                        f'<div class="user-bubble">'
                        f'<span style="font-size:0.7rem;color:#6b7280">You · {ts}</span><br>'
                        f'{msg["content"]}'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                else:
                    # Render assistant message with markdown
                    with st.container():
                        st.markdown(
                            f'<div style="font-size:0.7rem;color:#6b7280;margin-bottom:4px">'
                            f'🤖 Super Agent · {ts}</div>',
                            unsafe_allow_html=True,
                        )
                        st.markdown(
                            f'<div class="agent-bubble">{msg["content"]}</div>',
                            unsafe_allow_html=True,
                        )

    # ── Input area
    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    # Check for pending quick task
    pending = st.session_state.pop("pending_task", None)

    with st.form("chat_form", clear_on_submit=True):
        user_input = st.text_area(
            "Task",
            value=pending or "",
            placeholder="e.g. Create a VPC called prod-vpc with flow logs, or list all S3 buckets...",
            height=90,
            label_visibility="collapsed",
        )
        submitted = st.form_submit_button("▶ Send", use_container_width=True, type="primary")

    if submitted and user_input.strip():
        task = user_input.strip()
        now = datetime.now().strftime("%H:%M:%S")

        # Add user message
        st.session_state.messages.append({"role": "user", "content": task, "timestamp": now})
        st.session_state.task_count += 1

        # Validate prerequisites
        if not agent_ids:
            st.session_state.messages.append({
                "role": "assistant",
                "content": "⚠️ **Agent IDs not loaded.** Please check that `agent_ids.json` exists and is valid.",
                "timestamp": now,
                "traces": [],
            })
            st.session_state.errors += 1
            st.rerun()

        elif not runtime_client:
            st.session_state.messages.append({
                "role": "assistant",
                "content": f"⚠️ **AWS connection error:** {cred_error}",
                "timestamp": now,
                "traces": [],
            })
            st.session_state.errors += 1
            st.rerun()

        else:
            super_agent_id  = agent_ids["super_agent"]["agent_id"]
            super_alias_id  = agent_ids["super_agent"]["alias_id"]

            # Show routing hints immediately
            hints = routing_hints(task)
            if hints:
                hint_text = " · ".join(
                    f'{AGENT_META[k]["icon"]} {label}' for k, label in hints
                )
                st.markdown(
                    f'<div class="trace-card">🔍 Likely routing: {hint_text}</div>',
                    unsafe_allow_html=True,
                )

            # Stream response
            response_parts: list[str] = []
            traces: list[dict] = []

            with st.spinner("Super Agent is thinking..."):
                response_placeholder = st.empty()
                trace_placeholder    = st.empty()

                for event_type, data in invoke_agent(
                    task, super_agent_id, super_alias_id,
                    runtime_client, st.session_state.session_id
                ):
                    if event_type == "text":
                        response_parts.append(data)
                        current = "".join(response_parts)
                        response_placeholder.markdown(
                            f'<div class="agent-bubble">{current}▌</div>',
                            unsafe_allow_html=True,
                        )

                    elif event_type == "delegation":
                        agent_key = next(
                            (k for k, m in AGENT_META.items()
                             if m["label"].lower() in data["agent"].lower()), "super"
                        )
                        st.session_state.agent_calls[agent_key] = (
                            st.session_state.agent_calls.get(agent_key, 0) + 1
                        )
                        traces.append({"type": "delegation", "data": data})
                        trace_placeholder.markdown(
                            f'<div class="trace-card delegation">'
                            f'↳ Delegating to <b>{data["agent"]}</b><br>'
                            f'<span style="color:#6b7280">{data["input"][:150]}…</span>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )

                    elif event_type == "tool":
                        traces.append({"type": "tool", "data": data})
                        trace_placeholder.markdown(
                            f'<div class="trace-card tool">⚙️ Tool: <b>{data}</b></div>',
                            unsafe_allow_html=True,
                        )

                    elif event_type == "response":
                        traces.append({"type": "response", "data": data})
                        trace_placeholder.markdown(
                            f'<div class="trace-card response">'
                            f'✅ <b>{data["agent"]}</b> responded<br>'
                            f'<span style="color:#6b7280">{data["output"][:200]}…</span>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )

                    elif event_type == "error":
                        st.session_state.errors += 1
                        traces.append({"type": "error", "data": data})
                        response_placeholder.error(f"❌ {data}")
                        break

            final_response = "".join(response_parts)
            response_placeholder.empty()
            trace_placeholder.empty()

            # Store in history
            st.session_state.messages.append({
                "role": "assistant",
                "content": final_response or "_(no response)_",
                "timestamp": datetime.now().strftime("%H:%M:%S"),
                "traces": traces,
            })

            st.rerun()


# ── Trace / Activity column ───────────────────────────────────────────────────
with trace_col:
    st.markdown("### 🔍 Activity Trace")

    # Agent call breakdown
    if st.session_state.agent_calls:
        st.markdown("**Calls by agent**")
        total = sum(st.session_state.agent_calls.values()) or 1
        for key, count in sorted(
            st.session_state.agent_calls.items(), key=lambda x: -x[1]
        ):
            meta = AGENT_META.get(key, {"icon": "?", "label": key, "color": "#888"})
            pct = int(count / total * 100)
            st.markdown(
                f'<div style="margin:4px 0">'
                f'<div style="display:flex;justify-content:space-between;font-size:0.8rem;color:#9ca3af">'
                f'<span>{meta["icon"]} {meta["label"]}</span><span>{count} ({pct}%)</span></div>'
                f'<div style="height:6px;background:#1f2937;border-radius:3px;margin-top:3px">'
                f'<div style="height:100%;width:{pct}%;background:{meta["color"]};border-radius:3px"></div>'
                f'</div></div>',
                unsafe_allow_html=True,
            )
        st.markdown("---")

    # Latest traces from most recent message
    last_traces = []
    for msg in reversed(st.session_state.messages):
        if msg["role"] == "assistant" and msg.get("traces"):
            last_traces = msg["traces"]
            break

    if last_traces:
        st.markdown("**Last exchange**")
        for t in last_traces:
            if t["type"] == "delegation":
                d = t["data"]
                st.markdown(
                    f'<div class="trace-card delegation">'
                    f'↳ <b>{d["agent"]}</b><br>'
                    f'<span style="color:#6b7280;font-size:0.78rem">{d["input"][:120]}…</span></div>',
                    unsafe_allow_html=True,
                )
            elif t["type"] == "tool":
                st.markdown(
                    f'<div class="trace-card tool">⚙️ {t["data"]}</div>',
                    unsafe_allow_html=True,
                )
            elif t["type"] == "response":
                d = t["data"]
                st.markdown(
                    f'<div class="trace-card response">'
                    f'✅ <b>{d["agent"]}</b><br>'
                    f'<span style="color:#6b7280;font-size:0.78rem">{d["output"][:150]}…</span></div>',
                    unsafe_allow_html=True,
                )
            elif t["type"] == "error":
                st.markdown(
                    f'<div class="trace-card" style="border-left-color:#ef4444">'
                    f'❌ {t["data"][:200]}</div>',
                    unsafe_allow_html=True,
                )
    else:
        st.markdown(
            '<div style="color:#4b5563;font-size:0.85rem;text-align:center;padding:30px 0">'
            'Routing traces will appear<br>here during task execution</div>',
            unsafe_allow_html=True,
        )

    st.markdown("---")

    # ── Dependency map reference
    with st.expander("📋 Dependency Map", expanded=False):
        st.markdown("""
**Auto-resolved by Super Agent:**

🌐 **VPC + Flow Logs**
→ IAM Agent creates delivery role
→ VPC Agent receives role ARN

💻 **EC2 launch**
→ VPC Agent provides subnet
→ IAM Agent provides instance profile
→ Compute Agent launches instance

☁️ **New service provisioning**
→ Compute / VPC create resources
→ Observability Agent adds alarms
        """)

    # ── Export chat
    with st.expander("💾 Export Chat", expanded=False):
        if st.session_state.messages:
            export = []
            for m in st.session_state.messages:
                export.append({"role": m["role"], "content": m["content"],
                                "timestamp": m.get("timestamp", "")})
            st.download_button(
                "⬇️ Download JSON",
                data=json.dumps(export, indent=2),
                file_name=f"agent-chat-{st.session_state.session_id[:8]}.json",
                mime="application/json",
                use_container_width=True,
            )
        else:
            st.caption("No messages to export yet.")

