# Frame State Management - Incident Detection

> Part of behavior-analytics docs. See `../README.md` for the project overview.

## Overview

The `frame_state_management.py` module processes **enhanced frames** to detect violations and generate incidents when violations persist beyond configured thresholds.

## Data Pipeline

**Raw Frame** (objects) → **Frame Enhancement** (adds violation data) → **This Module** (tracks states & generates incidents)

## Key Concepts

**Enhanced Frame** → Frame with pre-calculated violation data (proximity, ROI violations, FOV metrics, etc.)  
**Violation** → Detected event from enhanced frame (e.g., people too close, unauthorized area access, objects in FOV more than a threshold)
**Violation State** → Internal tracking of violation duration  
**Incident** → Violation that exceeds time threshold and needs reporting  

## Four Violation Types (from Enhanced Frames)

The frame enhancement pre-calculates these violations and embeds them in the frame data:

### 1. Proximity Violations
- **Source field**: `frame.socialDistancing.info["proximityViolationObjects"]`
- **Detects**: Objects too close to each other
- **Use case**: Social distancing, collision prevention
- **Data format**: `"object1,object2|object3,object4"` (groups separated by `|`)
- **Incident name**: Proximity Violation
- **Primary object**: First object in each group

### 2. Restricted Area Violations  
- **Source field**: `frame.rois[].info["restrictedAreaViolation"]` and `frame.rois[].objectIds`
- **Detects**: Objects entering prohibited zones (ROIs)
- **Use case**: Unauthorized access to dangerous/secure areas
- **Tracked by**: Object ID + ROI ID combination
- **Incident name**: Restricted Area Violation
- **Primary object**: Each individual object in ROI

### 3. Confined Area Violations
- **Source field**: `frame.info["confinedAreaViolationObjects"]`
- **Detects**: Objects leaving designated safe zones
- **Use case**: Ensuring people/assets stay in safe areas
- **Data format**: `"object1,object2|object3"` (objects outside area)
- **Incident name**: Confined Area Violation
- **Primary object**: Each individual object outside area

### 4. FOV Count Violations (NEW)
- **Source field**: `frame.fov[]` (TypeMetrics with type, count, objectIds)
- **Detects**: Too many objects of a specific type in field of view
- **Use case**: Crowd control, capacity management, safety monitoring
- **Tracked by**: Sensor ID only (aggregate violation)
- **Incident name**: FOV Count Violation
- **Primary object**: None (aggregate violation for entire sensor)
- **Special characteristics**:
  - Single violation state per sensor (not per object)
  - No primary object ID (violation_id = sensor_id)
  - Monitors specific object type (e.g., "Person")

## Time Parameters

Each violation type has configurable timing parameters:

| Parameter | Purpose | Typical Value |
|-----------|---------|---------------|
| **Expiration Window** | Max gap between detections before creating new state | 0.5-2 sec |
| **Incident Threshold** | Min duration to become reportable incident | 0.1-3 sec |

## Configuration

### Frame Enhancement Configuration (Violation Detection)
These settings control how violations are detected during frame enhancement:

```python
# Proximity Detection
config.proximityDetectionEnable = True              # Enable proximity detection
config.proximityDetectionThreshold = 1.8            # Distance threshold in meters
config.proximityDetectionCenterClasses = ["Person"]       # Center object types
config.proximityDetectionSurroundingClasses = ["Person", "Forklift"]  # Surrounding objects to check proximity with

# ROI Configuration (in calibration file)
# Restricted Area: Objects that should NOT be in certain ROIs
roi.restrictedObjectTypes = ["Person", "Vehicle"]

# Confined Area: Objects that should STAY within certain ROIs  
roi.confinedObjectTypes = ["Forklift", "Robot"]

# FOV Metrics are automatically calculated for all object types in view
```

### Incident Generation Configuration
These settings control when violations become reportable incidents. All incident enables default to **false**—set them to `true` to activate.

```python
# Enable/Disable incident generation (defaults are false)
config.proximity_violation_incident_enable = True
config.restricted_area_violation_incident_enable = True  
config.confined_area_violation_incident_enable = True
config.fov_count_violation_incident_enable = True  # NEW

# Time thresholds (in seconds)
# Example for proximity violations
config.proximity_violation_incident_expiration_window = 0.2  # Max gap between detections
config.proximity_violation_incident_threshold = 0.5         # Min duration to become incident

# FOV Count Violation specific settings
config.fov_count_violation_incident_enable = True
config.fov_count_violation_incident_object_threshold = 3   # Trigger when >= 3 objects
config.fov_count_violation_incident_threshold = 2.0       # Duration before incident
config.fov_count_violation_incident_expiration_window = 1.0  # Max gap
config.fov_count_violation_incident_object_type = "Person"   # Object type to monitor
```

## Usage

```python
from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.stream.state.frame.frame_state_management import FrameStateMgmt

# Initialize
config = AppConfig()
frame_manager = FrameStateMgmt(config)

# List of enhanced frames with violation data from frame enhancement process
enhanced_frames = [...]
frame_manager.update_frames("sensor_001", enhanced_frames)

# Get all incidents
incidents = frame_manager.get_incidents("sensor_001")

# Get specific types
proximity_incidents = frame_manager.get_proximity_violation_incidents("sensor_001")
restricted_incidents = frame_manager.get_restricted_area_violation_incidents("sensor_001")
confined_incidents = frame_manager.get_confined_area_violation_incidents("sensor_001")
fov_count_incidents = frame_manager.get_fov_count_violation_incidents("sensor_001")  # NEW
```

