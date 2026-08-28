#!/usr/bin/env python3
"""VSS <-> OpenClaw agent adapter (POC).

Exposes an OpenAI-shaped streaming chat endpoint that the VSS UI can point at
via NEXT_PUBLIC_SIDEBAR_CHAT_HTTP_CHAT_COMPLETION_URL, and drives an agent
harness behind it.

Default transport is the OpenAI Responses API (POST /v1/responses), which
OpenClaw and Hermes both expose with identical shapes -- one driver, any
harness. The OpenClaw WebSocket gateway remains available via
AGENT_BACKEND=openclaw because it additionally emits tool events.

  POST /chat/stream   {"messages":[...]}  -> text/event-stream
  GET  /health

Gateway protocol notes (discovered empirically against OpenClaw 2026.6.10):
  - server opens with event connect.challenge (nonce is NOT echoed back)
  - connect params: omit `device` entirely to skip device-identity/pairing
  - scopes must be named explicitly; "*" is rejected
  - sessions.messages.subscribe takes {"key": ...}, chat.send takes
    {"sessionKey", "message", "idempotencyKey"}
"""
import hashlib, hmac, io, ipaddress, json, os, queue, re, select, shutil, socket, subprocess, tarfile, threading, time, urllib.request, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import websocket

GATEWAY_URL = os.environ.get("OPENCLAW_GATEWAY_URL", "ws://localhost:18789/")
GATEWAY_TOKEN = os.environ.get("OPENCLAW_GATEWAY_TOKEN", "")
SESSION_PREFIX = os.environ.get("OPENCLAW_SESSION_PREFIX", "agent:main:vss")
LISTEN_PORT = int(os.environ.get("ADAPTER_PORT", "9099"))
TURN_TIMEOUT = int(os.environ.get("ADAPTER_TURN_TIMEOUT", "600"))
SKILLS_DIR = os.environ.get(
    "VSS_SKILLS_DIR",
    os.path.expanduser("~/video-search-and-summarization/skills"))
HOST_ALIAS = os.environ.get("VSS_HOST_ALIAS", "host.openshell.internal")

# Harness sentinels that must never reach a user. OpenClaw emits these for
# heartbeat turns; they are exact tokens, unlike the model's chain-of-thought
# which arrives as ordinary prose and is NOT safely strippable.
# Which harness this adapter drives. "openclaw" speaks a WebSocket gateway and
# needs real translation; "hermes" already exposes an OpenAI-compatible API, so
# its driver is mostly a passthrough that adds auth and the bootstrap.
AGENT_BACKEND = os.environ.get("AGENT_BACKEND", "openclaw").strip().lower()

# How the conversation is identified to the harness.
#
# Default is the OpenAI `user` body field, which OpenClaw and Hermes both
# accept and both derive a stable session from -- so the common case needs no
# configuration and stays vendor-neutral. Harnesses that want their own name or
# a header instead are handled by config rather than code:
#   AGENT_SESSION_FIELD=x-openclaw-session-key AGENT_SESSION_IN=header
#   AGENT_SESSION_FIELD=conversation           AGENT_SESSION_IN=body
# Set AGENT_SESSION_FIELD empty to send nothing.
AGENT_SESSION_FIELD = os.environ.get("AGENT_SESSION_FIELD", "user").strip()
AGENT_SESSION_IN = os.environ.get("AGENT_SESSION_IN", "body").strip().lower()

# Stable across restarts only if set. Left unset the value is random per
# process, which costs continuity after a restart but never reuses an
# identity a previous process handed out.
_SESSION_SALT = (os.environ.get("AGENT_SESSION_SALT", "").encode()
                 or os.urandom(32))


def session_identity(session_key: str) -> str:
    """Opaque conversation identity derived here, never taken from the caller.

    OpenClaw does not validate session-key ownership (openclaw#11793, CVSS 8.1,
    closed as not planned) and its keys are predictable, so forwarding a
    client-supplied conversation id would let one caller land on another's
    session and read or drive it. Keying an HMAC with a process secret makes the
    value unguessable and untargetable, while staying stable for a conversation.
    """
    digest = hmac.new(_SESSION_SALT, session_key.encode(), hashlib.sha256)
    return f"{SESSION_PREFIX}-{digest.hexdigest()[:32]}"
HERMES_API_URL = os.environ.get(
    "HERMES_API_URL", "http://127.0.0.1:8642/v1/chat/completions")
HERMES_TOKEN = os.environ.get("HERMES_TOKEN", "").strip()
HERMES_MODEL = os.environ.get("HERMES_MODEL", "hermes-agent")
# Hermes exposes /v1/responses with the same shape OpenClaw does, so both can
# share one driver. Derived from HERMES_API_URL so only one var needs setting.
HERMES_RESPONSES_URL = os.environ.get(
    "HERMES_RESPONSES_URL",
    HERMES_API_URL.replace("/chat/completions", "/responses"))
# OpenClaw's OpenAI Responses endpoint, off by default and enabled with
# gateway.http.endpoints.responses.enabled. Speaking this instead of the
# WebSocket gateway is what lets one HTTP driver serve both harnesses.
OPENCLAW_HTTP_URL = os.environ.get(
    "OPENCLAW_HTTP_URL", "http://localhost:18789/v1/responses")
OPENCLAW_HTTP_MODEL = os.environ.get("OPENCLAW_HTTP_MODEL", "openclaw")

BOOTSTRAP_ENABLED = os.environ.get("ADAPTER_BOOTSTRAP", "1") != "0"
ADAPTER_PUBLIC_URL = os.environ.get(
    "ADAPTER_PUBLIC_URL", f"http://{HOST_ALIAS}:{os.environ.get('ADAPTER_PORT', '9099')}")

