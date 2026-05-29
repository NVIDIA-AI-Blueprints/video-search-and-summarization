# Entity Management Configuration

This directory contains the external configuration file that defines **ALL** default values for VSS and VLM parameters. **The system requires complete configuration** - missing parameters will cause startup failure.

## 📁 Configuration File

### `defaults.yaml` (Required - Complete Configuration)
The main configuration file that **MUST** contain all default values for:
- **VLM Parameters**: Token limits, temperature, sampling parameters
- **VSS Parameters**: Video processing, batch sizes, feature flags
- **Request Defaults**: Default values for optional request fields
- **Validation Settings**: Performance tuning, error handling
- **Constraints**: Min/max validation rules

**⚠️ Important**: All parameters must be defined in the configuration file. The system will fail at startup if any required parameter is missing.

## 🔧 How It Works

1. **Configuration Loading**: The system loads `defaults.yaml` at startup
2. **Complete Validation**: Validates that ALL required parameters are present
3. **Type Checking**: Ensures all parameters have correct types
4. **Constraint Validation**: Validates values against defined constraints
5. **Fail Fast**: Throws clear error messages if anything is missing or invalid
6. **Default Application**: Pydantic models use these values as defaults
7. **Caching**: Configuration is cached for performance after first load

## 📝 Configuration Examples

### Complete Configuration Structure

```yaml
# defaults.yaml - ALL sections required
vlm_params:
  response_format:
    type: "text"
  max_tokens: 512
  temperature: 0.3
  top_p: 1.0
  top_k: 100
  seed: 20
  stream: false
  stream_options:
    include_usage: true

vss_params:
  # Core processing - ALL required
  chunk_duration: 60
  chunk_overlap_duration: 10
  cv_metadata_overlay: true
  num_frames_per_chunk: 8
  enable_caption: true
  debug: false
  
  # Video dimensions - ALL required
  vlm_input_width: 1280
  vlm_input_height: 720
  
  # API parameters - ALL required
  summarize_top_p: 0.7
  summarize_temperature: 0.2
  summarize_max_tokens: 2048
  summarize_batch_size: 6
  chat_top_p: 0.7
  chat_temperature: 0.2
  chat_max_tokens: 512
  notification_top_p: 0.7
  notification_temperature: 0.2
  notification_max_tokens: 2048
  
  # RAG parameters - ALL required
  rag_batch_size: 1
  rag_type: "graph-rag"
  rag_top_k: 5
  
  # Feature flags - ALL required
  enable_cv_metadata: false
  enable_audio: false
  enable_chat_history: true
  enable_chat: true
  highlight: false
  
  # Prompts - ALL required (can be empty strings)
  cv_pipeline_prompt: ""
  caption_summarization_prompt: "Output the original caption directly."
  summary_aggregation_prompt: "Output the original caption directly."

request_defaults:
  # Optional field defaults (applied only if not provided in request)
  confidence: 0.0              # Default confidence score
  cv_metadata_path: null       # No default CV metadata path
  meta_labels: []              # Empty default metadata labels

validation:
  continue_on_validation_error: true
  max_validation_errors_per_batch: 10
  log_validation_stats: true
  log_applied_defaults: false

# Constraints are optional but recommended
constraints:
  vlm_params:
    max_tokens:
      min: 1
      max: 4096
    temperature:
      min: 0.0
      max: 2.0
  vss_params:
    chunk_duration:
      min: 1
      max: 300
```

### Parameter Tuning Examples

```yaml
# Production optimization
vss_params:
  summarize_batch_size: 8   # Increase for better throughput
  rag_batch_size: 2         # Adjust based on memory
  debug: false              # Always false in production

vlm_params:
  max_tokens: 256          # Reduce for faster responses
  temperature: 0.2         # Lower for consistent output
```

## 🛡️ Validation and Error Handling

### Required Configuration Validation
The system validates that all required sections and parameters are present:

```yaml
# These sections MUST exist:
vlm_params: { ... }      # ALL VLM parameters required
vss_params: { ... }      # ALL VSS parameters required  
request_defaults: { ... } # Request defaults required
```

### Type Validation
All parameters are validated for correct types:
- **Integers**: `max_tokens`, `chunk_duration`, etc.
- **Floats**: `temperature`, `top_p`, etc.
- **Booleans**: `debug`, `enable_caption`, etc.
- **Strings**: `rag_type`, prompts, etc.
- **Dictionaries**: `response_format`, `stream_options`

### Error Messages
Clear error messages help identify missing or invalid configuration:

```
RuntimeError: External configuration is required but failed to load: 
Configuration missing required sections: ['vss_params']
Ensure entity_management/config/defaults.yaml exists and is valid.
```

```
ValueError: Required parameter 'max_tokens' not found in 'vlm_params' configuration
```

## 🔍 Debugging Configuration

### Check Configuration Loading
```python
from entity_management import AlertsDefaultsConfigLoader

loader = AlertsDefaultsConfigLoader()
try:
    config = loader.load_defaults()
    print("Configuration loaded successfully")
    print(f"VLM params: {len(config.vlm_params)} parameters")
    print(f"VSS params: {len(config.vss_params)} parameters")
except Exception as e:
    print(f"Configuration error: {e}")
```

