#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Temporary read-only progress probe; emit allowlisted metadata, never raw logs."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
from urllib.parse import quote, urlsplit
from urllib.request import urlopen


def tool_kind(command):
    for marker, kind in (
        ("run_setup_notebook.py", "setup_notebook"),
        ("vss-build-vision-ai/SKILL.md", "build_skill_reference"),
        ("docker compose", "compose"),
        ("vss configure memory check", "memory_check"),
        ("vss configure check", "configuration_check"),
        ("vss vlm run", "vlm_probe"),
        ("openshell", "openshell"),
        ("nemoclaw", "nemoclaw"),
        ("vss configure", "vss_configuration"),
        ("ngc ", "ngc"),
        ("uv ", "python_environment"),
        ("docker ", "docker"),
    ):
        if marker in command:
            return kind
    return "other"


def summarize_log(path):
    kinds, errors = Counter(), Counter()
    recent = []
    started, completed = 0, 0
    last_failed_notebook = None
    signals = {
        "scope upgrade pending approval": "gateway_scope_approval",
        "pairing required": "gateway_pairing",
        "origin not allowed": "gateway_origin",
        "SSRF preflight": "inference_preflight",
        "sandbox not found": "sandbox_missing",
        "apply_patch: command not found": "patch_command_missing",
        "Connection refused": "connection_refused",
        "SUPERVISOR_UNAVAILABLE": "supervisor_unavailable",
        "gateway is down after webhook config": "webhook_restart_failed",
        "policy add failed": "policy_apply_failed",
        "allowed_ips": "policy_address_pins",
        "notebook failed": "notebook_failed",
        "AssertionError": "assertion_failed",
        "still not answering from a forward": "dashboard_forward_failed",
        "config set failed": "config_set_failed",
        "scope upgrade": "scope_upgrade",
        "gateway startup timed out": "gateway_startup_timeout",
        "timed out": "timeout",
        "unreachable": "unreachable",
        "unhealthy": "unhealthy",
        "CONNECT tunnel failed": "egress_tunnel_failed",
        "configuration error": "configuration_error",
        "not configured": "not_configured",
        "403": "http_403",
        "502": "http_502",
    }
    with path.open() as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            item = event.get("item")
            if not isinstance(item, dict) or item.get("type") != "command_execution":
                continue
            command = item.get("command")
            if not isinstance(command, str):
                continue
            kind = tool_kind(command)
            if event.get("type") == "item.started":
                started += 1
            if event.get("type") == "item.completed":
                completed += 1
                kinds[kind] += 1
                rc = item.get("exit_code")
                rc = rc if type(rc) is int and -255 <= rc <= 255 else None
                recent.append({"kind": kind, "exit_code": rc})
                if rc not in (0, None):
                    output = item.get("aggregated_output", "")
                    if isinstance(output, str):
                        if kind == 'setup_notebook':
                            clean = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', output)
                            cell = clean.rsplit('An error occurred while executing the following cell:', 1)[-1]
                            cell = cell.split('----- stdout -----', 1)[0].split('Traceback', 1)[0]
                            stage = 'other'
                            for marker, label in [('AGENT_IMAGE_DOCKERFILE', 'onboarding'), ('_policy_add_cmd', 'network_policy'), ('restart_agent_gateway', 'webhook_config'), ('_forward_owned', 'dashboard'), ('workspace docs', 'workspace_verify')]:
                                if marker in cell:
                                    stage = label
                                    break
                            last_failed_notebook = {'stage': stage}
                            stages = re.findall(r'\[([1-8])/8\]', clean)
                            last_failed_notebook['onboard_last_stage'] = int(stages[-1]) if stages else None
                            failure_lines = '\n'.join(line.lower() for line in clean.splitlines() if re.search(r'(failed|failure|error|not ready|not healthy|incomplete|timeout|timed out|unavailable|pending approval)',line,re.I) and not line.lstrip().startswith(('\"', '\'', '#')))
                            last_failed_notebook['verification_failures'] = []
                            for line in clean.splitlines():
                                matched = re.search(r'[✗!]\s+(gateway|dashboard|inference|api):', line)
                                if matched:
                                    codes = re.findall(r'HTTP ([0-9]{3})', line)
                                    last_failed_notebook['verification_failures'].append({'link':matched.group(1),'http_code':int(codes[0]) if codes else None,'sandbox_unreachable':'sandbox unreachable' in line})
                            last_failed_notebook['pairing_incomplete_reasons'] = [key for key, cause in {'runtime-identity-invalid': 'its recorded OpenClaw runtime identity changed or is invalid', 'pairing-lock-unavailable': 'NemoClaw could not acquire the pairing settlement locks', 'pairing-unavailable': 'its canonical CLI device pairing did not appear', 'scope-warmup-failed': 'the bounded CLI scope warm-up could not run', 'scope-upgrade-not-requested': 'its canonical CLI scope upgrade was not requested', 'scope-upgrade-not-approved': 'its canonical CLI scope upgrade remained pending', 'scope-upgrade-rejected': 'the sandbox watcher rejected its canonical CLI scope upgrade', 'scope-upgrade-approval-timeout': 'the sandbox watcher timed out while approving its canonical CLI scope upgrade', 'scope-upgrade-approval-failed': 'the sandbox watcher failed to approve its canonical CLI scope upgrade', 'scope-upgrade-watcher-unavailable': 'the sandbox scope-upgrade approval watcher was not running'}.items() if cause in clean]
                            last_failed_notebook['process_recovery_incomplete'] = 'required process or secret-boundary check did not pass' in clean
                            last_failed_notebook['error_terms'] = [term for term in ['gateway','sandbox','pairing','device','scope','supervisor','webhook','origin','port','provider','inference','validation','timeout','health','startup','preflight','ssrf','upload','policy','image','build','forward','watcher','unavailable','approval'] if re.search(r'\b'+term+r'\b',failure_lines)]
                            for marker, label in [('ENV.md upload failed', 'workspace_upload'), ('policy add failed', 'policy_apply'), ('onboard failed', 'onboarding'), ('gateway is down after', 'gateway_restart'), ('origin', 'origin')]:
                                if any(marker in line for line in clean.splitlines() if re.match(r'^(AssertionError|RuntimeError):', line)):
                                    last_failed_notebook['failure_kind'] = label
                                    break
                        recent[-1]['failure_signals'] = [signal for needle,signal in signals.items() if needle in output]
                        for needle, signal in signals.items():
                            if needle in output:
                                errors[signal] += 1
    return {
        "bytes": path.stat().st_size,
        "modified_at": path.stat().st_mtime,
        "tools_started": started,
        "tools_completed": completed,
        "completed_tool_kinds": dict(kinds),
        "failed_tool_signals": dict(errors),
        "last_failed_notebook": last_failed_notebook,
        "recent_completed_tools": recent[-12:],
    }