# Archive search over HTTP.
#
# `vss-search-archive` drives a host CLI (`vss search run` via uv) against a
# source checkout. A sandboxed or hosted agent has neither, so the skill is
# unrunnable there. This endpoint runs the same CLI on
# the host and exposes it as plain HTTP, which is the one thing every agent can
# reach.
VSS_REPO_ROOT = os.environ.get(
    "VSS_REPO_ROOT", os.path.expanduser("~/video-search-and-summarization"))
UV_BIN = os.environ.get("UV_BIN") or shutil.which("uv") or os.path.expanduser("~/.local/bin/uv")
SEARCH_MODES = ("embed", "attribute", "fusion", "object")
VSS_INGRESS = os.environ.get("VSS_INGRESS_ORIGIN", "http://127.0.0.1:7777").rstrip("/")
SEARCH_TIMEOUT = int(os.environ.get("ADAPTER_SEARCH_TIMEOUT", "180"))

# --- access control -------------------------------------------------------
# The adapter holds the gateway token and can drive the agent, so it must not
# be open to anything that can route to it. It binds 0.0.0.0 by necessity (the
# UI container and the sandbox both reach it over docker bridges), so the
# control is a caller allowlist plus an optional shared token.
#
# `chat.ts` sends a fixed header set with no auth header, so the token is also
# accepted as `?token=` -- the UI passes its configured URL through verbatim,
# which makes a query param the only way to authenticate without UI changes.
ADAPTER_TOKEN = os.environ.get("ADAPTER_TOKEN", "").strip()
ALLOW_CIDRS = [
    ipaddress.ip_network(c.strip())
    for c in os.environ.get(
        "ADAPTER_ALLOW_CIDRS", "127.0.0.1/32,::1/128,172.16.0.0/12").split(",")
    if c.strip()
]
# Seconds between SSE keepalive comments. cloudflared/haproxy will drop an idle
# stream, and an agent turn can be silent for minutes while a skill runs.
SSE_KEEPALIVE = int(os.environ.get("ADAPTER_SSE_KEEPALIVE", "15"))

SENTINELS = ("NO_REPLY", "HEARTBEAT_OK")
_HOLD = max(len(x) for x in SENTINELS) - 1


# Only tools whose absence genuinely blocks a skill are reported as blocking.
#
# Learned by observation: an earlier version reported every mentioned tool as
# required, and the agent concluded NONE of the 18 skills could run -- minutes
# after it had successfully used one. Two reasons that was wrong:
#
#   * `docker` is mentioned by 17 of 18 skills, but usually as one discovery
#     path among several (`docker inspect` for env) or as a *prohibition*
#     ("never run docker directly, call the orchestrator MCP"). Its absence
#     rarely blocks anything.
#   * `mcp` is not a binary at all. It is a protocol reached over HTTP, so
#     "mcp is missing" is a category error.
#
# uv and vss-repo are different: without them the search CLI cannot be built or
# run at all. Those are the only verified blockers.
_BLOCKING_PATTERNS = [
    ("uv", re.compile(r"(?:^|[^a-z])uv (?:run|tool)|uvx ", re.M)),
    ("vss-repo", re.compile(r"VSS_REPO_ROOT|services/agent")),
]


def _detect_requirements(skill_md):
    """Tools whose absence actually blocks this skill. Usually empty."""
    try:
        with open(skill_md, encoding="utf-8", errors="replace") as fh:
            body = fh.read()
    except OSError:
        return []
    return [name for name, pat in _BLOCKING_PATTERNS if pat.search(body)]


def _frontmatter_description(skill_md):
    """Pull `description:` out of SKILL.md YAML frontmatter (agentskills.io)."""
    try:
        with open(skill_md, encoding="utf-8", errors="replace") as fh:
            if fh.readline().strip() != "---":
                return ""
            desc, cont = "", False
            for line in fh:
                if line.strip() == "---":
                    break
                if cont and (line.startswith("  ") or line.startswith("\t")):
                    desc += " " + line.strip()
                    continue
                cont = False
                if line.lower().startswith("description:"):
                    val = line.split(":", 1)[1].strip().strip("\"'")
                    # `>` / `|` are YAML block-scalar markers, not content --
                    # the text itself is on the following indented lines.
                    desc = "" if val[:1] in (">", "|") else val
                    cont = True
            return " ".join(desc.split())
    except OSError:
        return ""


def skills_manifest():
    """One entry per skill dir containing a SKILL.md.

    Walks two levels: skills/<name>/ and skills/<group>/<name>/ -- the canonical
    `skills/*/` glob misses the nested ones.
    """
    entries = []
    if not os.path.isdir(SKILLS_DIR):
        return entries
    for root, dirs, files in os.walk(SKILLS_DIR):
        if root.count(os.sep) - SKILLS_DIR.count(os.sep) > 2:
            dirs[:] = []
            continue
        if "SKILL.md" in files:
            entries.append({
                "name": os.path.basename(root),
                "path": os.path.relpath(root, SKILLS_DIR),
                "description": _frontmatter_description(
                    os.path.join(root, "SKILL.md")),
                "requirements": _detect_requirements(
                    os.path.join(root, "SKILL.md")),
            })
            dirs[:] = []
    return sorted(entries, key=lambda e: e["name"])


_BOOTSTRAP_CACHE = {}


def BOOTSTRAP_TEXT():
    if "text" not in _BOOTSTRAP_CACHE:
        _BOOTSTRAP_CACHE["text"] = build_bootstrap(
            skills_manifest(), ADAPTER_PUBLIC_URL)
    return _BOOTSTRAP_CACHE["text"]


