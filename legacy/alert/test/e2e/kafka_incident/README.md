# Kafka Incident Pytest Flow

Pytest-driven end-to-end verification for `enhance_alert_with_vlm` handling of Kafka incidents. The suite can run entirely against local simulators or point to real infrastructure by toggling a flag.

## Quick Start (Simulators)

```
python3 -m pytest test/e2e/kafka_incident -k incident_flow
```

The fixtures will:
- start VST, VSS, NIM, and Elastic simulators;
- ensure Kafka topics exist (skips if `confluent_kafka` is missing);
- launch the service with the existing `config.yaml`;
- publish the bundled sample incident and wait for an indexed response.

Failing assertions print simulator/service logs to aid debugging.

## Switching to Real Endpoints

Run the same test with `--use-real-endpoints` or set `ALERT_AGENT_USE_REAL_ENDPOINTS=1`. Provide overrides through environment variables:

- `KAFKA_BOOTSTRAP`
- `KAFKA_INCIDENT_TOPIC`
- `KAFKA_ENHANCED_TOPIC`
- `KAFKA_INCIDENTS_TOPIC`
- `ELASTIC_URL`

When this flag is active, fixtures skip starting simulators and only validate connectivity.

## Notes

- The test depends on `test/protobuf/sample_incident.json` and the production `config.yaml`—adjust as needed for custom payloads/configs.
- Elastic verification uses the simulator’s `_all` endpoint; real clusters should expose compatible APIs or provide an adapter.

