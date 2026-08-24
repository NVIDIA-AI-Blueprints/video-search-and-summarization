# Entity Management Tests

This directory contains all unit tests and integration tests for the `entity_management` module, which handles Alert Request Entity validation, processing, and response generation.

## 🧪 Test Structure

### **Core Test Modules**

#### `test_entity_management_simple.py`
- **Purpose**: Basic component tests and imports verification
- **Coverage**: 
  - Module imports
  - EntityValidator creation
  - EntityBuilder creation
  - Configuration loading
  - Error response creation
  - Enum functionality (AlertSeverity, AlertStatus)
  - Basic entity creation (AlertInfo, EventInfo)

#### `test_alert_request_simple.py`
- **Purpose**: AlertRequestEntity validation and field handling
- **Coverage**:
  - Minimal required fields validation
  - Optional fields with provided values
  - Configuration-driven default values
  - Input value override of config defaults
  - Missing required fields validation
  - Invalid enum values
  - Confidence score range validation
  - JSON serialization/deserialization

#### `test_validator_simple.py`
- **Purpose**: EntityValidator functionality and batch processing
- **Coverage**:
  - Valid request processing
  - Invalid request filtering
  - Mixed valid/invalid request batches
  - Validation statistics tracking
  - Empty request lists
  - Batch processing performance (20 requests)

#### `test_response_simple.py`
- **Purpose**: AlertResponseEntity and EntityBuilder tests
- **Coverage**:
  - Basic AlertResponseEntity creation
  - Response with VLM evaluations
  - Error response scenarios
  - EntityBuilder error response creation
  - VSSEvaluation object creation
  - JSON serialization/deserialization
  - ProcessingStatus enum validation

#### `test_integration.py`
- **Purpose**: End-to-end integration tests
- **Coverage**:
  - Complete alert processing flow (request → validation → response)
  - Error handling workflows
  - Batch processing simulation (8 requests)
  - Configuration integration
  - Redis stream data format compatibility

### **Test Runner**

#### `run_all_tests.py`
- **Purpose**: Comprehensive test suite runner with detailed reporting
- **Features**:
  - Runs all test modules in sequence
  - Performance timing and statistics
  - System readiness assessment
  - Feature coverage summary
  - Exit codes for CI/CD integration

## 🚀 Running Tests

### **Run All Tests**
```bash
cd test/entity_management_tests
python run_all_tests.py
```

### **Run Individual Test Modules**
```bash
# From project root with PYTHONPATH
PYTHONPATH=. python test/entity_management_tests/test_entity_management_simple.py
PYTHONPATH=. python test/entity_management_tests/test_validator_simple.py
PYTHONPATH=. python test/entity_management_tests/test_response_simple.py
PYTHONPATH=. python test/entity_management_tests/test_integration.py
```

### **Run with Pytest** (if available)
```bash
cd test/entity_management_tests
PYTHONPATH=../.. pytest test_*.py -v
```

## 📊 Test Coverage

### **Entity Models**
- ✅ AlertRequestEntity (validation, defaults, serialization)
- ✅ AlertResponseEntity (creation, evaluation handling)
- ✅ AlertInfo and EventInfo (nested entity validation)
- ✅ VSSEvaluation (VLM evaluation results)

### **Processing Components**
- ✅ EntityValidator (batch validation, statistics, error handling)
- ✅ EntityBuilder (error response creation)
- ✅ Configuration loading (external YAML defaults)

### **Enums and Constants**
- ✅ AlertSeverity (LOW, MEDIUM, HIGH, CRITICAL)
- ✅ AlertStatus (PENDING, ACTIVE, RESOLVED, DISMISSED)
- ✅ ProcessingStatus (PENDING, IN_PROGRESS, COMPLETED, FAILED)

### **Configuration System**
- ✅ External YAML configuration loading
- ✅ Default value application for optional fields
- ✅ Config-driven parameter defaults (VLM, VSS)
- ✅ Field validation and constraints

### **Data Flow**
- ✅ Redis stream data format compatibility
- ✅ JSON serialization for inter-service communication
- ✅ End-to-end processing pipeline
- ✅ Error propagation and handling

## 🎯 Test Scenarios

### **Valid Data Tests**
- Minimal required fields only
- All optional fields provided
- Mixed optional field scenarios
- Boundary value testing (confidence: 0.0, 1.0)

### **Invalid Data Tests**
- Missing required fields
- Invalid enum values
- Out-of-range confidence scores (< 0.0, > 1.0)
- Empty required strings

### **Configuration Tests**
- Config-driven default application
- Input value override of config defaults
- Missing config handling (fail-fast behavior)

### **Performance Tests**
- Batch processing (20+ requests)
- Validation statistics tracking
- Processing time measurements

## 🔧 Test Dependencies

### **Required Packages**
- `pydantic` - Entity validation and serialization
- `yaml` - Configuration file loading

### **Test Data**
- Uses realistic alert scenarios (security, traffic, safety)
- Compatible with actual Redis stream message format
- Covers edge cases and error scenarios

## 📈 Success Criteria

Tests pass when:
- ✅ All entity validation works correctly
- ✅ Configuration defaults are applied properly
- ✅ Error handling is robust
- ✅ Serialization/deserialization is reliable
- ✅ End-to-end flow completes successfully
- ✅ Performance is within acceptable limits

## 🐛 Debugging

### **Common Issues**
1. **Import Errors**: Ensure `PYTHONPATH` includes project root
2. **Config Loading Errors**: Verify `entity_management/config/defaults.yaml` exists
3. **Validation Errors**: Check field names and data types match schema

### **Debug Mode**
Add debug logging to see detailed validation flow:
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

## 🔄 Maintenance

### **Adding New Tests**
1. Follow existing naming pattern: `test_[component]_[scenario].py`
2. Update `run_all_tests.py` to include new test module
3. Ensure tests cover both success and failure cases
4. Update this README with new test coverage

### **Updating Tests**
- Keep tests in sync with entity schema changes
- Update test data when adding new fields or enums
- Maintain backward compatibility where possible 