def build_bootstrap(manifest, base_url):
    """Context prepended to a session's first turn.

    This is the whole BYO story in one string: it needs nothing from the harness
    but the ability to accept text, so it works for any agent -- unlike
    `skill install`, which assumes an OpenClaw-shaped workspace.

    The skills *index* is inlined (small, and removes a round trip that a harness
    might simply not make). Skill *bodies* stay remote and are fetched on demand.
    """
    lines = [
        "# VSS deployment context",
        "",
        "You are connected to a NVIDIA VSS (Video Search and Summarization)",
        "deployment. The user is talking to you from the VSS UI.",
        "",
        "## Reaching VSS",
        f"{base_url} is the VSS agent adapter -- not a VSS backend service. It",
        "serves your skills, the resolved endpoint list, and archive search.",
        f"Resolved service endpoints and their live status: GET {base_url}/v1/skills/env",
        "Inside a sandbox, always call the host alias from that document -- never",
        "`localhost` and never a literal IP, or the egress policy denies the call.",
        "",
        "## VSS skills available to you",
        "When a request relates to VSS, pick the matching skill below, FETCH its",
        "instructions, and follow them:",
        f"  GET {base_url}/v1/skills/<name>",
        "The path is exactly that -- do not drop the /skills/ segment.",
        "",
        "Do not guess a skill's steps from its description alone. And do NOT go",
        "looking through the local filesystem, config files or /dev for VSS state:",
        "VSS data lives in the services listed above, reached over HTTP. The",
        "sandbox itself holds none of it.",
        "",
        "(These come from the VSS deployment and are separate from any skills your",
        "own harness ships with. Asked which VSS skills exist, answer from this",
        "list; both sets coexist.)",
        "",
    ]
    for e in manifest:
        # Annotate only genuine blockers. Listing every mentioned tool made the
        # agent pre-refuse skills it could actually run.
        blockers = e.get("requirements") or []
        tag = f" [requires: {', '.join(blockers)}]" if blockers else ""
        lines.append(
            f"- {e['name']}{tag}: {e.get('description') or '(no description)'}")
    lines += [
        "",
        "## Archive search over HTTP",
        f"POST {base_url}/v1/search  ->  SearchOutput (a `data` array of hits).",
        'Body: {\"mode\": \"embed\"|\"attribute\"|\"fusion\"|\"object\",',
        '       \"query\": \"...\", \"top_k\": 10,',
        '       \"source_type\": \"video_file\"|\"rtsp\", \"video_sources\": [..]}',
        "Use this instead of the `vss` CLI when uv or a repo checkout is absent;",
        "it runs the same command host-side. Present hits as prose, not raw JSON.",
        "",
        "## Services currently deployed",
        "Not every VSS service is running on every profile. Right now:",
        "__SERVICE_STATUS__",
        "A skill whose backend is down is not broken -- the service simply is not",
        "deployed here. Say which service is missing rather than blaming the skill.",
        "",
        "## Conventions",
        "- A `[requires: ...]` tag means that skill genuinely cannot run without",
        "  those tools. `vss-repo` means a VSS source checkout plus its CLI, which a",
        "  sandbox will not have and no amount of network access fixes.",
        "- Skills with no tag: just try them. Most work with curl and jq against the",
        "  VSS HTTP APIs. A skill mentioning `docker` usually offers an HTTP path too,",
        "  or is telling you NOT to use docker directly -- do not treat a mention as a",
        "  blocker, and never report a skill unrunnable without actually attempting it.",
        "- Deployment/teardown goes through the VSS Orchestrator MCP, never raw",
        "  `docker compose` or host shell commands. That MCP runs on the host, so",
        "  you do not need local docker to deploy.",
        "- Report progress in chat as you go; do not go silent during long tasks.",
        "- Never invent a host:port URL for the user; read the deployed public",
        "  origin from the deployment rather than constructing one.",
        "",
        "---",
        "",
    ]
    reach = service_reachability()
    up = sorted(n for n, ok in reach.items() if ok)
    down = sorted(n for n, ok in reach.items() if not ok)
    status = (f"  available: {', '.join(up) or 'none'}\n"
              f"  NOT deployed: {', '.join(down) or 'none'}")
    return "\n".join(lines).replace("__SERVICE_STATUS__", status)


class SentinelFilter:
    """Strip sentinel tokens from a token stream without breaking streaming.

    Holds back the last `_HOLD` characters so a sentinel split across two
    deltas ("NO_" + "REPLY") is still caught, then flushes the remainder.
    """

    def __init__(self):
        self.buf = ""

    def feed(self, text: str) -> str:
        # Strip the whole buffer BEFORE splitting: stripping only the emitted
        # prefix lets a sentinel spanning the emit/holdback boundary escape.
        self.buf = self._strip(self.buf + text)
        if len(self.buf) <= _HOLD:
            return ""
        emit, self.buf = self.buf[:-_HOLD], self.buf[-_HOLD:]
        return emit

    def flush(self) -> str:
        out, self.buf = self._strip(self.buf), ""
        return out

    @staticmethod
    def _strip(text: str) -> str:
        for token in SENTINELS:
            text = text.replace(token, "")
        return text


# VSS services an agent may need, and the host port each listens on. Probed
# live because "the skill can run" and "its backend is deployed" are different
# questions -- a search-profile deployment has no alert-bridge or VA-MCP, and
# an agent told only about tools will confidently plan against services that
# are not there.
VSS_SERVICES = {
    "vss_agent": 8000,
    "orchestrator_mcp": 9988,
    "va_mcp": 9901,
    "alert_bridge": 9080,
    "elasticsearch": 9200,
    "vst_vios": 30888,
    "rt_vlm": 8018,
}
_REACH_CACHE = {"at": 0.0, "data": None}
_REACH_TTL = 30.0


