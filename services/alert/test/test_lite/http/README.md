# Alert Agent HTTP API Testing

This directory contains HTTP API testing tools and test payloads for the Alert Bridge HTTP endpoints.

## Files

### Test Scripts
- **`test_alert_http.py`** - Basic HTTP API endpoint tests
- **`auto_validate_http_req_response.py`** - Automated end-to-end HTTP-to-Redis validation

### Test Payloads
- **`prompt/`** - Directory containing prompt API test payloads

## Testing Workflows

### 🤖 Automated End-to-End Testing (Recommended)

**For comprehensive HTTP-to-Redis flow validation:**

```bash
cd test/test_lite/http

# Full automated test (5 different payloads)
python auto_validate_http_req_response.py

# Custom endpoint and timeout
python auto_validate_http_req_response.py --host localhost --port 9080 --timeout 120

# Verbose output with debugging
python auto_validate_http_req_response.py --verbose
```

**Features:**
- ✅ **HTTP Request Testing**: Posts payloads to FastAPI `/api/v1/alerts` endpoint
- ✅ **Redis Response Monitoring**: Monitors output streams for responses  
- ✅ **Deep Schema Validation**: Validates nested alert/event/verification objects
- ✅ **Response Time Tracking**: Measures end-to-end latency
- ✅ **Comprehensive Reporting**: Detailed success/failure analysis
- ✅ **Verification Details**: Extracts VSS verification results and confidence

### 🔧 Basic HTTP Endpoint Testing

**For simple HTTP API validation:**

```bash
cd test/test_lite/http

# Basic endpoint tests
python test_alert_http.py

# With verbose output
python test_alert_http.py --verbose --host localhost --port 9080
```

**Features:**
- ✅ **HTTP Status Validation**: Tests response codes
- ✅ **Payload Validation**: Tests different payload types
- ✅ **Error Handling**: Tests validation and JSON parsing

## Prerequisites

```bash
# Install required dependencies
pip install requests redis pyyaml

# Make sure Alert Bridge and Redis are running
docker compose -f ../../../test_docker-compose.yml up -d redis
python ../../../enhance_alert_with_vlm.py --config ../../../config.yaml
```

## Automated Test Cases

The `auto_validate_http_req_response.py` includes:

1. **Wrong Way Detection** - HIGH severity traffic violation
2. **Restricted Area Access** - CRITICAL security intrusion  
3. **Object Detection** - MEDIUM suspicious object alert
4. **Vehicle Count** - LOW traffic density analysis
5. **Safety Equipment** - HIGH safety protocol violation

Each test case:
- Uses unique HTTP payloads with different VLM prompts
- Monitors Redis streams for enhanced responses
- Validates complete response schema structure
- Tracks verification status and confidence scores

## Expected Output

### Automated Test Report
```
================================================================================
📊 HTTP AUTOMATED TEST REPORT
================================================================================

📈 SUMMARY:
   HTTP Endpoint: http://localhost:9080/api/v1/alerts
   Payloads Sent: 5
   Responses Received: 5
   Valid Responses: 5
   Response Rate: 100.0%
   Success Rate: 100.0%

📋 TEST CASE DETAILS:

🔸 HTTP Payload 1: wrong_way_detection
   📤 Event ID: http-test-a1b2c3d4-e5f6-7890-abcd-ef1234567890
   📥 Response Received: ✅ YES
   🔍 Schema Valid: ✅ YES
   🔍 Verification Status: SUCCESS
   🔍 Verification Result: True
   🔍 Verification Confidence: 0.92
   ⏱️  Response Time: 15.3s

🔸 HTTP Payload 2: restricted_area_access
   📤 Event ID: http-test-b2c3d4e5-f6g7-8901-bcde-f12345678901
   📥 Response Received: ✅ YES
   🔍 Schema Valid: ✅ YES
   🔍 Verification Status: SUCCESS
   🔍 Verification Result: False
   🔍 Verification Confidence: 0.88
   ⏱️  Response Time: 12.7s

[... additional test cases ...]

================================================================================
🎉 ALL HTTP TESTS PASSED! Perfect success rate.
================================================================================
```

### Basic Test Report
```
============================================================
TEST SUMMARY
============================================================
Total Tests:  5
Passed:       5 ✅
Failed:       0 ❌
Success Rate: 100.0%

PERFORMANCE:
Average Response Time: 45.2ms
Max Response Time:     125.3ms
============================================================
```

## Response Schema Structure

The automated tests validate against the new response schema:

```json
{
  "id": "unique-event-id",
  "version": "1.0",
  "@timestamp": "ISO-8601-timestamp",
  "sensorId": "sensor-identifier",
  "videoPath": "path/to/video.mp4",
  "alert": {
    "severity": "HIGH|MEDIUM|LOW|CRITICAL",
    "status": "VERIFIED",  // Set after successful verification
    "type": "alert-type",
    "description": "alert description"
  },
  "event": {
    "type": "event-type", 
    "description": "event description"
  },
  "verification": {  // New verification object
    "status": "SUCCESS|FAILURE",
    "result": true,  // Boolean verification result
    "confidence": 0.92,
    "verification_method": "VSS",
    "verified_by": "MVILA-15B v1.0",
    "verified_at": "2025-01-15T10:00:00Z",
    "description": "Summary of verification result",
    "alert_reasoning": "Detailed reasoning for the alert"
  }
}
```

## Server Requirements

### For Automated Testing
Both Alert Bridge **and** Redis must be running:

```bash
# Start Redis
docker compose -f ../../../test_docker-compose.yml up -d redis

# Start Alert Bridge with FastAPI
python ../../../enhance_alert_with_vlm.py --config ../../../config.yaml
```

### For Basic Testing
Only Alert Bridge needs to be running:

```bash
# Start server from project root
python ../../../enhance_alert_with_vlm.py --config ../../../config.yaml
```

Default server endpoint: `http://localhost:9080`

## Integration with CI/CD

Both test scripts return appropriate exit codes:
- **Exit 0**: All tests passed
- **Exit 1**: One or more tests failed

### Example CI Pipeline
```bash
#!/bin/bash
# CI Pipeline for HTTP-to-Redis validation

# Start infrastructure
docker compose -f test_docker-compose.yml up -d redis
python enhance_alert_with_vlm.py --config config.yaml &
SERVER_PID=$!

# Wait for services
sleep 10

# Run automated end-to-end tests
cd test/test_lite/http
python auto_validate_http_req_response.py --timeout 120

# Store exit code
TEST_RESULT=$?

# Cleanup
kill $SERVER_PID
docker compose -f ../../../test_docker-compose.yml down

# Exit with test result
exit $TEST_RESULT
```

## Debugging

### Common Issues

**HTTP endpoint not available:**
```bash
# Check if server is running
curl http://localhost:9080/health

# Check server logs
python enhance_alert_with_vlm.py --config config.yaml
```

**Redis connection failed:**
```bash
# Check Redis status
docker compose -f ../../../test_docker-compose.yml ps redis

# Test Redis connection
redis-cli ping
```

**No responses received:**
- Check Redis stream configuration in `config.yaml`
- Verify `incidents_stream` and `enhanced_anomaly_stream` settings
- Use `--verbose` flag for detailed monitoring logs

### Advanced Debugging
```bash
# Monitor Redis streams in real-time
cd ../redis
python verify_responses.py

# Send individual HTTP requests using prompt folder payloads
curl -X POST http://localhost:9080/api/v1/alerts \
  -H "Content-Type: application/json" \
  -d @prompt/test_prompt_payload.json
``` 