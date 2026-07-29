#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Serve a live eight-host dashboard over direct coordinator SSH aliases."""

from __future__ import annotations

import argparse
import json
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOSTS = [f"vss-skill-validator-distributed-{index}" for index in range(1, 9)]
REMOTE_PROBE = "/tmp/vss-host-metrics.py"
LOCAL_PROBE = Path(__file__).with_name("host_metrics_probe.py")
PROBE_VERSION = 6
MAX_SAMPLE_AGE_SEC = 90
MAX_BACKUP_AGE_SEC = 2 * 60 * 60
MAX_RESTORE_AGE_SEC = 8 * 24 * 60 * 60
SSH_OPTIONS = (
    "-o",
    "BatchMode=yes",
    "-o",
    "ControlMaster=no",
    "-o",
    "ControlPath=none",
    "-o",
    "ConnectTimeout=10",
)
STATE_LOCK = threading.Lock()
STATE = {
    host: {"host": host, "status": "waiting", "error": None, "metrics": None}
    for host in HOSTS
}

HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>VSS Distributed Coordinators</title>
<style>
:root{color-scheme:dark;--bg:#0b0e11;--card:#151a20;--line:#28313b;--text:#f3f6f8;--muted:#93a1ad;--green:#76b900;--warn:#f5a623;--bad:#ef5350}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(145deg,#080a0d,#111820);font:14px Inter,system-ui,sans-serif;color:var(--text)}
main{max-width:1500px;margin:auto;padding:28px}.head{display:flex;justify-content:space-between;gap:20px;align-items:center;margin-bottom:22px}
h1{font-size:26px;margin:0}.accent{color:var(--green)}.sub{color:var(--muted);margin-top:6px}.status{padding:8px 12px;border:1px solid var(--line);border-radius:18px;color:var(--muted)}
.summary{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:16px}.stat,.card{background:rgba(21,26,32,.95);border:1px solid var(--line);border-radius:12px}
.stat{padding:16px}.stat b{font-size:25px;display:block;margin-top:5px}.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:14px}.card{padding:17px}
.cardhead{display:flex;justify-content:space-between;align-items:center;margin-bottom:15px}.name{font-weight:650}.pill{font-size:12px;padding:4px 9px;border-radius:12px;background:#24310f;color:#a5d84f}
.pill.bad{background:#3d1d1d;color:#ff8c89}.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.metric .label{color:var(--muted);font-size:12px}.value{font-size:21px;font-weight:650;margin:4px 0 8px}
.bar{height:7px;background:#252c33;border-radius:8px;overflow:hidden}.fill{height:100%;background:var(--green);transition:width .3s}.fill.warn{background:var(--warn)}.fill.bad{background:var(--bad)}
.foot{display:flex;justify-content:space-between;color:var(--muted);font-size:12px;margin-top:14px;border-top:1px solid var(--line);padding-top:11px}.svc{margin-top:11px;color:var(--muted);font-size:12px}.svc.bad{color:var(--bad)}
@media(max-width:900px){.grid{grid-template-columns:1fr}.summary{grid-template-columns:repeat(2,1fr)}.metrics{grid-template-columns:1fr}}
</style></head>
<body><main>
<div class="head"><div><h1><span class="accent">VSS</span> Distributed Coordinators</h1><div class="sub">Live CPU, RAM and root-disk health · 8 Brev machines</div></div><div id="updated" class="status">Connecting…</div></div>
<section id="summary" class="summary"></section><section id="grid" class="grid"></section>
</main>
<script>
const pctClass=v=>v>=90?'bad':v>=80?'warn':'';
const bytes=n=>{if(n==null)return'—';const u=['B','GB','TB'];let i=0,x=n;while(x>=1000&&i<u.length-1){x/=1000;i++}return x.toFixed(i?1:0)+' '+u[i]};
const age=s=>{if(s==null)return'—';if(s<60)return s+'s';if(s<3600)return Math.floor(s/60)+'m';if(s<86400)return Math.floor(s/3600)+'h';return Math.floor(s/86400)+'d'};
const metric=(label,value,pct)=>`<div class="metric"><div class="label">${label}</div><div class="value">${value}</div><div class="bar"><div class="fill ${pctClass(pct)}" style="width:${Math.min(pct||0,100)}%"></div></div></div>`;
const serviceHealth=(host,m)=>{const s=m.services||{},db=/distributed-[123]$/.test(host),backup=/distributed-[45]$/.test(host),bad=(db&&(s.postgres_ha!=='active'||s.etcd!=='active'||s.patroni_cluster!=='healthy'||s.etcd_quorum!=='healthy'))||(backup&&(s.backup_timer!=='active'||s.restore_test_timer!=='active'||s.backup_result!=='success'||s.restore_test_result!=='success'||s.backup_age_seconds==null||s.backup_age_seconds>7200||s.restore_test_age_seconds==null||s.restore_test_age_seconds>691200));let text=db?`Patroni ${s.patroni_cluster||'—'} · leaders ${s.patroni_leaders??'—'} · sync ${s.patroni_sync_standbys??'—'} · etcd ${s.etcd_healthy_endpoints??'—'}/3`:backup?`Backups ${s.backup_timer||'—'} · age ${age(s.backup_age_seconds)} · restore ${s.restore_test_timer||'—'} / ${s.restore_test_result||'—'} · age ${age(s.restore_test_age_seconds)}`:'Coordinator only';return{bad,text}};
async function refresh(){
 try{
  const r=await fetch('/api/metrics',{cache:'no-store'}),d=await r.json(),rows=d.hosts,fresh=rows.filter(x=>x.status==='online'&&x.metrics&&(d.generated_at-x.metrics.collected_at)<=90),vals=fresh.map(x=>x.metrics);
  const avg=k=>vals.length?(vals.reduce((a,x)=>a+x[k],0)/vals.length).toFixed(1):'—';
  document.querySelector('#summary').innerHTML=[
   ['Fresh hosts',`${fresh.length} / 8`],['Average CPU',`${avg('cpu_percent')}%`],['Average RAM',`${avg('ram_percent')}%`],['Highest disk',`${vals.length?Math.max(...vals.map(x=>x.disk_percent)).toFixed(1):'—'}%`]
  ].map(x=>`<div class="stat"><span class="sub">${x[0]}</span><b>${x[1]}</b></div>`).join('');
  document.querySelector('#grid').innerHTML=rows.map(x=>{
   if(!x.metrics)return`<article class="card"><div class="cardhead"><span class="name">${x.host}</span><span class="pill bad">${x.status}</span></div><div class="sub">${x.error||'Waiting for first sample'}</div></article>`;
   const m=x.metrics,svc=serviceHealth(x.host,m),sampleAge=d.generated_at-m.collected_at,isFresh=x.status==='online'&&sampleAge<=90,bad=!isFresh||svc.bad;
   return`<article class="card"><div class="cardhead"><span class="name">${x.host}</span><span class="pill ${bad?'bad':''}">${!isFresh?x.status:svc.bad?'service alert':'online'}</span></div>
   <div class="metrics">${metric('CPU',m.cpu_percent.toFixed(1)+'%',m.cpu_percent)}${metric('RAM',m.ram_percent.toFixed(1)+'%',m.ram_percent)}${metric('Root disk',m.disk_percent.toFixed(1)+'%',m.disk_percent)}</div>
   <div class="svc ${svc.bad?'bad':''}">${svc.text}</div><div class="foot"><span>${m.cpu_count} vCPU · load ${m.load_1m} · sample ${age(sampleAge)} old</span><span>RAM ${bytes(m.ram_used)} / ${bytes(m.ram_total)} · Disk free ${bytes(m.disk_free)} · Uptime ${age(m.uptime_seconds)}</span></div></article>`}).join('');
  document.querySelector('#updated').textContent=(d.health.status==='ok'?'Healthy · ':'Degraded · ')+new Date(d.generated_at*1000).toLocaleTimeString();
 }catch(e){document.querySelector('#updated').textContent='Dashboard API unavailable'}
}
refresh();setInterval(refresh,10000);
</script></body></html>"""


def parse_json(output: str) -> dict:
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            return json.loads(line)
    raise ValueError("probe returned no JSON object")


def run_probe(host: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["ssh", *SSH_OPTIONS, host, "python3", REMOTE_PROBE],
        capture_output=True,
        check=False,
        text=True,
        timeout=20,
    )


def stage_probe(host: str) -> None:
    process = subprocess.run(
        ["scp", "-q", *SSH_OPTIONS, str(LOCAL_PROBE), f"{host}:{REMOTE_PROBE}"],
        capture_output=True,
        check=False,
        text=True,
        timeout=20,
    )
    if process.returncode != 0:
        raise RuntimeError((process.stderr or process.stdout).strip()[-300:])


def collect_host(host: str) -> None:
    try:
        process = run_probe(host)
        if process.returncode != 0:
            # /tmp is cleared on reboot. Restage once before reporting the
            # host unavailable; network/auth failures still surface below.
            stage_probe(host)
            process = run_probe(host)
        if process.returncode != 0:
            raise RuntimeError((process.stderr or process.stdout).strip()[-300:])
        metrics = parse_json(process.stdout)
        if metrics.get("probe_version") != PROBE_VERSION:
            stage_probe(host)
            process = run_probe(host)
            if process.returncode != 0:
                raise RuntimeError((process.stderr or process.stdout).strip()[-300:])
            metrics = parse_json(process.stdout)
        if metrics.get("probe_version") != PROBE_VERSION:
            raise RuntimeError("remote metrics probe version mismatch")
        record = {"host": host, "status": "online", "error": None, "metrics": metrics}
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
        with STATE_LOCK:
            previous = STATE[host].get("metrics")
        record = {
            "host": host,
            "status": "stale" if previous else "offline",
            "error": str(exc)[:300],
            "metrics": previous,
        }
    with STATE_LOCK:
        STATE[host] = record


def collector(interval: int) -> None:
    while True:
        # A slow SSH endpoint must not delay health visibility for the other
        # seven coordinators.
        with ThreadPoolExecutor(max_workers=len(HOSTS)) as executor:
            list(executor.map(collect_host, HOSTS))
        time.sleep(interval)


def health_summary(rows: list[dict], generated_at: int) -> dict:
    problems = []
    for row in rows:
        host = row["host"]
        metrics = row.get("metrics")
        if row.get("status") != "online" or not metrics:
            problems.append(f"{host}: {row.get('status')}")
            continue
        sample_age = generated_at - int(metrics.get("collected_at", 0))
        if sample_age < 0 or sample_age > MAX_SAMPLE_AGE_SEC:
            problems.append(f"{host}: sample is {sample_age}s old")
        services = metrics.get("services", {})
        if host.endswith(("-1", "-2", "-3")):
            if services.get("patroni_cluster") != "healthy":
                problems.append(f"{host}: Patroni cluster is not healthy")
            if services.get("etcd_quorum") != "healthy":
                problems.append(f"{host}: etcd quorum is not healthy")
        if host.endswith(("-4", "-5")):
            if services.get("backup_timer") != "active":
                problems.append(f"{host}: backup timer is inactive")
            if services.get("restore_test_timer") != "active":
                problems.append(f"{host}: restore-test timer is inactive")
            if services.get("backup_result") != "success":
                problems.append(f"{host}: latest backup failed")
            if services.get("restore_test_result") != "success":
                problems.append(f"{host}: latest restore test failed")
            backup_age = services.get("backup_age_seconds")
            if backup_age is None or backup_age > MAX_BACKUP_AGE_SEC:
                problems.append(f"{host}: backup is missing or stale")
            restore_age = services.get("restore_test_age_seconds")
            if restore_age is None or restore_age > MAX_RESTORE_AGE_SEC:
                problems.append(f"{host}: restore proof is missing or stale")
    return {"status": "ok" if not problems else "degraded", "problems": problems}


def snapshot() -> dict:
    generated_at = int(time.time())
    with STATE_LOCK:
        rows = [dict(record) for record in STATE.values()]
    return {
        "generated_at": generated_at,
        "hosts": rows,
        "health": health_summary(rows, generated_at),
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        status_code = 200
        if self.path == "/":
            body, content_type = HTML.encode(), "text/html; charset=utf-8"
        elif self.path == "/api/metrics":
            payload = snapshot()
            body, content_type = json.dumps(payload).encode(), "application/json"
        elif self.path == "/health":
            payload = snapshot()["health"]
            status_code = 200 if payload["status"] == "ok" else 503
            body, content_type = json.dumps(payload).encode(), "application/json"
        else:
            self.send_error(404)
            return
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--interval", default=30, type=int)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    threading.Thread(target=collector, args=(args.interval,), daemon=True).start()
    print(f"dashboard listening on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
