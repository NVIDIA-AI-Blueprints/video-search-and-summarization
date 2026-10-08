# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Temporary read-only SSH metadata snapshot; no raw output is published."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import shlex
import subprocess


SANDBOX_SCRIPT = r'''
import json, os, re, subprocess
from pathlib import Path
from urllib.parse import urlsplit

def load(path):
    try: return json.loads(Path(path).read_text())
    except Exception: return None

def endpoint(value):
    try:
        url=urlsplit(value)
        return {'scheme':url.scheme,'host':url.hostname,'port':url.port}
    except Exception: return {'invalid':True}

def proxies(env):
    return {k:endpoint(env[k]) for k in ('HTTP_PROXY','HTTPS_PROXY','http_proxy','https_proxy') if k in env}

out={'exec_proxies':proxies(os.environ)}
out['python_executable']=str(Path('/usr/bin/python3').resolve())
try:
    shebang=Path('/usr/local/bin/vss').read_text().splitlines()[0]
    if re.fullmatch(r'#!/[A-Za-z0-9/_+.-]+',shebang):out['vss_shebang']=shebang
except Exception: out['vss_missing']=True
out['gateway_processes']=[]
try:processes=list(Path('/proc').iterdir())
except OSError:processes=[];out['proc_unreadable']=True
for p in processes:
    if not p.name.isdigit(): continue
    try:
        cmd=(p/'cmdline').read_bytes()
        if b'openclaw' not in cmd or b'gateway' not in cmd or Path(cmd.split(b'\0')[0].decode()).name not in {'node','openclaw','openclaw-gateway'}: continue
        env={k.decode():v.decode() for k,v in (x.split(b'=',1) for x in (p/'environ').read_bytes().split(b'\0') if b'=' in x)}
        out['gateway_processes'].append({'pid':int(p.name),'proxies':proxies(env),'no_proxy_has_host_alias':'host.openshell.internal' in env.get('no_proxy',env.get('NO_PROXY',''))})
    except Exception: continue
status=load('/tmp/nemoclaw-auto-pair-status.json')
if isinstance(status,dict) and status.get('state') in {'running','degraded','stopped','starting'}: out['pairing_watcher']=status['state']
config=load('/sandbox/.openclaw/openclaw.json')
if isinstance(config,dict):
    port=config.get('gateway',{}).get('port')
    if type(port) is int and 1024<=port<=65535:out['gateway_port']=port
config=load('/sandbox/.vss/config.json')
if isinstance(config,dict):
    out['vss_origin']=endpoint(config.get('base_url',''))
    out['vss_services']={k:endpoint(v.get('url','')) for k,v in config.get('services',{}).items() if k in {'vst','rt_vlm','elasticsearch','lvs','nvstreamer','video_analytics'} and isinstance(v,dict)}
out['read_only_vios_probe']={}
if isinstance(config,dict):
    url=config.get('services',{}).get('vst',{}).get('url','')
    parsed=urlsplit(url)
    if parsed.hostname=='host.openshell.internal' and parsed.scheme=='http' and parsed.port in {7777,30888}:
        result=subprocess.run(['/usr/bin/curl','--connect-timeout','3','--max-time','10','-sS','-o','/dev/null','-w','%{http_code}',url.rstrip('/')+'/api/v1/sensor/list'],capture_output=True,text=True,timeout=15)
        out['read_only_vios_probe']={'exit_code':result.returncode,'http_status':result.stdout if re.fullmatch(r'[0-9]{3}',result.stdout) else None}
out['devices']={}
for name in ('pending','paired'):
    devices=load('/sandbox/.openclaw/devices/'+name+'.json')
    if not isinstance(devices,dict): continue
    rows=[]
    for device in devices.values():
        if not isinstance(device,dict):continue
        row={}
        for k,allowed in [('clientId',{'cli','openclaw-cli','openclaw-control-ui','gateway-client'}),('clientMode',{'cli','ui','backend','webchat'}),('role',{'operator','node'})]:
            if isinstance(device.get(k),str) and device[k] in allowed:row[k]=device[k]
        scopes=device.get('scopes',[])
        if isinstance(scopes,list):row['scopes']=[s for s in scopes if isinstance(s,str) and s in {'operator.read','operator.write','operator.pairing','operator.approvals','operator.admin'}]
        for field in ('approvedScopes','requestedScopes'):
            scopes=device.get(field,[])
            if isinstance(scopes,list):row[field]=[s for s in scopes if s in {'operator.read','operator.write','operator.pairing','operator.approvals','operator.admin'}]
        tokens=device.get('tokens',{})
        if isinstance(tokens,dict):
            token=tokens.get('operator',{})
            if isinstance(token,dict):row['token_scopes']=[s for s in token.get('scopes',[]) if s in {'operator.read','operator.write','operator.pairing','operator.approvals','operator.admin'}]
        rows.append(row)
    out['devices'][name]=rows
out['vlm_errors']=[]
out['vios_errors']=[]
for session in sorted(Path('/sandbox/.openclaw/agents/main/sessions').glob('*.jsonl'),key=lambda p:p.stat().st_mtime,reverse=True)[:20]:
    calls=set(); vios_calls={}
    try:
        for line in session.read_text().splitlines():
            try:message=json.loads(line).get('message',{})
            except Exception:continue
            for block in message.get('content',[]) if isinstance(message.get('content'),list) else []:
                if isinstance(block,dict) and block.get('type')=='toolCall' and 'vss vlm run' in json.dumps(block.get('arguments',{})):
                    calls.add(block.get('id'))
                if isinstance(block,dict) and block.get('type')=='toolCall':
                    arguments=json.dumps(block.get('arguments',{}))
                    if '/vst/' in arguments or 'vss vios ' in arguments:
                        vios_calls[block.get('id')]=[endpoint(url) for url in re.findall(r'https?://[^\\\s"<>]+',arguments)][:5]
            if message.get('role')!='toolResult':continue
            text=json.dumps(message.get('content',[]))
            if message.get('toolCallId') in vios_calls and (re.search(r'\b403\b',text) or 'policy_denied' in text):
                out['vios_errors'].append({'kind':'policy_denied' if 'policy_denied' in text else 'http_403','request_origins':vios_calls[message.get('toolCallId')]})
            if message.get('toolCallId') not in calls:continue
            text=json.dumps(message.get('content',[]))
            if not re.search(r'\b403\b',text):continue
            urls=[endpoint(url) for url in re.findall(r'https?://[^\\\s"<>]+',text)]
            out['vlm_errors'].append({'kind':'proxy_tunnel_denied' if 'tunnel' in text.lower() or 'connect' in text.lower() else 'http_403','origins':urls[:5]})
    except Exception:continue
print(json.dumps(out))
'''