def service_reachability():
    """{name: bool} for VSS services, cached briefly."""
    now = time.monotonic()
    if _REACH_CACHE["data"] is not None and now - _REACH_CACHE["at"] < _REACH_TTL:
        return _REACH_CACHE["data"]
    out = {}
    for name, port in VSS_SERVICES.items():
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1.5):
                out[name] = True
        except OSError:
            out[name] = False
    _REACH_CACHE.update(at=now, data=out)
    return out


def _query_param(path, key):
    _, _, qs = path.partition("?")
    for pair in qs.split("&"):
        name, _, value = pair.partition("=")
        if name == key:
            return value
    return ""


def sse(data: str) -> bytes:
    return f"data: {data}\n\n".encode()


# Most recent successful search, so the UI can render hits without the model
# transcribing them into chat text (2.5). The agent's own HTTP call carries no
# conversation id, so this is a single most-recent slot rather than per-session:
# fine for a single-user POC, and the staleness window keeps it honest.
_LAST_SEARCH: dict = {"at": 0.0, "query": None, "payload": None,
                      "conversation": None}
# Which conversations have a turn in flight. A search arrives from the sandbox
# on its own connection with no conversation of its own, so it is attributed to
# the turn that must have caused it. With more than one in flight that is a
# guess, and guessing here would hand one user another user's media -- so it is
# left unattributed and simply not served.
_ACTIVE_TURNS: dict = {}
_ACTIVE_LOCK = threading.Lock()


class Turn:
    """Cancellation handle for one in-flight turn.

    Checking a flag between stream iterations is not enough: when the agent is
    silent the driver sits blocked in a socket read and never reaches the
    check, so an abandoned turn would run until ADAPTER_TURN_TIMEOUT. Drivers
    therefore register their upstream connection here, and cancelling closes it
    -- which makes the blocked read raise at once.
    """

    def __init__(self):
        self._event = threading.Event()
        self._closables: list = []
        self._lock = threading.Lock()

    def is_set(self) -> bool:
        return self._event.is_set()

    def register(self, closable) -> None:
        with self._lock:
            if self._event.is_set():
                self._close(closable)   # cancelled between open and register
                return
            self._closables.append(closable)

    def set(self) -> None:
        self._event.set()
        with self._lock:
            pending, self._closables = self._closables, []
        for c in pending:
            self._close(c)

    @staticmethod
    def _close(closable) -> None:
        try:
            closable.close()
        except Exception:  # noqa: BLE001 - closing a dead socket is fine
            pass


def _begin_turn(session_key):
    with _ACTIVE_LOCK:
        _ACTIVE_TURNS[session_key] = _ACTIVE_TURNS.get(session_key, 0) + 1


def _end_turn(session_key):
    with _ACTIVE_LOCK:
        n = _ACTIVE_TURNS.get(session_key, 0) - 1
        if n > 0:
            _ACTIVE_TURNS[session_key] = n
        else:
            _ACTIVE_TURNS.pop(session_key, None)


def _sole_active_conversation():
    """The conversation to attribute a search to, or None if ambiguous."""
    with _ACTIVE_LOCK:
        keys = list(_ACTIVE_TURNS)
    if len(keys) != 1:
        return None
    key = keys[0]
    prefix = SESSION_PREFIX + "-"
    return key[len(prefix):] if key.startswith(prefix) else key
_LAST_SEARCH_TTL = 180.0


def _empty_result_diagnostics():
    """Explain an empty result set: no matches, or nothing indexed at all."""
    info = {
        "retrieval_returned": "no hits",
        "retry_guidance": (
            "Do NOT retry with different parameters. An empty result from this "
            "endpoint means retrieval found nothing, not that the request was "
            "malformed. Report the outcome to the user."
        ),
    }
    try:
        req = urllib.request.Request(
            f"{VSS_INGRESS}/elasticsearch/_cat/indices/mdx-*?h=index,docs.count&format=json")
        with urllib.request.urlopen(req, timeout=8) as resp:
            indices = json.load(resp)
        total = sum(int(i.get("docs.count") or 0) for i in indices)
        info["indices"] = [i.get("index") for i in indices]
        info["indexed_documents"] = total
        info["likely_cause"] = (
            "nothing has been ingested yet - ingest a video before searching"
            if total == 0 else
            "content is indexed but nothing matched this query semantically. "
            "The archive may simply not contain what was asked for. Consider "
            "rewording, or check what the registered sources actually show "
            "before concluding the search is broken."
        )
    except Exception as exc:  # noqa: BLE001 - diagnostics must never fail the call
        info["index_check_failed"] = f"{type(exc).__name__}: {exc}"
    return info


def _extract_search_output(out):
    """Pull the SearchOutput object out of the CLI's stdout.

    stdout is not a single JSON document: the CLI prefixes log lines, and as of
    the newer `vss` CLI it also appends a job-completion event, e.g.
    `{"event":"vss_job_completed",...}`. Decoding from the first brace to the
    end therefore fails with "Extra data", which is how this surfaced.

    So walk every JSON document present and return the one that looks like a
    search result (carries `data`) rather than trusting position -- the event
    line is last today, but ordering is not a contract.
    """
    decoder = json.JSONDecoder()
    docs, idx = [], 0
    while True:
        start = out.find("{", idx)
        if start == -1:
            break
        try:
            obj, end = decoder.raw_decode(out, start)
        except json.JSONDecodeError:
            idx = start + 1
            continue
        docs.append(obj)
        idx = end
    for obj in docs:
        if isinstance(obj, dict) and "data" in obj:
            return obj
    return docs[-1] if docs else None