def worker(run_id):
    matches = []
    sandbox = None
    for process in Path("/proc").iterdir():
        if not process.name.isdigit():
            continue
        try:
            entries = (process / "environ").read_bytes().split(b"\0")
            if f"GITHUB_RUN_ID={run_id}".encode() not in entries:
                continue
            for entry in entries:
                if entry.startswith(b"NEMOCLAW_SANDBOX_NAME="):
                    candidate = entry.split(b"=", 1)[1].decode("utf-8", "replace")
                    if re.fullmatch(r"se-[A-Za-z0-9-]{1,100}", candidate):
                        sandbox = candidate
            executable = (process / "exe").resolve().name
            if executable in {"node", "python3", "python3.13", "codex", "bash", "sh", "openshell"}:
                matches.append(executable)
        except OSError:
            continue
    report = {"run_id": run_id, "matching_process_kinds": dict(Counter(matches))}
    if matches:
        path = Path("/logs/agent/codex.txt")
        if path.is_file():
            report["coding_trace_metadata"] = summarize_log(path)
        result = subprocess.run(
            ["docker", "ps", "--format", "{{json .}}"],
            capture_output=True, text=True, timeout=10,
        )
        states = Counter()
        for line in result.stdout.splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            name = row.get("Names", "")
            group = "vss" if name.startswith("vss-") else "nim" if name.startswith("skill-eval-nim-") else "other"
            state = "healthy" if "(healthy)" in row.get("Status", "") else "running"
            states[f"{group}_{state}"] += 1
        report["running_containers"] = dict(states)
        if sandbox:
            try:
                session = json.loads((Path.home()/'.nemoclaw/onboard-session.json').read_text())
            except (OSError, ValueError):
                session = {}
            if session.get('sandboxName') == sandbox:
                phases = {'init','preflight','gateway','provider_selection','inference','sandbox','agent_setup','openclaw','policies','finalizing','post_verify','completed','failed','cancelled'}
                report['onboard_session'] = {key:session.get(key) if session.get(key) in phases else 'other' for key in ['lastStepStarted','lastCompletedStep']}
                machine = (session.get('machine') or {}).get('state')
                report['onboard_session']['machine_state'] = machine if machine in phases else 'other'
            openshell = str(Path.home() / ".local/bin/openshell")
            state = subprocess.run([openshell, "sandbox", "get", sandbox, "-o", "json"], capture_output=True, text=True, timeout=15)
            report["sandbox_get_exit_code"] = state.returncode
            if state.returncode == 0:
                data = json.loads(state.stdout)
                phase = data.get("phase")
                report["sandbox_phase"] = phase if phase in {"Ready", "Running", "Pending", "Stopped", "Creating", "Provisioning"} else "other"
                code = r'''
import json, pathlib, socket, os
from urllib.parse import urlsplit
from collections import Counter
processes=Counter()
for proc in pathlib.Path('/proc').iterdir():
    if not proc.name.isdigit():
        continue
    try:
        command=(proc/'cmdline').read_bytes().replace(b'\x00',b' ')
        for marker,label in [(b'openclaw-gateway','gateway'), (b'gateway run','gateway'), (b'nemoclaw-start','launcher'), (b'nemoclaw-auto-pair','pair_watcher'), (b'nemoclaw-supervisor','supervisor')]:
            if marker in command:
                processes[label]+=1
                break
    except OSError:
        pass
def read(path):
    try:
        return json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError):
        return {}
config = read('/sandbox/.openclaw/openclaw.json')
gateway = config.get('gateway', {})
listeners = {}
for port in (18789, 18790):
    with socket.socket() as client:
        client.settimeout(1)
        listeners[str(port)] = client.connect_ex(('127.0.0.1', port)) == 0
vss_config = read(pathlib.Path.home() / '.vss/config.json')
memory = vss_config.get('memory') or {}
def origin(value):
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        kind = 'loopback' if host in ['localhost','127.0.0.1','::1'] else 'managed_host' if host in ['host.openshell.internal','host.docker.internal'] else 'other'
        return {'host_kind':kind,'port':parsed.port,'https':parsed.scheme=='https'}
    except (TypeError, ValueError):
        return {'host_kind':'unavailable'}
service_origins={k:origin(v.get('url')) for k,v in (vss_config.get('services') or {}).items() if k in ['elasticsearch','rt_vlm','vst','agent','lvs'] and isinstance(v,dict)}
proxy_metadata={}
for key in ['http_proxy','HTTP_PROXY','https_proxy','HTTPS_PROXY']:
    value=os.environ.get(key)
    if not value:
        continue
    metadata=origin(value)
    try:
        target=urlsplit(value)
        if target.hostname and target.port:
            with socket.socket() as client:
                client.settimeout(1)
                metadata['tcp_reachable']=client.connect_ex((target.hostname,target.port))==0
    except (OSError, ValueError):
        metadata['tcp_reachable']=False
    proxy_metadata[key]=metadata
status = read('/tmp/nemoclaw-auto-pair-status.json').get('state')
log_signals = {}
for path, label in [('/tmp/gateway.log','gateway'),('/tmp/nemoclaw-start.log','launcher')]:
    try:
        data = pathlib.Path(path).read_text(errors='replace')[-200000:]
    except OSError:
        continue
    needles = {
        'Invalid config':'invalid_config', 'Config validation failed':'invalid_config',
        'Cannot find module':'module_missing', 'Cannot find package':'module_missing',
        'EADDRINUSE':'port_in_use', 'Permission denied':'permission_denied',
        'scope upgrade':'scope_upgrade', 'pairing required':'pairing_required',
        'Unknown config':'unknown_config', 'timed out':'timeout',
        'Error loading plugin':'plugin_load', 'plugin failed':'plugin_load',
        'config hash':'config_hash', 'SANDBOX_CONFIG_HASH_MISMATCH':'config_hash',
        'OpenClaw child OOM-score':'oom_score', 'OOM':'oom',
        'OPENCLAW_DEVICE_AUTH':'device_auth', 'SIGTERM':'sigterm',
    }
    log_signals[label] = sorted(set(value for needle,value in needles.items() if needle in data))
print(json.dumps({
    'gateway_port': gateway.get('port') if type(gateway.get('port')) is int else None,
    'gateway_listeners': listeners,
    'process_kinds':dict(processes),
    'startup_log_signals':log_signals,
    'vss_base_origin':origin(vss_config.get('base_url')),
    'vss_service_origins':service_origins,
    'proxy_metadata':proxy_metadata,
    'managed_host_in_no_proxy':any(host in (os.environ.get('no_proxy','')+os.environ.get('NO_PROXY','')) for host in ['host.openshell.internal','host.docker.internal']),
    'pending_devices': len(read('/sandbox/.openclaw/devices/pending.json')),
    'paired_devices': len(read('/sandbox/.openclaw/devices/paired.json')),
    'pair_watcher_state': status if status in ['running','stopped','request-not-produced','request-observed','request-rejected','approval-timeout','approval-failed','approval-completed','canonical-settled'] else 'other',
    'sandbox_memory_enabled': memory.get('enabled') is True,
    'sandbox_introspection_enabled': (memory.get('introspection') or {}).get('enabled') is True,
}))
'''
                probe = subprocess.run([openshell, "sandbox", "exec", "-n", sandbox, "--", "python3", "-c", code], capture_output=True, text=True, timeout=20)
                report["sandbox_metadata_exit_code"] = probe.returncode
                if probe.returncode == 0:
                    report["sandbox_metadata"] = json.loads(probe.stdout.strip().splitlines()[-1])
    return report


