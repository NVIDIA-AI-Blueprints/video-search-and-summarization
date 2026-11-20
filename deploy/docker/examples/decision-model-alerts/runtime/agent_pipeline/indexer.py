# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Kafka -> Elasticsearch with deterministic IDs and commit-after-write semantics."""
import json
import logging
import os
import time
from datetime import datetime, timezone
from urllib.parse import quote
import httpx
from .bus import DECISIONS, EVENTS, REVIEWS, consumer

LOG = logging.getLogger(__name__)
KINDS = {"d1.decision": "decisions", "d1.event": "events", "d1.review": "reviews"}


def index_document(client, base, event):
    kind = KINDS[event["kind"]]
    date = datetime.fromtimestamp(event["captured_at"], timezone.utc).strftime("%Y.%m.%d")
    document = {**event, "@timestamp": datetime.fromtimestamp(event["captured_at"], timezone.utc).isoformat()}
    result = client.put(f'{base}/d1-{kind}-{date}/_doc/{quote(event["id"], safe="")}', json=document)
    result.raise_for_status()


def main():
    logging.basicConfig(level=logging.INFO)
    base = os.getenv("ELASTICSEARCH_URL", "http://127.0.0.1:9200").rstrip("/")
    key = os.getenv("ELASTICSEARCH_API_KEY")
    headers = {"Authorization": "ApiKey " + key} if key else {}
    kafka = consumer("d1-elasticsearch-v1", [DECISIONS, EVENTS, REVIEWS])
    with httpx.Client(timeout=30, headers=headers) as client:
        template = {"index_patterns": ["d1-*"], "template": {"settings": {"number_of_shards": 1, "number_of_replicas": 0},
            "mappings": {"dynamic": False, "properties": {
                "@timestamp": {"type": "date"}, "id": {"type": "keyword"}, "kind": {"type": "keyword"},
                "stream_id": {"type": "keyword"}, "event_id": {"type": "keyword"}, "decision_ids": {"type": "keyword"},
                "config_revision": {"type": "integer"}, "rule_id": {"type": "keyword"},
                "context": {"type": "text"}, "model": {"type": "keyword"},
                "answer": {"properties": {"score": {"type": "float"}, "noul": {"type": "float"},
                    "confidence": {"type": "float"}, "choice": {"type": "keyword"}, "probabilities": {"type": "flattened"}}},
                "question": {"properties": {"instructions": {"type": "text"}, "type": {"type": "keyword"}}}}}}}
        response = client.put(base + "/_index_template/d1-agent-v1", json=template)
        response.raise_for_status()
        try:
            while True:
                message = kafka.poll(1)
                if message is None:
                    continue
                if message.error():
                    LOG.error("Kafka error: %s", message.error())
                    continue
                while True:
                    try:
                        index_document(client, base, json.loads(message.value()))
                        kafka.commit(message=message, asynchronous=False)
                        break
                    except Exception:
                        LOG.exception("Indexing failed; Kafka offset not committed")
                        time.sleep(5)
        finally:
            kafka.close()


if __name__ == "__main__":
    main()
