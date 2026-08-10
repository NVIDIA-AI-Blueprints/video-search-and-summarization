# Summary persistence contract

Pass exactly one JSON object to `persist_summary.py` through standard input:

```json
{
  "completion_id": "11111111-1111-4111-8111-111111111111",
  "video_id": "22222222-2222-4222-8222-222222222222",
  "created": 1784083200,
  "model": "nim_nvidia_cosmos3-nano-reasoner_bf16-final",
  "media_ref": {
    "source": "vst",
    "stream_id": "camera-warehouse-07",
    "name": "warehouse-shift.mp4"
  },
  "content": {
    "video_summary": "A forklift moved through an aisle.",
    "events": [
      {
        "start_time": 12.0,
        "end_time": 18.5,
        "type": "near miss",
        "description": "A forklift passed close to a pedestrian."
      }
    ]
  }
}
```

All fields are required except `media_ref.stream_id` and `media_ref.name`. Unknown fields are rejected. `completion_id`
and `video_id` must be UUIDs from the VSS/VST workflow. `created` is Unix epoch seconds. `events` may be empty.

The executable assigns the stable summary ID, one-based event ordinals, stable event IDs, derived summary time range,
event count, and deterministic passages for the summary and each event. It retains every complete original description,
embeds each passage separately, and does not create averaged summary or event vectors. Never generate these values in
the agent.

Successful output:

```json
{
  "status": "complete",
  "summary_id": "summary:11111111-1111-4111-8111-111111111111",
  "event_ids": ["event:11111111-1111-4111-8111-111111111111:0001"],
  "attempted_records": 2,
  "successful_records": 2
}
```

Failure output contains `status`, `error_code`, `message`, and `retryable`. Exit codes are `0` complete, `2` invalid
input/configuration, `3` transient embedding or repository failure, `4` degraded/partial write, and `5` unexpected
failure.