## Incident Structure

### Standard Violations (Object-specific)
```python
Incident(
    timestamp=datetime,              # Start time
    end=datetime,                   # End time  
    sensorId="sensor_001",          # Sensor ID
    objectIds=["obj1", "obj2"],     # Objects involved
    category="Proximity Violation",  # Incident type
    place=Place(name="Location"),    # Location name
    info={
        "primaryObjectId": "obj1",   # Primary object
        "roiId": "zone_1",           # For restricted area only
        "isComplete": "true"           # Present for completed violations
    }
)
```

### FOV Count Violation (Aggregate)
```python
Incident(
    timestamp=datetime,              # Start time
    end=datetime,                   # End time  
    sensorId="sensor_001",          # Sensor ID
    objectIds=["p1", "p2", "p3"],   # All objects exceeding threshold
    category="FOV Count Violation", # Incident type
    place=Place(name="Location"),   # Location name
    info={
        # No primary object ID for aggregate violations
        "isComplete": "true",          # Present for completed violations
        # objectTimeline: JSON string of start/end intervals per object during the violation window
        "objectTimeline": '{"p1": [{"start": "...", "end": "..."}], "p2": [{"start": "...", "end": "..."}]}'
    }
)
```

## Data Flow

```
Raw Frames (objects) → Frame Enhancement (calculates violations & FOV metrics) → 
Enhanced Frames → This Module (update_frames):
  1. Filter frames by time
  2. For each frame:
     a. Complete expired violations (move to completed_states if gap > expiration_window)
     b. Extract and update violation states from frame fields
  3. When get_incidents is called:
     a. Generate incidents from active states (if duration >= threshold)
     b. Generate incidents from completed states (if duration >= threshold)
     c. Clear processed completed states
```

## State Management Details

### Object-Specific Violations (Proximity, Restricted, Confined)
- States tracked in dictionary: `{violation_id: IncidentState}`
- Violation ID format: `"sensor_id #-# primary_object_id"`
- Multiple concurrent violations per sensor

### FOV Count Violations
- Single state per sensor: `IncidentState`
- Violation ID = sensor_id (no primary object)
- Object IDs accumulate while the violation is continuous (within the expiration window); new state starts after a gap beyond the expiration window.
- Intervals per object are tracked in `object_presence` and emitted as `objectTimeline` on incidents.
- Completed violations are automatically cleaned up when `get_incidents` is called.

## Common Issues

| Problem | Solution |
|---------|----------|
| Too many false incidents | Increase incident threshold |
| Missed violations | Decrease expiration window |
| No incidents detected | Check if enabled in config |
| FOV count not triggering | Verify object type matches config |
| FOV triggering too often | Increase count threshold or duration |

## JSON Configuration Example

### App Configuration (config.json)
```json
{
  "sensors": [{
    "id": "default",
    "configs": [
      {
        "name": "proximityDetectionEnable",
        "value": "true"
      },
      {
        "name": "proximityDetectionThreshold",
        "value": "1.8"
      },
      {
        "name": "proximityDetectionCenterClasses",
        "value": "[\"Person\"]"
      },
      {
        "name": "proximityDetectionSurroundingClasses",
        "value": "[\"Person\", \"Forklift\"]"
      }
    ]
  }],
  "app": [
    {
      "name": "proximityViolationIncidentEnable",
      "value": "true"
    },
    {
      "name": "proximityViolationIncidentExpirationWindow",
      "value": "0.2"
    },
    {
      "name": "proximityViolationIncidentThreshold",
      "value": "0.5"
    },
    {
      "name": "restrictedAreaViolationIncidentEnable",
      "value": "true"
    },
    {
      "name": "restrictedAreaViolationIncidentThreshold",
      "value": "0.1"
    },
    {
      "name": "restrictedAreaViolationIncidentExpirationWindow",
      "value": "0.5"
    },
    {
      "name": "fovCountViolationIncidentEnable",
      "value": "true"
    },
    {
      "name": "fovCountViolationIncidentObjectThreshold",
      "value": "3"
    },
    {
      "name": "fovCountViolationIncidentThreshold",
      "value": "2.0"
    },
    {
      "name": "fovCountViolationIncidentExpirationWindow",
      "value": "1.0"
    },
    {
      "name": "fovCountViolationIncidentObjectType",
      "value": "Person"
    }
  ]
}
```

### Calibration Configuration (calibration.json)
```json
{
  "sensors": [{
    "id": "sensor_001",
    "rois": [
      {
        "id": "restricted_zone_1",
        "restrictedObjectTypes": ["Person", "Vehicle"],
        "roiCoordinates": [...]
      },
      {
        "id": "confined_zone_1",
        "confinedObjectTypes": ["Forklift", "Robot"],
        "roiCoordinates": [...]
      }
    ]
  }]
}
```

## Implementation Notes

### When to Use Each Violation Type
- **Proximity**: Prevent collisions, maintain social distancing
- **Restricted Area**: Keep unauthorized people/objects out of dangerous zones
- **Confined Area**: Ensure critical assets stay in designated areas
- **FOV Count**: Monitor crowd density, occupancy limits, or equipment counts