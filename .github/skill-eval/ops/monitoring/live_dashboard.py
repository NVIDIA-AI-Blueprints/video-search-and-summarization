#!/usr/bin/env python3
"""Serve a live eight-host dashboard using Brev as the collection transport."""
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
.foot{display:flex;justify-content:space-between;color:var(--muted);font-size:12px;margin-top:14px;border-top:1px solid var(--line);padding-top:11px}
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
async function refresh(){
 try{
  const r=await fetch('/api/metrics',{cache:'no-store'}),d=await r.json(),rows=d.hosts,online=rows.filter(x=>x.status==='online'),vals=online.map(x=>x.metrics);
  const avg=k=>vals.length?(vals.reduce((a,x)=>a+x[k],0)/vals.length).toFixed(1):'—';
  document.querySelector('#summary').innerHTML=[
   ['Hosts online',`${online.length} / 8`],['Average CPU',`${avg('cpu_percent')}%`],['Average RAM',`${avg('ram_percent')}%`],['Highest disk',`${vals.length?Math.max(...vals.map(x=>x.disk_percent)).toFixed(1):'—'}%`]
  ].map(x=>`<div class="stat"><span class="sub">${x[0]}</span><b>${x[1]}</b></div>`).join('');
  document.querySelector('#grid').innerHTML=rows.map(x=>{
   if(!x.metrics)return`<article class="card"><div class="cardhead"><span class="name">${x.host}</span><span class="pill bad">${x.status}</span></div><div class="sub">${x.error||'Waiting for first sample'}</div></article>`;
   const m=x.metrics;
   return`<article class="card"><div class="cardhead"><span class="name">${x.host}</span><span class="pill">${x.status}</span></div>
   <div class="metrics">${metric('CPU',m.cpu_percent.toFixed(1)+'%',m.cpu_percent)}${metric('RAM',m.ram_percent.toFixed(1)+'%',m.ram_percent)}${metric('Root disk',m.disk_percent.toFixed(1)+'%',m.disk_percent)}</div>
   <div class="foot"><span>${m.cpu_count} vCPU · load ${m.load_1m}</span><span>RAM ${bytes(m.ram_used)} / ${bytes(m.ram_total)} · Disk free ${bytes(m.disk_free)} · Uptime ${age(m.uptime_seconds)}</span></div></article>`}).join('');
  document.querySelector('#updated').textContent='Updated '+new Date(d.generated_at*1000).toLocaleTimeString();
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
        ["brev", "exec", host, f"python3 {REMOTE_PROBE}"],
        capture_output=True,
        text=True,
        timeout=45,
    )


def stage_probe(host: str) -> None:
    process = subprocess.run(
        ["brev", "copy", str(LOCAL_PROBE), f"{host}:{REMOTE_PROBE}"],
        capture_output=True,
        text=True,
        timeout=45,
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
        record = {"host": host, "status": "online", "error": None, "metrics": metrics}
    except Exception as exc:
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
        # A slow Brev tunnel on one host must not delay health visibility for
        # the other seven coordinators.
        with ThreadPoolExecutor(max_workers=len(HOSTS)) as executor:
            list(executor.map(collect_host, HOSTS))
        time.sleep(interval)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/":
            body, content_type = HTML.encode(), "text/html; charset=utf-8"
        elif self.path == "/api/metrics":
            with STATE_LOCK:
                payload = {"generated_at": int(time.time()), "hosts": list(STATE.values())}
            body, content_type = json.dumps(payload).encode(), "application/json"
        elif self.path == "/health":
            body, content_type = b'{"status":"ok"}', "application/json"
        else:
            self.send_error(404)
            return
        self.send_response(200)
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
    threading.Thread(target=collector, args=(args.interval,), daemon=True).start()
    print(f"dashboard listening on http://{args.host}:{args.port}", flush=True)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
