# Alerts Developer Profile

## Capabilities and routing cues

- `2d_cv`: RT-CV detections, behavior analytics, VLM verification, incidents,
  and alert APIs.
- `2d_vlm`: continuous RT-VLM inspection and real-time alert APIs.
- Choose for alert verification, incident reporting, or live VLM alerts.

## Profile Service Set

Authoritative source:
`deploy/docker/developer-profiles/dev-profile-alerts/overrides.env`.

`2d_cv`:

```text
vss-behavior-analytics-alerts,nvstreamer-alerts,perception-alerts,kibana-init-container-alerts,vss-video-analytics-api,vss-va-mcp,vss-agent,alert-bridge,phoenix,elasticsearch,elasticsearch-init-container,kafka,kafka-topic-init-container,redis,kibana,logstash,broker-health-check,vss-haproxy-ingress,init-dirs,render-config,wdm-env-from-config,wait-for-redis,wait-for-docker-workloads,sdr-controller,rtvi-vlm,vss-ui,centralizedb,vst-ingress,sensor-ms,streamprocessing-ms,llm_${LLM_MODE}_${LLM_NAME_SLUG}
