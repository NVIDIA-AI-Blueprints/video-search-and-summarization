"""Source SURF evaluation payload construction."""
from datetime import datetime
from typing import Any, Dict
class PayloadBuilder:
    wait_for_results = True
    custom_payload = {}
    def _build_eval_payload(
        self,
        job_id: str,
        sensor_data: Dict[str, Dict[str, Any]],
        total_sensors: int,
        trigger_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Build the evaluation request payload for AB Evaluator /eval/batch API.

        Expected payload format:
        {
            "incidents": [
                {
                    "video": "sensor_name",
                    "stream_uuid": "<VST sensor UUID when from nvstreamer trigger>",
                    "end_timestamp": "...",
                    "start_timestamp": "..."
                },
                ...
            ],
            "wait_for_results": false
        }

        Args:
            job_id: Unique job identifier
            sensor_data: Sensor completion data from trigger
            total_sensors: Total number of sensors
            trigger_data: Full trigger data

        Returns:
            Evaluation request payload
        """
        # Build incidents array from sensor data
        incidents = []

        def _to_iso_timestamp(ts: Any) -> str:
            """Convert epoch (seconds or ms) or string to ISO 8601."""
            if ts is None:
                return datetime.utcnow().isoformat() + 'Z'
            if isinstance(ts, (int, float)):
                if ts > 1e12:
                    ts = ts / 1000
                return datetime.utcfromtimestamp(ts).isoformat() + 'Z'
            return str(ts)

        for sensor_name, info in sensor_data.items():
            incident = {
                'video': sensor_name,
            }

            # VST sensor UUID (dict key from /vst/api/v1/sensor/status); same id used for timelines
            sid = info.get('sensor_id')
            if sid:
                incident['stream_uuid'] = sid

            # Use completed_at timestamp if available, otherwise use end_timestamp
            if 'completed_at' in info:
                incident['end_timestamp'] = info['completed_at']
            elif 'end_timestamp' in info:
                incident['end_timestamp'] = _to_iso_timestamp(info['end_timestamp'])
            else:
                incident['end_timestamp'] = datetime.utcnow().isoformat() + 'Z'

            # Add start_timestamp when present
            if 'start_timestamp' in info:
                incident['start_timestamp'] = _to_iso_timestamp(info['start_timestamp'])

            incidents.append(incident)

        # Build payload in expected format (job_id: resolved value from execute(), not config-only)
        payload = {
            'job_id': job_id,
            'incidents': incidents,
            'wait_for_results': self.wait_for_results,
        }

        # Merge with custom payload if provided (allows adding extra fields)
        if self.custom_payload:
            payload.update(self.custom_payload)

        return payload
