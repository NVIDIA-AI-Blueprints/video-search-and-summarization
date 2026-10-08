# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Temporary read-only SSH metadata snapshot; no raw output is published."""

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess


SANDBOX_SCRIPT = r'''
import json, os, re
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
for p in Path('/proc').iterdir():
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
        rows.append(row)
    out['devices'][name]=rows
print(json.dumps(out))
'''


def run(args, *, env=None, timeout=30):
    result = subprocess.run(args, capture_output=True, text=True, env=env, timeout=timeout)
    if result.returncode:
        return {'exit_code':result.returncode}
    try: return json.loads(result.stdout)
    except ValueError: return {'invalid_json':True}


def host_snapshot():
    out = {'containers':[]}
    listing = subprocess.run(['docker','ps','--format','{{.Names}}'],capture_output=True,text=True,timeout=10)
    for name in listing.stdout.splitlines():
        if not (name.startswith('vss-vios-') or name.startswith('vss-rtvi-') or name.startswith('skill-eval-nim-')):continue
        info=run(['docker','inspect',name],timeout=10)
        if not isinstance(info,list) or not info:continue
        state=info[0].get('State',{})
        out['containers'].append({'name':name,'status':state.get('Status'),'health':state.get('Health',{}).get('Status'),'oom':state.get('OOMKilled'),'restarts':info[0].get('RestartCount')})
    env=os.environ.copy()
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
        out['native']=run(['openshell','sandbox','exec','--name',sandbox,'--','python3','-c',SANDBOX_SCRIPT],env=env,timeout=45)
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
        if args.worker not in {'Spark-ba-WiFi','vss-eval-l40s','vss-eval-l40s-1g',*(f'vss-eval-l40s-{n}' for n in range(2,7))}:raise ValueError('unexpected worker')
        script=Path(__file__).read_text()
        snapshot=run(['ssh','-T','-o','BatchMode=yes','-o','ConnectTimeout=10','-o','ConnectionAttempts=1',args.worker.lower(),'python3 -c '+shlex.quote(script)],timeout=150)
        args.out.write_text(json.dumps(snapshot,indent=2)+'\n')