def coordinator(run_id):
    trials = []
    for path in Path("/tmp/skill-eval/results").glob(f"*/{run_id}/*/step-*__*/result.json"):
        step = path.parent.name.split("__")[0]
        if not re.fullmatch(r"step-[0-9]+", step):
            continue
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        info = data.get("exception_info") or {}
        exception = info.get("exception_type") if isinstance(info, dict) else None
        reward = ((data.get('verifier_result') or {}).get('rewards') or {}).get('reward')
        try:
            judge = json.loads((path.parent/'verifier/judge.json').read_text())
        except (OSError, ValueError):
            judge = {}
        trials.append({
            "step": step,
            "reward": reward if type(reward) in [int,float] and 0 <= reward <= 1 else None,
            "checks_passed": judge.get('passed') if type(judge.get('passed')) is int else None,
            "checks_total": judge.get('total') if type(judge.get('total')) is int else None,
            "failed_judge_rows": [n for n,row in enumerate(judge.get('checks') or [],1) if isinstance(row,dict) and row.get('pass') is False],
            "finished": bool(data.get("finished_at")),
            "exception": exception if isinstance(exception, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,100}", exception) else None,
        })
    trace_metadata = []
    for path in Path('/tmp/skill-eval/results').glob(f'*/{run_id}/trace-urls.tsv'):
        for line in path.read_text().splitlines():
            parts = line.split('\t')
            if len(parts) != 3:
                continue
            parsed = urlsplit(parts[2])
            route = parsed.path.split('/')
            if parsed.scheme != 'https' or parsed.hostname != 'harbor-b742km29r.brevlab.com' or parsed.query or parsed.fragment or len(route) < 5 or route[1] != 'jobs' or route[3] != 'tasks' or run_id not in route[2]:
                continue
            row = {'step':parts[0],'url':parts[2]}
            try:
                with urlopen('http://127.0.0.1:8080/api/jobs/'+quote(route[2],safe='')+'/tasks',timeout=5) as response:
                    data = json.load(response)
                    row['viewer_http_status'] = response.status
                    row['viewer_task_count'] = len(data) if isinstance(data,list) else len(data.get('tasks') or []) if isinstance(data,dict) else None
            except Exception:
                row['viewer_available'] = False
            trace_metadata.append(row)
    command = shlex.join(["python3", "-", "--worker", run_id])
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2", "spark-ba-wifi", command],
        input=Path(__file__).read_text(), capture_output=True, text=True, timeout=45,
    )
    report = {"run_id": run_id, "completed_trial_metadata": trials, "trace_metadata":trace_metadata, "worker_probe_exit_code": result.returncode}
    if result.returncode == 0:
        report["worker"] = json.loads(result.stdout)
    else:
        for error_type in ("FileNotFoundError", "TimeoutExpired", "JSONDecodeError", "KeyError", "AttributeError", "TypeError", "PermissionError"):
            if re.search(r"^" + error_type + r":", result.stderr, re.MULTILINE):
                report["worker_error_type"] = error_type
    # Never print transport errors or raw remote output.
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("run_id")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9]{1,20}", args.run_id):
        parser.error("run_id must contain only digits")
    print(json.dumps(worker(args.run_id) if args.worker else coordinator(args.run_id), indent=2))
