## Deploy with NIM

From this directory:

```bash
docker compose --profile nim up -d
# or explicitly
docker compose -f compose.yml --profile nim up -d
```

## Config quick guide (configs/config.yml)
- vst_config: Base URLs for VST APIs and storage.
- kafka: Broker settings and topics for input/output events.
- vss_agent: Optional VSS endpoints (disabled by default here).
- vlm: NIM endpoint/model and video processing params (frames/sampling).
- event_bridge: Source/sink via Kafka or Redis (hosts, streams, consumer).
- prompt: Preference for payload-provided prompts.
- alert_agent: Worker count and clip duration bounds.
- websocket: Optional realtime broadcasting (disabled by default).
- elastic: Enable and target index for persisted results.
- logging: Global log level/format.