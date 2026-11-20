"""
Latency analysis service (ported from latency.sh and redis_latency.py).

Extracts info.latency.* fields from Elasticsearch documents and computes
per-incident and aggregate latency statistics for the Alert Bridge pipeline.

Metrics:
- Upstream (CV + Analytics):  end -> kafka_published_at
- AB Consumer Lag:            kafka_published_at -> kafka_consumed_at
- VST Fetch:                  get_video_stream_url_with_overlay.duration
- VLM Inference:              vlm_request.duration
- Idx-End (E2E):              end -> indexed_at  (requires ES ingest pipeline)
"""

import json
import math
import re
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional

from .elasticsearch_client import ElasticsearchClient

logger = logging.getLogger(__name__)

_ISO_RE = re.compile(
    r"^(?P<prefix>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})"
    r"(?P<frac>\.\d+)?"
    r"(?P<tz>Z|[+-]\d{2}:\d{2})?$"
)


def _parse_ts(s: Optional[str]) -> Optional[datetime]:
    """Parse ES ISO-8601 timestamps; truncates nanoseconds to microseconds."""
    if not s:
        return None
    m = _ISO_RE.match(s)
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


def _get_nested(d: dict, path: list):
    cur = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def _get_first_nested(d: dict, paths: List[List[str]]):
    for path in paths:
        value = _get_nested(d, path)
        if value is not None:
            return value
    return None


def _percentile(sorted_x: list, p: float) -> float:
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


def _compute_metric_stats(arr: list) -> Dict[str, Any]:
    """Return avg / p50 / p90 / p99 / max for a list of float values."""
    if not arr:
        return {"avg": None, "p50": None, "p90": None, "p99": None, "max": None, "n": 0}
    s = sorted(arr)
    return {
        "avg": round(sum(s) / len(s), 4),
        "p50": round(_percentile(s, 0.50), 4),
        "p90": round(_percentile(s, 0.90), 4),
        "p99": round(_percentile(s, 0.99), 4),
        "max": round(max(s), 4),
        "n": len(s),
    }


def _fmt_duration(v: Optional[float]) -> str:
    """Format duration: ms if <1s, else seconds."""
    if v is None:
        return "N/A"
    if v < 1.0:
        return f"{int(v * 1000)}ms"
    return f"{v:.2f}s"


