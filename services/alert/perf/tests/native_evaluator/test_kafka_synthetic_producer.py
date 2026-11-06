import unittest

from alert_bridge_evaluator.mdx.protobuf import Incident as NvSchemaIncident
from alert_bridge_evaluator.services.alert_bridge_client import AlertBridgeError
from alert_bridge_evaluator.services.kafka_synthetic_producer import KafkaSyntheticIncidentProducer


class _FakeProducer:
    def __init__(self, config):
        self.config = config
        self.calls = []
        self.flush_timeout = None

    def produce(self, **kwargs):
        self.calls.append(kwargs)
        callback = kwargs.get("callback")
        if callback:
            callback(None, object())

    def flush(self, timeout):
        self.flush_timeout = timeout
        return 0


class TestKafkaSyntheticIncidentProducer(unittest.TestCase):
    def test_submit_incident_publishes_protobuf_payload(self):
        fake = _FakeProducer({})

        producer = KafkaSyntheticIncidentProducer(
            bootstrap_servers="kafka-kafka:9092",
            incident_topic="mdx-incidents",
            flush_timeout=7.5,
            producer_factory=lambda config: (setattr(fake, "config", config) or fake),
        )

        response = producer.submit_incident(
            {
                "id": "incident-1",
                "sensorId": "sensor-1",
                "timestamp": "2026-06-30T00:00:00Z",
                "end": "2026-06-30T00:00:05Z",
                "category": "FOV Count Violation",
                "isAnomaly": True,
                "objectIds": [],
                "place": {"name": "warehouse"},
                "analyticsModule": {"id": "Evaluation Module"},
                "info": {"media_urls": ["http://example/video.mp4"]},
            }
        )

        self.assertEqual(response["status"], "accepted")
        self.assertEqual(fake.config["bootstrap.servers"], "kafka-kafka:9092")
        self.assertEqual(fake.flush_timeout, 7.5)
        self.assertEqual(len(fake.calls), 1)
        call = fake.calls[0]
        self.assertEqual(call["topic"], "mdx-incidents")
        self.assertEqual(call["key"], "sensor-1")

        message = NvSchemaIncident()
        message.ParseFromString(call["value"])
        self.assertEqual(message.sensorId, "sensor-1")
        self.assertEqual(message.category, "FOV Count Violation")
        self.assertEqual(message.info["media_urls"], '["http://example/video.mp4"]')

    def test_submit_incident_requires_bootstrap_servers(self):
        producer = KafkaSyntheticIncidentProducer(
            bootstrap_servers="",
            incident_topic="mdx-incidents",
            producer_factory=lambda _config: _FakeProducer({}),
        )

        with self.assertRaises(AlertBridgeError):
            producer.submit_incident({"sensorId": "sensor-1"})


if __name__ == "__main__":
    unittest.main()
