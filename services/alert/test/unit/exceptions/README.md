# VSS Exception Tests

## Exception Types and Trigger Conditions

### 1. VSSConnectionError
- **Trigger**: Failed to establish connection to VSS service
- **Common Causes**: Wrong host/port, service down, network issues

### 2. VSSMediaUploadError  
- **Trigger**: Failed to upload media file to VSS
- **Common Causes**: File not found, invalid file path, unsupported format
- **Test**: Uses non-existent file path `/media/nonexistent/wrong_file.mp4`

### 3. VSSAPIError
- **Trigger**: VSS API call failures
- **Common Causes**: Invalid model ID, timeout, HTTP errors, empty response

### 4. VSSPromptError
- **Trigger**: No suitable prompt found for entity
- **Common Causes**: Missing prompts in payload, unknown alert type, no matching template
- **Test**: Removes prompts and uses unknown alert type `unknown_alert_type_xyz`

### 5. VSSResponseError
- **Trigger**: No valid evaluations returned from VSS
- **Common Causes**: All prompts failed, empty results, parsing failures

### 6. VSSRetryExhaustedError
- **Trigger**: Operation failed after max retry attempts
- **Note**: Only raised for retriable errors (not for VSSMediaUploadError or VSSPromptError)

## Running the Tests

### Prerequisites
```bash
pip install redis pyyaml
```

### Run Tests
```bash
cd /home/user/alert_agent/test/exception_test
python3 test_exceptions.py
```

### Test Flow
1. **Valid Payload**: Sends correct payload as control test
2. **VSSMediaUploadError**: Tests with invalid media file path
3. **VSSPromptError**: Tests with missing prompts and unknown alert type