def run_search(body):
    """Invoke `vss search run <mode> --raw` and return its SearchOutput.

    Args are built as a list and passed without a shell, and every value is
    validated or coerced, so a request body cannot inject flags or commands.
    """
    mode = body.get("mode", "embed")
    if mode not in SEARCH_MODES:
        return 400, {"error": f"mode must be one of {list(SEARCH_MODES)}"}
    if not os.path.isdir(VSS_REPO_ROOT):
        return 503, {"error": f"VSS_REPO_ROOT not found: {VSS_REPO_ROOT}"}
    if not os.path.exists(UV_BIN):
        return 503, {"error": "uv not found on the host; set UV_BIN"}

    cmd = [UV_BIN, "run", "--project", os.path.join(VSS_REPO_ROOT, "services", "agent"),
           "--no-dev", "--extra", "cli", "vss", "search", "run", mode, "--raw"]

    query = body.get("query")
    if query:
        cmd += ["--query", str(query)]
    try:
        top_k = int(body.get("top_k", 10))
    except (TypeError, ValueError):
        return 400, {"error": "top_k must be an integer"}
    cmd += ["--top-k", str(max(1, min(top_k, 1000)))]
    if body.get("source_type") in ("video_file", "rtsp"):
        cmd += ["--source-type", body["source_type"]]
    for src in body.get("video_sources") or []:
        cmd += ["--video-source", str(src)]
    for key, flag in (("timestamp_start", "--timestamp-start"),
                      ("timestamp_end", "--timestamp-end"),
                      ("object_id", "--object-id")):
        if body.get(key):
            cmd += [flag, str(body[key])]

    try:
        proc = subprocess.run(cmd, cwd=VSS_REPO_ROOT, capture_output=True,
                              text=True, timeout=SEARCH_TIMEOUT)
    except subprocess.TimeoutExpired:
        return 504, {"error": f"search timed out after {SEARCH_TIMEOUT}s"}
    if proc.returncode != 0:
        return 502, {"error": "search command failed",
                     "exit_code": proc.returncode,
                     "stderr": (proc.stderr or "")[-800:]}
    out = (proc.stdout or "").strip()
    payload = _extract_search_output(out)
    if payload is None:
        return 502, {"error": "no SearchOutput in search output",
                     "stdout": out[-500:]}

    # A bare {"data": [], "search_messages": []} is indistinguishable from
    # "you called it wrong", and an agent given that will retry with different
    # parameters indefinitely -- observed doing 66 tool calls and never
    # answering. Say what is actually known so it can stop.
    #
    # Note the diagnostics must not assert that retrieval is broken. Searching
    # for warehouses against a clip of a tree returns nothing because that is
    # the correct answer; claiming a known defect there sends the agent (and
    # the reader) chasing an imaginary bug.
    if isinstance(payload, dict) and not payload.get("data"):
        payload["diagnostics"] = _empty_result_diagnostics()
    elif isinstance(payload, dict):
        _LAST_SEARCH.update(at=time.monotonic(), query=query, payload=payload,
                            conversation=_sole_active_conversation())
    return 200, payload


BOOTSTRAPPED = set()
_BOOTSTRAP_LOCK = threading.Lock()


def run_turn_responses(message, session_key, out, cancel=None, *,
                       url, token, model, label):
    """Drive any harness that speaks the OpenAI **Responses** API.

    OpenClaw and Hermes both expose POST /v1/responses with identical request
    and SSE shapes, so one driver serves both -- which is what "bring your own
    harness" should actually mean. Verified against both.

    The same job as run_turn's ~200 lines of WebSocket handshake, scopes,
    session creation and event translation -- in about forty, because the
    protocol work disappears when the harness speaks HTTP.

    Two tradeoffs versus the WebSocket path, both deliberate:
      - text only. The WS stream also yields `agent` tool events, which is what
        populates the intermediate-steps panel.
      - no per-conversation isolation. OpenClaw's Responses endpoint answers
        from one shared session: a follow-up recalls earlier context even with
        no `previous_response_id`, so distinct UI conversations share memory.
        The WS driver isolates via `sessionKey`. History is therefore carried in
        the prompt, which behaves the same on every harness.
    """
    if _first_turn(session_key):
        message = BOOTSTRAP_TEXT() + message
    payload = {"model": model, "input": message, "stream": True}
    identity = session_identity(session_key)
    if AGENT_SESSION_FIELD and AGENT_SESSION_IN == "body":
        payload[AGENT_SESSION_FIELD] = identity
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if AGENT_SESSION_FIELD and AGENT_SESSION_IN == "header":
        req.add_header(AGENT_SESSION_FIELD, identity)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    sentinels = SentinelFilter()
    try:
        with urllib.request.urlopen(req, timeout=TURN_TIMEOUT) as resp:
            if cancel is not None:
                cancel.register(resp)
            for raw in resp:
                # The reader is the only thing holding the upstream turn open;
                # if the browser is gone, stop rather than burning gateway and
                # host resources until TURN_TIMEOUT.
                if cancel is not None and cancel.is_set():
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data: "):
                    continue
                try:
                    frame = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                if frame.get("type") == "response.output_text.delta":
                    clean = sentinels.feed(frame.get("delta") or "")
                    if clean:
                        out.put(sse(json.dumps(
                            {"choices": [{"delta": {"content": clean}}]})))
                elif frame.get("type") in ("response.completed", "response.failed"):
                    break
        tail = sentinels.flush()
        if tail:
            out.put(sse(json.dumps({"choices": [{"delta": {"content": tail}}]})))
    except Exception as exc:  # noqa: BLE001 - POC: surface everything to the UI
        if cancel is None or not cancel.is_set():
            out.put(sse(json.dumps({"choices": [{"delta": {
                "content": f"\n[adapter/{label}] {type(exc).__name__}: {exc}"}}]})))
    finally:
        out.put(sse("[DONE]"))
        out.put(None)


