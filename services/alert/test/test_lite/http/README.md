# HTTP ingestion — lightweight checks

The service accepts alerts over HTTP at `POST /api/v1/alerts` (see
`src/web/api/alert_routes.py`) in addition to the Kafka source. Nothing here
needs a broker to submit a request; a verdict still requires the VLM and,
for persistence, Elasticsearch.

```bash
curl -X POST http://localhost:9080/api/v1/alerts \
  -H "Content-Type: application/json" \
  -d @payload.json
```

Health is at `GET /health`. The default endpoint is `http://localhost:9080`.

## State of this directory

`validate_manifest.py` checks that an `alert_expectations.yaml` manifest covers
every JSON payload beside it. Neither the manifest nor the payloads are present
in this directory, so the script cannot run as it stands.

The end-to-end scripts this directory used to hold (`test_alert_http.py`,
`auto_validate_http_req_response.py`) were built around Redis Streams and were
removed with the Redis dependency. Send-and-verify tooling now lives in
[`../kafka/`](../kafka/) — `send_payload.py`, `send_direct_media_payload.py` and
`verify_responses.py`, with `create_topics.py` to prepare the topics. For
response schema and the full test layout, see
[`../../TEST_README.md`](../../TEST_README.md).
