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
out['streamprocessing_probes']={}
if isinstance(config,dict):
    base=config.get('services',{}).get('vst',{}).get('url','').rstrip('/')
    parsed=urlsplit(base)
    if parsed.hostname=='host.openshell.internal' and parsed.scheme=='http' and parsed.port in {7777,30888}:
        for service in ('live','record'):
            result=subprocess.run(['/usr/bin/curl','-sS','--max-time','5','-o','/dev/null','-w','%{http_code}',base+'/api/v1/'+service+'/version'],capture_output=True,text=True,timeout=7)
            out['streamprocessing_probes'][service]={'exit_code':result.returncode,'http_status':result.stdout if re.fullmatch(r'[0-9]{3}',result.stdout) else None}
out['producer_egress_probe']={}
if PRODUCER_ORIGIN:
    for label,target in [('published',PRODUCER_ORIGIN+'/vst/api/v1/sensor/version'),('configured',config.get('services',{}).get('vst',{}).get('url','').rstrip('/')+'/api/v1/sensor/version')]:
        result=subprocess.run(['/usr/bin/curl','-sS','--max-time','8','-w','\\nDIAG_HTTP_STATUS:%{http_code}',target],capture_output=True,text=True,timeout=10)
        body,_,status=result.stdout.rpartition('DIAG_HTTP_STATUS:')
        row={'exit_code':result.returncode,'http_status':status if re.fullmatch(r'[0-9]{3}',status) else None}
        try:
            payload=json.loads(body.strip());value=payload.get('type');row['service_type']=value if value in {'vst','streamer'} else None
        except Exception:pass
        row['policy_message']=any(marker in body.lower() for marker in ('policy','not allowed','denied','forbidden'))
        out['producer_egress_probe'][label]=row
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
            try:event=json.loads(line);message=event.get('message',{})
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
        firewall=subprocess.run(['sudo','-n','ufw','status'],capture_output=True,text=True,timeout=15)
        out['ufw_status']='active' if 'Status: active' in firewall.stdout else 'inactive' if 'Status: inactive' in firewall.stdout else 'unavailable'
    except Exception:out['ufw_status']='unavailable'
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
    out['vios_publication']=[]
    out['sandbox_image_layers']=[]
    for name in listing.stdout.splitlines():
        if not (name.startswith('vss-vios-') or name.startswith('vss-rtvi-') or name.startswith('skill-eval-nim-') or name.startswith('openshell-') or 'sdr-controller' in name):continue
        info=run(['docker','inspect',name],timeout=10)
        if not isinstance(info,list) or not info:continue
        state=info[0].get('State',{})
        out['containers'].append({'name':name,'status':state.get('Status'),'health':state.get('Health',{}).get('Status'),'oom':state.get('OOMKilled'),'restarts':info[0].get('RestartCount'),'started_at':state.get('StartedAt') if re.fullmatch(r'[0-9T:Z.+-]{15,40}',str(state.get('StartedAt',''))) else None,'recent_health_checks':[{'start':row.get('Start'),'end':row.get('End'),'exit_code':row.get('ExitCode')} for row in state.get('Health',{}).get('Log',[])[-5:]]})
        if name.startswith('openshell-'):
            image=run(['docker','image','inspect',info[0].get('Image','')],timeout=10)
            if isinstance(image,list) and image:out['sandbox_image_layers'].append({'container':name,'layers':len(image[0].get('RootFS',{}).get('Layers',[]))})
        if name in {'vss-vios-streamprocessing','vss-vios-sensor'}:
            from urllib.parse import urlsplit
            publication={'container':name,'endpoint_settings':{}}
            def public_endpoint(value):
                try:
                    parsed=urlsplit(value if '://' in value else 'http://'+value)
                    return {'host':parsed.hostname,'port':parsed.port,'path_is_vst':parsed.path.rstrip('/')=='/vst'}
                except Exception:return {'invalid':True}
            for value in info[0].get('Config',{}).get('Env') or []:
                key,_,val=value.partition('=')
                if key in {'VST_INGRESS_ENDPOINT','VST_INTERNAL_IP'}:publication['endpoint_settings'][key]=public_endpoint(val)
            config=run(['docker','exec',name,'cat','/home/vst/vst_release/configs/vst_config.json'],timeout=10)
            if isinstance(config,dict):
                def settings(value):
                    if isinstance(value,dict):
                        for key,item in value.items():
                            if key=='reverse_proxy_server_address' and isinstance(item,str):publication[key]=public_endpoint(item)
                            elif key=='use_reverse_proxy' and type(item) is bool:publication[key]=item
                            else:settings(item)
                    elif isinstance(value,list):
                        for item in value:settings(item)
                settings(config)
            out['vios_publication'].append(publication)
    out['build_containers']=[]
    for container_id in subprocess.run(['docker','ps','--no-trunc','--format','{{.ID}}'],capture_output=True,text=True,timeout=10).stdout.splitlines()[:30]:
        info=run(['docker','inspect',container_id],timeout=10)
        if not isinstance(info,list) or not info:continue
        config=info[0].get('Config',{})
        if (config.get('Labels') or {}).get('com.docker.compose.service'):continue
        command=' '.join(config.get('Cmd') or [])
        categories={'package_install':'apt-get','npm_install':'npm ci','python_install':'uv pip','ngc_download':'ngccli.zip','source_fetch':'git init','plugin_install':'openclaw plugins install','workspace_staging':'stage-assets.sh','onboard_config':'apply_onboard_config'}
        category=next((k for k,marker in categories.items() if marker in command),None)
        if category:
            state=info[0].get('State',{})
            out['build_containers'].append({'category':category,'status':state.get('Status'),'oom':state.get('OOMKilled')})
    out['owned_gateway_policies']=[]
    for receipt in (Path.home()/'.nemoclaw/gateways').glob('*/network-policy.json'):
        try:policy=json.loads(receipt.read_text())
        except Exception:continue
        chain=policy.get('chain','');port=policy.get('port')
        if not re.fullmatch(r'SE-NC-[a-f0-9]{20}',chain) or type(port) is not int or not 1024<=port<=65535:continue
        state=subprocess.run(['sudo','-n','iptables','-S',chain],capture_output=True,text=True,timeout=15)
        rules=state.stdout.splitlines()
        out['owned_gateway_policies'].append({'port':port,'chain_present':state.returncode==0,'accept_interfaces':sorted(re.findall(r'-i (lo|docker0|br\+) -j ACCEPT',state.stdout)),'other_traffic_returns':any(line.endswith('-j RETURN') for line in rules),'rule_count':sum(line.startswith('-A ') for line in rules)})
    # Verify teardown of the completed db7 hosted NvStreamer leg, read-only.
    chain='SE-NC-3fa9cc781e015b0f89b5'
    chain_state=subprocess.run(['sudo','-n','iptables','-S',chain],capture_output=True,text=True,timeout=15)
    input_state=subprocess.run(['sudo','-n','iptables','-S','INPUT'],capture_output=True,text=True,timeout=15)
    out['completed_gateway_cleanup']={'port':28608,'receipt_present':(Path.home()/'.nemoclaw/gateways/28608/network-policy.json').exists(),'chain_present':chain_state.returncode==0,'input_references':sum(('-j '+chain) in line for line in input_state.stdout.splitlines())}
    import hashlib
    out['targeted_cancellation_cleanup']=[]
    for cancel_run in ('37770839464','37773243124'):
        for stem in ('vios_ops','nvstreamer_ops'):
            owner=hashlib.sha256((cancel_run+':vss-manage-video-io-storage__'+stem+'__L40S').encode()).hexdigest()
            chain='SE-NC-'+owner[:20]
            for receipt in (Path.home()/'.nemoclaw/gateways').glob('*/skill-eval-owner.json'):
                try:namespace=json.loads(receipt.read_text())
                except Exception:continue
                if namespace.get('owner')!=owner:continue
                ports=namespace.get('ports',[])
                if len(ports)!=3 or any(type(value) is not int or not 1024<=value<=65535 for value in ports):continue
                chain_state=subprocess.run(['sudo','-n','iptables','-S',chain],capture_output=True,text=True,timeout=15)
                out['targeted_cancellation_cleanup'].append({'run':int(cancel_run),'spec':stem,'port':ports[0],'receipt_present':(receipt.parent/'network-policy.json').exists(),'chain_present':chain_state.returncode==0,'input_references':sum(('-j '+chain) in line for line in input_state.stdout.splitlines())})
    out['media_probe']={'status':'not_applicable'}
    sample=Path('/tmp/vss-sample-data/dev-profile-sample-data/warehouse_safety_0001.mp4')
    if os.environ.get('SKILL_EVAL_DIAG_MEDIA_PROBE')=='1' and os.uname().machine=='aarch64' and sample.is_file() and 0<sample.stat().st_size<70_000_000 and any(row['name']=='vss-rtvi-vlm' for row in out['containers']):
        import base64, urllib.request, urllib.error
        origin='http://127.0.0.1:7777/rtvi-vlm/v1'
        try:
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(origin+'/models',timeout=5) as response:models=json.load(response)
            model=models['data'][0]['id']
            video=sample.read_bytes();probe_metadata={'source':'warehouse_safety_0001.mp4','bytes':len(video)}
            import shutil, tempfile
            original=sample.with_name('warehouse_sample.mp4')
            if shutil.which('ffmpeg') and original.is_file():
                with tempfile.TemporaryDirectory(prefix='skill-eval-media-probe-') as directory:
                    clip=Path(directory)/'clip.mp4'
                    trimmed=subprocess.run(['ffmpeg','-nostdin','-v','error','-ss','0','-i',str(original),'-t','3','-c','copy','-an','-movflags','+faststart',str(clip)],capture_output=True,timeout=20)
                    if trimmed.returncode==0 and clip.is_file() and 0<clip.stat().st_size<70_000_000:
                        video=clip.read_bytes();probe_metadata={'source':'warehouse_sample.mp4','window_seconds':3,'bytes':len(video)}
            payload={'model':model,'messages':[{'role':'user','content':[{'type':'text','text':'Describe the scene briefly.'},{'type':'video_url','video_url':{'url':'data:video/mp4;base64,'+base64.b64encode(video).decode()}}]}],'num_frames_per_second_or_fixed_frames_chunk':2,'use_fps_for_chunking':False,'max_tokens':32,'temperature':0}
            request=urllib.request.Request(origin+'/chat/completions',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
            try:
                with opener.open(request,timeout=45) as response:
                    answer=json.load(response);out['media_probe']={'status':'completed','http_status':response.status,'has_choices':bool(answer.get('choices')),**probe_metadata}
            except urllib.error.HTTPError as exc:
                body=exc.read(65536).decode('utf-8','replace')
                out['media_probe']={'status':'failed','http_status':exc.code,'error_markers':[word for word in ('gst-stream-error-quark','Internal data stream error','not-negotiated','not-linked','no element','Could not decode stream','nvv4l2decoder','avdec_h264','h264parse','qtdemux','permission denied') if word in body],**probe_metadata}
                if exc.code==422:
                    try:
                        errors=json.loads(body).get('detail',[])
                        out['media_probe']['validation_errors']=[{'type':e['type'],'location':[x for x in e.get('loc',[]) if type(x) is int or x in {'body','messages','content','video_url','url','model','media_io_kwargs','max_tokens','temperature'}]} for e in errors if isinstance(e,dict) and e.get('type') in {'string_too_long','extra_forbidden','missing','value_error','literal_error','less_than_equal','greater_than_equal'}]
                    except Exception:pass
        except Exception as exc:out['media_probe']={'status':'failed','exception_type':type(exc).__name__}
    out['rt_vlm_decode_errors']=[]
    if any(row['name']=='vss-rtvi-vlm' for row in out['containers']):
        try:
            logs=subprocess.run(['docker','logs','--since','30m','--tail','300','vss-rtvi-vlm'],capture_output=True,text=True,timeout=15)
            body=logs.stdout+'\n'+logs.stderr
            out['rt_vlm_decode_errors']=[word for word in ('gst-stream-error-quark','gst-resource-error-quark','gst-library-error-quark','not-negotiated','not-linked','no element','Could not decode stream','nvv4l2decoder','avdec_h264','h264parse','qtdemux','permission denied','HTTP 403','HTTP 404','HTTP 500') if word in body]
        except Exception as exc:out['rt_vlm_log_error_type']=type(exc).__name__
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
    out['host_streamprocessing_probes']={}
    if out.get('active_eval_run') in {'37766151982','37763719776','37768823981','37770839464','37773243124'}:
        for port in (7777,30888):
            for service in ('live','record'):
                result=subprocess.run(['/usr/bin/curl','-sS','--max-time','4','-o','/dev/null','-w','%{http_code}','http://127.0.0.1:'+str(port)+'/vst/api/v1/'+service+'/version'],capture_output=True,text=True,timeout=6)
                out['host_streamprocessing_probes'][str(port)+'/'+service]={'exit_code':result.returncode,'http_status':result.stdout if re.fullmatch(r'[0-9]{3}',result.stdout) else None}
    out['local_nim_probes']={}
    if out.get('active_eval_run') in {'37763711360','37766151982','37768823981'}:
        for label,endpoint in [('adapter','http://127.0.0.1:18400/health/liveliness'),('nim','http://127.0.0.1:18410/v1/health/ready')]:
            result=subprocess.run(['/usr/bin/curl','-sS','--max-time','5','-o','/dev/null','-w','%{http_code}',endpoint],capture_output=True,text=True,timeout=7)
            out['local_nim_probes'][label]={'exit_code':result.returncode,'http_status':result.stdout if re.fullmatch(r'[0-9]{3}',result.stdout) else None}
    sandbox=env.get('NEMOCLAW_SANDBOX_NAME','')
    if re.fullmatch(r'se-[0-9]+-[a-f0-9]+',sandbox):
        out['sandbox']=sandbox
        out['gateway_port']=env.get('NEMOCLAW_GATEWAY_PORT')
        out['gateway_firewall']={}
        port=env.get('NEMOCLAW_GATEWAY_PORT','')
        if port.isdecimal() and 1024<=int(port)<=65535:
            for name,argv in [('ufw', ['sudo','-n','ufw','status']),('iptables',['sudo','-n','iptables','-S'])]:
                result=subprocess.run(argv,capture_output=True,text=True,timeout=15)
                rows=[]
                for line in result.stdout.splitlines():
                    if not re.search(r'(?<![0-9])'+port+r'(?![0-9])',line):continue
                    words=line.split()
                    rows.append({'port':int(port),'accept':'ACCEPT' in words or 'ALLOW' in words,'reject':'REJECT' in words or 'DENY' in words,'bridge_interface':any(word in {'docker0','br+'} or word.startswith('br-') for word in words),'ip_operands':re.findall(r'(?<![0-9])[0-9]{1,3}(?:\.[0-9]{1,3}){3}(?:/[0-9]{1,2})?',line),'chain':words[1] if len(words)>1 and words[0]=='-A' and re.fullmatch(r'[A-Za-z0-9_-]{1,50}',words[1]) else None})
                out['gateway_firewall'][name]={'exit_code':result.returncode,'port_rules':rows[:20],'input_policy_drop':bool(re.search(r'^-P INPUT DROP$',result.stdout,re.M))}
        producer_origin=''
        if out.get('active_eval_run') in {'37744385620','37752059953','37754840797','37757505544','37757514212','37763711360','37763719776','37766151982','37767605818','37768823981','37770839464','37773243124'}:
            for publication in out['vios_publication']:
                endpoint=publication.get('endpoint_settings',{}).get('VST_INGRESS_ENDPOINT',{})
                host=endpoint.get('host','');producer_port=endpoint.get('port')
                if publication['container']=='vss-vios-streamprocessing' and re.fullmatch(r'[0-9]+(?:\.[0-9]+){3}',host) and type(producer_port) is int and endpoint.get('path_is_vst'):
                    producer_origin='http://'+host+':'+str(producer_port)
                    result=subprocess.run(['/usr/bin/curl','-sS','--max-time','8','-w','\nDIAG_HTTP_STATUS:%{http_code}',producer_origin+'/vst/api/v1/sensor/version'],capture_output=True,text=True,timeout=10)
                    body,_,status=result.stdout.rpartition('DIAG_HTTP_STATUS:')
                    out['host_published_probe']={'exit_code':result.returncode,'http_status':status if re.fullmatch(r'[0-9]{3}',status) else None}
                    try:out['host_published_probe']['service_type']=json.loads(body.strip()).get('type')
                    except Exception:pass
        sandbox_script='PRODUCER_ORIGIN='+repr(producer_origin)+'\n'+SANDBOX_SCRIPT
        wrapped='import json\ntry:\n exec('+repr(sandbox_script)+')\nexcept Exception as exc:\n print(json.dumps({"collector_error_type":type(exc).__name__}))'
        command='python3 -c '+shlex.quote(wrapped)
        port=env.get('NEMOCLAW_GATEWAY_PORT','')
        if port.isdecimal() and 1024<=int(port)<=65535:
            out['native_access']=run(['openshell','sandbox','exec','--name',sandbox,'-g','nemoclaw-'+port,'--','sh','-lc','printf 1'],env=env,timeout=15)
            if out['native_access']==1:out['native_access']={'exit_code':0,'probe_output_valid':True}
            phase=run(['openshell','sandbox','get',sandbox,'-g','nemoclaw-'+port,'-o','json'],env=env,timeout=15)
            out['sandbox_phase']=phase.get('phase') if isinstance(phase,dict) and phase.get('phase') in {'Ready','Pending','Created','Creating','Starting','Error','Failed','Terminated'} else phase.get('category') if isinstance(phase,dict) else None
            if out['sandbox_phase'] in {'Error','Failed','Terminated'}:
                out['phase_schema_keys']=sorted(phase) if isinstance(phase,dict) else []
                annotations=phase.get('annotations',{}) if isinstance(phase,dict) else {}
                out['phase_annotation_keys']=sorted(annotations) if isinstance(annotations,dict) else []
                details=json.dumps({key:value for key,value in annotations.items() if any(word in key.lower() for word in ('error','reason','status','condition'))}) if isinstance(annotations,dict) else ''
                markers=('ImagePullBackOff','CrashLoopBackOff','OCI runtime','executable file not found','permission denied','Permission denied','authentication','config hash','managed config','No such file','no such file','address already in use','failed to create','certificate','nvidia','NVIDIA','landlock','seccomp','read-only','not permitted','failed to start','exit code','image','policy','mount','device','entrypoint','connection refused','sandbox-safety-net','ECONNREFUSED','ErrImagePull','CreateContainerConfigError')
                out['phase_error_markers']=[marker for marker in markers if marker in details]
                out['stopped_sandbox_containers']=[]
                names=subprocess.run(['docker','ps','-a','--format','{{.Names}}'],capture_output=True,text=True,timeout=10).stdout.splitlines()
                for name in names:
                    if not name.startswith('openshell-') or sandbox not in name:continue
                    info=run(['docker','inspect',name],timeout=10)
                    if not isinstance(info,list) or not info:continue
                    state=info[0].get('State',{})
                    logs=subprocess.run(['docker','logs','--tail','100',name],capture_output=True,text=True,timeout=10)
                    text=str(state.get('Error',''))+'\n'+logs.stdout+'\n'+logs.stderr
                    out['stopped_sandbox_containers'].append({'status':state.get('Status'),'exit_code':state.get('ExitCode'),'oom':state.get('OOMKilled'),'restarts':info[0].get('RestartCount'),'error_markers':[marker for marker in markers if marker in text]})
            if out['sandbox_phase']=='Ready':
                try:
                    audit=subprocess.run(['openshell','logs',sandbox,'-g','nemoclaw-'+port,'-n','1000','--source','all'],stdin=subprocess.DEVNULL,capture_output=True,text=True,env=env,timeout=20)
                    out['audit_exit_code']=audit.returncode
                    out['denied_flows']=[]
                    for line in audit.stdout.splitlines():
                        if not any(marker in line for marker in ('NET:OPEN DENIED','policy_denied','DENIED')):continue
                        flow={}
                        match=re.search(r'(?:->\s*(?:(?:GET|POST|PUT|DELETE|HEAD|CONNECT)\s+)?(?:https?://)?|CONNECT\s+)([a-z0-9.-]+):([0-9]{1,5})\b',line)
                        if match:
                            flow['host']=match.group(1);flow['port']=int(match.group(2))
                        binary=re.search(r'DENIED (/[-A-Za-z0-9_./]+)\([0-9]+\)',line)
                        if binary:flow['binary']=binary.group(1)
                        flow['reason_markers']=[word for word in ('resolve peer binary','identity binding','binary integrity','ancestor integrity','no matching','not allowed in policy','policy generation','policy changed','allowed_ips','internal IP','credential') if word in line]
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



def coordinator_traces():
    from urllib.parse import urlsplit
    root=Path('/tmp/skill-eval/results/_viewer')
    out=[]
    for run_id in ('37729097734','37728051451','37720370667','37729095977','37732351784','37732353838','37736233569','37736239578','37736245915','37720368695','37737621976','37739130338','37743611504','37744378089','37744385620','37744461082','37745938465','37749284519','37749293650','37752059953','37754840797','37757505544','37757514212','37763711360','37763719776','37766151982','37767605818','37768823981','37770839464','37773243124'):
        for job in sorted(root.glob('*__'+run_id+'__*')):
            for path in sorted(job.glob('step-*/agent/openclaw.session.jsonl')):
                calls={}; rows=[]; shapes=[]
                try:lines=path.read_text().splitlines()
                except OSError:continue
                for line in lines:
                    try:event=json.loads(line);message=event.get('message',{})
                    except Exception:continue
                    content=message.get('content',[])
                    if not isinstance(content,list):continue
                    for block in content:
                        if not isinstance(block,dict) or block.get('type')!='toolCall':continue
                        args=json.dumps(block.get('arguments',{}))
                        if block.get('name')=='vss_cli' and isinstance(block.get('arguments',{}).get('args'),list):args='vss '+shlex.join(block['arguments']['args'])
                        shapes.append({'tool':block.get('name') if re.fullmatch(r'[a-zA-Z0-9_.]{1,60}',str(block.get('name',''))) else None,'argument_keys':sorted(block.get('arguments',{})) if isinstance(block.get('arguments'),dict) else []})
                        family=next((name for name,marker in [('vss_vlm','vss vlm '),('vss_vios','vss vios '),('vss_configure','vss configure '),('vios_curl','/vst/'),('curl','curl ')] if marker in args),None)
                        if not family:continue
                        def origins(text):
                            origins=[]
                            for value in re.findall(r'https?://[^\\\s"<>]+',text):
                                try:
                                    url=urlsplit(value)
                                    if url.hostname and re.fullmatch(r'[a-z0-9.-]+',url.hostname):origins.append({'scheme':url.scheme,'host':url.hostname,'port':url.port,'path':url.path if url.path in {'','/','/vst','/api/v1','/vst/api/v1','/live/version','/record/version','/api/v1/live/version','/api/v1/record/version','/vst/api/v1/live/version','/vst/api/v1/record/version','/vst/api/v1/sensor/version','/vst/api/v1/sensor/list'} else '<other>'})
                                except ValueError:pass
                            return origins[:8]
                        calls[block.get('id')]={'family':family,'request_origins':origins(args),
                            'version_paths':[name for name in ('sensor','live','record') if '/api/v1/'+name+'/version' in args],'config_selectors':[name for name in ('.base_url','.services.vst.url','base_url','services','vst','url') if name in args],'base_assignments':[value for value in re.findall(r'(?:VST_API_BASE|VST_BASE_URL|BASE_URL)[^=]{0,3}=[^A-Za-z0-9]{0,3}(http://host\.openshell\.internal:[0-9]{4,5}(?:/vst)?(?:/api/v1)?)',args)][:4],'wait_seconds':[int(value) for value in re.findall(r'sleep[^0-9]{0,8}([0-9]{1,3})',args)][:5],'timestamp':event.get('timestamp') if re.fullmatch(r'[0-9T:Z.+-]{15,40}',str(event.get('timestamp',''))) else None,
                            'proxy_overrides':sorted(set(re.findall(r'\b(?:HTTP_PROXY|HTTPS_PROXY|ALL_PROXY|NO_PROXY|http_proxy|https_proxy|all_proxy|no_proxy)\b',args))),
                            'binary_paths':sorted(set(re.findall(r'/usr/(?:local/)?(?:bin|vss/bin)/(?:python3(?:\.[0-9]+)?|curl|node|vss)\b',args))),
                            'proxy_bypass':any(marker in args for marker in ('--noproxy','unset HTTP_PROXY','unset HTTPS_PROXY')),
                            'curl_modes':[flag for flag in ('-I','--head','-x','--proxy','--resolve','-L','--location','--connect-to','--unix-socket','-k','--insecure') if flag in args],
                            'shell_forms':[form for form in ('python3','python ','bash -lc','sh -lc','sh -c','curl ') if form in args],
                            'vss_flags':sorted(set(re.findall(r'--(?:sensor|start-time|end-time|fps|max-frames|no-persist|file|media-url)\b',args))),
                            'numeric_time_bounds':bool(re.search(r'--(?:start-time|end-time)[^A-Za-z0-9]{1,10}[0-9]{1,6}[^A-Za-z0-9:-]',args+' '))}
                    if message.get('role')!='toolResult' or message.get('toolCallId') not in calls:continue
                    def strings(value):
                        if isinstance(value,str):
                            yield value
                            try:parsed=json.loads(value)
                            except (ValueError,TypeError):return
                            if not isinstance(parsed,str):yield from strings(parsed)
                        elif isinstance(value,list):
                            for item in value:yield from strings(item)
                        elif isinstance(value,dict):
                            for item in value.values():yield from strings(item)
                    text='\n'.join(strings(content)); row=dict(calls[message['toolCallId']])
                    row['result_markers']=[marker for marker in ('No such command','No such option','Invalid value','configuration error','window','recorded range','outside','beyond','cannot parse','invalid timestamp','ISO-8601','timed out','timeout','HTTP 500','HTTP 404','permission denied','failed','exit code','job_id','answer') if marker.lower() in text.lower()]
                    row['http_status_mentions']=sorted(set(re.findall(r'(?<![0-9])[245][0-9]{2}(?![0-9])',text)))[:12]
                    row['exit_codes']=sorted(set(int(x) for x in re.findall(r'(?:exit(?:[_ ]code)?|Process exited with code)[^0-9]{0,12}([0-9]{1,3})\b',text,re.I)))[:5]

                    if re.search(r'\b403\b',text) or 'policy_denied' in text:
                        row['failure']='policy_denied' if 'policy_denied' in text else 'proxy_tunnel_denied' if 'tunnel' in text.lower() else 'http_403'
                        row['error_origins']=origins(text)
                        row['reason_markers']=[marker for marker in ('no_matching_policy','no matching policy','no matching endpoint','binary','blocked','denied','CONNECT','connect tunnel','proxy','host','port','allowed_ips','protocol','IP address') if marker.lower() in text.lower()]
                        details=re.findall(r'\\?"detail\\?"\s*:\s*\\?"([^"\\]{1,300})',text)
                        safe_words={'host','port','not','allowed','denied','by','policy','no','matching','endpoint','binary','process','identity','source','destination','network','address','ip','range','in','allowlist','found','resolved','private','unknown','missing','required','invalid','request','method','http','https','tcp','tls','protocol','connect','proxy','hostname','untrusted','blocked','loopback','does','match','the','configured','unauthorized','tunnel','a','for','this','and','or','rule','rules','access','is','with','permission','unauthenticated','credentials','authentication','list','defined','detected','unavailable','target','to','permitted','get','post','put','head','delete'}
                        row['policy_details']=[value for value in details if all(word.lower() in safe_words or word in {'host.openshell.internal','localhost','127.0.0.1'} or word.isdecimal() and len(word)<=5 for word in re.findall(r'[a-zA-Z0-9_.]+',value))]
                        row['response_keys']=sorted(set(re.findall(r'\\?"([a-z_]{2,40})\\?"\s*:',text)))[:30]
                    else:row['failure']=None
                    if row not in rows:rows.append(row)
                result_path=path.parent.parent/'result.json'
                try:
                    result=json.loads(result_path.read_text());reward=result.get('verifier_result',{}).get('rewards',{}).get('reward')
                except Exception:reward=None
                out.append({'run':run_id,'trial':path.parent.parent.name,'spec':job.name.split('__')[1],'reward':reward,'calls':rows[:40],'tool_shapes':shapes[:15] if not rows else []})
            for trial in sorted(job.glob('step-*')):
                out.append({'run':run_id,'trial':trial.name,'agent_files':[{'name':p.name,'size':p.stat().st_size} for p in (trial/'agent').glob('*') if p.is_file() and re.fullmatch(r'[a-zA-Z0-9_.-]{1,80}',p.name)]})
                for path in trial.glob('agent/*'):
                    if not path.is_file() or path.name not in {'codex.txt','trajectory.json','openclaw.session.jsonl','codex.jsonl','trajectory.jsonl'} or path.stat().st_size>20_000_000:continue
                    try:body=path.read_text()
                    except (OSError,UnicodeError):continue
                    markers=[word for word in ('gst-stream-error-quark','gst-resource-error-quark','gst-library-error-quark','not-negotiated','not-linked','no element','Could not decode stream','nvv4l2decoder','avdec_h264','h264parse','qtdemux','base64','video_url') if word in body]
                    if any(word.startswith('gst-') for word in markers):out.append({'run':run_id,'trial':trial.name,'spec':job.name.split('__')[1],'decode_markers':markers})


    scan_stream_evidence=[]
    for job in root.glob('*__37743611504__*'):
        if 'nvstreamer' not in job.name:continue
        for path in job.glob('step-4*/agent/openclaw.session.jsonl'):
            trial=path.parent.parent
            receipts=list(trial.glob('artifacts/**/host-fixture.json'))
            if not receipts:continue
            try:receipt=json.loads(receipts[0].read_text())
            except Exception:continue
            basename=receipt.get('basename','')
            if receipt.get('status')!='passed' or not re.fullmatch(r'sample-clip-[a-f0-9]{12}',basename):continue
            calls=set()
            for line in path.read_text().splitlines():
                try:event=json.loads(line);message=event.get('message',{})
                except ValueError:continue
                content=message.get('content',[])
                if not isinstance(content,list):continue
                for block in content:
                    if isinstance(block,dict) and block.get('type')=='toolCall' and '/api/v1/sensor/streams' in json.dumps(block.get('arguments',{})):calls.add(block.get('id'))
                if message.get('role')!='toolResult' or message.get('toolCallId') not in calls:continue
                for value in re.findall(r'rtsp://[^\s"<>\\]+',json.dumps(content)):
                    try:url=urlsplit(value)
                    except ValueError:continue
                    if not url.path.endswith('/'+basename+'.mp4'):continue
                    scan_stream_evidence.append({'run':37743611504,'trial':trial.name,'basename':basename,'host':url.hostname,'port':url.port,'path_matches_fixture':True,'path_has_nvstream_prefix':url.path.startswith('/nvstream/'),'port_in_default_pool':31554<=url.port<=31561 if url.port else False})

    scan_order=[]
    for job in root.glob('*__37749284519__*'):
        if 'nvstreamer' not in job.name:continue
        for path in job.glob('step-4*/agent/openclaw.session.jsonl'):
            trial=path.parent.parent
            receipts=list(trial.glob('artifacts/**/host-fixture.json'))
            if not receipts:continue
            receipt=json.loads(receipts[0].read_text());basename=receipt.get('basename','')
            if not re.fullmatch(r'sample-clip-[a-f0-9]{12}',basename):continue
            rows=[];calls={}
            for line in path.read_text().splitlines():
                try:event=json.loads(line);message=event.get('message',{})
                except ValueError:continue
                content=message.get('content',[])
                if not isinstance(content,list):continue
                for block in content:
                    if not isinstance(block,dict) or block.get('type')!='toolCall':continue
                    args=json.dumps(block.get('arguments',{}))
                    actions=[a for a in ('sensor/list','sensor/scan','sensor/streams','storage/file','nvstreamer_scan.json') if a in args]
                    if actions:
                        row={'actions':actions,'mentions_fixture':basename in args,'post':bool(re.search(r'POST|post\(',args)),'timestamp':event.get('timestamp') if re.fullmatch(r'[0-9T:Z.+-]{15,40}',str(event.get('timestamp',''))) else None}
                        calls[block.get('id')]=row;rows.append(row)
                if message.get('role')=='toolResult' and message.get('toolCallId') in calls:
                    text=json.dumps(content);row=calls[message['toolCallId']]
                    row['fixture_mentions']=text.count(basename)
                    row['counts']=re.findall(r'(?:before[^:]{0,100}:|count[^:]{0,15}:)[^0-9]{0,5}([0-9]{1,3})',text,re.I)[:10]
                    row['sensor_entry_mentions']=len(re.findall(r'(?:name|sensorId)[^A-Za-z0-9]{1,10}'+re.escape(basename),text))
            scan_order.append({'run':37749284519,'trial':trial.name,'basename':basename,'receipt_status':receipt.get('status'),'actions':rows[:30]})

    # Inspect only setup tool results for the failed startup, with token/URL/path
    # data removed before emitting a bounded excerpt.
    startup=[]
    for job in [job for run_id in ('37743611504','37749284519','37749293650','37752059953','37754840797','37757505544','37757514212','37763711360','37763719776','37766151982','37767605818','37768823981','37770839464','37773243124') for job in root.glob('*__'+run_id+'__*')]:
        for path in job.glob('step-1*/agent/codex.txt'):
            calls={}
            for line in path.read_text().splitlines():
                try: item=json.loads(line).get('item',{})
                except ValueError:continue
                if item.get('type')!='command_execution':continue
                command=item.get('command','')
                family=next((name for name,marker in [('container_logs','docker logs'),('native_logs','openshell logs'),('onboard','nemoclaw onboard'),('notebook','run_setup_notebook.py'),('container_inspect','docker inspect'),('sandbox_get','openshell sandbox get'),('sandbox_recovery','nemoclaw ')] if marker in command),None)
                if not family or item.get('aggregated_output') is None:continue
                text=item['aggregated_output']
                markers=('MainProcessExited','ContainerStartFailed','ReadyFalse','EADDRINUSE','EACCES','ENOENT','EXIT_CODE','Invalid config','Config validation failed','Missing env var','main process','exited with','startup','not found','Error:','error:','failed','FATAL','fatal','Permission denied','Unknown config','unrecognized','scope upgrade','pairing required','preflight','refused','identity deletion','atomic','cannot','Cannot')
                selected=[]
                for value in text.splitlines():
                    if not any(marker in value for marker in markers):continue
                    value=re.sub(r'https?://\S+', '<origin>', value)
                    value=re.sub(r'/[-A-Za-z0-9_./]+','<path>',value)
                    value=re.sub(r'(?i)(token|key|secret|password|credential)(\s*[=:]\s*)\S+',r'\1\2<redacted>',value)
                    value=re.sub(r'[A-Za-z0-9_+/=.-]{30,}','<id>',value)
                    value=re.sub(r'"[^"\n]*"|\x1b\[[0-9;]*[a-zA-Z]','<quoted>',value)
                    selected.append(value[:500])
                if selected:startup.append({'run':job.name.split('__')[-2] if '__' in job.name else None,'family':family,'exit_code':item.get('exit_code'),'lines':selected[:10]+selected[-10:]})
    firewall_commands=[]
    for run_id in ('37743611504','37744461082','37745938465','37749284519','37749293650','37752059953','37754840797','37757505544','37757514212','37763711360','37763719776','37766151982','37767605818','37768823981','37770839464','37773243124'):
        for job in root.glob('*__'+run_id+'__*'):
            for path in job.glob('step-1*/agent/codex.txt'):
                for line in path.read_text().splitlines():
                    try:item=json.loads(line).get('item',{})
                    except ValueError:continue
                    command=item.get('command','')
                    if item.get('type')!='command_execution' or not ('ufw ' in command or 'iptables ' in command or 'NEMOCLAW_AUTO_FIX_FIREWALL' in command):continue
                    firewall_commands.append({'run':run_id,'exit_code':item.get('exit_code'),'ufw': 'ufw ' in command,'iptables':'iptables ' in command,'opt_in':'NEMOCLAW_AUTO_FIX_FIREWALL' in command,'ip_operands':re.findall(r'(?<![0-9])[0-9]{1,3}(?:\.[0-9]{1,3}){3}(?:/[0-9]{1,2})?',command)[:10],'ufw_allow':bool(re.search(r'ufw(?:[\s\\]+|[^a-zA-Z]{1,8})allow',command)),'ports':sorted(set(int(value) for value in re.findall(r'(?:--dport|port)[^0-9]{0,10}([0-9]{4,5})',command)))[:10]})
    machines=[]
    for run_id in ('37770839464','37773243124','37774258173'):
        for receipt in Path('/tmp/skill-eval/results').glob('*/'+run_id+'/machine.txt'):
            try:fields=receipt.read_text().strip().split('\t')
            except OSError:continue
            if len(fields)==3 and fields[2]==run_id and re.fullmatch(r'[A-Za-z0-9_-]{1,64}',fields[0]) and re.fullmatch(r'[A-Za-z0-9_-]{1,180}',fields[1]):machines.append({'run':int(run_id),'worker':fields[0],'slug':fields[1]})
    leg_processes=[]
    for entry in Path('/proc').iterdir():
        if not entry.name.isdecimal():continue
        try:
            argv=(entry/'cmdline').read_bytes().decode().split('\0')
            if len(argv)<2 or not argv[1].endswith('/.github/skill-eval/run_leg.py'):continue
            fields=dict(item.split('=',1) for item in (entry/'environ').read_bytes().decode().split('\0') if '=' in item)
            run=fields.get('GITHUB_RUN_ID')
            if run not in {'37770839464','37773243124','37774258173','37775381660','37768823981'}:continue
            locks=[value.name for fd in (entry/'fd').iterdir() if str(value:=fd.resolve()).startswith('/tmp/brev/') and value.name.endswith('.lock')]
            leg_processes.append({'run':int(run),'pid':int(entry.name),'worker_lock_files':locks,'attempt':fields.get('GITHUB_RUN_ATTEMPT')})
        except (OSError,ValueError,UnicodeError):continue
    return {'leg_processes':leg_processes,'machines':machines,'viewer_exists':root.is_dir(),'traces':out,'startup_failure_results':startup[:8]+startup[-25:],'scan_stream_evidence':scan_stream_evidence,'scan_order':scan_order,'firewall_commands':firewall_commands}


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
        snapshots['coordinator']=coordinator_traces()
        args.out.write_text(json.dumps(snapshots if len(workers)>1 else snapshots[workers[0]],indent=2)+'\n')