def run_turn_openclaw_http(message, session_key, out, cancel=None):
    return run_turn_responses(message, session_key, out, cancel, url=OPENCLAW_HTTP_URL,
                              token=GATEWAY_TOKEN, model=OPENCLAW_HTTP_MODEL,
                              label="openclaw-http")


def run_turn_hermes_responses(message, session_key, out, cancel=None):
    return run_turn_responses(message, session_key, out, cancel,
                              url=HERMES_RESPONSES_URL, token=HERMES_TOKEN,
                              model=HERMES_MODEL, label="hermes-responses")


def _first_turn(session_key):
    with _BOOTSTRAP_LOCK:
        first = session_key not in BOOTSTRAPPED
        BOOTSTRAPPED.add(session_key)
    return first and BOOTSTRAP_ENABLED


def run_turn_hermes(message, session_key, out, cancel=None):
    """Drive Hermes, which already speaks OpenAI-compatible streaming chat.

    Almost a passthrough: attach the bearer token, prepend the bootstrap on a
    session's first turn, and re-emit content deltas through the sentinel
    filter. The frames Hermes produces are already the shape the VSS UI parses,
    which is the clearest evidence that the OpenAI-shaped contract (2.2) was
    the right choice -- a different harness arrived at it independently.
    """
    if _first_turn(session_key):
        message = BOOTSTRAP_TEXT() + message
    body = json.dumps({
        "model": HERMES_MODEL,
        "messages": [{"role": "user", "content": message}],
        "stream": True,
    }).encode()
    req = urllib.request.Request(HERMES_API_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if HERMES_TOKEN:
        req.add_header("Authorization", f"Bearer {HERMES_TOKEN}")
    sentinels = SentinelFilter()
    step = 0
    try:
        with urllib.request.urlopen(req, timeout=TURN_TIMEOUT) as resp:
            if cancel is not None:
                cancel.register(resp)
            for raw in resp:
                # The reader is the only thing holding the upstream turn open;
                # if the browser is gone, stop rather than burning gateway and
                # host resources until TURN_TIMEOUT.
                if cancel is not None and cancel.is_set():
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data: "):
                    continue
                payload = line[6:].strip()
                if payload == "[DONE]":
                    break
                try:
                    frame = json.loads(payload)
                except json.JSONDecodeError:
                    continue

                # Hermes reports tool activity as extra top-level fields on
                # otherwise ordinary chunks -- tool/label/status/toolCallId --
                # rather than inside the delta. Reading only delta.content threw
                # all of it away, which is why the main panel showed no steps
                # while the sidebar did.
                if frame.get("tool"):
                    status = ("complete" if frame.get("status") == "completed"
                              else "error" if frame.get("status") == "failed"
                              else "in_progress")
                    step += 1
                    out.put(("intermediate_data: " + json.dumps({
                        # Key on toolCallId so the running frame and its
                        # completion collapse into one step rather than two.
                        "id": frame.get("toolCallId") or str(step),
                        "status": status,
                        "name": frame.get("tool") or "tool",
                        "payload": frame.get("label") or "",
                        "parent_id": "default",
                        "index": step,
                    }) + "\n").encode())

                try:
                    delta = frame["choices"][0].get("delta", {})
                except (KeyError, IndexError, TypeError):
                    continue
                clean = sentinels.feed(delta.get("content") or "")
                if clean:
                    out.put(sse(json.dumps(
                        {"choices": [{"delta": {"content": clean}}]})))
        tail = sentinels.flush()
        if tail:
            out.put(sse(json.dumps({"choices": [{"delta": {"content": tail}}]})))
    except Exception as exc:  # noqa: BLE001 - POC: surface everything to the UI
        out.put(sse(json.dumps({"choices": [{"delta": {
            "content": f"\n[adapter/hermes] {type(exc).__name__}: {exc}"}}]})))
    finally:
        out.put(sse("[DONE]"))
        out.put(None)


def run_turn(message: str, session_key: str, out: queue.Queue, cancel=None):
    """Drive one agent turn, pushing SSE-ready strings onto `out`."""
    ws = None
    try:
        ws = websocket.create_connection(GATEWAY_URL, timeout=15)
        if cancel is not None:
            cancel.register(ws)
        ws.settimeout(TURN_TIMEOUT)
        ws.recv()  # connect.challenge

        def req(method, params):
            rid = str(uuid.uuid4())
            ws.send(json.dumps({"type": "req", "id": rid, "method": method, "params": params}))
            return rid

        connect_id = req("connect", {
            "minProtocol": 4, "maxProtocol": 4,
            "client": {"id": "openclaw-control-ui", "version": "vss-adapter-0.1",
                       "platform": "node", "mode": "webchat"},
            "role": "operator",
            "scopes": ["operator.read", "operator.write"],
            "caps": ["tool-events"],
            "auth": {"token": GATEWAY_TOKEN},
            "userAgent": "vss-agent-adapter", "locale": "en-US",
        })
        # The gateway rejects any request that arrives before `connect`
        # completes ("invalid handshake: first request must be connect"),
        # so await the handshake response rather than firing off in parallel.
        while True:
            hello = json.loads(ws.recv())
            if hello.get("type") == "res" and hello.get("id") == connect_id:
                if not hello.get("ok"):
                    raise RuntimeError(f"connect failed: {hello.get('error')}")
                break

        # Fresh, isolated session per conversation: reusing agent:main:main
        # inherits heartbeat/main-session history and derails the reply.
        req("sessions.create", {"key": session_key})
        req("sessions.messages.subscribe", {"key": session_key})
        # Prepend deployment context on a session's first turn only. Sending it
        # as its own turn would make the agent reply to it and surface a stray
        # message to the user.
        if _first_turn(session_key):
            message = BOOTSTRAP_TEXT() + message

        req("chat.send", {"sessionKey": session_key, "message": message,
                          "idempotencyKey": str(uuid.uuid4())})

        step = 0
        sentinels = SentinelFilter()
        while True:
            if cancel is not None and cancel.is_set():
                break
            msg = json.loads(ws.recv())
            if msg.get("type") == "res" and msg.get("ok") is False:
                err = msg.get("error", {})
                out.put(sse(json.dumps({"choices": [{"delta": {
                    "content": f"\n[adapter] gateway error: {err.get('message')}"}}]})))
                break
            if msg.get("type") != "event":
                continue
            ev, p = msg.get("event"), msg.get("payload", {}) or {}

            if ev == "chat":
                if p.get("state") == "delta" and p.get("deltaText"):
                    clean = sentinels.feed(p["deltaText"])
                    if clean:
                        out.put(sse(json.dumps(
                            {"choices": [{"delta": {"content": clean}}]})))
                elif p.get("state") == "final":
                    tail = sentinels.flush()
                    if tail:
                        out.put(sse(json.dumps(
                            {"choices": [{"delta": {"content": tail}}]})))
                    break
            elif ev == "agent" and p.get("stream") == "tool":
                # Surface tool activity as NAT-UI intermediate steps.
                d = p.get("data", {}) or {}
                step += 1
                out.put(("intermediate_data: " + json.dumps({
                    "id": str(p.get("seq", step)), "status": "in_progress",
                    "name": d.get("name") or d.get("tool") or "tool",
                    "payload": json.dumps(d)[:800],
                    "parent_id": "default", "index": step,
                }) + "\n").encode())
    except Exception as exc:  # noqa: BLE001 - POC: surface everything to the UI
        out.put(sse(json.dumps({"choices": [{"delta": {
            "content": f"\n[adapter] {type(exc).__name__}: {exc}"}}]})))
    finally:
        if ws:
            try:
                ws.close()
            except Exception:
                pass
        out.put(sse("[DONE]"))
        out.put(None)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # ?token= is a documented auth path (chat.ts cannot set an auth header),
    # so the request line carries a credential. Redact before it reaches
    # stdout or adapter.log.
    _TOKEN_IN_URL = re.compile(r"([?&]token=)[^&\s]+")

    def log_message(self, fmt, *args):
        line = self._TOKEN_IN_URL.sub(r"\1[REDACTED]", fmt % args)
        print(f"[adapter] {self.address_string()} {line}", flush=True)

    def _authorized(self):
        try:
            peer = ipaddress.ip_address(self.client_address[0])
        except (ValueError, IndexError):
            return False
        if not any(peer in net for net in ALLOW_CIDRS):
            return False
        if not ADAPTER_TOKEN:
            return True
        supplied = (self.headers.get("X-Adapter-Token")
                    or (self.headers.get("Authorization", "")
                        .removeprefix("Bearer ").strip())
                    or _query_param(self.path, "token"))
        return supplied == ADAPTER_TOKEN

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _client_gone(self) -> bool:
        """True once the peer has closed its end (readable with no data)."""
        try:
            ready, _, _ = select.select([self.connection], [], [], 0)
            if not ready:
                return False
            return self.connection.recv(1, socket.MSG_PEEK) == b""
        except OSError:
            return True

    def _json(self, obj, code=200):
        body = json.dumps(obj, indent=1).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _skills_manifest(self):
        return skills_manifest()

    def _tgz(self, members, filename):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for arcname, src in members.items():
                tar.add(src, arcname=arcname)
        data = buf.getvalue()
        self.send_response(200)
        self.send_header("Content-Type", "application/gzip")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition",
                         f'attachment; filename="{filename}"')
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self._authorized():
            self.send_error(403, "forbidden")
            return
        path = self.path.split("?")[0].rstrip("/") or "/"

        if path == "/v1/skills":
            self._json({"count": len(self._skills_manifest()),
                        "skills": self._skills_manifest()})
            return

        if path == "/v1/search/last":
            # Scoped to the asking conversation: this result is media from
            # someone's video, and the store is process-wide.
            asked = _query_param(self.path, "conversation")
            owner = _LAST_SEARCH["conversation"]
            fresh = time.monotonic() - _LAST_SEARCH["at"] < _LAST_SEARCH_TTL
            if fresh and _LAST_SEARCH["payload"] and asked and owner == asked:
                self._json({"query": _LAST_SEARCH["query"],
                            **_LAST_SEARCH["payload"]})
            else:
                self._json({"data": [], "stale": True})
            return

        if path == "/v1/skills/env":
            reach = service_reachability()
            # Where VSS actually is, so a BYO agent does not have to re-derive
            # host resolution from prose in deployment_resolution.md / ENV.md.
            self._json({
                "host_alias": HOST_ALIAS,
                "note": "In-sandbox only. Never use localhost or a literal IP; "
                        "the egress policy whitelists this alias on fixed ports.",
                "services": {
                    name: {"url": f"http://{HOST_ALIAS}:{port}",
                           "reachable": reach.get(name, False)}
                    for name, port in VSS_SERVICES.items()
                },
                "archive_search": {"url": f"{ADAPTER_PUBLIC_URL}/v1/search",
                                   "reachable": True},
            })
            return

        if path.startswith("/v1/skills/") and path != "/v1/skills/env":
            rest = path[len("/v1/skills/"):]
            want_bundle = rest.endswith("/bundle.tar.gz")
            name = rest[:-len("/bundle.tar.gz")] if want_bundle else rest
            entry = next((e for e in skills_manifest() if e["name"] == name), None)
            if entry:
                skill_dir = os.path.join(SKILLS_DIR, entry["path"])
                if want_bundle:
                    self._tgz({name: skill_dir}, f"{name}.tar.gz")
                    return
                with open(os.path.join(skill_dir, "SKILL.md"),
                          encoding="utf-8", errors="replace") as fh:
                    body = fh.read().encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/markdown; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self._cors()
                self.end_headers()
                self.wfile.write(body)
                return
            if name != "bundle.tar.gz":
                self._json({"error": f"unknown skill: {name}"}, 404)
                return

        if path == "/v1/skills/bundle.tar.gz":
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:gz") as tar:
                for e in self._skills_manifest():
                    tar.add(os.path.join(SKILLS_DIR, e["path"]),
                            arcname=e["name"])
            data = buf.getvalue()
            self.send_response(200)
            self.send_header("Content-Type", "application/gzip")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition",
                             'attachment; filename="vss-skills.tar.gz"')
            self._cors()
            self.end_headers()
            self.wfile.write(data)
            return

        if path != "/health":
            self.send_error(404)
            return
        # Report the endpoint actually in use, not always the OpenClaw one.
        endpoint = {
            "hermes": HERMES_RESPONSES_URL,
            "hermes-chat": HERMES_API_URL,
            "openclaw-http": OPENCLAW_HTTP_URL,
        }.get(AGENT_BACKEND, GATEWAY_URL)
        self._json({"ok": True, "backend": AGENT_BACKEND, "endpoint": endpoint,
                    "sessionPrefix": SESSION_PREFIX,
                    "skills": len(self._skills_manifest())})

    def do_POST(self):
        if not self._authorized():
            self.send_error(403, "forbidden")
            return
        path = self.path.split("?")[0].rstrip("/")
        if path not in ("/chat/stream", "/generate/stream", "/v1/search"):
            self.send_error(404)
            return
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0") or "0"))
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self.send_error(400, "invalid JSON")
            return

        if path == "/v1/search":
            code, payload = run_search(body)
            self._json(payload, code)
            return

        msgs = body.get("messages") or []
        user = next((m.get("content") for m in reversed(msgs)
                     if m.get("role") == "user"), None) or body.get("input_message")
        if not user:
            self.send_error(400, "no user message")
            return
        if isinstance(user, list):  # OpenAI content-parts form
            user = " ".join(part.get("text", "") for part in user
                            if isinstance(part, dict))

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        # Close when the turn ends. An SSE body has no Content-Length, so on a
        # kept-alive HTTP/1.1 connection the client cannot tell the response is
        # over and hangs after [DONE] -- the UI stays stuck "streaming".
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self._cors()
        self.end_headers()
        self.close_connection = True

        # One session per UI conversation; header lets the UI keep continuity.
        conv = (self.headers.get("Conversation-Id")  # sent natively by the VSS UI
                or self.headers.get("X-VSS-Session")
                or body.get("conversation_id"))
        session_key = f"{SESSION_PREFIX}-{conv or uuid.uuid4().hex[:12]}"

        driver = {
            # Responses-API drivers: one code path, two harnesses.
            "hermes": run_turn_hermes_responses,
            "openclaw-http": run_turn_openclaw_http,
            # Legacy/richer paths kept available.
            "hermes-chat": run_turn_hermes,
            "openclaw": run_turn,          # WebSocket: adds tool events
        }.get(AGENT_BACKEND, run_turn)
        # Left unbounded deliberately: `cancel` stops the driver within one
        # loop iteration, so the queue cannot grow far after a disconnect.
        # Bounding it instead would make the driver's terminal put() block
        # forever once the consumer is gone -- a wedged thread in place of a
        # short-lived queue.
        out: queue.Queue = queue.Queue()
        cancel = Turn()
        _begin_turn(session_key)
        threading.Thread(target=driver, args=(user, session_key, out, cancel),
                         daemon=True).start()
        try:
            while True:
                # Detect the client leaving directly rather than waiting for a
                # write to fail. A browser closing sends FIN, and writing to a
                # half-closed socket still succeeds -- so a write-error check
                # can miss a departed client for a long time, or entirely.
                if self._client_gone():
                    cancel.set()
                    print("[adapter] client disconnected; cancelling turn",
                          flush=True)
                    break
                try:
                    chunk = out.get(timeout=SSE_KEEPALIVE)
                except queue.Empty:
                    # SSE comment: ignored by every client, keeps proxies from
                    # dropping a stream that is silent while a skill runs.
                    chunk = b": keepalive\n\n"
                if chunk is None:
                    break
                try:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    # Tell the driver to abandon the turn; otherwise it keeps
                    # the upstream turn alive and writing until TURN_TIMEOUT.
                    cancel.set()
                    print("[adapter] client disconnected; cancelling turn",
                          flush=True)
                    break
        finally:
            _end_turn(session_key)


if __name__ == "__main__":
    if not GATEWAY_TOKEN:
        raise SystemExit("OPENCLAW_GATEWAY_TOKEN is required")
    print(f"[adapter] listening on :{LISTEN_PORT} -> {GATEWAY_URL} ({SESSION_PREFIX}-*)",
          flush=True)
    ThreadingHTTPServer(("0.0.0.0", LISTEN_PORT), Handler).serve_forever()
