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
from urllib.error import HTTPError, URLError
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
    kinds, errors, event_types = Counter(), Counter(), Counter()
    launch_signals = set()
    recent = []
    started, completed = 0, 0
    inference_writes = []
    credential_route_commands = []
    last_failed_notebook = None
    last_started_tool = None
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
            for needle, label in {
                "401": "http_401", "403": "http_403", "429": "http_429",
                "invalid api key": "authentication", "model not found": "model_unavailable",
                "unknown model": "model_unavailable", "Connection refused": "connection_refused",
                "Permission denied": "permission_denied", "not found": "missing_dependency",
                "stream disconnected": "stream_disconnected", "Reconnecting": "reconnecting",
                "failed to": "launch_failure", "Error:": "launch_error",
            }.items():
                if needle in line:
                    launch_signals.add(label)
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            event_type = event.get("type")
            if isinstance(event_type, str) and re.fullmatch(r"[A-Za-z_.-]{1,80}", event_type):
                event_types[event_type] += 1
            item = event.get("item")
            if not isinstance(item, dict) or item.get("type") != "command_execution":
                continue
            command = item.get("command")
            if not isinstance(command, str):
                continue
            if event.get('type') == 'item.completed' and any(marker in command for marker in ['COMPATIBLE_API_KEY', 'SKILL_EVAL_LOCAL_NIM_API_KEY', 'provider update', 'provider create', 'inference set']):
                output = item.get('aggregated_output') or ''
                credential_route_commands.append({
                    'notebook': 'run_setup_notebook.py' in command,
                    'provider_mutation': any(marker in command for marker in ['provider update', 'provider create']),
                    'inference_mutation': 'inference set' in command,
                    'placeholder_key_assignment': bool(re.search(r'COMPATIBLE_API_KEY\s*=\s*[\"\']?(?:EMPTY|unused)', command)),
                    'eval_key_reference': 'SKILL_EVAL_LOCAL_NIM_API_KEY' in command,
                    'nvidia_key_reference': 'NVIDIA_API_KEY' in command,
                    'comp_key_reference': 'COMPATIBLE_API_KEY' in command,
                    'reused_gateway_credential': 'Reusing existing gateway credential' in output,
                    'key_lengths': [int(n) for n in re.findall(r'COMPATIBLE_API_KEY=\(set, ([0-9]+) chars\)', output)],
                    'exit_code': item.get('exit_code') if type(item.get('exit_code')) is int else None,
                })
            if event.get("type") == "item.completed" and any(marker in command for marker in ['openclaw.json', 'models.providers.inference', 'inference set']):
                if any(marker in command for marker in ['write_text', 'json.dump', 'sed -i', 'config set', 'inference set']):
                    inference_writes.append({
                        'direct_adapter': bool(re.search(r'https?://[^\s\"\']+:18400', command)),
                        'managed_route': 'inference.local' in command,
                        'placeholder_key': bool(re.search(r'(?:apiKey|COMPATIBLE_API_KEY).{0,40}(?:unused|EMPTY)', command)),
                        'credential_env_ref': any(k in command for k in ['SKILL_EVAL_LOCAL_NIM_API_KEY', 'COMPATIBLE_API_KEY']),
                        'exit_code': item.get('exit_code') if type(item.get('exit_code')) is int else None,
                    })
            kind = tool_kind(command)
            if event.get("type") == "item.started":
                started += 1
                last_started_tool = {
                    "kind": kind,
                    "notebook_execution": "run_setup_notebook.py" in command and "--notebook" in command,
                    "notebook_inspection": "run_setup_notebook.py" in command and any(part in command for part in ["sed ", "rg ", "cat ", "--help"]),
                    "local_model_probe": "/v1/models" in command or "/health/ready" in command,
                }
            if event.get("type") == "item.completed":
                completed += 1
                kinds[kind] += 1
                rc = item.get("exit_code")
                rc = rc if type(rc) is int and -255 <= rc <= 255 else None
                recent.append({"kind": kind, "exit_code": rc})
                if kind == "setup_notebook":
                    recent[-1]["notebook_execution"] = "--notebook" in command
                    recent[-1]["notebook_inspection"] = any(part in command for part in ["sed ", "rg ", "cat ", "--help"])
                if kind == 'compose':
                    recent[-1]['actions']=[label for marker,label in [(' up ','up'), (' down','down'), (' build','build'), (' config','config'), (' pull','pull'), (' restart','restart'), (' stop','stop'), (' ps','ps')] if marker in command]
                recent[-1]['global_container_operation'] = any(marker in command for marker in ['docker ps -aq','docker ps -q','docker system prune','docker container prune'])
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
        "json_event_types": dict(event_types),
        "launch_signals": sorted(launch_signals),
        "completed_tool_kinds": dict(kinds),
        "failed_tool_signals": dict(errors),
        "last_failed_notebook": last_failed_notebook,
        "last_started_tool": last_started_tool,
        "inference_write_commands": inference_writes,
        "credential_route_commands": credential_route_commands,
        "recent_completed_tools": recent[-12:],
    }