class LatencyService:
    """
    Extracts latency data from ES docs and computes pipeline latency
    metrics.  Designed to be called from EvaluationService when
    eval_mode == 'latency'.
    """

    def __init__(self, es_client: ElasticsearchClient, default_duration: str = "1h"):
        self._es = es_client
        self._default_duration = default_duration

    def extract_latency_from_docs(self, docs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Parse a list of ES _source docs and return per-row latency data
        plus aggregate statistics.

        Returns:
            {
                "rows": [ { "#": 1, "upstream": ..., ... }, ... ],
                "stats": { "upstream": {avg,p50,...}, ... },
                "total": <total hits>,
                "usable": <rows with valid latency data>,
                "skipped": <rows skipped>
            }
        """
        rows: List[Dict[str, Any]] = []
        upstreams: List[float] = []
        kafka_lags: List[float] = []
        vsts: List[float] = []
        vlms: List[float] = []
        idx_ends: List[float] = []
        skipped = 0

        for src in docs:
            try:
                latency_obj = _get_nested(src, ["info", "latency"])
                if isinstance(latency_obj, str):
                    try:
                        latency_obj = json.loads(latency_obj)
                    except (json.JSONDecodeError, TypeError):
                        skipped += 1
                        continue
                if not isinstance(latency_obj, dict):
                    skipped += 1
                    continue

                ts = latency_obj.get("timestamps") or {}

                indexed_dt = _parse_ts(src.get("indexed_at"))
                kafka_pub_dt = _parse_ts(_get_first_nested(ts, [
                    ["kafka_published_at"],
                    ["kafkaPublishedAt"],
                ]))
                kafka_con_dt = _parse_ts(_get_first_nested(ts, [
                    ["kafka_consumed_at"],
                    ["kafkaConsumedAt"],
                ]))
                end_dt = _parse_ts(src.get("end"))

                vlm_raw = _get_first_nested(latency_obj, [
                    ["vlm_request", "duration"],
                    ["vlmRequest", "duration"],
                ])
                vst_raw = _get_first_nested(latency_obj, [
                    ["get_video_stream_url_with_overlay", "duration"],
                    ["getVideoStreamUrlWithOverlay", "duration"],
                ])

                if not all([kafka_pub_dt, kafka_con_dt, vlm_raw]):
                    skipped += 1
                    continue

                vlm = float(vlm_raw)
                vst = float(vst_raw) if vst_raw else 0.0

                kafka_lag: Optional[float] = (kafka_con_dt - kafka_pub_dt).total_seconds()
                upstream: Optional[float] = (
                    (kafka_pub_dt - end_dt).total_seconds() if (kafka_pub_dt and end_dt) else None
                )
                idx_minus_end: Optional[float] = (
                    (indexed_dt - end_dt).total_seconds() if (indexed_dt and end_dt) else None
                )

                if kafka_lag is not None and kafka_lag < 0:
                    kafka_lag = None
                if upstream is not None and upstream < 0:
                    upstream = None
                if idx_minus_end is not None and idx_minus_end < 0:
                    idx_minus_end = None

                row_num = len(rows) + 1
                row = {
                    "#": row_num,
                    "sensor_id": src.get("sensorId", ""),
                    "upstream": upstream,
                    "upstream_fmt": _fmt_duration(upstream),
                    "kafka_lag": kafka_lag,
                    "kafka_lag_fmt": _fmt_duration(kafka_lag),
                    "vst": vst,
                    "vst_fmt": _fmt_duration(vst),
                    "vlm": vlm,
                    "vlm_fmt": _fmt_duration(vlm),
                    "e2e": idx_minus_end,
                    "e2e_fmt": _fmt_duration(idx_minus_end),
                }
                rows.append(row)

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
                logger.warning(f"Row skipped: {type(exc).__name__}: {exc}")

        stats = {
            "upstream": _compute_metric_stats(upstreams),
            "kafka_lag": _compute_metric_stats(kafka_lags),
            "vst": _compute_metric_stats(vsts),
            "vlm": _compute_metric_stats(vlms),
            "e2e": _compute_metric_stats(idx_ends),
        }

        return {
            "rows": rows,
            "stats": stats,
            "total": len(docs),
            "usable": len(rows),
            "skipped": skipped,
        }

    def evaluate_latency_for_sensor(
        self,
        sensor_id: str,
        start_ts: Optional[str] = None,
        end_ts: Optional[str] = None,
        duration: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Query ES for latency docs (optionally filtered by sensor / time range)
        and return structured latency result.

        Designed to be a drop-in replacement for evaluate_incident_v2 when
        eval_mode == 'latency'.
        """
        docs = self._es.query_latency_docs(
            duration=duration or self._default_duration,
            sensor_id=sensor_id,
            start_ts=start_ts,
            end_ts=end_ts,
        )
        latency_data = self.extract_latency_from_docs(docs)

        return {
            "video": sensor_id,
            "sensor_id": sensor_id,
            "start_ts": start_ts or "",
            "end_ts": end_ts or "",
            "timestamp": end_ts or start_ts or "",
            "eval_version": "v2",
            "eval_mode": "latency",
            "status": "success" if latency_data["usable"] > 0 else "no_data",
            "total_hits": latency_data["total"],
            "usable_hits": latency_data["usable"],
            "skipped": latency_data["skipped"],
            "stats": latency_data["stats"],
            "rows": latency_data["rows"],
        }

    def evaluate_latency_batch(
        self, incidents: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Evaluate latency for a batch of incidents.

        Each incident dict should contain 'video' and optionally
        'start_timestamp' / 'end_timestamp'.
        """
        results = []
        for inc in incidents:
            result = self.evaluate_latency_for_sensor(
                sensor_id=inc["video"],
                start_ts=inc.get("start_timestamp"),
                end_ts=inc.get("end_timestamp"),
            )
            results.append(result)
        return results

    def calculate_latency_summary(
        self, results: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Aggregate latency statistics across multiple sensor results.
        """
        all_upstreams: List[float] = []
        all_kafka: List[float] = []
        all_vst: List[float] = []
        all_vlm: List[float] = []
        all_e2e: List[float] = []
        total_hits = 0
        total_usable = 0

        for r in results:
            total_hits += r.get("total_hits", 0)
            total_usable += r.get("usable_hits", 0)
            for row in r.get("rows", []):
                if row.get("upstream") is not None:
                    all_upstreams.append(row["upstream"])
                if row.get("kafka_lag") is not None:
                    all_kafka.append(row["kafka_lag"])
                if row.get("vst") is not None:
                    all_vst.append(row["vst"])
                if row.get("vlm") is not None:
                    all_vlm.append(row["vlm"])
                if row.get("e2e") is not None:
                    all_e2e.append(row["e2e"])

        return {
            "total_sensors": len(results),
            "total_hits": total_hits,
            "total_usable": total_usable,
            "upstream": _compute_metric_stats(all_upstreams),
            "kafka_lag": _compute_metric_stats(all_kafka),
            "vst": _compute_metric_stats(all_vst),
            "vlm": _compute_metric_stats(all_vlm),
            "e2e": _compute_metric_stats(all_e2e),
        }
