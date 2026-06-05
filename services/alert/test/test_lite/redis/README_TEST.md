# VSS Core API Test

This guide provides instructions for testing the VSS Core API using Redis streams.

## Test Steps

### 1. Setup Redis Streams

Initialize the required Redis streams for testing:

```bash
cd test/test_lite/redis
python3 setup_streams.py
```

This will create the necessary input and output streams in Redis.

### 2. Send Test Request

Send a test payload to the VSS API:

```bash
cd test/test_lite/redis
python3 send_payload.py 1  # or 2, 3, 4, 5 for different payloads
```

### 3. Monitor Responses

Monitor and verify the responses from the VSS API:

```bash
cd test/test_lite/redis
python3 verify_responses.py
```

This will display real-time responses and validate them against the expected schema.