def worker(run_id):
    matches = []
    sandbox = None
    nim_owner = None
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
            for entry in entries:
                if entry.startswith(b"SKILL_EVAL_LOCAL_NIM_PLAN="):
                    try:
                        plan = json.loads(entry.split(b"=", 1)[1])
                        if re.fullmatch(r"[a-f0-9]{24}", plan.get("owner", "")):
                            nim_owner = plan["owner"]
                    except (ValueError, TypeError):
                        pass
            executable = (process / "exe").resolve().name
            if re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", executable):
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
        report["docker_ps_exit_code"] = result.returncode
        vss = subprocess.run([
            "docker", "ps", "-a", "--filter", "label=com.docker.compose.project=vss",
            "--format", '{{.Label "com.docker.compose.service"}}\t{{.State}}\t{{.Status}}\t{{.Image}}',
        ], capture_output=True, text=True, timeout=10)
        services = []
        for line in vss.stdout.splitlines():
            values = line.split("\t")
            if len(values) != 4:
                continue
            service, state, status, image = values
            if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,100}", service):
                continue
            row = {"service": service, "state": state if state in ["running", "created", "exited", "restarting"] else "other", "healthy": "(healthy)" in status}
            row["requested_lightning_nim"] = image.startswith("nvcr.io/nim/nvidia/nemotron-3.5-lightning-30b-a3b")
            services.append(row)
        report["vss_services"] = services
        nim = subprocess.run([
            "docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=vss",
        ], capture_output=True, text=True, timeout=10)
        if nim.stdout.strip():
            inspected = subprocess.run(["docker", "inspect", *nim.stdout.split()], capture_output=True, text=True, timeout=10)
            probes = []
            for container in json.loads(inspected.stdout) if inspected.returncode == 0 else []:
                image = container.get("Config", {}).get("Image", "")
                if not image.startswith("nvcr.io/nim/nvidia/nemotron-3.5-lightning-30b-a3b"):
                    continue
                state = container.get("State", {})
                row = {"running":state.get("Running") is True, "oom_killed":state.get("OOMKilled") is True, "restart_count":container.get("RestartCount"), "health_status":(state.get("Health") or {}).get("Status")}
                processes = subprocess.run(["docker", "top", container["Id"], "-eo", "comm"], capture_output=True, text=True, timeout=10)
                row["process_kinds"] = dict(Counter(name for name in processes.stdout.splitlines()[1:] if re.fullmatch(r"[A-Za-z0-9_.:/-]{1,100}", name)))
                cached = subprocess.run(["docker", "exec", container["Id"], "du", "-sk", "/opt/nim/.cache"], capture_output=True, text=True, timeout=10)
                parts = cached.stdout.split()
                row["cache_kib"] = int(parts[0]) if parts and parts[0].isdigit() else None
                ports = container.get("NetworkSettings", {}).get("Ports", {}).get("8000/tcp") or []
                if ports and str(ports[0].get("HostPort", "")).isdigit():
                    port = int(ports[0]["HostPort"])
                    if 1 <= port <= 65535:
                        for route, label in [("/v1/health/ready", "readiness_http"), ("/v1/models", "models_http")]:
                            try:
                                with urlopen(f"http://127.0.0.1:{port}{route}", timeout=3) as response:
                                    row[label] = response.status
                                    if label == "models_http":
                                        names = [m.get("id") for m in json.load(response).get("data", [])]
                                        row["requested_model_advertised"] = names == ["nvidia/nemotron-3.5-lightning-30b-a3b"]
                            except HTTPError as error:
                                row[label] = error.code
                            except (OSError, URLError, TimeoutError):
                                row[label] = 0
                logs = subprocess.run(["docker", "logs", "--tail", "1000", container["Id"]], capture_output=True, text=True, timeout=10)
                data = logs.stdout + logs.stderr
                row["log_signals"] = [label for needle,label in [("Downloading", "downloading"), ("Loading", "loading"), ("CUDA graph", "cuda_graph"), ("Application startup complete", "api_started"), ("Traceback", "traceback"), ("out of memory", "out_of_memory"), ("ValueError", "value_error"), ("AssertionError", "assertion_error"), ("max_model_len", "model_context"), ("GPU memory", "gpu_memory")] if needle.lower() in data.lower()]
                # Bound and redact relevant startup lines, never credentials or
                # entire raw logs. Model-serving logs can contain signed URLs.
                secrets = []
                for value in container.get("Config", {}).get("Env", []):
                    name, sep, secret = value.partition("=")
                    if sep and secret and re.search(r"key|token|secret|password|credential", name, re.I):
                        secrets.append(secret)
                for secret in secrets:
                    data = data.replace(secret, "[redacted]")
                data = re.sub(r"https?://[^\s<>]+", "[url]", data)
                data = re.sub(r"(?i)(bearer\s+|(?:token|key|secret|password)\s*[=:]\s*)[^\s,;]+", r"\1[redacted]", data)
                row["startup_messages"] = [line[:400] for line in data.splitlines() if re.search(r"error|download|loading|profile|snapshot|engine", line, re.I) and not re.search(r"(?:GET|POST) /v1/", line)][-8:]
                probes.append(row)
            report["vss_nim_probes"] = probes
        if nim_owner:
            try:
                ready = json.loads((Path.home()/'.cache/skill-eval-nim'/nim_owner/'ready.json').read_text())
            except (OSError, ValueError):
                ready = {}
            owned = subprocess.run([
                "docker", "ps", "--filter", f"label=vss.skill-eval.nim-owner={nim_owner}",
                "--format", "{{.Names}}",
            ], capture_output=True, text=True, timeout=10)
            names = owned.stdout.split()
            sources = Counter(model.get('source', 'unspecified') for model in ready.get('models', []))
            report['local_model_selection'] = {
                'operational_pending': ready.get('operational_pending') is True,
                'operational_prepared': ready.get('operational_prepared') is True,
                'selection': ready.get('selection') if ready.get('selection') in ['matching-vss-nim', 'no-matching-vss-nim'] else 'not-recorded',
                'model_sources': {k:v for k,v in sources.items() if k in ['vss', 'eval', 'unspecified']},
                'owned_nim_containers': sum(bool(re.fullmatch(r'skill-eval-nim-[a-f0-9]{24}-[0-9]+', n)) for n in names),
                'owned_proxy_containers': sum(n.endswith('-proxy') for n in names),
            }
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
    process_kinds=Counter()
    run_leg_pids=[]
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit(): continue
        try:
            entries=(proc/'environ').read_bytes().split(b'\0')
            if f'GITHUB_RUN_ID={run_id}'.encode() not in entries: continue
            command=(proc/'cmdline').read_bytes()
            kind='run_leg' if b'run_leg.py' in command else 'coordinator_agent' if b'skills_eval_agent.py' in command else 'harbor' if b'harbor' in command else 'other'
            process_kinds[kind]+=1
            if kind=='run_leg': run_leg_pids.append(int(proc.name))
        except OSError: pass
    lock_rows=[]
    for lock in Path('/tmp/brev').glob('*.lock'):
        if lock.name not in ['Spark-ba-WiFi.lock','spark-ba-wifi.lock']: continue
        try:
            inode=lock.stat().st_ino
            for line in Path('/proc/locks').read_text().splitlines():
                parts=line.split()
                if len(parts)>5 and parts[1]=='FLOCK' and parts[5].endswith(':'+str(inode)):
                    owner_run=None
                    try:
                        for value in Path('/proc/'+parts[4]+'/environ').read_bytes().split(b'\0'):
                            if value.startswith(b'GITHUB_RUN_ID='):
                                candidate=value.split(b'=',1)[1].decode()
                                if re.fullmatch(r'[0-9]{1,20}', candidate):
                                    owner_run=candidate
                    except (OSError,UnicodeError): pass
                    lock_rows.append({'worker':'Spark-ba-WiFi','owned_by_requested_leg':int(parts[4]) in run_leg_pids,'owner_monitored_run':owner_run})
        except (OSError,ValueError): pass
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
        tool_metadata = {'available': False}
        trajectory_path = path.parent / 'agent/trajectory.json'
        try:
            trajectory = json.loads(trajectory_path.read_text())
            tool_metadata = {'available': True, 'tool_counts': {}, 'startup_doc_reads': 0, 'markdown_memory_reads': 0, 'vlm_calls': 0, 'introspection_calls': 0, 'inference_config_write_commands': 0, 'direct_adapter_config_write_commands': 0, 'api_key_config_write_commands': 0}
            counts = Counter()
            for entry in trajectory.get('steps') or []:
                if not isinstance(entry,dict) or entry.get('source') != 'agent':
                    continue
                for call in entry.get('tool_calls') or []:
                    if not isinstance(call,dict):
                        continue
                    name = call.get('function_name')
                    arguments = call.get('arguments')
                    if isinstance(arguments,str):
                        try: arguments=json.loads(arguments)
                        except ValueError: arguments={}
                    arguments=arguments if isinstance(arguments,dict) else {}
                    counts[name if name in ['Bash','Read','read','Skill','skill','vss_cli','memory_search','exec','tool_call'] else 'other']+=1
                    filename=arguments.get('path') or arguments.get('file_path')
                    if isinstance(filename,str) and name in ['Read','read']:
                        if Path(filename).name in ['ENV.md','AGENTS.md','SKILL.md']:
                            tool_metadata['startup_doc_reads']+=1
                        if Path(filename).name == 'MEMORY.md' or '/memory/' in filename:
                            tool_metadata['markdown_memory_reads']+=1
                    args=arguments.get('args')
                    command = arguments.get('command') or arguments.get('cmd')
                    if isinstance(command, str) and 'openclaw.json' in command:
                        writes_config = any(marker in command for marker in ['write_text', 'json.dump', 'sed -i', 'config set'])
                        if writes_config and any(marker in command for marker in ['baseUrl', 'apiKey', "['inference']", '["inference"]']):
                            tool_metadata['inference_config_write_commands'] += 1
                            tool_metadata['direct_adapter_config_write_commands'] += bool(re.search(r'https?://[^\s\"\']+:18400', command))
                            tool_metadata['api_key_config_write_commands'] += 'apiKey' in command
                    if name == 'vss_cli' and isinstance(args,list):
                        tool_metadata['vlm_calls']+=args[:2]==['vlm','run']
                        tool_metadata['introspection_calls']+=args[:2]==['memory','introspect']
            tool_metadata['tool_counts']=dict(counts)
        except (OSError,ValueError,TypeError):
            pass
        trials.append({
            'leg':path.parts[-5],
            'trajectory_tool_metadata':tool_metadata,
            "step": step,
            "reward": reward if type(reward) in [int,float] and 0 <= reward <= 1 else None,
            "checks_passed": judge.get('passed') if type(judge.get('passed')) is int else None,
            "checks_total": judge.get('total') if type(judge.get('total')) is int else None,
            "failed_judge_rows": [n for n,row in enumerate(judge.get('checks') or [],1) if isinstance(row,dict) and row.get('pass') is False],
            "finished": bool(data.get("finished_at")),
            "exception": exception if isinstance(exception, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,100}", exception) else None,
        })
    trace_metadata = []
    viewer_coding_traces = []
    for directory in Path('/tmp/skill-eval/results/_viewer').glob(f'*__{run_id}__*'):
        if not directory.is_dir():
            continue
        for path in directory.glob('step-*__*/agent/codex.txt'):
            viewer_coding_traces.append({'job': directory.name, 'step':path.parent.parent.name.split('__')[0], 'metadata': summarize_log(path)})
    version_traces = []
    for base in [Path('/tmp/skill-eval/results'), Path('/tmp/skill-eval/results/_viewer')]:
        for path in base.glob(f'*{run_id}*/**/agent/trajectory.json'):
            if 'vss-manage-alerts' not in str(path):
                continue
            try:
                raw = path.read_text()
            except OSError:
                continue
            # Emit only known public VSS image references and hex revisions;
            # never raw commands, request bodies, environments or log text.
            refs = sorted(set(re.findall(r'(?:ghcr\.io/nvidia-ai-blueprints/vss|nvcr\.io/(?:nvidia|nvstaging)/vss-core)/vss-[a-z0-9-]+:[a-zA-Z0-9_.-]+', raw)))
            shape = {}
            try:
                parsed_trace = json.loads(raw)
                shape['json_kind'] = type(parsed_trace).__name__
                if isinstance(parsed_trace,dict):
                    shape['keys'] = [key for key in parsed_trace if re.fullmatch(r'[A-Za-z_]{1,50}',key)]
                    for key in ['steps','messages','trajectory']:
                        entries = parsed_trace.get(key)
                        if isinstance(entries,list) and entries and isinstance(entries[0],dict):
                            shape[key+'_entry_keys'] = [k for k in entries[0] if re.fullmatch(r'[A-Za-z_]{1,50}',k)]
            except ValueError:
                shape['json_kind'] = 'invalid'
            commands = []
            def visit(value, source=None):
                if isinstance(value, dict):
                    source = value.get("source", source)
                    for key, item in value.items():
                        if isinstance(item,str):
                            images = sorted(set(re.findall(r'(?:ghcr\.io/nvidia-ai-blueprints/vss|nvcr\.io/(?:nvidia|nvstaging)/vss-core)/vss-[a-z0-9-]+:[a-zA-Z0-9_.-]+', item)))
                            actions = [label for marker,label in [('docker run','docker_run'),('docker compose','compose'),('git clone','clone'),('git checkout','checkout'),('sed ','edit_or_read'),('cat ','read_or_write')] if marker in item]
                            branch = re.findall(r'(?:--branch|-b)\s+[\"\']?([A-Za-z0-9_.-]{1,80})',item) if 'git clone' in item else []
                            if images or branch:
                                commands.append({'actions':actions,'image_refs':images,'clone_branches':branch,'source':source if source in ['agent','environment','user','system'] else None,'mentions_registry_denied':'denied' in item.lower(),'mentions_fallback':'fallback' in item.lower() or 'fall back' in item.lower()})
                        visit(item, source)
                elif isinstance(value,list):
                    for item in value: visit(item, source)
                elif isinstance(value,str) and value.startswith('{'):
                    try: visit(json.loads(value), source)
                    except ValueError: pass
            try: visit(json.loads(raw))
            except ValueError: pass
            version_traces.append({'trajectory_shape':shape if path.parent.parent.name == 'step-1__SzoAfci' else {},'image_command_metadata':commands[:50] if path.parent.parent.name in ['step-1__SzoAfci','step-1__xpTbtgP'] else [],'trial':path.parent.parent.name,
                                   'image_references_mentioned':refs[:80],
                                   'source_tree_shas_mentioned':sorted(set(re.findall(r'\b[0-9a-f]{40}\b', raw)))[:40],
                                   'legacy_alert_image_mentioned':'vss-alert-verification' in raw,
                                   'source_clone_mentioned':bool(re.search(r'git (?:clone|checkout|reset|fetch)',raw)),
                                   'old_release_mentioned':'3.1.0' in raw,
                                   'trajectory_bytes':len(raw)})
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
                    row['viewer_response_kind'] = 'list' if isinstance(data,list) else 'dict' if isinstance(data,dict) else 'other'
                    if isinstance(data,dict):
                        row['viewer_collection_keys'] = [k for k in ['tasks','data','agents','models','results','groups'] if k in data]
                    row['viewer_task_count'] = len(data) if isinstance(data,list) else len(data['tasks']) if isinstance(data,dict) and isinstance(data.get('tasks'),list) else None
            except Exception:
                row['viewer_available'] = False
            trace_metadata.append(row)
    command = shlex.join(["python3", "-", "--worker", run_id])
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2", "spark-ba-wifi", command],
        input=Path(__file__).read_text(), capture_output=True, text=True, timeout=45,
    )
    report = {"version_trace_metadata": version_traces, "run_id": run_id, "coordinator_process_kinds":dict(process_kinds),"worker_lock_metadata":lock_rows, "completed_trial_metadata": trials, "viewer_coding_trace_metadata":viewer_coding_traces, "trace_metadata":trace_metadata, "worker_probe_exit_code": result.returncode}
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
