# Critic Troubleshooting Feedback Loop

Isolate the problem with the critic agent then iterate to resolve it. Examples of useful flows below.

---

## Symptom: `404 Not Found` on `/api/v1/critic`

The endpoint is not registered in the currently-running profile.

- The dedicated `/api/v1/critic` route is only wired in profiles whose `config.yml` explicitly lists it under `endpoints`. The `dev-profile-search` profile includes it. The base, LVS, and alerts profiles do not.
- Confirm which profile is deployed with the `deploy` skill.
- If the wrong profile is running, ask user if they want to redeploy it as a search profile with the `deploy` skill.

---

## Symptom: All results `unverified` — `criteria_met` is `{}` on every clip

Possible causes:

1. The VLM is unreachable or erroring silently. The critic falls back to `unverified` rather than crashing.

Check if the VLM is up:

```bash
# VLM typically runs on port 30082
curl -s http://${HOST_IP}:30082/v1/models | jq .

# Quick sanity-call
curl -s -X POST http://${HOST_IP}:30082/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "<VLM_MODEL_NAME>",
    "max_tokens": 64,
    "messages": [{"role": "user", "content": "Hello!"}]
  }' | jq .
```

If the VLM does not respond, identify the VLM service with the `deploy` skill. Check its logs to find root cause. If needed, restart it ONLY after asking user permission.
Then re-run the critic call.

2. The VLM is not able to pull the video URLs or video frames to analyze them.

This often happens, if the VLM runs on a different machine than the one running VST which stores the videos. 
Understand what video URLs are submitted to the VLM in the VSS agent logs (identify the container with `deploy` skill), and cross-check available videos in VST with `vios` skill. Ensure those URLs are reachable from the VLM machine.

If the VLM runs as a cloud service, it may not be able to retrieve the videos on a private server with the video URLs. Investigate this with the user, the VSS agent may need to use base64 or video frames to pass data to the VLM. 

---

## Symptom: Timestamps rejected or clips return no VLM output

The critic requires **ISO 8601 UTC** timestamps (e.g. `"2025-08-25T03:05:55Z"`). Other formats (epoch seconds, local time without timezone) are not supported and may produce silent failures.

- Verify that the timestamps passed match this format exactly.
- If the timestamps came from a search result, copy them as-is — search results already use this format.
- If converting from a different format, use: `date -u -d "@<epoch_seconds>" +"%Y-%m-%dT%H:%M:%SZ"` (Linux) or Python `datetime.utcfromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%SZ")`.

---

## Symptom: `sensor_id` not found / empty results from VLM

The `sensor_id` must be the VST UUID, not a friendly camera name. Use the `vios` skill to list sensors and get their UUIDs.
Match the camera name the user mentioned to the correct UUID, then retry the critic call with the correct `sensor_id`.

---

## Symptom: Majority of results `rejected` — criteria seem overly strict

This could be expected behavior when the VLM applies **subject-anchored** evaluation. A criterion is only `true` if the *exact described subject* satisfies it — not any entity in the frame.

- Review `criteria_met` to see which criterion failed.
- If the subject description is ambiguous (e.g. "a person" vs. "the forklift operator"), try refining the `query` to be more specific.
- If the query is correct and results are still largely rejected, the video clips may genuinely not match — that is the critic working as intended.
- Consider lowering `evaluation_count` and inspecting the top-scored clips manually (download `screenshot_url` to `/tmp` and read them) before concluding the search has no matches.
