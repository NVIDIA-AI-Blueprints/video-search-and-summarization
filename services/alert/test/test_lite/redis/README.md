# Redis Test Scripts

## Scripts Overview

### `send_payload.py` - Send Test Payloads
Sends test Alert Bridge events to Redis input streams.

**Usage:**
```bash
python send_payload.py [1|2|3|4|5|--heartbeat]
```

**Test Payloads:**
- **Payload 1**: Wrong way detection (minimal optional fields)
- **Payload 2**: Safety violation (some optional fields)  
- **Payload 3**: Weather conditions (all optional fields)
- **Payload 4**: ❌ VALIDATION TEST: Invalid alert.type
- **Payload 5**: 🔄 AUTO-PROMPT TEST: Valid alert.type + auto-generated prompt

### `verify_responses.py` - Monitor & Validate Responses ✨ **UPDATED**
Enhanced script that monitors Redis output streams and validates responses against the new response schema structure.

**Usage:**
```bash
# Monitor live responses with schema validation and payload matching
python verify_responses.py

# Test schema validation with sample responses
python verify_responses.py --test

# Validate the last N responses from output stream
python verify_responses.py --validate-last 5

# Show Redis stream information
python verify_responses.py --info
```

**New Schema Validation Features:**

🔍 **Schema Validation**
- Validates complete response structure
- Checks required fields: `id`, `@timestamp`, `alert{}`, `event{}`, `verification{}`
- Validates nested object structures and data types
- Verifies `alert.status = "VERIFIED"` for successful verification
- Validates `verification.result` boolean and `verification.status`

🔗 **Payload Matching**
- Automatically loads sent payloads from `send_payload.py`
- Matches responses to original requests by event ID
- Compares original vs response fields (alert type, sensor ID, etc.)
- Shows verification results and confidence scores

📊 **Validation Reporting**
- Real-time validation results with ✅/❌ status
- Detailed error messages for failed validations
- Warnings for optional field issues
- Final validation summary with success rates

**Example Output:**
```
📡 Monitoring Redis Output Streams: ['enhanced_anomaly_stream', 'incidents_stream']
   🔍 Schema Validation: ENABLED
   🔗 Payload Matching: ENABLED

📋 RESPONSE PAYLOAD (Event ID: event-abc123):
{
  "id": "event-abc123",
  "@timestamp": "2025-01-15T10:00:00Z",
  "alert": {"status": "VERIFIED", "type": "wrong_way_detection"},
  "verification": {"status": "SUCCESS", "result": true, "confidence": 0.92}
}

🔍 SCHEMA VALIDATION:
✅ Schema Validation: PASSED
   📊 Validated Fields (12): id, @timestamp, alert, event, verification, alert.status=VERIFIED, verification.status=SUCCESS, verification.result=true

🔗 PAYLOAD MATCH FOUND:
   📤 Original Alert Type: wrong_way_detection
   📥 Response Alert Type: wrong_way_detection
   🔍 Verification Status: SUCCESS
   🔍 Verification Result: true
   🔍 Verification Confidence: 0.92
```

### `auto_validate_req_response.py` - Automated End-to-End Testing ✨ **NEW**
Comprehensive automated testing script that sends all 5 payloads and validates responses.

**Usage:**
```bash
# Run automated test suite with default settings
python auto_validate_req_response.py

# Run with custom timeout and verbose output
python auto_validate_req_response.py --timeout 120 --verbose

# Get help
python auto_validate_req_response.py --help
```

**Features:**
- 🤖 **Automated Testing**: Sends all 5 test payloads sequentially
- 📡 **Live Monitoring**: Monitors output streams for responses in real-time
- 🔍 **Schema Validation**: Validates each response against the response schema
- 📊 **Detailed Reporting**: Comprehensive report with success rates and validation details
- ⏱️ **Timeout Handling**: Configurable timeout for response collection
- 🔗 **Payload Matching**: Matches responses to original requests by event ID

**Example Output:**
```
🚀 Starting Automated Test Suite...
✅ Connected to Redis
📋 Loaded 5 test payloads

📤 SENDING PAYLOADS:
📤 Sent Payload 1: event-abc123
📤 Sent Payload 2: event-def456
📤 Sent Payload 3: event-ghi789
📤 Sent Payload 4: event-jkl012
📤 Sent Payload 5: event-mno345

📥 COLLECTING RESPONSES:
⏳ Waiting for responses (timeout: 60s)...
📥 Response 1/5: Payload 1 - ✅ VALID
📥 Response 2/5: Payload 2 - ✅ VALID
📥 Response 3/5: Payload 3 - ✅ VALID
📥 Response 4/5: Payload 4 - ❌ INVALID
📥 Response 5/5: Payload 5 - ✅ VALID

================================================================================
📊 AUTOMATED TEST REPORT
================================================================================

📈 SUMMARY:
   Payloads Sent: 5
   Responses Received: 5
   Valid Responses: 4
   Response Rate: 100.0%
   Success Rate: 80.0%

🎉 ALL TESTS PASSED! Perfect success rate.
```

### `setup_streams.py` - Initialize Redis Streams
Creates required Redis streams for testing.

## Testing Workflow

### **Option A: Automated Testing (Recommended) 🚀**

**Quick End-to-End Test:**
```bash
# 1. Start Redis
docker compose -f ../../../test_docker-compose.yml up -d redis

# 2. Run automated test suite
python auto_validate_req_response.py

# 3. Review results in comprehensive report
```

**Advanced Automated Testing:**
```bash
# Run with verbose output and custom timeout
python auto_validate_req_response.py --timeout 120 --verbose

# The script will automatically:
# - Send all 5 test payloads
# - Monitor responses in real-time  
# - Validate schemas
# - Generate detailed report
# - Exit with success/failure code
```

### **Option B: Manual Testing (For Debugging)**

**Step-by-Step Manual Testing:**
```bash
# 1. Setup Environment
docker compose -f ../../../test_docker-compose.yml up -d redis
python setup_streams.py  # If needed

# 2. Start Response Monitoring (Terminal 1)
python verify_responses.py

# 3. Send Test Payloads (Terminal 2)
python send_payload.py 1  # Send payload 1
python send_payload.py 2  # Send payload 2
python send_payload.py 3  # Send payload 3

# 4. Observe live validation results in Terminal 1

# 5. Validate Historical Responses
python verify_responses.py --validate-last 5
```

**When to Use Manual Testing:**
- 🐛 Debugging specific payload issues
- 🔍 Detailed inspection of individual responses
- 📡 Real-time monitoring during development
- 🧪 Testing custom payloads

## Response Schema Structure

The new response schema includes:

```json
{
  "id": "unique-event-id",
  "version": "1.0",
  "@timestamp": "ISO-8601-timestamp",
  "sensorId": "sensor-identifier",
  "videoPath": "path/to/video.mp4",
  "alert": {
    "severity": "HIGH|MEDIUM|LOW",
    "status": "VERIFIED",  // Set to VERIFIED after successful verification
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
    "description": "Summary of verification",
    "alert_reasoning": "Detailed reasoning"
  }
}
```

## Validation Results

The validator checks:
- ✅ **Required Fields**: All mandatory fields present
- ✅ **Nested Objects**: Correct structure for alert/event/verification  
- ✅ **Data Types**: Boolean for verification.result, valid timestamps, etc.
- ✅ **Status Values**: VERIFIED for alert.status, SUCCESS/FAILURE for verification.status
- ✅ **Ranges**: Confidence values between 0.0-1.0
- ⚠️  **Warnings**: Optional field issues, unexpected values 