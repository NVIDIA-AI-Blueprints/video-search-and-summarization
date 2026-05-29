#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

set -euo pipefail

# Alert Bridge Latency Analysis (cap at 1000 hits)

if [ $# -lt 2 ]; then
  echo "Usage: $0 <duration> <host>"
  echo "  duration: ES time notation, e.g. 30m, 1h, 2d"
  echo "Example: $0 1h localhost"
  exit 1
fi

DURATION="$1"
HOST="$2"

if ! [[ "$DURATION" =~ ^[0-9]+[mhd]$ ]]; then
  echo "ERROR: <duration> must be a number followed by m, h, or d (got: $DURATION)"
  exit 2
fi

if ! command -v python3 &>/dev/null; then
  echo "ERROR: python3 is required but not installed"
  exit 1
fi

CSV="alert_bridge_latency_$(date +%Y%m%d_%H%M%S).csv"
TMP="$(mktemp /tmp/es_data.XXXXXX.json)"
cleanup() { rm -f "$TMP"; }
trap cleanup EXIT

# Build ES query body
read -r -d '' ES_BODY <<EOF || true
{
  "query": {
    "bool": {
      "filter": [
        { "range": { "end": { "gte": "now-${DURATION}", "lte": "now" } } },
        { "exists": { "field": "info.latency" } }
      ]
    }
  },
  "sort": [{ "end": "asc" }],
  "_source": [
    "end",
    "sensorId",
    "info.latency",
    "info.indexedAt"
  ]
}
EOF

# Fetch data from Elasticsearch
HTTP_CODE="$(
  curl -sS -m 30 \
    -o "$TMP" \
    -w "%{http_code}" \
    "http://${HOST}:9200/mdx-vlm-incidents-*/_search?size=1000" \
    -H 'Content-Type: application/json' \
    -d "$ES_BODY" || true
)"

if [ -z "$HTTP_CODE" ] || [ "$HTTP_CODE" = "000" ]; then
  echo "ERROR: Failed to connect to Elasticsearch at ${HOST}:9200"
  exit 3
fi

if [ "$HTTP_CODE" != "200" ]; then
  echo "ERROR: Elasticsearch returned HTTP $HTTP_CODE"
  head -c 500 "$TMP" || true
  echo
  exit 3
fi

# Process data with Python
python3 - "$TMP" "$CSV" "$DURATION" << 'EOF'
import sys, json, csv, math, re
from datetime import datetime

tmp_json = sys.argv[1]
csv_file = sys.argv[2]
duration = sys.argv[3]

def get_nested(d, path):
    cur = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur

def get_first_nested(d, paths):
    for path in paths:
        value = get_nested(d, path)
        if value is not None:
            return value
    return None

def parse_latency_payload(raw):
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        if not raw.strip():
            return {}
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    return {}

_iso_re = re.compile(r"^(?P<prefix>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?P<frac>\.\d+)?(?P<tz>Z|[+-]\d{2}:\d{2})?$")

def parse_ts(s: str) -> datetime:
    """Parse ES ISO-8601 timestamps; truncates nanoseconds to microseconds."""
    if not s:
        return None
    m = _iso_re.match(s)
    if not m:
        return None
    prefix = m.group("prefix")
    frac = m.group("frac") or ""
    tz = m.group("tz") or "Z"
    if frac:
        digits = frac[1:]
        digits = (digits + "000000")[:6]
        frac = "." + digits
    if tz == "Z":
        tz = "+00:00"
    return datetime.fromisoformat(prefix + frac + tz)