### Validate Configuration File
```bash
# Check YAML syntax
yamllint entity_management/config/defaults.yaml

# Check file exists and is readable
ls -la entity_management/config/defaults.yaml
```

## 🎛️ Optional Field Configuration

The `request_defaults` section controls which optional fields get default values and what those defaults are. This follows a precise three-step logic:

### Optional Field Logic:
1. **Field provided in input** → Use input value (always)
2. **Field missing in input + defined in config** → Use config value  
3. **Field missing in input + not defined in config** → Field won't exist in entity

### Configuration Options:
- **Include field with value**: Field gets that default when missing from input
- **Include field with `null`**: Field gets `None` when missing from input
- **Exclude field entirely**: Field won't exist unless provided in input

### Examples:
```yaml
request_defaults:
  confidence: 0.0              # Missing → gets 0.0
  cv_metadata_path: null       # Missing → gets None
  meta_labels: []              # Missing → gets empty list
  # correlation_id: not configured → won't exist unless provided
```

### Real-World Scenarios:
```yaml
# Scenario 1: Conservative defaults
request_defaults:
  confidence: 0.0              # Safe default
  # cv_metadata_path: omitted  # Only exists if provided
  
# Scenario 2: Comprehensive defaults  
request_defaults:
  confidence: 0.5              # Medium confidence default
  cv_metadata_path: "/default/cv/metadata.json"  # Default path
  meta_labels:                 # Default labels
    - key: "source"
      value: "auto_generated"
      
# Scenario 3: Minimal defaults
request_defaults:
  # All optional fields omitted - only exist if explicitly provided
```

## 📋 Required Configuration Parameters

### VLM Parameters (ALL Required)
| Parameter | Type | Description |
|-----------|------|-------------|
| `response_format` | dict | Response format specification |
| `max_tokens` | int | Maximum response tokens |
| `temperature` | float | Sampling temperature |
| `top_p` | float | Nucleus sampling parameter |
| `top_k` | int | Top-k sampling parameter |
| `seed` | int | Random seed |
| `stream` | bool | Enable streaming |
| `stream_options` | dict | Streaming configuration |

### VSS Parameters (ALL Required)
| Parameter | Type | Description |
|-----------|------|-------------|
| `chunk_duration` | int | Video chunk duration (seconds) |
| `chunk_overlap_duration` | int | Overlap duration (seconds) |
| `cv_metadata_overlay` | bool | Enable CV metadata overlay |
| `num_frames_per_chunk` | int | Frames per chunk |
| `enable_caption` | bool | Enable video captioning |
| `debug` | bool | Enable debug mode |
| `vlm_input_width` | int | VLM input width (pixels) |
| `vlm_input_height` | int | VLM input height (pixels) |
| **... and 15+ more parameters** | various | See complete example above |

## 🚨 Production Recommendations

### Configuration Management
- **Version Control**: Store `defaults.yaml` in version control
- **Environment-Specific**: Use different files for dev/staging/prod
- **Validation**: Test configuration in CI/CD pipelines
- **Backup**: Keep backups of working configurations

### Security
- **File Permissions**: `chmod 644 defaults.yaml`
- **Access Control**: Restrict who can modify configuration
- **Audit**: Log configuration changes

### Deployment Process
1. **Validate Syntax**: `yamllint defaults.yaml`
2. **Test Loading**: Run validation in staging
3. **Deploy**: Update production configuration
4. **Restart**: Restart application to load new config
5. **Verify**: Check logs for successful loading

## 🆘 Troubleshooting

### Application Won't Start
1. **Check if `defaults.yaml` exists**
2. **Verify all required sections are present**
3. **Validate YAML syntax**: `yamllint defaults.yaml`
4. **Check file permissions**: should be readable
5. **Review error messages**: they specify what's missing

### Common Configuration Errors

#### Missing Sections
```
Error: Configuration missing required sections: ['vlm_params']
Solution: Add the missing section with all required parameters
```

#### Missing Parameters
```
Error: Required parameter 'max_tokens' not found in 'vlm_params'
Solution: Add the missing parameter to the vlm_params section
```

#### Wrong Types
```
Error: Parameter 'vlm_params.max_tokens' must be of type int, got str
Solution: Remove quotes from numeric values
```

#### Invalid YAML
```
Error: Invalid YAML in configuration file: mapping values are not allowed here
Solution: Fix YAML syntax (indentation, colons, etc.)
```

## 🎯 Quick Start

1. **Copy Template**: Use the complete configuration example above
2. **Customize Values**: Modify parameters for your environment
3. **Validate Syntax**: `yamllint defaults.yaml`
4. **Test Loading**: Run a quick test to ensure all parameters load
5. **Deploy**: Start your application
6. **Monitor**: Check logs for successful configuration loading

**Remember**: The system is designed to fail fast if configuration is incomplete. This ensures you always know exactly what needs to be configured! 