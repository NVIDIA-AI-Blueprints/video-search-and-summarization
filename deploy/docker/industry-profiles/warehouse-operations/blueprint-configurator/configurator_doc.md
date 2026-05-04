# Blueprint Configuration Documentation

This document provides comprehensive documentation for the `blueprint_config.yml` file, which defines hardware-specific configurations for GPU profiles and deployment modes in the Spatial AI Analytics Configurator system.

## Table of Contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Configuration File Structure](#configuration-file-structure)
- [Commons Section](#commons-section)
- [Hardware Profiles](#hardware-profiles)
- [Field Descriptions](#field-descriptions)
- [Operation Types](#operation-types)
- [Variable System](#variable-system)
  - [Conditional Expressions (If-Else)](#conditional-expressions-if-else)
- [Variable Validation](#variable-validation)
  - [Compound Conditions (AND/OR)](#compound-conditions-andor)
  - [Trigger-Based Validation (when_equals)](#trigger-based-validation-when_equals--validate_conditions)
- [Environment Variables](#environment-variables)
- [Use Commons Behavior](#use-commons-behavior)
- [Complete Examples](#complete-examples)
- [Best Practices](#best-practices)

---

## Overview

The Blueprint Configuration system automatically adjusts application settings based on:
- **Hardware Profile**: The type of GPU (H100, L4, L40S, RTXA6000, RTXA6000ADA, IGX-THOR, or default)
- **Deployment Mode**: The application mode (2d or 3d)

The system performs various file operations including:
- YAML file updates
- Text configuration file updates
- JSON file updates
- File management operations (e.g., keeping only N files)

### Key Features

- **Reusable Commons**: Define shared configurations once and reuse across multiple hardware profiles
- **Variable Expressions**: Support for mathematical expressions and environment variable substitution
- **Conditional Variables**: Support for if-else (ternary) expressions in variable definitions
- **Variable Validation**: Validate environment variables with allowed values, patterns, and conditional rules
- **Compound Conditions**: Support for nested AND/OR conditions in validation rules
- **Trigger-Based Validation**: Validate dependent variables using `when_equals` + `validate_conditions` pattern
- **Flexible Inheritance**: Control which common configurations to use with `use_commons` settings
- **Profile-Specific Overrides**: Add hardware-specific configurations that append to common ones
- **Environment Variable Substitution**: Use `${VAR_NAME}` syntax throughout the configuration

---

## Prerequisites

The configurator requires the following environment variables to function properly:

### Required Environment Variables

- **`HARDWARE_PROFILE`**: GPU type identifier (e.g., H100, L4, L40S, RTXA6000, RTXA6000ADA, IGX-THOR)
  - If not set or not found in the configuration, the system uses the `default` profile
  - **Note**: When using the default profile, the system doesn't perform any config updates and uses baseline configurations

- **`MODE`**: Deployment mode
  - `"2d"`: For 2D deployment mode
  - `"3d"`: For 3D deployment mode
  - Default: `"3d"` if not specified

### Common Path Environment Variables

The following environment variables are typically used in file operations:

- **`DS_CONFIG_DIR`**: DeepStream configuration directory (e.g., `/opt/deepstream/configs`)
- **`VST_CONFIG_DIR`**: VST configuration directory (e.g., `/opt/vst/configs`)
- **`MDX_DATA_DIR`**: MDX data directory (e.g., `/opt/mdx/data`)
- **`MDX_SAMPLE_APPS_DIR`**: MDX sample applications directory (e.g., `/opt/mdx/apps`)
- **`NUM_STREAMS`**: Desired number of concurrent streams (user-defined)

---

## Configuration File Structure

The `blueprint_config.yml` file follows this hierarchical structure:

```yaml
commons:
  variable_validation:
    2d:
      - variable: "VAR_NAME"
        allowed_values: ["value1", "value2"]
    3d:
      - variable: "VAR_NAME"
        allowed_patterns: ["pattern*"]
      - variable: "OTHER_VAR"
        disallowed_patterns: ["*_test", "*_debug"]
  variables:
    2d:
      - variable_name: "expression"
    3d:
      - variable_name: "expression"
  file_operations:
    2d:
      - operation_type: "..."
        # operation details
    3d:
      - operation_type: "..."
        # operation details

HARDWARE_PROFILE:
  2d:
    max_streams_supported: <integer>
    use_commons:
      variable_validation: true|false|2d|3d
      variables: true|false|2d|3d
      file_operations: true|false|2d|3d
    variable_validation:
      - variable: "VAR_NAME"
        # validation rules
    variables:
      - variable_name: "expression"
    file_operations:
      - operation_type: "..."
  3d:
    max_streams_supported: <integer>
    # ... similar structure
```

---

## Commons Section

The `commons` section defines shared configurations that can be reused across multiple hardware profiles. This promotes consistency and reduces duplication.

### Structure

```yaml
commons:
  variable_validation:
    2d:
      - variable: "VAR_NAME"
        allowed_values: ["value1", "value2"]
    3d:
      - variable: "VAR_NAME"
        allowed_patterns: ["pattern*"]
      - variable: "OTHER_VAR"
        disallowed_patterns: ["*_test", "*_debug"]
  
  variables:
    2d:
      - var1: "expression1"
      - var2: "expression2"
    3d:
      - var1: "expression1"
      - var2: "expression2"
  
  file_operations:
    2d:
      - operation_type: "..."
        # operation details
    3d:
      - operation_type: "..."
        # operation details
```

### Commons Variables

**Purpose**: Define computed variables that can be used in file operations across all hardware profiles.

**Example**:
```yaml
commons:
  variables:
    2d:
      - final_stream_count: "min(${NUM_STREAMS}, ${max_streams_supported})"
    3d:
      - final_stream_count: "min(${NUM_STREAMS}, ${max_streams_supported})"
```

**What this means**:
- `final_stream_count`: A computed variable that takes the minimum of the user-requested `NUM_STREAMS` and the hardware's `max_streams_supported`
- This ensures the system never tries to run more streams than the GPU can handle
- The variable can then be used in file operations as `${final_stream_count}`

### Commons File Operations

**Purpose**: Define file operations that are common across hardware profiles for each deployment mode.

**Example**:
```yaml
commons:
  file_operations:
    2d:
      - operation_type: "file_management"
        target_directories:
          - "${MDX_DATA_DIR}/videos/warehouse-2d-app"
        file_management:
          action: "keep_count"
          parameters:
            count: ${final_stream_count}
            pattern: "*.mp4"
```

**What this means**:
- For 2D mode, keep only `final_stream_count` number of MP4 video files in the specified directory
- This prevents disk space issues by limiting the number of sample videos to match the number of streams

---

## Hardware Profiles

Hardware profiles define GPU-specific configurations. Each profile must specify settings for both 2d and 3d deployment modes.

### Basic Structure

```yaml
H100:
  2d:
    max_streams_supported: 4
  3d:
    max_streams_supported: 4

L4:
  2d:
    max_streams_supported: 4
  3d:
    max_streams_supported: 2
```

### Profile Naming Convention

- Use **UPPERCASE** for hardware profile names (e.g., H100, L4, L40S)
- Use exact GPU model names for clarity
- Profile names must match the `HARDWARE_PROFILE` environment variable

---

## Field Descriptions

### max_streams_supported

**Type**: Integer  
**Required**: Yes (for each deployment mode)  
**Purpose**: Defines the maximum number of concurrent streams the GPU can handle for the specific deployment mode

**Example**:
```yaml
L4:
  2d:
    max_streams_supported: 4  # L4 can handle 4 streams in 2D mode
  3d:
    max_streams_supported: 2  # L4 can handle 2 streams in 3D mode
```

**What this means**:
- The L4 GPU has different capabilities depending on the deployment mode
- 3D mode is more computationally intensive, so it supports fewer streams
- This value is automatically added to environment variables as `${max_streams_supported}`
- It's typically used in variable expressions to limit user-requested streams

### use_commons

**Type**: Object  
**Required**: No (defaults to using commons if not specified)  
**Purpose**: Controls whether to inherit variable_validation, variables, and file_operations from the commons section

**Structure**:
```yaml
use_commons:
  variable_validation: true|false|2d|3d
  variables: true|false|2d|3d
  file_operations: true|false|2d|3d
```

**Values**:
- `true` or `""` (empty/not specified): Use commons for the **current** deployment mode (2d or 3d)
- `false`: Don't use commons, only use profile-specific configurations
- `"2d"`: Always use commons **2d** configurations, regardless of current mode
- `"3d"`: Always use commons **3d** configurations, regardless of current mode

**Example**:
```yaml
IGX-THOR:
  3d:
    max_streams_supported: 4
    use_commons:
      variable_validation: true  # Use commons 3d validations
      variables: true            # Use commons 3d variables
      file_operations: "3d"      # Always use commons 3d file_operations
```

**Important Notes**:
- Profile-specific variable_validation/variables/file_operations are **APPENDED** to commons (not replaced)
- If you want to completely override commons, set `use_commons` to `false`
- You can mix and match: use common variables but custom file_operations

### variables

**Type**: List of dictionaries  
**Required**: No  
**Purpose**: Define profile-specific computed variables that are appended to common variables

**Format**: Each list item is a dictionary with one key-value pair (variable name and expression)

**Example**:
```yaml
variables:
  - timeout_multiplier: "2"
  - adjusted_timeout: "${timeout_multiplier} * 25000"
  - custom_batch_size: "max(1, ${final_stream_count} - 1)"
```

**What this means**:
- `timeout_multiplier`: A literal value of "2"
- `adjusted_timeout`: Computed by multiplying `timeout_multiplier` by 25000 (result: 50000)
- `custom_batch_size`: Uses the `max` function to ensure batch size is at least 1
- Variables are evaluated **in order**, so later variables can reference earlier ones

### file_operations

**Type**: List of operation objects  
**Required**: No  
**Purpose**: Define profile-specific file operations that are appended to common file operations

**Example**:
```yaml
file_operations:
  - operation_type: "text_config_update"
    target_file: "${DS_CONFIG_DIR}/ds-main-config.txt"
    updates:
      batched-push-timeout: "50000"
```

---

## Operation Types

The system supports four types of file operations. Each operation type is designed for different configuration file formats.

### 1. yaml_update

**Purpose**: Update values in YAML configuration files while preserving structure and comments.

**Required Fields**:
- `operation_type`: Must be `"yaml_update"`
- `target_file`: Path to the YAML file (supports environment variable substitution)
- `updates`: Dictionary of key-value pairs to update

**Features**:
- Supports **dot notation** for nested keys (e.g., `nested.key.path`)
- Automatically converts string numbers to integers/floats when appropriate
- Preserves existing YAML structure and formatting

**Example**:
```yaml
- operation_type: "yaml_update"
  target_file: "${DS_CONFIG_DIR}/config.yaml"
  updates:
    num_sensors: ${final_stream_count}
    enable_debug: false
    nested.config.batch_size: 4
```

**File Transformation**:
```yaml
# Before (config.yaml)
num_sensors: 1
enable_debug: true
nested:
  config:
    batch_size: 1
    other: "value"

# After
num_sensors: 4
enable_debug: false
nested:
  config:
    batch_size: 4
    other: "value"
```

**What each update does**:
- `num_sensors: ${final_stream_count}`: Updates `num_sensors` to the computed `final_stream_count` value
- `enable_debug: false`: Sets the debug flag to false
- `nested.config.batch_size: 4`: Uses dot notation to update a nested value without affecting sibling keys

### 2. text_config_update

**Purpose**: Update key-value pairs in text-based configuration files (`.txt`, `.conf`, `.cfg` files).

**Required Fields**:
- `operation_type`: Must be `"text_config_update"`
- `target_file`: Path to the text configuration file
- `updates`: Dictionary of key-value pairs to update

**Supported Formats**:
- `key=value` (with or without spaces)
- `key: value`
- `key value`

**Example**:
```yaml
- operation_type: "text_config_update"
  target_file: "${DS_CONFIG_DIR}/ds-main-config.txt"
  updates:
    num-source-bins: "0"
    max-batch-size: "${final_stream_count}"
    batched-push-timeout: "50000"
```

**File Transformation**:
```txt
# Before (ds-main-config.txt)
num-source-bins=4
list=rtsp://camera1,rtsp://camera2
max-batch-size=4
batched-push-timeout=40000

# After
num-source-bins=0
list=
max-batch-size=4  # (assuming final_stream_count=4)
batched-push-timeout=50000
```

**What each update does**:
- `num-source-bins: "0"`: Resets the number of source bins to 0
- `max-batch-size: "${final_stream_count}"`: Sets batch size to match the computed stream count
- `batched-push-timeout: "50000"`: Increases the timeout value for better performance

### 3. json_update

**Purpose**: Update values in JSON configuration files with support for nested object updates.

**Required Fields**:
- `operation_type`: Must be `"json_update"`
- `target_file`: Path to the JSON file
- `updates`: Dictionary of key-value pairs to update

**Features**:
- Supports **dot notation** for nested keys
- Automatically preserves JSON structure
- Handles boolean, number, and string values correctly

**Example**:
```yaml
- operation_type: "json_update"
  target_file: "${VST_CONFIG_DIR}/vst-config.json"
  updates:
    data.nv_streamer_sync_file_count: ${final_stream_count}
    overlay.enable_overlay_skip_frame: true
```

**File Transformation**:
```json
// Before (vst-config.json)
{
  "data": {
    "nv_streamer_sync_file_count": 1,
    "other_param": "value"
  },
  "overlay": {
    "enable_overlay_skip_frame": false
  }
}

// After
{
  "data": {
    "nv_streamer_sync_file_count": 4,
    "other_param": "value"
  },
  "overlay": {
    "enable_overlay_skip_frame": true
  }
}
```

**What each update does**:
- `data.nv_streamer_sync_file_count: ${final_stream_count}`: Updates the nested value to match stream count
- `overlay.enable_overlay_skip_frame: true`: Enables frame skipping in overlay rendering for better performance

### 4. file_management

**Purpose**: Perform file system operations like managing the number of files in directories.

**Required Fields**:
- `operation_type`: Must be `"file_management"`
- `target_directories`: List of directory paths to operate on
- `file_management`: Object containing action and parameters

**Supported Actions**:
- `keep_count`: Keep only N files matching a pattern, removing excess files

**Example**:
```yaml
- operation_type: "file_management"
  target_directories:
    - "${MDX_DATA_DIR}/videos/warehouse-2d-app"
    - "${MDX_DATA_DIR}/videos/backup"
  file_management:
    action: "keep_count"
    parameters:
      count: ${final_stream_count}
      pattern: "*.mp4"
```

**What this does**:
- Scans each target directory for files matching the pattern (`*.mp4`)
- Sorts files (for consistent behavior)
- Keeps only the first `count` files
- Removes all other files that match the pattern

**Use Case**:
In 2D mode, sample video files are used as input streams. If the hardware can only support 4 streams but there are 10 sample videos in the directory, this operation removes the excess 6 videos to match the hardware capability.

**Directory Before**:
```
/data/videos/warehouse-2d-app/
├── video1.mp4
├── video2.mp4
├── video3.mp4
├── video4.mp4
├── video5.mp4
├── video6.mp4
├── video7.mp4
├── video8.mp4
├── video9.mp4
└── video10.mp4
```

**Directory After** (with count=4):
```
/data/videos/warehouse-2d-app/
├── video1.mp4
├── video2.mp4
├── video3.mp4
└── video4.mp4
```

---

## Variable System

The configuration system includes a powerful variable system that supports mathematical expressions and environment variable substitution.

### Variable Declaration

Variables are declared as a list of dictionaries, where each dictionary has one key-value pair:

```yaml
variables:
  - final_stream_count: "min(${NUM_STREAMS}, ${max_streams_supported})"
  - batch_size: "max(1, ${final_stream_count})"
  - timeout: "${batch_size} * 10000"
```

### Variable Evaluation Order

Variables are evaluated **sequentially** in the order they are declared:
1. Environment variables are substituted first
2. Each variable can reference previously defined variables
3. Mathematical expressions are evaluated
4. Results are converted to strings and added to the environment

**Example**:
```yaml
variables:
  - multiplier: "2"                    # Evaluated first: multiplier = 2
  - base_value: "${multiplier} * 100"  # Can reference multiplier: base_value = 200
  - final_value: "${base_value} + 50"  # Can reference base_value: final_value = 250
```

### Environment Variable Substitution

Use `${VAR_NAME}` syntax to reference environment variables:

```yaml
variables:
  - stream_count: "${NUM_STREAMS}"              # Use environment variable directly
  - config_dir: "${DS_CONFIG_DIR}/configs"      # Concatenate with strings
  - limited_count: "min(${NUM_STREAMS}, 4)"     # Use in expressions
```

### Supported Mathematical Functions

The variable system supports the following safe functions:

- **`min(a, b, ...)`**: Returns the minimum value
- **`max(a, b, ...)`**: Returns the maximum value
- **`abs(x)`**: Returns the absolute value
- **`round(x)`**: Rounds to the nearest integer
- **`int(x)`**: Converts to integer
- **`float(x)`**: Converts to float

### Supported Mathematical Operators

- **Addition**: `+` (e.g., `"${NUM_STREAMS} + 1"`)
- **Subtraction**: `-` (e.g., `"${MAX_COUNT} - 2"`)
- **Multiplication**: `*` (e.g., `"${BATCH_SIZE} * 1000"`)
- **Division**: `/` (e.g., `"${TOTAL} / ${COUNT}"`)
- **Floor Division**: `//` (e.g., `"${TOTAL} // 2"`)
- **Modulo**: `%` (e.g., `"${NUM} % 10"`)
- **Exponentiation**: `**` (e.g., `"2 ** 8"`)

### Variable Usage in File Operations

Once declared, variables can be used in any string value in file operations:

```yaml
variables:
  - final_stream_count: "min(${NUM_STREAMS}, ${max_streams_supported})"

file_operations:
  - operation_type: "yaml_update"
    target_file: "${DS_CONFIG_DIR}/config.yaml"
    updates:
      num_sensors: ${final_stream_count}  # Use the variable
```

### Literal Values vs Expressions

```yaml
variables:
  - literal_string: "hello"              # Stored as string "hello"
  - literal_number: "42"                 # Evaluated as number 42
  - expression: "min(10, 20)"           # Evaluated as number 10
  - env_var: "${PATH}"                  # Substituted with PATH value
  - mixed: "${NUM_STREAMS} * 2 + 1"     # Expression with env var
```

### Conditional Expressions (If-Else)

The variable system supports Python-style conditional (ternary) expressions, allowing you to set variable values based on conditions.

#### Basic Ternary Expression

```yaml
variables:
  - batch_size: "4 if ${num_cameras} > 10 else 2"
```

**What this means**:
- If `num_cameras` is greater than 10, `batch_size` = 4
- Otherwise, `batch_size` = 2

#### Syntax

```
value_if_true if condition else value_if_false
```

#### Supported Comparison Operators

| Operator | Description | Example |
|----------|-------------|---------|
| `>` | Greater than | `${x} > 10` |
| `<` | Less than | `${x} < 5` |
| `>=` | Greater than or equal | `${x} >= 10` |
| `<=` | Less than or equal | `${x} <= 5` |
| `==` | Equal to | `${x} == 4` |
| `!=` | Not equal to | `${x} != 0` |

#### Supported Logical Operators

| Operator | Description | Example |
|----------|-------------|---------|
| `and` | Logical AND | `${x} > 5 and ${y} < 10` |
| `or` | Logical OR | `${x} == 0 or ${y} == 0` |
| `not` | Logical NOT | `not ${enabled}` |

#### Examples

**Simple Conditional**:
```yaml
variables:
  # Set quality based on GPU memory
  - quality_mode: "high if ${gpu_memory} >= 16 else standard"
```

**Multiple Conditions (AND)**:
```yaml
variables:
  # Enable high quality only if we have enough memory AND few cameras
  - enable_high_quality: "true if ${gpu_memory} >= 16 and ${num_cameras} < 20 else false"
```

**Multiple Conditions (OR)**:
```yaml
variables:
  # Use fallback mode if either condition is met
  - use_fallback: "true if ${mode} == 'legacy' or ${compatibility} == 'true' else false"
```

**Nested Ternary (If-Elif-Else)**:
```yaml
variables:
  # Choose quality level based on batch size
  - quality_level: "high if ${batch_size} >= 4 else medium if ${batch_size} >= 2 else low"
```

**What this means**:
- If `batch_size` >= 4: `quality_level` = "high"
- Else if `batch_size` >= 2: `quality_level` = "medium"
- Else: `quality_level` = "low"

#### Mathematical Expression with Conditional

```yaml
variables:
  # Calculate stream density with conditional logic
  - stream_density: "min(${num_cameras} / 2, ${max_streams_supported})"
  
  # Adjust timeout based on stream count
  - timeout: "50000 if ${stream_density} > 8 else 25000"
  
  # Calculate batch size with multiple factors
  - optimal_batch: "max(1, ${final_stream_count} - 1) if ${mode} == '3d' else ${final_stream_count}"
```

#### Complete Example with Conditionals

```yaml
commons:
  variables:
    2d:
      # Basic computed variable
      - final_stream_count: "min(${NUM_STREAMS}, ${max_streams_supported})"
      
      # Conditional batch size based on camera count
      - batch_size: "4 if ${final_stream_count} > 10 else 2"
      
      # Quality selection with nested conditional
      - quality_level: "ultra if ${final_stream_count} <= 2 else high if ${final_stream_count} <= 4 else standard"
      
      # Enable features based on multiple conditions
      - enable_overlay: "true if ${final_stream_count} <= 8 and ${gpu_memory} >= 8 else false"
      
      # Timeout calculation with conditional multiplier
      - timeout_multiplier: "2 if ${quality_level} == 'ultra' else 1"
      - batched_timeout: "${timeout_multiplier} * 25000"
    
    3d:
      - final_stream_count: "min(${NUM_STREAMS}, ${max_streams_supported})"
      
      # 3D mode has different thresholds
      - batch_size: "2 if ${final_stream_count} > 6 else 1"
      
      # More aggressive timeout for 3D
      - batched_timeout: "67000 if ${final_stream_count} > 4 else 50000"
```

#### Using Conditional Variables in File Operations

```yaml
variables:
  - use_high_performance: "true if ${final_stream_count} > 8 else false"
  - perf_config_file: "ds-high-perf-config.txt if ${use_high_performance} == 'true' else ds-standard-config.txt"

file_operations:
  - operation_type: "text_config_update"
    target_file: "${DS_CONFIG_DIR}/${perf_config_file}"
    updates:
      batch-size: "${batch_size}"
```

#### Important Notes

1. **String vs Number Comparison**: When comparing with strings, use quotes: `${mode} == '3d'`
2. **Boolean Values**: Use `'true'` or `'false'` as strings for boolean comparisons
3. **Evaluation Order**: Conditionals are evaluated after environment variable substitution
4. **Nested Depth**: While nested ternaries work, keep them simple for readability

---

## Variable Validation

The configuration system supports validating environment variables before processing file operations. This ensures that required variables have valid values and helps catch configuration errors early.

### Structure

Variable validation rules are defined in the `variable_validation` section, organized by deployment mode:

```yaml
commons:
  variable_validation:
    2d:
      - variable: VAR_NAME
        allowed_values: ["value1", "value2"]
        error_message: "Custom error message"
    3d:
      - variable: VAR_NAME
        allowed_patterns: ["pattern*"]

# Or in a specific hardware profile:
L4:
  2d:
    variable_validation:
      - variable: CUSTOM_VAR
        allowed_values: ["a", "b", "c"]
```

### Validation Types

The system supports several validation types:

| Type | Description | Use Case |
|------|-------------|----------|
| `allowed_values` | Exact match against a list | Enumerated options (e.g., modes, flags) |
| `allowed_patterns` | Wildcard match (must match one) | Flexible naming conventions |
| `regex` | Regular expression match | Complex format validation |
| `disallowed_values` | Exact match exclusion | Blocking specific values |
| `disallowed_patterns` | Wildcard match exclusion | Blocking patterns (e.g., test paths) |

#### 1. Allowed Values (Exact Match)

Validates that a variable's value exactly matches one of the allowed values:

```yaml
- variable: NIM
  allowed_values: ["none", "local", "remote"]
  error_message: "NIM must be one of: none, local, remote"
```

**Behavior**:
- Case-sensitive comparison
- Value must exactly match one item in the list
- Fails if the value is not in the list

#### 2. Allowed Patterns (Wildcard Match)

Validates that a variable's value matches at least one wildcard pattern:

```yaml
- variable: COMPOSE_PROFILES
  allowed_patterns:
    - "bp_wh_kafka*"
    - "playback_kafka*"
    - "bp_wh*"
  error_message: "COMPOSE_PROFILES must match an allowed pattern"
```

**Supported Wildcards**:
- `*`: Matches any sequence of characters (including empty)
- `?`: Matches any single character

**Examples**:
- Pattern `bp_wh*` matches: `bp_wh`, `bp_wh_kafka`, `bp_wh_redis`, etc.
- Pattern `video?.mp4` matches: `video1.mp4`, `videoA.mp4`, but not `video10.mp4`

#### 3. Regular Expression (Regex Match)

Validates that a variable's value matches a regular expression:

```yaml
- variable: STREAM_ID
  regex: "^stream_[0-9]{3}$"
  error_message: "STREAM_ID must be in format stream_XXX (3 digits)"
```

**Examples**:
- Pattern `^[a-z]+_[0-9]+$` matches: `camera_1`, `sensor_42`, but not `Camera_1` or `cam-1`

#### 4. Disallowed Values (Exact Match)

Validates that a variable's value is NOT one of the disallowed values:

```yaml
- variable: MODE
  disallowed_values: ["deprecated", "legacy", "test"]
  error_message: "MODE cannot be a deprecated value"
```

**Behavior**:
- Case-sensitive comparison
- Value must NOT match any item in the list
- Fails if the value is in the list

#### 5. Disallowed Patterns (Wildcard Match)

Validates that a variable's value does NOT match any of the disallowed wildcard patterns:

```yaml
- variable: CONFIG_PATH
  disallowed_patterns:
    - "/tmp/*"
    - "*/test/*"
    - "*_debug"
  error_message: "CONFIG_PATH cannot be a temporary, test, or debug path"
```

**Supported Wildcards**:
- `*`: Matches any sequence of characters (including empty)
- `?`: Matches any single character

**Examples**:
- Pattern `/tmp/*` disallows: `/tmp/config`, `/tmp/data/file`, etc.
- Pattern `*_debug` disallows: `app_debug`, `config_debug`, etc.
- Pattern `*/test/*` disallows: `/home/test/config`, `app/test/data`, etc.

**Behavior**:
- Value must NOT match any pattern in the list
- Fails if the value matches any pattern
- Useful for blocking certain path patterns, naming conventions, or configurations

### Conditional Validation

You can apply validation rules only when certain conditions are met using the `condition` field:

```yaml
- variable: COMPOSE_PROFILES
  condition:
    variable: STREAM_TYPE
    equals: "redis"
  allowed_patterns:
    - "bp_wh_kafka*"
    - "playback_kafka*"
    - "bp_wh*"
  error_message: "When STREAM_TYPE=redis, COMPOSE_PROFILES must match allowed patterns"
```

**What this means**:
- The validation for `COMPOSE_PROFILES` only runs if `STREAM_TYPE` equals `"redis"`
- If `STREAM_TYPE` has any other value (or is not set), this validation is skipped

### Condition Operators

The `condition` field supports several comparison operators:

| Operator | Description | Example |
|----------|-------------|---------|
| `equals` | Variable equals a specific value | `equals: "redis"` |
| `not_equals` | Variable does not equal a value | `not_equals: "none"` |
| `in` | Variable is in a list of values | `in: ["a", "b", "c"]` |
| `not_in` | Variable is not in a list | `not_in: ["x", "y"]` |
| `matches` | Variable matches a wildcard pattern | `matches: "prod_*"` |
| `regex` | Variable matches a regular expression | `regex: "^v[0-9]+_.*$"` |
| `is_set` | Variable is set (or not set) | `is_set: true` |

**Condition Examples**:

```yaml
# Only validate if STREAM_TYPE equals "redis"
condition:
  variable: STREAM_TYPE
  equals: "redis"

# Only validate if MODE is NOT "test"
condition:
  variable: MODE
  not_equals: "test"

# Only validate if ENV is one of production environments
condition:
  variable: ENV
  in: ["prod", "staging", "production"]

# Only validate if FEATURE_FLAG is set
condition:
  variable: FEATURE_FLAG
  is_set: true

# Only validate if CONFIG matches a wildcard pattern
condition:
  variable: CONFIG
  matches: "v2_*"

# Only validate if VERSION matches a regex pattern
condition:
  variable: VERSION
  regex: "^[0-9]+\\.[0-9]+\\.[0-9]+$"
```

### Compound Conditions (AND/OR)

For complex validation scenarios, you can combine multiple conditions using `and` and `or` operators:

#### AND Condition

All conditions must be true for the validation to apply:

```yaml
- variable: COMPOSE_PROFILES
  condition:
    and:
      - variable: MODE
        equals: "2d"
      - variable: STREAM_TYPE
        equals: "kafka"
  allowed_patterns:
    - "bp_wh_kafka_2d*"
  error_message: "When MODE=2d AND STREAM_TYPE=kafka, COMPOSE_PROFILES must match bp_wh_kafka_2d*"
```

#### OR Condition

At least one condition must be true for the validation to apply:

```yaml
- variable: CONFIG_PATH
  condition:
    or:
      - variable: ENV
        equals: "production"
      - variable: ENV
        equals: "staging"
  disallowed_patterns:
    - "/tmp/*"
  error_message: "In production/staging, CONFIG_PATH cannot be a temporary path"
```

#### Nested AND/OR Conditions

You can nest `and` and `or` conditions for complex logic:

```yaml
# Validation applies when: (MODE=2d AND STREAM_TYPE=kafka) OR (MODE=3d AND STREAM_TYPE=redis)
- variable: CUSTOM_CONFIG
  condition:
    or:
      - and:
          - variable: MODE
            equals: "2d"
          - variable: STREAM_TYPE
            equals: "kafka"
      - and:
          - variable: MODE
            equals: "3d"
          - variable: STREAM_TYPE
            equals: "redis"
  allowed_values: ["enabled", "disabled"]
```

#### OR Inside AND (Common Pattern)

A common use case is validating a variable when multiple conditions are met, with one condition having multiple acceptable values:

```yaml
# When SAMPLE_VIDEO_DATASET=warehouse-4cams, validate:
# - MODE must be 3d
# - NUM_STREAMS must be 4
# - COMPOSE_PROFILES must match kafka OR redis pattern
- variable: SAMPLE_VIDEO_DATASET
  when_equals: "warehouse-4cams-20mx20m-synthetic"
  validate_conditions:
    and:
      - variable: MODE
        equals: "3d"
      - variable: NUM_STREAMS
        equals: "4"
      - or:
          - variable: COMPOSE_PROFILES
            matches: "bp_wh_kafka_3d,*"
          - variable: COMPOSE_PROFILES
            matches: "bp_wh_redis_3d,*"
  error_message: "When using warehouse-4cams dataset, MODE must be 3d, NUM_STREAMS must be 4, and COMPOSE_PROFILES must match kafka or redis pattern"
```

### Trigger-Based Validation (when_equals + validate_conditions)

For scenarios where you want to validate that **other variables have correct values** when a specific variable has a certain value, use the `when_equals` and `validate_conditions` pattern:

#### Syntax

```yaml
- variable: TRIGGER_VAR
  when_equals: "trigger_value"
  validate_conditions:
    and:
      - variable: OTHER_VAR1
        equals: "expected_value1"
      - variable: OTHER_VAR2
        matches: "pattern*"
  error_message: "Custom error message"
```

#### How It Works

1. **Check Trigger**: If `TRIGGER_VAR` does NOT equal `"trigger_value"`, skip this validation entirely
2. **Validate Conditions**: If `TRIGGER_VAR` equals `"trigger_value"`, evaluate the `validate_conditions`
3. **Pass/Fail**: If all conditions in `validate_conditions` are met, validation passes; otherwise, it fails

#### Example: Dataset-Dependent Validation

```yaml
# When using the nv-warehouse-4cams dataset, ensure correct configuration
- variable: SAMPLE_VIDEO_DATASET
  when_equals: "nv-warehouse-4cams"
  validate_conditions:
    and:
      - variable: MODE
        equals: "2d"
      - variable: NUM_STREAMS
        equals: "4"
      - variable: COMPOSE_PROFILES
        matches: "bp_wh_2d,*"
  error_message: "When SAMPLE_VIDEO_DATASET=nv-warehouse-4cams, MODE must be 2d, NUM_STREAMS must be 4, and COMPOSE_PROFILES must start with bp_wh_2d"
```

**Logic**:
- IF `SAMPLE_VIDEO_DATASET` = `"nv-warehouse-4cams"` THEN:
  - `MODE` must equal `"2d"` AND
  - `NUM_STREAMS` must equal `"4"` AND
  - `COMPOSE_PROFILES` must match `"bp_wh_2d,*"`
- IF `SAMPLE_VIDEO_DATASET` ≠ `"nv-warehouse-4cams"`: Skip validation

#### Example: Multiple Dataset Validations

```yaml
commons:
  variable_validation:
    2d:
      # 4-camera warehouse dataset requires specific configuration
      - variable: SAMPLE_VIDEO_DATASET
        when_equals: "nv-warehouse-4cams"
        validate_conditions:
          and:
            - variable: MODE
              equals: "2d"
            - variable: NUM_STREAMS
              equals: "4"
            - variable: COMPOSE_PROFILES
              matches: "bp_wh_2d,*"
      
      # 3-camera loading dock dataset has different requirements
      - variable: SAMPLE_VIDEO_DATASET
        when_equals: "warehouse-loading-dock-3cams-synthetic"
        validate_conditions:
          and:
            - variable: MODE
              equals: "2d"
            - variable: NUM_STREAMS
              equals: "3"
            - or:
                - variable: COMPOSE_PROFILES
                  matches: "bp_wh_kafka_2d,*"
                - variable: COMPOSE_PROFILES
                  matches: "bp_wh_redis_2d,*"
    
    3d:
      # 3D warehouse dataset validation
      - variable: SAMPLE_VIDEO_DATASET
        when_equals: "warehouse-4cams-20mx20m-synthetic"
        validate_conditions:
          and:
            - variable: MODE
              equals: "3d"
            - variable: NUM_STREAMS
              equals: "4"
            - or:
                - variable: COMPOSE_PROFILES
                  matches: "bp_wh_kafka_3d,*"
                - variable: COMPOSE_PROFILES
                  matches: "bp_wh_redis_3d,*"
```

#### Comparison: condition vs when_equals

| Pattern | Use Case | Logic |
|---------|----------|-------|
| `condition` + `allowed_values` | Validate THIS variable when OTHER conditions are met | IF (conditions) THEN validate variable's value |
| `when_equals` + `validate_conditions` | Validate OTHER variables when THIS variable has a specific value | IF (this variable = value) THEN validate other conditions |

**Example - Using `condition`**:
```yaml
# Validate COMPOSE_PROFILES when MODE=2d
- variable: COMPOSE_PROFILES
  condition:
    variable: MODE
    equals: "2d"
  allowed_patterns: ["bp_wh_2d*"]
```

**Example - Using `when_equals`**:
```yaml
# When SAMPLE_VIDEO_DATASET=nv-warehouse-4cams, validate MODE, NUM_STREAMS, and COMPOSE_PROFILES
- variable: SAMPLE_VIDEO_DATASET
  when_equals: "nv-warehouse-4cams"
  validate_conditions:
    and:
      - variable: MODE
        equals: "2d"
      - variable: NUM_STREAMS
        equals: "4"
```

### Validation Options

#### required

Controls whether validation fails when the variable is not set:

```yaml
- variable: OPTIONAL_VAR
  required: false              # Skip validation if variable is not set
  allowed_values: ["a", "b"]

- variable: REQUIRED_VAR
  required: true               # Fail if variable is not set (default)
  allowed_values: ["x", "y"]
```

**Default**: `true` (variable must be set)

#### error_message

Provides a custom error message when validation fails:

```yaml
- variable: NIM
  allowed_values: ["none", "local", "remote"]
  error_message: "NIM configuration error: value must be 'none', 'local', or 'remote'. Check your environment settings."
```

**Default**: Auto-generated message describing the validation failure

### Use Commons Behavior for Validation

The `use_commons` field also applies to variable validation:

```yaml
CUSTOM_GPU:
  2d:
    max_streams_supported: 4
    use_commons:
      variable_validation: false  # Don't use commons validations
    variable_validation:
      - variable: CUSTOM_VAR
        allowed_values: ["special"]
```

**Values**:
- `true` or `""` (empty/not specified): Use commons validations for the current deployment mode
- `false`: Don't use commons validations, only use profile-specific
- `"2d"`: Use commons 2d validations regardless of current mode
- `"3d"`: Use commons 3d validations regardless of current mode

### Complete Validation Examples

#### Example 1: Simple Allowed Values

```yaml
commons:
  variable_validation:
    2d:
      - variable: NIM
        required: false
        allowed_values: ["none", "local", "remote"]
        error_message: "NIM must be one of: none, local, remote"
```

#### Example 2: Conditional Pattern Validation

```yaml
commons:
  variable_validation:
    2d:
      - variable: COMPOSE_PROFILES
        required: false
        condition:
          variable: STREAM_TYPE
          equals: "redis"
        allowed_patterns:
          - "bp_wh_kafka*"
          - "playback_kafka*"
          - "bp_wh*"
        error_message: "When STREAM_TYPE=redis, COMPOSE_PROFILES must match allowed patterns"
```

#### Example 3: Multiple Validations with Different Conditions

```yaml
commons:
  variable_validation:
    3d:
      # Always validate NIM
      - variable: NIM
        allowed_values: ["none", "local", "remote"]
      
      # Validate API_KEY only in production
      - variable: API_KEY
        condition:
          variable: ENV
          equals: "production"
        regex: "^[A-Za-z0-9]{32}$"
        error_message: "Production API_KEY must be 32 alphanumeric characters"
      
      # Validate DEBUG is not enabled in production
      - variable: DEBUG
        condition:
          variable: ENV
          equals: "production"
        disallowed_values: ["true", "1", "yes"]
        error_message: "DEBUG must be disabled in production"
```

#### Example 4: Profile-Specific Validation

```yaml
IGX-THOR:
  3d:
    max_streams_supported: 6
    variable_validation:
      # IGX-THOR specific validation
      - variable: MEMORY_MODE
        allowed_values: ["high", "ultra"]
        error_message: "IGX-THOR requires MEMORY_MODE to be 'high' or 'ultra'"
```

#### Example 5: Disallowed Patterns Validation

```yaml
commons:
  variable_validation:
    3d:
      # Block temporary and test paths
      - variable: CONFIG_PATH
        disallowed_patterns:
          - "/tmp/*"
          - "*/test/*"
          - "*_backup"
        error_message: "CONFIG_PATH cannot be a temporary, test, or backup path"
      
      # Block debug and development configurations
      - variable: PROFILE_NAME
        disallowed_patterns:
          - "*_debug"
          - "*_dev"
          - "test_*"
        error_message: "Production environment cannot use debug/dev/test profiles"
```

### Validation Execution Order

Variable validation runs in the following order:

1. **Environment variables loaded** from the system
2. **Variable validation executed** (validates raw environment variables)
3. **Prerequisites executed** (e.g., file counts)
4. **Config variables processed** (computed variables)
5. **File operations executed**

This means validation checks the **original** environment variable values before any computed variables are created.

### Error Handling

When validation fails:

1. Error messages are logged for each failed validation
2. All validations are checked (not stopped at first failure)
3. A summary of all failures is reported
4. The configurator continues execution (warnings only by default)

**Example Log Output**:
```
ERROR - Validation failed: NIM must be one of: none, local, remote
ERROR - Validation failed: When STREAM_TYPE=redis, COMPOSE_PROFILES must match allowed patterns
ERROR - Variable validation completed with 2 error(s)
```

---

## Environment Variables

### Automatic Environment Variables

The system automatically adds certain configuration values to the environment:

- **`max_streams_supported`**: Automatically added from the profile configuration
- **All declared variables**: Variables from `commons` and profile-specific `variables` sections

### Common Environment Variables

The following environment variables are typically available and used in configurations:

| Variable | Description | Example Value |
|----------|-------------|---------------|
| `HARDWARE_PROFILE` | GPU hardware type | `H100`, `L4`, `RTXA6000` |
| `MODE` | Deployment mode | `2d`, `3d` |
| `NUM_STREAMS` | Desired number of streams (user input) | `4`, `8`, `16` |
| `DS_CONFIG_DIR` | DeepStream config directory | `/opt/deepstream/configs` |
| `VST_CONFIG_DIR` | VST config directory | `/opt/vst/configs` |
| `MDX_DATA_DIR` | MDX data directory | `/opt/mdx/data` |
| `MDX_SAMPLE_APPS_DIR` | MDX sample apps directory | `/opt/mdx/apps` |

### Using Environment Variables

Reference environment variables anywhere in the configuration using `${VAR_NAME}`:

```yaml
# In file paths
target_file: "${DS_CONFIG_DIR}/config.yaml"

# In values
updates:
  config_path: "${HOME}/configs"

# In expressions
variables:
  - adjusted_count: "min(${NUM_STREAMS}, 4)"
```

---

## Use Commons Behavior

The `use_commons` field provides fine-grained control over configuration inheritance.

### Default Behavior

If `use_commons` is not specified, the default behavior is:
- Use commons **variable_validation** for the current deployment mode (2d or 3d)
- Use commons **variables** for the current deployment mode (2d or 3d)
- Use commons **file_operations** for the current deployment mode

```yaml
L4:
  3d:
    max_streams_supported: 2
    # Implicitly uses commons.variable_validation.3d, commons.variables.3d, and commons.file_operations.3d
```

### Explicit Control

You can explicitly control inheritance for variable_validation, variables, and file_operations separately:

```yaml
L4:
  3d:
    max_streams_supported: 2
    use_commons:
      variable_validation: true  # Use commons 3d validations
      variables: true            # Use commons 3d variables
      file_operations: false     # Don't use any commons file_operations
```

### Cross-Mode Inheritance

You can use commons from a different deployment mode:

```yaml
CUSTOM_GPU:
  2d:
    max_streams_supported: 8
    use_commons:
      variables: "3d"        # Use commons 3d variables in 2d mode
      file_operations: "2d"  # Use commons 2d file_operations
```

### Complete Override

To completely override commons and use only profile-specific configurations:

```yaml
CUSTOM_GPU:
  3d:
    max_streams_supported: 4
    use_commons:
      variable_validation: false  # Don't use any commons validations
      variables: false            # Don't use any commons variables
      file_operations: false      # Don't use any commons file_operations
    variable_validation:
      - variable: CUSTOM_VAR
        allowed_values: ["special"]
    variables:
      - custom_var: "value"
    file_operations:
      - operation_type: "yaml_update"
        # ... custom operations only
```

### Append Behavior

**Important**: When `use_commons` is `true` or a mode string (`"2d"`/`"3d"`), profile-specific configurations are **APPENDED**, not replaced:

```yaml
commons:
  variable_validation:
    3d:
      - variable: NIM
        allowed_values: ["none", "local", "remote"]
  variables:
    3d:
      - var_a: "1"
      - var_b: "2"

L4:
  3d:
    max_streams_supported: 2
    use_commons:
      variable_validation: true
      variables: true
    variable_validation:
      - variable: CUSTOM_VAR       # This is APPENDED to NIM validation
        allowed_values: ["x", "y"]
    variables:
      - var_c: "3"  # This is APPENDED to var_a and var_b

# Result: L4 3d has NIM validation + CUSTOM_VAR validation, and var_a, var_b, var_c
```

---

## Complete Examples

### Example 1: Simple Profile Using All Commons

This is the most common pattern - use all commons with just a custom `max_streams_supported`:

```yaml
H100:
  2d:
    max_streams_supported: 4
    # Implicitly uses all commons for 2d
  
  3d:
    max_streams_supported: 4
    # Implicitly uses all commons for 3d
```

**Behavior**:
- Uses `commons.variables.2d` for 2d mode
- Uses `commons.file_operations.2d` for 2d mode
- Uses `commons.variables.3d` for 3d mode
- Uses `commons.file_operations.3d` for 3d mode

### Example 2: Profile with Additional File Operations

Add GPU-specific operations while still using commons:

```yaml
IGX-THOR:
  3d:
    max_streams_supported: 4
    # Implicitly uses commons 3d variables and file_operations
    file_operations:
      # These are APPENDED to commons.file_operations.3d
      - operation_type: "text_config_update"
        target_file: "${DS_CONFIG_DIR}/ds-main-config.txt"
        updates:
          batched-push-timeout: "50000"
      
      - operation_type: "json_update"
        target_file: "${MDX_SAMPLE_APPS_DIR}/services/vios/configs/vst_config_kafka.json"
        updates:
          overlay.enable_overlay_skip_frame: true
```

**Behavior**:
1. First executes all operations from `commons.file_operations.3d`
2. Then executes the IGX-THOR specific operations
3. Uses `commons.variables.3d` for variable definitions

### Example 3: Profile with Custom Variables

Add custom variables for complex calculations:

```yaml
L4:
  3d:
    max_streams_supported: 2
    use_commons:
      variables: true
      file_operations: true
    variables:
      # Additional variables appended to commons variables
      - timeout_multiplier: "2"
      - adjusted_timeout: "${timeout_multiplier} * 25000"
      - batch_multiplier: "max(1, ${final_stream_count} - 1)"
    file_operations:
      # Use the custom variables
      - operation_type: "text_config_update"
        target_file: "${DS_CONFIG_DIR}/ds-main-config.txt"
        updates:
          batched-push-timeout: "${adjusted_timeout}"
          batch-multiplier: "${batch_multiplier}"
```

**Behavior**:
1. Loads `commons.variables.3d` (including `final_stream_count`)
2. Appends L4-specific variables (`timeout_multiplier`, `adjusted_timeout`, `batch_multiplier`)
3. Variables are evaluated in order, so `adjusted_timeout` can reference `timeout_multiplier`
4. Executes `commons.file_operations.3d` first
5. Executes L4-specific file operations using the custom variables

### Example 4: Completely Custom Profile

Override all commons for a highly customized profile:

```yaml
CUSTOM_GPU:
  2d:
    max_streams_supported: 8
    use_commons:
      variables: false        # Don't use any commons
      file_operations: false
    variables:
      # Define all variables from scratch
      - custom_stream_count: "min(${NUM_STREAMS}, 8)"
      - custom_batch: "${custom_stream_count} * 2"
    file_operations:
      # Define all operations from scratch
      - operation_type: "yaml_update"
        target_file: "${DS_CONFIG_DIR}/custom-config.yaml"
        updates:
          streams: ${custom_stream_count}
          batch_size: ${custom_batch}
```

**Behavior**:
- Completely ignores commons
- Uses only the custom variables and file operations defined in the profile
- Useful for experimental or special-purpose hardware

### Example 5: Cross-Mode Commons Usage

Use commons from a different mode:

```yaml
SPECIAL_GPU:
  2d:
    max_streams_supported: 4
    use_commons:
      variables: "3d"        # Use 3d variables in 2d mode
      file_operations: "2d"  # Use 2d file_operations (normal)
```

**Behavior**:
- Uses `commons.variables.3d` even though we're in 2d mode
- Uses `commons.file_operations.2d` as expected
- Can be useful if 3d variable logic applies to a special 2d scenario