def percentile(sorted_x, p: float) -> float:
    if not sorted_x:
        return float("nan")
    n = len(sorted_x)
    if n == 1:
        return sorted_x[0]
    k = (n - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_x[int(k)]
    return sorted_x[f] * (c - k) + sorted_x[c] * (k - f)

def avg(x):
    return sum(x) / len(x) if x else float("nan")

def fmt(v):
    if v is None or math.isnan(v):
        return "N/A"
    return f"{v:.2f}s"

with open(tmp_json) as f:
    data = json.load(f)

if isinstance(data, dict) and "error" in data:
    print("Elasticsearch error:")
    print(json.dumps(data.get("error"), indent=2)[:2000])
    sys.exit(1)

hits = (data.get("hits") or {}).get("hits") or []
if not hits:
    print("No data found. Ensure indexedAt pipeline is enabled and new events have been processed.")
    sys.exit(1)

# Metrics arrays
idx_ends, kafka_lags, upstreams = [], [], []
vsts, vlms = [], []
skipped = 0

def fmt_duration(v):
    """Format duration: ms if <1s, else seconds"""
    if v is None:
        return "N/A"
    if v < 1.0:
        return f"{int(v * 1000)}ms"
    return f"{v:.2f}s"

row_num = 0
with open(csv_file, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow([
        "#", "Upstream (CV+Analytics)", "AB Consumer Lag", "VST Fetch", "VLM Inference", "Idx-End(E2E)"
    ])

    for h in hits:
        try:
            src = h.get("_source") or {}
            info = src.get("info") or {}

            latency_data = parse_latency_payload(info.get("latency"))
            ts = latency_data.get("timestamps") or {}

            indexed_dt = parse_ts(info.get("indexedAt"))
            kafka_pub_dt = parse_ts(ts.get("kafkaPublishedAt") or ts.get("kafka_published_at"))
            kafka_con_dt = parse_ts(ts.get("kafkaConsumedAt") or ts.get("kafka_consumed_at"))
            end_dt = parse_ts(src.get("end"))

            vlm = get_first_nested(latency_data, [
                ["vlmRequest", "duration"],
                ["vlm_request", "duration"],
            ])
            vst = get_first_nested(latency_data, [
                ["getVideoStreamUrlWithOverlay", "duration"],
                ["get_video_stream_url_with_overlay", "duration"],
            ])
            
            # Required: latency timestamps and durations (indexed_at optional)
            if not all([kafka_pub_dt, kafka_con_dt, vlm]):
                skipped += 1
                continue

            vlm = float(vlm)
            vst = float(vst) if vst else 0.0
            
            # Calculate latency breakdown (starting from kafka_published_at)
            kafka_lag = (kafka_con_dt - kafka_pub_dt).total_seconds()
            upstream = (kafka_pub_dt - end_dt).total_seconds() if (kafka_pub_dt and end_dt) else None

            # indexed_at is optional (requires ES ingest pipeline)
            idx_minus_end = (indexed_dt - end_dt).total_seconds() if (indexed_dt and end_dt) else None
            # Exclude negative values from stats (clock skew)
            if kafka_lag < 0:
                kafka_lag = None
            if upstream is not None and upstream < 0:
                upstream = None
            if idx_minus_end is not None and idx_minus_end < 0:
                idx_minus_end = None

            row_num += 1
            w.writerow([
                row_num,
                fmt_duration(upstream),
                fmt_duration(kafka_lag),
                fmt_duration(vst),
                fmt_duration(vlm),
                fmt_duration(idx_minus_end),
            ])
            if upstream is not None:
                upstreams.append(upstream)
            if kafka_lag is not None:
                kafka_lags.append(kafka_lag)
            vsts.append(vst)
            vlms.append(vlm)
            if idx_minus_end is not None:
                idx_ends.append(idx_minus_end)

        except Exception as exc:
            skipped += 1
            print(f"[WARN] Row skipped: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue

if not vlms:
    print("No usable rows after filtering/parsing.")
    sys.exit(1)

def print_stats(name, arr):
    if not arr:
        print(f"{name:<28} {'N/A':>8} {'N/A':>8} {'N/A':>8} {'N/A':>8} {'N/A':>8}  (n=0)")
        return
    s = sorted(arr)
    print(f"{name:<28} {avg(s):>7.2f}s {percentile(s,.5):>7.2f}s {percentile(s,.9):>7.2f}s {percentile(s,.99):>7.2f}s {max(s):>7.2f}s  (n={len(arr)})")

print()
print("=" * 100)
print(f"ALERT VERIFICATION LATENCY - Last {duration} (hits={len(hits)} usable={len(vlms)})")
print("=" * 100)
print(f"{'Metric':<28} {'Avg':>8} {'p50':>8} {'p90':>8} {'p99':>8} {'Max':>8}")
print("-" * 100)
print_stats("Upstream (CV+Analytics)", upstreams)
print_stats("AB Consumer Lag", kafka_lags)
print_stats("VST Fetch", vsts)
print_stats("VLM Inference", vlms)
print("-" * 100)
print_stats("Idx-End(E2E)", idx_ends)
print("=" * 100)
print(f"Skipped: {skipped}")
print(f"CSV: {csv_file}")
EOF
