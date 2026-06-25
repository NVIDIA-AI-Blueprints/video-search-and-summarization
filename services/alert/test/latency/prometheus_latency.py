#!/usr/bin/env python3
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

import argparse
import csv
import json
import math
import re
import sys
from datetime import datetime, timezone
from typing import Any, List, Optional, Tuple

try:
    import requests
except ImportError:
    print("ERROR: 'requests' is required. Install with: pip install requests")
    sys.exit(1)


# ── Shared helpers ────────────────────────────────────────────────────────────

def parse_duration_to_seconds(d: str) -> int:
    m = re.fullmatch(r"(\d+)([mhd])", d)
    if not m:
        raise ValueError(f"Invalid duration '{d}': must be a number followed by m, h, or d")
    val, unit = int(m.group(1)), m.group(2)
    return val * {"m": 60, "h": 3600, "d": 86400}[unit]


def percentile(sorted_x: List[float], p: float) -> float:
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


def list_avg(x: List[float]) -> float:
    return sum(x) / len(x) if x else float("nan")


def fmt_val(v: Optional[float]) -> str:
    """Format a seconds value: ms if <1s, else 2dp seconds. N/A if missing."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "    N/A"
    if v < 1.0:
        return f"{int(v * 1000):>6}ms"
    return f"{v:>7.2f}s"


W = 100


# ── Prometheus section ────────────────────────────────────────────────────────


class PromQueryError(RuntimeError):
    """Raised when a Prometheus HTTP query fails at transport level.

    Keeping this as a typed exception (rather than ``sys.exit`` from inside
    the helper) lets ``main()`` decide the process exit code after every
    report section has had a chance to run — a single transient hiccup on
    one of the ~30 queries no longer kills the whole report mid-stream.
    """


def prom_query(base_url: str, promql: str) -> Optional[float]:
    """Run an instant PromQL query and return the first scalar result, or None.

    Raises :class:`PromQueryError` on transport-level failures (timeouts,
    connection resets, non-2xx responses). Returns ``None`` when the query
    succeeded but no samples matched — that is a legitimate "no data in
    window" outcome, not an error.
    """
    try:
        r = requests.get(
            f"{base_url}/api/v1/query",
            params={"query": promql},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as e:
        raise PromQueryError(f"Prometheus request failed: {e}") from e

    results = (data.get("data") or {}).get("result") or []
    if not results:
        return None
    try:
        return float(results[0]["value"][1])
    except (KeyError, IndexError, ValueError):
        return None


def _label_filter(label_filter: str = "") -> str:
    return f"{{{label_filter}}}" if label_filter else ""


def prom_quantile(
    base_url: str,
    metric: str,
    q: float,
    window: str,
    label_filter: str = "",
) -> Optional[float]:
    filt = _label_filter(label_filter)
    return prom_query(
        base_url,
        f"histogram_quantile({q}, sum by (le) (rate({metric}_bucket{filt}[{window}])))"
    )


def prom_avg(
    base_url: str,
    metric: str,
    window: str,
    label_filter: str = "",
) -> Optional[float]:
    filt = _label_filter(label_filter)
    return prom_query(
        base_url,
        f"sum(rate({metric}_sum{filt}[{window}])) / "
        f"sum(rate({metric}_count{filt}[{window}]))"
    )


def prom_count(
    base_url: str,
    metric: str,
    window: str,
    label_filter: str = "",
) -> int:
    filt = _label_filter(label_filter)
    v = prom_query(base_url, f"sum(increase({metric}_count{filt}[{window}]))")
    return int(round(v)) if v is not None else 0


def prom_counter(base_url: str, metric: str, window: str,
                 label_filter: str = "") -> int:
    filt = _label_filter(label_filter)
    v = prom_query(base_url, f"sum(increase({metric}{filt}[{window}]))")
    return int(round(v)) if v is not None else 0


def prom_fetch_row(
    base_url: str,
    metric: str,
    window: str,
    label_filter: str = "",
) -> Tuple:
    """Return (avg, p50, p90, p99, count) for a histogram metric."""
    return (
        prom_avg(base_url, metric, window, label_filter),
        prom_quantile(base_url, metric, 0.50, window, label_filter),
        prom_quantile(base_url, metric, 0.90, window, label_filter),
        prom_quantile(base_url, metric, 0.99, window, label_filter),
        prom_count(base_url, metric, window, label_filter),
    )


def print_prom_row(name: str, avg_v, p50_v, p90_v, p99_v, n: int) -> None:
    print(
        f"  {name:<28}"
        f" {fmt_val(avg_v)}"
        f" {fmt_val(p50_v)}"
        f" {fmt_val(p90_v)}"
        f" {fmt_val(p99_v)}"
        f"   (n={n})"
    )


def run_prometheus(
    base_url: str,
    window: str,
    now_str: str,
    sensor_id: Optional[str] = None,
) -> bool:
    """Print the Prometheus latency report.

    Returns ``True`` iff the endpoint was reachable and every query completed
    (even if the window contained no samples). Returns ``False`` when the
    endpoint is down or a query raised :class:`PromQueryError` — callers
    should propagate that into the process exit code.
    """
    try:
        requests.get(f"{base_url}/-/healthy", timeout=5).raise_for_status()
    except requests.RequestException as exc:
        print(f"\nERROR: Cannot reach Prometheus at {base_url}: {exc}", file=sys.stderr)
        print("       Check host/port and that Prometheus is running.", file=sys.stderr)
        return False

    sensor_filter = ""
    if sensor_id:
        sensor_filter = f'sensorId="{_promql_escape(sensor_id)}"'
        histogram_metrics = {
            "upstream": "alert_bridge_upstream_duration_by_sensor_seconds",
            "kafka_lag": "alert_bridge_kafka_lag_duration_by_sensor_seconds",
            "queue_wait": "alert_bridge_worker_queue_wait_duration_by_sensor_seconds",
            "vst": "alert_bridge_vst_duration_by_sensor_seconds",
            "video_len": "alert_bridge_video_length_by_sensor_seconds",
            "vlm": "alert_bridge_vlm_duration_by_sensor_seconds",
            "worker": "alert_bridge_worker_processing_by_sensor_seconds",
            "e2e": "alert_bridge_e2e_duration_by_sensor_seconds",
        }
        counter_metrics = {
            "after_dedup": "alert_bridge_events_after_dedup_by_sensor_total",
            "events": "alert_bridge_events_by_sensor_total",
            "skipped_confirmed": "alert_bridge_events_skipped_confirmed_by_sensor_total",
            "dropped": "alert_bridge_events_dropped_by_sensor_total",
            "failures": "alert_bridge_verification_failures_by_sensor_total",
        }
    else:
        histogram_metrics = {
            "upstream": "alert_bridge_upstream_duration_seconds",
            "kafka_lag": "alert_bridge_kafka_lag_duration_seconds",
            "queue_wait": "alert_bridge_worker_queue_wait_duration_seconds",
            "vst": "alert_bridge_vst_duration_seconds",
            "video_len": "alert_bridge_video_length_seconds",
            "vlm": "alert_bridge_vlm_duration_seconds",
            "worker": "alert_bridge_worker_processing_seconds",
            "e2e": "alert_bridge_e2e_duration_seconds",
        }
        counter_metrics = {
            "after_dedup": "alert_bridge_events_after_dedup_total",
            "events": "alert_bridge_events_total",
            "skipped_confirmed": "alert_bridge_events_skipped_confirmed_total",
            "dropped": "alert_bridge_events_dropped_total",
            "failures": "alert_bridge_verification_failures_total",
        }

    def _labels(*parts: str) -> str:
        return ",".join(part for part in parts if part)

    try:
        upstream = prom_fetch_row(base_url, histogram_metrics["upstream"], window, sensor_filter)
        kafka_lag = prom_fetch_row(base_url, histogram_metrics["kafka_lag"], window, sensor_filter)
        queue_wait = prom_fetch_row(base_url, histogram_metrics["queue_wait"], window, sensor_filter)
        vst = prom_fetch_row(base_url, histogram_metrics["vst"], window, sensor_filter)
        video_len = prom_fetch_row(base_url, histogram_metrics["video_len"], window, sensor_filter)
        vlm = prom_fetch_row(base_url, histogram_metrics["vlm"], window, sensor_filter)
        worker = prom_fetch_row(base_url, histogram_metrics["worker"], window, sensor_filter)
        e2e = prom_fetch_row(base_url, histogram_metrics["e2e"], window, sensor_filter)

        after_dedup = prom_counter(base_url, counter_metrics["after_dedup"], window, sensor_filter)
        confirmed = prom_counter(
            base_url, counter_metrics["events"], window,
            _labels('verdict="confirmed"', sensor_filter),
        )
        rejected = prom_counter(
            base_url, counter_metrics["events"], window,
            _labels('verdict="rejected"', sensor_filter),
        )
        failed = prom_counter(
            base_url, counter_metrics["events"], window,
            _labels('verdict="verification-failed"', sensor_filter),
        )
        unknown_ev = prom_counter(
            base_url, counter_metrics["events"], window,
            _labels('verdict="unknown"', sensor_filter),
        )
        total_ev    = confirmed + rejected + failed + unknown_ev

        skipped_confirmed = prom_counter(
            base_url,
            counter_metrics["skipped_confirmed"],
            window,
            sensor_filter,
        )

        drop_end_time = prom_counter(
            base_url, counter_metrics["dropped"], window,
            _labels('reason="end_time_delta"', sensor_filter),
        )
        drop_dedup = prom_counter(
            base_url, counter_metrics["dropped"], window,
            _labels('reason="dedup"', sensor_filter),
        )
        drop_ratelimit = prom_counter(
            base_url, counter_metrics["dropped"], window,
            _labels('reason="rate_limit"', sensor_filter),
        )
        total_dropped  = drop_end_time + drop_dedup + drop_ratelimit

        # VST-side reasons (C14): dashboards previously filtering on
        # ``reason="vst_failure"`` should migrate to the structured set
        # below. Anyone still reading ``reason="vst_failure"`` will see
        # zero — we leave it off the query list deliberately so the
        # migration is visible.
        fail_vst_timeout = prom_counter(
            base_url, counter_metrics["failures"], window,
            _labels('reason="vst_timeout"', sensor_filter),
        )
        fail_vst_overloaded = prom_counter(
            base_url, counter_metrics["failures"], window,
            _labels('reason="vst_overloaded"', sensor_filter),
        )
        fail_vst_not_found = prom_counter(
            base_url, counter_metrics["failures"], window,
            _labels('reason="vst_not_found"', sensor_filter),
        )
        fail_vst_unavailable = prom_counter(
            base_url, counter_metrics["failures"], window,
            _labels('reason="vst_unavailable"', sensor_filter),
        )
        fail_vst_client = prom_counter(
            base_url, counter_metrics["failures"], window,
            _labels('reason="vst_client_error"', sensor_filter),
        )
        fail_vst_server = prom_counter(
            base_url, counter_metrics["failures"], window,
            _labels('reason="vst_server_error"', sensor_filter),
        )
        fail_vst_unknown = prom_counter(
            base_url, counter_metrics["failures"], window,
            _labels('reason="vst_unknown"', sensor_filter),
        )

        fail_url = prom_counter(
            base_url, counter_metrics["failures"], window,
            _labels('reason="url_validation"', sensor_filter),
        )
        fail_parse = prom_counter(
            base_url, counter_metrics["failures"], window,
            _labels('reason="vlm_parse_failure"', sensor_filter),
        )
        fail_timeout = prom_counter(
            base_url, counter_metrics["failures"], window,
            _labels('reason="vlm_timeout"', sensor_filter),
        )
        fail_conn = prom_counter(
            base_url, counter_metrics["failures"], window,
            _labels('reason="vlm_connection_error"', sensor_filter),
        )
        fail_srv = prom_counter(
            base_url, counter_metrics["failures"], window,
            _labels('reason="vlm_server_error"', sensor_filter),
        )
        fail_inv = prom_counter(
            base_url, counter_metrics["failures"], window,
            _labels('reason="vlm_invalid_payload"', sensor_filter),
        )
        fail_no_prompt = prom_counter(
            base_url, counter_metrics["failures"], window,
            _labels('reason="no_prompt"', sensor_filter),
        )
        fail_redis = prom_counter(
            base_url, counter_metrics["failures"], window,
            _labels('reason="redis_unavailable"', sensor_filter),
        )
        fail_unk = prom_counter(
            base_url, counter_metrics["failures"], window,
            _labels('reason="unknown"', sensor_filter),
        )
    except PromQueryError as exc:
        print(f"\nERROR: Prometheus query failed mid-report: {exc}", file=sys.stderr)
        print("       Partial Prometheus results suppressed; exit code will reflect the failure.", file=sys.stderr)
        return False

    vlm_calls     = vlm[4]
    worker_events = worker[4]
    retry_rate    = (vlm_calls / worker_events) if worker_events > 0 else None

    print()
    print("=" * W)
    if sensor_id:
        print(
            f"  PROMETHEUS — Alert Agent Latency  |  "
            f"sensorId={sensor_id!r}  |  Last {window}  |  {now_str}"
        )
    else:
        print(f"  PROMETHEUS — Alert Agent Latency  |  Last {window}  |  {now_str}")
    print(f"  Source: {base_url}")
    print("=" * W)
    print(f"  {'Metric':<28} {'Avg':>8} {'p50':>8} {'p90':>8} {'p99':>8}   Count")
    print("-" * W)
    print_prom_row("Upstream (CV+Analytics)",  *upstream)
    print_prom_row("Kafka Consumer Lag",       *kafka_lag)
    print_prom_row("Worker Queue Wait",        *queue_wait)
    print("-" * W)
    print_prom_row("VST Fetch",                *vst)
    print_prom_row("Video Clip Length",        *video_len)
    print_prom_row("VLM Inference",            *vlm)
    print_prom_row("Worker Processing",        *worker)
    print("-" * W)
    print_prom_row("E2E (event end→ready)",    *e2e)
    print("=" * W)

    conf_pct = f"{100*confirmed/total_ev:.1f}%" if total_ev else "N/A"
    rej_pct  = f"{100*rejected/total_ev:.1f}%"  if total_ev else "N/A"
    fail_pct = f"{100*failed/total_ev:.1f}%"    if total_ev else "N/A"
    print()
    print(f"  Events (last {window})")
    # Reconciliation invariant (C2): the two identities printed below
    # should hold end-to-end. The first is complete (C22); the second
    # is complete after C9 modulo the C10 no-prompt path.
    #   raw_kafka  = (events dropped) + (events after dedup)
    #   after_dedup = Confirmed + Rejected + Verification fail + Unknown
    #                 + Skipped (confirmed) + (future C10: no-prompt)
    # Anyone adding new filters / early exits must update the
    # corresponding counter for the identity to stay valid.
    raw_kafka = after_dedup + total_dropped
    print(f"    Raw Kafka events : {raw_kafka}")
    print(f"    Dropped (pre)    : {total_dropped}")
    print(f"    After dedup      : {after_dedup}")
    print(f"    Confirmed        : {confirmed:<6}  ({conf_pct})")
    print(f"    Rejected         : {rejected:<6}  ({rej_pct})")
    print(f"    Verification fail: {failed:<6}  ({fail_pct})")
    if unknown_ev:
        print(f"    Unknown verdict  : {unknown_ev}  (drift — check upstream producers)")
    print(f"    Skipped (conf.)  : {skipped_confirmed}  (dedup of already-confirmed)")
    print(f"    Total published  : {total_ev}")
    if retry_rate is not None:
        print(f"    VLM retry rate   : {retry_rate:.2f}x  ({vlm_calls} VLM calls / {worker_events} events)")

    # ── Events dropped (pre-processing) ──────────────────────────────────
    # Always print all three rows — including zeros — so downstream tools
    # parsing the output see a stable shape (C19 style, applied here
    # preemptively for the new counter). Also makes silent misconfigs
    # (e.g. end_time_delta window too tight) immediately visible.
    print()
    pct_denom = raw_kafka or 1  # avoid division by zero on cold start
    print(f"  Events dropped (last {window})  [total: {total_dropped} of {raw_kafka} raw]")
    dropped_rows = [
        ("end_time_delta", drop_end_time,  "End timestamp outside configured record-time window"),
        ("dedup",          drop_dedup,     "Duplicate of a recently-processed event (Redis TTL)"),
        ("rate_limit",     drop_ratelimit, "Per-sensor VLM rate limiter"),
    ]
    for reason, count, desc in dropped_rows:
        pct = f"{100*count/pct_denom:.1f}%" if raw_kafka else "  N/A"
        print(f"    {reason:<16} {count:<6} ({pct:>6})  {desc}")

    fail_vst_total = (
        fail_vst_timeout + fail_vst_overloaded + fail_vst_not_found
        + fail_vst_unavailable + fail_vst_client + fail_vst_server + fail_vst_unknown
    )
    total_failures = (
        fail_vst_total + fail_url + fail_parse + fail_timeout + fail_conn
        + fail_srv + fail_inv + fail_no_prompt + fail_redis + fail_unk
    )
    # C19: always print the full failure table, even when everything is
    # zero. Downstream automation parsing this output expects a stable
    # shape; silently omitting the section when there are no failures
    # made the "healthy window" case indistinguishable from "the script
    # changed" on the consumer side. A leading "[total: 0]" line makes
    # the all-clear state explicit instead.
    print()
    print(f"  Verification failures (last {window})  [total: {total_failures}]")
    rows = [
        ("vst_timeout",          fail_vst_timeout,     "VST request timed out"),
        ("vst_overloaded",       fail_vst_overloaded,  "VST returned 503 overloaded"),
        ("vst_not_found",        fail_vst_not_found,   "No recording for the requested time"),
        ("vst_unavailable",      fail_vst_unavailable, "VST service unreachable"),
        ("vst_client_error",     fail_vst_client,      "VST rejected request (4xx)"),
        ("vst_server_error",     fail_vst_server,      "Bare VSTError / 5xx / missing video URL"),
        ("vst_unknown",          fail_vst_unknown,     "Non-VSTError on the VST path"),
        ("url_validation",       fail_url,             "Video URL unreachable"),
        ("vlm_parse_failure",    fail_parse,           "VLM response could not be parsed"),
        ("vlm_timeout",          fail_timeout,         "VLM request timed out"),
        ("vlm_connection_error", fail_conn,            "Could not connect to VLM"),
        ("vlm_server_error",     fail_srv,             "VLM 5xx server error"),
        ("vlm_invalid_payload",  fail_inv,             "VLM rejected payload (422)"),
        ("no_prompt",            fail_no_prompt,       "Alert type has no prompt configured"),
        ("redis_unavailable",    fail_redis,           "Redis failure during confirmed-verdict skip check"),
        ("unknown",              fail_unk,             "Unexpected exception"),
    ]
    for reason, count, desc in rows:
        # Percentage column shows "  N/A" when the window had no
        # failures at all (division by zero). Every row still prints
        # so automation sees a stable column layout.
        if total_failures > 0:
            pct = f"{100*count/total_failures:.1f}%"
        else:
            pct = "  N/A"
        print(f"    {reason:<24} {count:<6} ({pct:>6})  {desc}")

    no_data = [n for n, row in [("VLM", vlm), ("VST", vst), ("E2E", e2e)] if row[4] == 0]
    if no_data:
        print()
        print(f"  NOTE: No data for: {', '.join(no_data)}")
        if sensor_id:
            print(f"        Either this sensor had no events in the last {window},")
            print(f"        or alert_agent.metrics.per_sensor_labels is not enabled.")
        else:
            print(f"        Either no events were processed in the last {window},")
            print(f"        or PROMETHEUS_METRICS_ENABLED is not set on the alert-agent.")

    return True


# ── Elasticsearch section ─────────────────────────────────────────────────────

_iso_re = re.compile(
    r"^(?P<prefix>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})"
    r"(?P<frac>\.\d+)?(?P<tz>Z|[+-]\d{2}:\d{2})?$"
)


def parse_ts(s: str) -> Optional[datetime]:
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
        digits = (frac[1:] + "000000")[:6]
        frac = "." + digits
    if tz == "Z":
        tz = "+00:00"
    return datetime.fromisoformat(prefix + frac + tz)


def get_nested(d: dict, path: List[str]):
    cur = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def get_first_nested(d: dict, paths: List[List[str]]):
    for path in paths:
        value = get_nested(d, path)
        if value is not None:
            return value
    return None


def parse_latency_payload(raw: Any) -> dict:
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


def print_es_row(name: str, arr: List[float]) -> None:
    if not arr:
        print(f"  {name:<28} {'N/A':>8} {'N/A':>8} {'N/A':>8} {'N/A':>8} {'N/A':>8}  (n=0)")
        return
    s = sorted(arr)
    print(
        f"  {name:<28}"
        f" {fmt_val(list_avg(s))}"
        f" {fmt_val(percentile(s, 0.50))}"
        f" {fmt_val(percentile(s, 0.90))}"
        f" {fmt_val(percentile(s, 0.99))}"
        f" {fmt_val(max(s))}"
        f"  (n={len(arr)})"
    )


def run_elasticsearch(es_host: str, es_port: int, duration: str, now_str: str) -> bool:
    """Print the Elasticsearch latency report.

    Returns ``True`` iff the endpoint responded with HTTP 200 and the
    response was parseable (even if the window contained no usable rows).
    Returns ``False`` for any transport-level or endpoint-level failure —
    callers should propagate that into the process exit code so CI can
    distinguish "script ran cleanly, no data" from "ES is down".
    """
    es_base = f"http://{es_host}:{es_port}"

    # Alert Bridge 0.0.61 writes ``info.latency`` as a JSON string whose
    # payload uses camelCase keys. Query the parent field, then parse the
    # payload client-side. Nested ``exists`` filters do not match string
    # fields and cause false-empty ES latency reports.
    es_body = {
        "query": {
            "bool": {
                "filter": [
                    {"range": {"end": {"gte": f"now-{duration}", "lte": "now"}}},
                    {"exists": {"field": "info.latency"}},
                ]
            }
        },
        "sort": [{"end": "asc"}],
        "_source": [
            "end",
            "sensorId",
            "info.indexedAt",
            "info.latency",
        ],
    }

    try:
        r = requests.post(
            f"{es_base}/mdx-vlm-incidents-*/_search?size=1000",
            headers={"Content-Type": "application/json"},
            data=json.dumps(es_body),
            timeout=30,
        )
    except requests.RequestException as e:
        print(f"\nERROR: Failed to connect to Elasticsearch at {es_base}: {e}", file=sys.stderr)
        return False

    if r.status_code != 200:
        print(f"\nERROR: Elasticsearch returned HTTP {r.status_code}", file=sys.stderr)
        print(r.text[:500], file=sys.stderr)
        return False

    data = r.json()
    if "error" in data:
        print("\nElasticsearch error:", file=sys.stderr)
        print(json.dumps(data["error"], indent=2)[:2000], file=sys.stderr)
        return False

    hits = (data.get("hits") or {}).get("hits") or []
    if not hits:
        # "No data in window" is a legitimate successful run — the script
        # did its job, the window just happens to be empty. Return True so
        # CI does not flag this as an endpoint failure.
        print(f"\n  [ES] No data found for the last {duration}.")
        print("       Ensure the info.indexedAt ingest pipeline is enabled and events have been processed.")
        return True

    idx_ends, kafka_lags, upstreams, vsts, vlms = [], [], [], [], []
    skipped = 0
    csv_rows = []

    for h in hits:
        try:
            src = h.get("_source") or {}
            latency = parse_latency_payload(get_nested(src, ["info", "latency"]))
            ts = latency.get("timestamps") or {}

            # Prefer the Alert Bridge 0.0.61 camelCase contract, but keep
            # snake_case fallbacks for older evaluator fixtures/documents.
            indexed_dt   = parse_ts(get_nested(src, ["info", "indexedAt"]))
            kafka_pub_dt = parse_ts(ts.get("kafkaPublishedAt") or ts.get("kafka_published_at"))
            kafka_con_dt = parse_ts(ts.get("kafkaConsumedAt") or ts.get("kafka_consumed_at"))
            end_dt       = parse_ts(src.get("end"))

            vlm_dur = get_first_nested(latency, [
                ["vlmRequest", "duration"],
                ["vlm_request", "duration"],
            ])
            vst_dur = get_first_nested(latency, [
                ["getVideoStreamUrlWithOverlay", "duration"],
                ["get_video_stream_url_with_overlay", "duration"],
            ])

            if not all([kafka_pub_dt, kafka_con_dt, vlm_dur]):
                skipped += 1
                continue

            vlm_dur = float(vlm_dur)
            vst_dur = float(vst_dur) if vst_dur else 0.0

            kafka_lag = (kafka_con_dt - kafka_pub_dt).total_seconds()
            upstream  = (kafka_pub_dt - end_dt).total_seconds() if (kafka_pub_dt and end_dt) else None
            idx_end   = (indexed_dt - end_dt).total_seconds() if (indexed_dt and end_dt) else None

            # Drop negative values (clock skew)
            if kafka_lag < 0:
                kafka_lag = None
            if upstream is not None and upstream < 0:
                upstream = None
            if idx_end is not None and idx_end < 0:
                idx_end = None

            csv_rows.append([
                len(csv_rows) + 1,
                _fmt_csv(upstream),
                _fmt_csv(kafka_lag),
                _fmt_csv(vst_dur),
                _fmt_csv(vlm_dur),
                _fmt_csv(idx_end),
            ])

            if upstream is not None:
                upstreams.append(upstream)
            if kafka_lag is not None:
                kafka_lags.append(kafka_lag)
            vsts.append(vst_dur)
            vlms.append(vlm_dur)
            if idx_end is not None:
                idx_ends.append(idx_end)

        except Exception as exc:
            skipped += 1
            print(f"[WARN] ES row skipped: {type(exc).__name__}: {exc}", file=sys.stderr)

    if not vlms:
        # Same reasoning as "no hits": the endpoint is healthy, the data
        # just didn't match our filter (e.g. indexed_at pipeline not yet
        # enabled). Not an error.
        print("\n  [ES] No usable rows after filtering/parsing.")
        return True

    # Write CSV
    csv_file = f"alert_bridge_latency_es_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with open(csv_file, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["#", "Upstream (CV+Analytics)", "AB Consumer Lag",
                    "VST Fetch", "VLM Inference", "Idx-End(E2E)"])
        w.writerows(csv_rows)

    print()
    print("=" * W)
    print(f"  ELASTICSEARCH — Alert Verification Latency  |  Last {duration}  |  {now_str}")
    print(f"  Source: {es_base}  |  hits={len(hits)}  usable={len(vlms)}  skipped={skipped}")
    print("=" * W)
    print(f"  {'Metric':<28} {'Avg':>8} {'p50':>8} {'p90':>8} {'p99':>8} {'Max':>8}   Count")
    print("-" * W)
    print_es_row("Upstream (CV+Analytics)", upstreams)
    print_es_row("AB Consumer Lag",        kafka_lags)
    print_es_row("VST Fetch",              vsts)
    print_es_row("VLM Inference",          vlms)
    print("-" * W)
    print_es_row("Idx-End(E2E)",           idx_ends)
    print("=" * W)
    print(f"  CSV: {csv_file}")

    return True


def _fmt_csv(v: Optional[float]) -> str:
    """Human-readable duration for CSV cells."""
    if v is None:
        return "N/A"
    if v < 1.0:
        return f"{int(v * 1000)}ms"
    return f"{v:.2f}s"


# ── Per-sensor breakdown (C21, --per-sensor flag) ─────────────────────────────


def _prom_range_query(base_url: str, promql: str) -> list:
    """Run an instant PromQL query and return the raw result list.

    ``prom_query`` collapses to a single scalar — we need the full
    list here so ``sum by (sensorId)(...)`` can enumerate sensors.
    Raises :class:`PromQueryError` on transport failure so the caller
    can decide the exit code.
    """
    import requests as _requests
    try:
        r = _requests.get(
            f"{base_url}/api/v1/query",
            params={"query": promql},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
    except _requests.RequestException as e:
        raise PromQueryError(f"Prometheus request failed: {e}") from e
    return (data.get("data") or {}).get("result") or []


def _promql_escape(value: str) -> str:
    """Escape a Prometheus label-value for inline use in a PromQL query.

    PromQL label-value literals follow the same escaping rules as Go
    strings: ``\\`` and ``"`` must be escaped. This helper is
    **critical** — without it a sensor ID containing a quote or
    backslash would produce a syntax error (best case) or inject
    arbitrary PromQL (worst case). The C21 allowlist already bounds
    sensor IDs to ≤128 chars and rejects non-strings, but user-supplied
    CLI input is independent from the metric-label sanitization so we
    re-escape here.
    """
    return value.replace("\\", "\\\\").replace('"', '\\"')


def run_per_sensor_breakdown(base_url: str, window: str) -> None:
    """Print a per-sensor table of confirmed/rejected/failed/dropped counts.

    Pulls ``alert_bridge_events_by_sensor_total`` and its siblings. If
    every query returns empty (the feature flag is off on the
    alert-agent side), prints a one-line hint telling operators how to
    enable it and returns cleanly. We deliberately do NOT fail the
    report — ``--per-sensor`` is a "show me more if you can" flag, not
    a hard requirement.
    """
    try:
        confirmed_rows = _prom_range_query(
            base_url,
            f'sum by (sensorId) (increase(alert_bridge_events_by_sensor_total'
            f'{{verdict="confirmed"}}[{window}]))',
        )
        rejected_rows = _prom_range_query(
            base_url,
            f'sum by (sensorId) (increase(alert_bridge_events_by_sensor_total'
            f'{{verdict="rejected"}}[{window}]))',
        )
        failed_rows = _prom_range_query(
            base_url,
            f'sum by (sensorId) (increase(alert_bridge_events_by_sensor_total'
            f'{{verdict="verification-failed"}}[{window}]))',
        )
        dropped_rows = _prom_range_query(
            base_url,
            f'sum by (sensorId) (increase(alert_bridge_events_dropped_by_sensor_total[{window}]))',
        )
    except PromQueryError as exc:
        print(f"\nERROR: per-sensor query failed: {exc}", file=sys.stderr)
        return

    # Merge the four queries into one sensor → {confirmed,rejected,failed,dropped} map.
    def _by_sensor(rows):
        out = {}
        for row in rows:
            sid = row.get("metric", {}).get("sensorId", "unknown")
            try:
                out[sid] = int(round(float(row["value"][1])))
            except (KeyError, IndexError, ValueError):
                pass
        return out

    conf = _by_sensor(confirmed_rows)
    rej = _by_sensor(rejected_rows)
    fail = _by_sensor(failed_rows)
    drop = _by_sensor(dropped_rows)

    sensors = sorted(set(conf) | set(rej) | set(fail) | set(drop))
    if not sensors:
        print()
        print(f"  Per-sensor breakdown (last {window}): no data")
        print("    Enable alert_agent.metrics.per_sensor_labels=true on the alert-agent")
        print("    to populate the ``*_by_sensor`` counters (C21 opt-in).")
        return

    print()
    print(f"  Per-sensor breakdown (last {window})")
    header = f"    {'sensorId':<28} {'confirmed':>9} {'rejected':>9} {'failed':>8} {'dropped':>9}"
    print(header)
    print("    " + "-" * (len(header) - 4))
    for sid in sensors:
        # Truncate long sensor IDs so the column layout stays stable.
        display = sid if len(sid) <= 28 else sid[:25] + "..."
        print(
            f"    {display:<28} "
            f"{conf.get(sid, 0):>9} {rej.get(sid, 0):>9} "
            f"{fail.get(sid, 0):>8} {drop.get(sid, 0):>9}"
        )


def run_single_sensor_breakdown(base_url: str, window: str, sensor_id: str) -> None:
    """Print a detailed counter breakdown for exactly one sensor.

    Uses the ``*_by_sensor`` counter variants filtered with
    ``sensorId="<escaped>"`` so every query returns at most one row.
    Includes the full VST/VLM failure-reason breakdown and the
    per-reason drop breakdown so a single-camera triage has all the
    signal the all-sensors view aggregates away.

    Requires ``alert_agent.metrics.per_sensor_labels=true`` on the
    alert-agent side. When the opt-in is off, every query returns
    empty — we print a one-line hint instead of failing the report,
    matching the behavior of :func:`run_per_sensor_breakdown`. The main
    ``--sensor-id`` CLI path now uses :func:`run_prometheus` so the latency
    table and counter sections are filtered together.
    """
    sid_quoted = f'"{_promql_escape(sensor_id)}"'
    # Common label filter prefix, reused in every query.
    sensor_filter = f'sensorId={sid_quoted}'

    def _scalar(promql: str) -> int:
        """Run ``promql`` and return the (single) scalar result as int.

        The per-sensor counters are already pre-filtered to one row, so
        any response with more than one result means the PromQL was
        under-specified. We still tolerate multi-row responses by
        summing — better to over-report than to silently drop signal.
        """
        rows = _prom_range_query(base_url, promql)
        total = 0.0
        for row in rows:
            try:
                total += float(row["value"][1])
            except (KeyError, IndexError, ValueError):
                pass
        return int(round(total))

    try:
        confirmed = _scalar(
            f'increase(alert_bridge_events_by_sensor_total'
            f'{{verdict="confirmed",{sensor_filter}}}[{window}])'
        )
        rejected = _scalar(
            f'increase(alert_bridge_events_by_sensor_total'
            f'{{verdict="rejected",{sensor_filter}}}[{window}])'
        )
        failed = _scalar(
            f'increase(alert_bridge_events_by_sensor_total'
            f'{{verdict="verification-failed",{sensor_filter}}}[{window}])'
        )
        unknown_ev = _scalar(
            f'increase(alert_bridge_events_by_sensor_total'
            f'{{verdict="unknown",{sensor_filter}}}[{window}])'
        )
        after_dedup = _scalar(
            f'increase(alert_bridge_events_after_dedup_by_sensor_total'
            f'{{{sensor_filter}}}[{window}])'
        )
        skipped = _scalar(
            f'increase(alert_bridge_events_skipped_confirmed_by_sensor_total'
            f'{{{sensor_filter}}}[{window}])'
        )

        drop_by_reason = {}
        for reason in ("end_time_delta", "dedup", "rate_limit"):
            drop_by_reason[reason] = _scalar(
                f'increase(alert_bridge_events_dropped_by_sensor_total'
                f'{{reason="{reason}",{sensor_filter}}}[{window}])'
            )

        # Every VERIFICATION_FAILURES reason (C14 + C10 + C25 taxonomy).
        fail_reasons = (
            "vst_timeout", "vst_overloaded", "vst_not_found",
            "vst_unavailable", "vst_client_error", "vst_server_error",
            "vst_unknown",
            "url_validation",
            "vlm_parse_failure", "vlm_timeout", "vlm_connection_error",
            "vlm_server_error", "vlm_invalid_payload",
            "no_prompt", "redis_unavailable", "unknown",
        )
        fail_by_reason = {}
        for reason in fail_reasons:
            fail_by_reason[reason] = _scalar(
                f'increase(alert_bridge_verification_failures_by_sensor_total'
                f'{{reason="{reason}",{sensor_filter}}}[{window}])'
            )
    except PromQueryError as exc:
        print(f"\nERROR: per-sensor query failed: {exc}", file=sys.stderr)
        return

    total_events = confirmed + rejected + failed + unknown_ev
    total_dropped = sum(drop_by_reason.values())
    total_failures = sum(fail_by_reason.values())
    raw_kafka = after_dedup + total_dropped

    if raw_kafka == 0 and total_events == 0 and skipped == 0 and total_failures == 0:
        print()
        print(f"  Per-sensor breakdown for sensorId={sensor_id!r} (last {window}): no data")
        print("    Ensure alert_agent.metrics.per_sensor_labels=true is set on the")
        print("    alert-agent and that this sensor has seen traffic in the window.")
        return

    print()
    print("=" * W)
    print(f"  PER-SENSOR BREAKDOWN — sensorId={sensor_id!r}  |  Last {window}")
    print("=" * W)

    def _pct(n, denom):
        return f"{100 * n / denom:.1f}%" if denom else "  N/A"

    # ── Event lifecycle ──
    print(f"  Events (last {window})")
    print(f"    Raw Kafka events : {raw_kafka}")
    print(f"    Dropped (pre)    : {total_dropped}")
    print(f"    After dedup      : {after_dedup}")
    print(f"    Confirmed        : {confirmed:<6}  ({_pct(confirmed, total_events)})")
    print(f"    Rejected         : {rejected:<6}  ({_pct(rejected, total_events)})")
    print(f"    Verification fail: {failed:<6}  ({_pct(failed, total_events)})")
    if unknown_ev:
        print(f"    Unknown verdict  : {unknown_ev}  (drift — check upstream producers)")
    print(f"    Skipped (conf.)  : {skipped}  (dedup of already-confirmed)")
    print(f"    Total published  : {total_events}")

    # ── Drop breakdown ──
    print()
    print(f"  Events dropped (last {window})  [total: {total_dropped}]")
    drop_descs = {
        "end_time_delta": "End timestamp outside configured window",
        "dedup":          "Duplicate of a recently-processed event",
        "rate_limit":     "Per-sensor VLM rate limiter",
    }
    for reason, count in drop_by_reason.items():
        pct = _pct(count, raw_kafka)
        print(f"    {reason:<16} {count:<6} ({pct:>6})  {drop_descs[reason]}")

    # ── Failure-reason breakdown ──
    if total_failures > 0:
        print()
        print(f"  Verification failures (last {window})  [total: {total_failures}]")
        for reason in fail_reasons:
            count = fail_by_reason[reason]
            if count > 0:
                pct = f"{100 * count / total_failures:.1f}%"
                print(f"    {reason:<24} {count:<6} ({pct:>6})")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Alert Agent latency report — queries Prometheus and optionally Elasticsearch",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 prometheus_latency.py 30m localhost\n"
            "  python3 prometheus_latency.py 1h localhost --es-host localhost\n"
            "  python3 prometheus_latency.py 2h localhost --prom-port 9090 "
            "--es-host localhost --es-port 9200"
        ),
    )
    parser.add_argument("duration",
                        help="Look-back window, e.g. 30m, 1h, 2d")
    parser.add_argument("prometheus_host",
                        help="Prometheus host (IP or hostname)")
    parser.add_argument("--prom-port", type=int, default=9090,
                        help="Prometheus port (default: 9090)")
    parser.add_argument("--es-host", default=None,
                        help="Elasticsearch host — if set, also prints the ES-based table")
    parser.add_argument("--es-port", type=int, default=9200,
                        help="Elasticsearch port (default: 9200)")
    parser.add_argument("--per-sensor", action="store_true",
                        help=("Print an all-sensors breakdown (one row per sensorId "
                              "with confirmed / rejected / failed / dropped counts). "
                              "Requires alert_agent.metrics.per_sensor_labels=true "
                              "on the alert-agent (C21 opt-in)."))
    parser.add_argument("--sensor-id", default=None,
                        help=("Restrict the Prometheus latency and counter report to "
                              "a single sensor. Requires "
                              "alert_agent.metrics.per_sensor_labels=true on the "
                              "alert-agent. Examples:\n"
                              "  --sensor-id cam-42\n"
                              "  --sensor-id 'camera/hall-west:stream0'"))
    args = parser.parse_args()

    try:
        parse_duration_to_seconds(args.duration)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)

    now_str  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    prom_url = f"http://{args.prometheus_host}:{args.prom_port}"

    # Run every requested section to completion before deciding an exit
    # code, so a failing Prometheus query does not suppress the ES report
    # (and vice versa). Each helper prints its own status / errors; we
    # only aggregate their success flags here.
    prom_ok = run_prometheus(prom_url, args.duration, now_str, sensor_id=args.sensor_id)
    if prom_ok:
        # ``--sensor-id`` makes the main Prometheus report sensor-specific,
        # so do not also print the all-sensors table.
        if args.per_sensor and not args.sensor_id:
            run_per_sensor_breakdown(prom_url, args.duration)

    es_ok = True
    if args.es_host and args.sensor_id:
        print()
        print("  [ES] Skipped because --sensor-id uses Prometheus per-sensor metrics.")
        print("       Elasticsearch may not contain every processed event.")
    elif args.es_host:
        es_ok = run_elasticsearch(args.es_host, args.es_port, args.duration, now_str)

    print()

    if not (prom_ok and es_ok):
        # CI / operator tooling needs a visible signal when any section
        # could not complete. Exit code 3 = "report may be incomplete".
        sys.exit(3)


if __name__ == "__main__":
    main()
