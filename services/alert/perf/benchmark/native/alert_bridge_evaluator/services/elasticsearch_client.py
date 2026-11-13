"""
Elasticsearch Client

Handles querying VLM verification results from Elasticsearch.
"""

import requests
import logging
import time
from typing import Dict, Any, Optional, List, Tuple
from ..config import Settings

logger = logging.getLogger(__name__)


class ElasticsearchClient:
    """
    Client for querying Elasticsearch for VLM verification results.

    Provides methods to:
    - Query results by sensorId
    - Query results by incident ID
    - Poll for results with retry logic
    """

    def __init__(self, settings: Settings):
        self._settings = settings
        self._base_url = settings.elasticsearch_url.rstrip('/')
        self._index_incidents = settings.elasticsearch_index_incidents
        self._index_alerts = settings.elasticsearch_index_alerts
        self._timeout = settings.elasticsearch_timeout
        self._poll_interval = settings.result_poll_interval
        self._poll_timeout = settings.result_poll_timeout  # Total timeout in seconds
        self._poll_max_attempts = settings.result_poll_max_attempts  # Max attempts (0 = use timeout)
        self._session = requests.Session()

    def health_check(self) -> Dict[str, Any]:
        """
        Check Elasticsearch cluster health.

        Returns:
            Health status response
        """
        try:
            response = self._session.get(
                f"{self._base_url}/_cluster/health",
                timeout=10
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"Elasticsearch health check failed: {e}")
            return {"status": "unhealthy", "error": str(e)}

    def query_by_sensor_id(
        self,
        sensor_id: str,
        timestamp: Optional[str] = None,
        end_timestamp: Optional[str] = None,
        size: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Query results by sensorId and optional time range.

        Args:
            sensor_id: Sensor identifier
            timestamp: Optional start timestamp filter
            end_timestamp: Optional end timestamp filter
            size: Max number of results

        Returns:
            List of matching documents
        """
        query = {
            "query": {
                "bool": {
                    "must": [
                        {"term": {"sensorId.keyword": sensor_id}}
                    ]
                }
            },
            "sort": [{"timestamp": {"order": "desc"}}],
            "size": size
        }

        # Add time range filter if provided
        if timestamp or end_timestamp:
            range_filter = {"range": {"timestamp": {}}}
            if timestamp:
                range_filter["range"]["timestamp"]["gte"] = timestamp
            if end_timestamp:
                range_filter["range"]["timestamp"]["lte"] = end_timestamp
            query["query"]["bool"]["must"].append(range_filter)

        return self._search(query)

    def query_incidents_by_sensor(
        self,
        sensor_id: str,
        size: int = 500
    ) -> List[Dict[str, Any]]:
        """
        Query by sensorId only: check mdx-vlm-alerts* first; if no docs, query mdx-vlm-incidents*.
        Returns all matching documents for the sensor, sorted by timestamp ascending.
        """
        query = {
            "query": {
                "term": {"sensorId.keyword": sensor_id}
            },
            "sort": [{"timestamp": {"order": "asc"}}],
            "size": size
        }
        docs = self._search(query, index=self._index_alerts)
        if docs:
            return docs
        return self._search(query, index=self._index_incidents)

    def query_alerts_then_incidents_by_time_range(
        self,
        sensor_id: str,
        start_timestamp: str,
        end_timestamp: str,
        size: int = 500
    ) -> List[Dict[str, Any]]:
        """
        Query by sensorId and time range [start_timestamp, end_timestamp]: check mdx-vlm-alerts* first;
        if no docs, query mdx-vlm-incidents*. Matches query_incidents_by_sensor behavior but with time filter.
        """
        docs = self._query_by_sensor_and_timestamp(
            self._index_alerts, sensor_id, start_timestamp, end_timestamp, size
        )
        if docs:
            return docs
        return self._query_by_sensor_and_timestamp(
            self._index_incidents, sensor_id, start_timestamp, end_timestamp, size
        )

    def query_alert_incident_window(
        self,
        start_timestamp: str,
        end_timestamp: str,
        *,
        sensor_ids: Optional[List[str]] = None,
        size: int = 10000,
    ) -> List[Dict[str, Any]]:
        """
        Query real alert/incident documents inside a measurement window.

        This is used for report metadata such as the observed incident window.
        It intentionally reads event documents from ES rather than deriving the
        window from eval input rows.
        """
        filters: List[Dict[str, Any]] = []
        if sensor_ids:
            filters.append({"terms": {"sensorId.keyword": sorted(set(sensor_ids))}})

        query = {
            "query": {
                "bool": {
                    "filter": filters,
                    "should": [
                        {"range": {"timestamp": {"gte": start_timestamp, "lte": end_timestamp}}},
                        {"range": {"end": {"gte": start_timestamp, "lte": end_timestamp}}},
                    ],
                    "minimum_should_match": 1,
                }
            },
            "sort": [{"timestamp": {"order": "asc"}}],
            "_source": [
                "id",
                "incidentId",
                "eventId",
                "sensorId",
                "timestamp",
                "start_timestamp",
                "start",
                "end_timestamp",
                "end",
                "info.verdict",
                "info.alert_type",
                "category",
            ],
            "size": size,
        }

        docs = self._search(query, index=self._index_alerts)
        docs.extend(self._search(query, index=self._index_incidents))
        return self._dedupe_documents(docs)

    def query_by_incident_id(self, incident_id: str) -> Optional[Dict[str, Any]]:
        """
        Query result by incident ID.

        Args:
            incident_id: Unique incident identifier

        Returns:
            Matching document or None
        """
        query = {
            "query": {
                "term": {"id.keyword": incident_id}
            },
            "size": 1
        }

        results = self._search(query)
        return results[0] if results else None

    def query_latest_by_sensor(
        self,
        sensor_id: str,
        timestamp: str
    ) -> Optional[Dict[str, Any]]:
        """
        Query the latest result for a sensor after a specific timestamp.

        Args:
            sensor_id: Sensor identifier
            timestamp: Minimum timestamp

        Returns:
            Latest matching document or None
        """
        query = {
            "query": {
                "bool": {
                    "must": [
                        {"term": {"sensorId.keyword": sensor_id}},
                        {"range": {"timestamp": {"gte": timestamp}}}
                    ]
                }
            },
            "sort": [{"timestamp": {"order": "desc"}}],
            "size": 1
        }

        results = self._search(query)
        return results[0] if results else None

    def poll_for_result(
        self,
        sensor_id: str,
        timestamp: str,
        timeout_seconds: Optional[int] = None,
        max_attempts: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Poll Elasticsearch for VLM verification result.

        Waits for the result to appear in Elasticsearch after submitting
        an incident to Alert Bridge.

        Args:
            sensor_id: Sensor identifier
            timestamp: Incident timestamp to match
            timeout_seconds: Optional custom timeout (default: RESULT_POLL_TIMEOUT)
            max_attempts: Optional max attempts (default: RESULT_POLL_MAX_ATTEMPTS, 0 = use timeout)

        Returns:
            Verification result or None if timeout/max attempts reached
        """
        timeout = timeout_seconds if timeout_seconds else self._poll_timeout
        attempts_limit = max_attempts if max_attempts is not None else self._poll_max_attempts
        start_time = time.time()
        attempt = 0

        # Log polling config
        if attempts_limit > 0:
            logger.info(f"Polling for result: sensorId={sensor_id}, max_attempts={attempts_limit}")
        else:
            logger.info(f"Polling for result: sensorId={sensor_id}, timeout={timeout}s")

        while True:
            attempt += 1
            result = self.query_latest_by_sensor(sensor_id, timestamp)

            if result and result.get("info", {}).get("verdict"):
                elapsed = time.time() - start_time
                logger.info(f"✓ Found result after {attempt} attempts ({elapsed:.1f}s)")
                return result

            # Check max attempts limit (if set)
            if attempts_limit > 0 and attempt >= attempts_limit:
                elapsed = time.time() - start_time
                logger.warning(f"Max attempts ({attempts_limit}) reached after {elapsed:.1f}s: sensorId={sensor_id}")
                return None

            # Check timeout (if no max_attempts or as fallback)
            if attempts_limit <= 0 and (time.time() - start_time) >= timeout:
                elapsed = time.time() - start_time
                logger.warning(f"Timeout after {elapsed:.1f}s waiting for result: sensorId={sensor_id}")
                return None

            time.sleep(self._poll_interval)

    def _search(self, query: Dict[str, Any], index: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Execute search query.

        Args:
            query: Elasticsearch query DSL
            index: Optional index name (default: self._index_incidents)

        Returns:
            List of document sources
        """
        idx = index or self._index_incidents
        try:
            response = self._session.post(
                f"{self._base_url}/{idx}/_search",
                json=query,
                headers={"Content-Type": "application/json"},
                timeout=self._timeout
            )
            response.raise_for_status()
            data = response.json()

            hits = data.get("hits", {}).get("hits", [])
            return [hit["_source"] for hit in hits]

        except requests.RequestException as e:
            logger.error(f"Elasticsearch query failed: {e}")
            raise ElasticsearchError(f"Query failed: {e}")

    def _query_by_sensor_and_timestamp(
        self,
        index: str,
        sensor_id: str,
        timestamp: str,
        end_timestamp: Optional[str] = None,
        size: int = 10
    ) -> List[Dict[str, Any]]:
        """Run sensor+timestamp query against a given index."""
        query = {
            "query": {
                "bool": {
                    "must": [
                        {"term": {"sensorId.keyword": sensor_id}},
                        {"range": {"timestamp": {"gte": timestamp}}}
                    ]
                }
            },
            "sort": [{"timestamp": {"order": "asc"}}],
            "size": size
        }
        if end_timestamp:
            query["query"]["bool"]["must"].append(
                {"range": {"timestamp": {"lte": end_timestamp}}}
            )
        return self._search(query, index=index)

    @staticmethod
    def _dedupe_documents(docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        deduped: List[Dict[str, Any]] = []
        seen = set()
        for doc in docs:
            key = (
                doc.get("id")
                or doc.get("incidentId")
                or doc.get("eventId")
                or "|".join(
                    str(doc.get(field) or "")
                    for field in ("sensorId", "timestamp", "end", "category")
                )
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(doc)
        return deduped

    def query_v2_by_sensor_and_timestamp(
        self,
        sensor_id: str,
        timestamp: str,
        end_timestamp: Optional[str] = None,
        size: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Query mdx-vlm-incident index by sensorId and optional time range.
        Returns documents that include start (timestamp) and end time for eval v2.
        (Legacy: single index. Prefer query_v2_alerts_then_incidents for alerts-first flow.)
        """
        return self._query_by_sensor_and_timestamp(
            self._index_incidents, sensor_id, timestamp, end_timestamp, size
        )

    def query_v2_alerts_then_incidents(
        self,
        sensor_id: str,
        timestamp: str,
        end_timestamp: Optional[str] = None,
        size: int = 10
    ) -> Tuple[List[Dict[str, Any]], str]:
        """
        Eval v2: check mdx-vlm-alerts* first; if no docs, query mdx-vlm-incidents*.

        Returns:
            (list of documents, source) where source is 'alerts' or 'incidents'
        """
        docs = self._query_by_sensor_and_timestamp(
            self._index_alerts, sensor_id, timestamp, end_timestamp, size
        )
        if docs:
            return docs, "alerts"
        docs = self._query_by_sensor_and_timestamp(
            self._index_incidents, sensor_id, timestamp, end_timestamp, size
        )
        return docs, "incidents"

    def get_all_results(
        self,
        from_timestamp: Optional[str] = None,
        size: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get all verification results, optionally filtered by timestamp.

        Args:
            from_timestamp: Optional minimum timestamp
            size: Max number of results

        Returns:
            List of documents
        """
        query: Dict[str, Any] = {
            "query": {"match_all": {}},
            "sort": [{"timestamp": {"order": "desc"}}],
            "size": size
        }

        if from_timestamp:
            query["query"] = {
                "range": {"timestamp": {"gte": from_timestamp}}
            }

        return self._search(query)

    def query_latency_docs(
        self,
        duration: str = "1h",
        sensor_id: Optional[str] = None,
        start_ts: Optional[str] = None,
        end_ts: Optional[str] = None,
        size: int = 1000
    ) -> List[Dict[str, Any]]:
        """
        Query ES for documents with info.latency.* fields (ported from latency.sh).

        Args:
            duration: ES time notation e.g. '30m', '1h', '2d' -- used when start_ts/end_ts not given
            sensor_id: Optional sensor filter
            start_ts: Optional absolute start (ISO)
            end_ts: Optional absolute end (ISO)
            size: Max docs to return

        Returns:
            List of _source dicts containing latency data
        """
        # info.latency may be stored as a JSON string (protobuf map<string,string>)
        # rather than a nested object, so we filter on the top-level field only.
        filters: List[Dict[str, Any]] = [
            {"exists": {"field": "info.latency"}},
        ]

        if start_ts and end_ts:
            filters.append({"range": {"end": {"gte": start_ts, "lte": end_ts}}})
        else:
            filters.append({"range": {"end": {"gte": f"now-{duration}", "lte": "now"}}})

        if sensor_id:
            filters.append({"term": {"sensorId.keyword": sensor_id}})

        query = {
            "query": {"bool": {"filter": filters}},
            "sort": [{"end": "asc"}],
            "_source": [
                "indexed_at",
                "end",
                "sensorId",
                "info.latency",
            ],
            "size": size,
        }
        return self._search(query, index=self._index_incidents)

    def setup_indexed_at_pipeline(self) -> Dict[str, Any]:
        """
        Create the 'add-indexed-at' ingest pipeline and apply it to
        mdx-vlm-incidents-* indices (ported from enable_indexed_at.sh).

        Returns:
            dict with step results and overall success flag
        """
        result: Dict[str, Any] = {"steps": [], "success": True}

        try:
            resp = self._session.put(
                f"{self._base_url}/_ingest/pipeline/add-indexed-at",
                json={"processors": [{"set": {"field": "indexed_at", "value": "{{_ingest.timestamp}}"}}]},
                headers={"Content-Type": "application/json"},
                timeout=self._timeout,
            )
            step = {"step": "create_pipeline", "status": resp.status_code, "body": resp.json()}
            result["steps"].append(step)
            if resp.status_code >= 400:
                result["success"] = False
                return result
        except requests.RequestException as e:
            result["steps"].append({"step": "create_pipeline", "error": str(e)})
            result["success"] = False
            return result

        try:
            base_settings = {}
            tmpl_resp = self._session.get(
                f"{self._base_url}/_index_template/metropolis_template",
                timeout=self._timeout,
            )
            if tmpl_resp.status_code == 200:
                templates = tmpl_resp.json().get("index_templates", [])
                if templates:
                    base_settings = (
                        templates[0]
                        .get("index_template", {})
                        .get("template", {})
                        .get("settings", {})
                    )

            merged_settings = {**base_settings, "index.default_pipeline": "add-indexed-at"}
            resp = self._session.put(
                f"{self._base_url}/_index_template/mdx-vlm-incidents-indexed-at",
                json={
                    "index_patterns": ["mdx-vlm-incidents-*"],
                    "template": {
                        "settings": merged_settings,
                        "mappings": {"properties": {"indexed_at": {"type": "date"}}},
                    },
                    "priority": 600,
                },
                headers={"Content-Type": "application/json"},
                timeout=self._timeout,
            )
            step = {"step": "create_template", "status": resp.status_code, "body": resp.json()}
            result["steps"].append(step)
            if resp.status_code >= 400:
                result["success"] = False
                return result
        except requests.RequestException as e:
            result["steps"].append({"step": "create_template", "error": str(e)})
            result["success"] = False
            return result

        try:
            resp = self._session.put(
                f"{self._base_url}/mdx-vlm-incidents-*/_settings",
                json={"index.default_pipeline": "add-indexed-at"},
                headers={"Content-Type": "application/json"},
                timeout=self._timeout,
            )
            result["steps"].append({"step": "apply_to_existing", "status": resp.status_code, "body": resp.json()})
        except requests.RequestException as e:
            result["steps"].append({"step": "apply_to_existing", "error": str(e)})

        return result

    def close(self) -> None:
        """Close the session."""
        self._session.close()


class ElasticsearchError(Exception):
    """Exception for Elasticsearch errors."""
    pass