def run(args, *, env=None, timeout=30):
    try:
        result = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError) as exc:
        return {'error_type':type(exc).__name__}
    if result.returncode:
        output=result.stderr+'\n'+result.stdout
        reason = next((name for name in ('FileNotFoundError','PermissionError','TimeoutExpired','ValueError','SyntaxError') if name in output), None)
        markers={'sandbox_not_found':'sandbox not found','connection_refused':'connection refused','pairing_pending':'pairing required','permission_denied':'permission denied','unknown_argument':'unexpected argument','gateway_missing':'no gateway','registry_missing':'is not registered'}
        category=next((name for name,marker in markers.items() if marker in output.lower()),None)
        return {'exit_code':result.returncode, **({'error_type':reason} if reason else {}), **({'category':category} if category else {})}
    try: return json.loads(result.stdout)
    except ValueError:
        for line in reversed(result.stdout.splitlines()[-5:]):
            try:return json.loads(line)
            except ValueError:pass
        return {'invalid_json':True}


def host_snapshot():
    out = {'containers':[], 'setup_processes':[]}
    try:
        out['memory_available_mib']=int(re.search(r'^MemAvailable:\s+([0-9]+)',Path('/proc/meminfo').read_text(),re.M).group(1))//1024
        space=os.statvfs(str(Path.home()));out['disk_free_gib']=round(space.f_bavail*space.f_frsize/2**30,1)
    except Exception:pass
    uptime=float(Path('/proc/uptime').read_text().split()[0]);ticks=os.sysconf('SC_CLK_TCK')
    for process in Path('/proc').iterdir():
        if not process.name.isdigit() or int(process.name)==os.getpid():continue
        try: command=(process/'cmdline').read_bytes()
        except OSError:continue
        markers={'setup_notebook':b'run_setup_notebook.py','sandbox_build':b'openshell\x00sandbox\x00create','onboarding':b'nemoclaw\x00onboard'}
        category=next((key for key,value in markers.items() if value in command),None)
        if category:
            try:elapsed=round(uptime-float((process/'stat').read_text().rsplit(')',1)[1].split()[19])/ticks)
            except Exception:elapsed=None
            out['setup_processes'].append({'pid':int(process.name),'kind':category,'elapsed_seconds':elapsed})
    listing = subprocess.run(['docker','ps','--format','{{.Names}}'],capture_output=True,text=True,timeout=10)
    for name in listing.stdout.splitlines():
        if not (name.startswith('vss-vios-') or name.startswith('vss-rtvi-') or name.startswith('skill-eval-nim-') or name.startswith('openshell-')):continue
        info=run(['docker','inspect',name],timeout=10)
        if not isinstance(info,list) or not info:continue
        state=info[0].get('State',{})
        out['containers'].append({'name':name,'status':state.get('Status'),'health':state.get('Health',{}).get('Status'),'oom':state.get('OOMKilled'),'restarts':info[0].get('RestartCount')})
    env=os.environ.copy()
    env['PATH']=str(Path.home()/'.local/bin')+os.pathsep+env.get('PATH','/usr/local/bin:/usr/bin:/bin')
    lines=[]
    for path in (Path.home()/'.eval_env',Path('/tmp/skill-eval/nemoclaw/nemoclaw.env')):
        try:lines+=path.read_text().splitlines()
        except OSError:pass
    for line in lines:
        if not line.startswith('export '):continue
        key,_,value=line[7:].partition('=')
        if key in {'NEMOCLAW_SANDBOX_NAME','NEMOCLAW_GATEWAY_PORT','GITHUB_RUN_ID','PR_HEAD_SHA'}:
            parts=shlex.split(value)
            if parts:env[key]=parts[0]
    if re.fullmatch(r'[0-9]+',env.get('GITHUB_RUN_ID','')):out['active_eval_run']=env['GITHUB_RUN_ID']
    if re.fullmatch(r'[a-f0-9]{40}',env.get('PR_HEAD_SHA','')):out['active_eval_head']=env['PR_HEAD_SHA']
    sandbox=env.get('NEMOCLAW_SANDBOX_NAME','')
    if re.fullmatch(r'se-[0-9]+-[a-f0-9]+',sandbox):
        out['sandbox']=sandbox
        out['gateway_port']=env.get('NEMOCLAW_GATEWAY_PORT')
        wrapped='import json\ntry:\n exec('+repr(SANDBOX_SCRIPT)+')\nexcept Exception as exc:\n print(json.dumps({"collector_error_type":type(exc).__name__}))'
        command='python3 -c '+shlex.quote(wrapped)
        port=env.get('NEMOCLAW_GATEWAY_PORT','')
        if port.isdecimal() and 1024<=int(port)<=65535:
            out['native_access']=run(['openshell','sandbox','exec','--name',sandbox,'-g','nemoclaw-'+port,'--','sh','-lc','printf 1'],env=env,timeout=15)
            phase=run(['openshell','sandbox','get',sandbox,'-g','nemoclaw-'+port,'-o','json'],env=env,timeout=15)
            out['sandbox_phase']=phase.get('phase') if isinstance(phase,dict) and phase.get('phase') in {'Ready','Pending','Created','Creating','Starting','Error','Failed','Terminated'} else phase.get('category') if isinstance(phase,dict) else None
            if out['sandbox_phase']=='Ready':
                try:
                    audit=subprocess.run(['openshell','logs',sandbox,'-g','nemoclaw-'+port,'-n','300','--source','all'],stdin=subprocess.DEVNULL,capture_output=True,text=True,env=env,timeout=20)
                    out['audit_exit_code']=audit.returncode
                    out['denied_flows']=[]
                    for line in audit.stdout.splitlines():
                        if not any(marker in line for marker in ('NET:OPEN DENIED','policy_denied','DENIED')):continue
                        flow={}
                        match=re.search(r'(?:->|CONNECT)\s+([a-z0-9.-]+):([0-9]{1,5})\b',line)
                        if match:
                            flow['host']=match.group(1);flow['port']=int(match.group(2))
                        binary=re.search(r'DENIED (/[-A-Za-z0-9_./]+)\([0-9]+\)',line)
                        if binary:flow['binary']=binary.group(1)
                        if flow and flow not in out['denied_flows']:out['denied_flows'].append(flow)
                    out['denied_flows']=out['denied_flows'][-20:]
                except Exception as exc:out['audit_error_type']=type(exc).__name__
            invocation='if [ -s "$HOME/.nvm/nvm.sh" ]; then . "$HOME/.nvm/nvm.sh"; fi; '+shlex.join(['nemoclaw',sandbox,'exec','--no-tty','--no-stdin','--timeout','30','--','python3','-c',wrapped])
            out['native']=run(['bash','-lc',invocation],env=env,timeout=45)
            if isinstance(out['native'],dict) and out['native'].get('exit_code'):
                native_command='[ -r /tmp/nemoclaw-proxy-env.sh ] || exit 1; . /tmp/nemoclaw-proxy-env.sh || exit $?; unset OPENCLAW_GATEWAY_TOKEN; '+shlex.join(['python3','-c',wrapped])
                out['native_runtime']=run(['openshell','sandbox','exec','--name',sandbox,'-g','nemoclaw-'+port,'--','sh','-lc',native_command],env=env,timeout=45)
    out['readiness']=[]
    for path in Path('/logs/artifacts/nemoclaw').glob('*readiness.json'):
        try: report=json.loads(path.read_text())
        except Exception:continue
        for row in report.get('stages',[]):
            if isinstance(row,dict) and row.get('status') in {'passed','failed'} and row.get('stage') in {'sandbox_phase','sandbox_access','gateway_health','gateway_authentication','vss_configuration','local_inference','hosted_inference'}:
                out['readiness'].append({'stage':row['stage'],'status':row['status']})
    print(json.dumps(out))


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--worker')
    parser.add_argument('--out',type=Path)
    args=parser.parse_args()
    if not args.worker:
        host_snapshot()
    else:
        workers=args.worker.split(',')
        allowed={'Spark-ba-WiFi','vss-eval-l40s','vss-eval-l40s-1g',*(f'vss-eval-l40s-{n}' for n in range(2,7))}
        if not 1<=len(workers)<=4 or len(set(workers))!=len(workers) or any(worker not in allowed for worker in workers):raise ValueError('unexpected workers')
        script=Path(__file__).read_text()
        command='sh -lc '+shlex.quote('python3 -c '+shlex.quote(script))
        def inspect(worker):
            return run(['ssh','-T','-o','BatchMode=yes','-o','ConnectTimeout=10','-o','ConnectionAttempts=1',worker.lower(),command],timeout=150)
        with ThreadPoolExecutor(max_workers=4) as pool:
            snapshots=dict(zip(workers,pool.map(inspect,workers)))
        args.out.write_text(json.dumps(snapshots if len(workers)>1 else snapshots[workers[0]],indent=2)+'\n')
