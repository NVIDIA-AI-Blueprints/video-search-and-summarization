# Calibration Data Management - Export Functionalities

This module provides comprehensive export capabilities for calibration data, matching the ReactJS implementation. All export functions are now implemented in TypeScript with proper error handling and user feedback.

## Export Functionalities

### 1. SENSORS - Export Sensor Calibrations
- **Purpose**: Exports calibration JSON for all calibrated and validated sensors
- **Output**: `calibration.json`
- **Content**: Complete calibration data including coordinates, metadata, ROIs, and settings
- **Supported Calibration Types**: image, cartesian, geo
- **Availability**: Only when there are calibrated and validated sensors

### 2. CAMERAS - Export Sensor Details  
- **Purpose**: Exports sensor metadata as CSV for integration with external systems
- **Output**: `sensorMetadata.csv`
- **Content**: sensorId, rtspURL, protocol, host, type, fps, deviceId, videoURL, depth, fieldOfView, direction
- **Supported Calibration Types**: All
- **Availability**: Only when there are calibrated and validated sensors

### 3. INTERSECTIONS - Export Intersection Road Networks
- **Purpose**: Exports road network JSON for intersection data
- **Output**: `roadNetwork.json`
- **Content**: Intersection names and road segments for geo-spatial calibration
- **Supported Calibration Types**: geo only
- **Availability**: Only visible and available for geo calibration type

### 4. Warped Images - Download Warped Images
- **Purpose**: Downloads warped/transformed images as a ZIP file
- **Output**: `Warped Images.zip`
- **Content**: Warped images from cartesian calibration
- **Supported Calibration Types**: cartesian only
- **Availability**: Only visible and available for cartesian calibration type

### 5. Get Images - Download All Images
- **Purpose**: Downloads all calibration images as a ZIP file
- **Output**: `Images.zip`  
- **Content**: All images used in calibration process
- **Supported Calibration Types**: All
- **Availability**: Always available

### 6. Get Image Metadata - Download Image Metadata
- **Purpose**: Exports image metadata as JSON for MDX Web API
- **Output**: `imageMetadata.json`
- **Content**: Image filenames, sensor IDs, view types (camera-view, warped-camera-view, plan-view)
- **Supported Calibration Types**: All (different metadata based on type)
- **Availability**: Always available

### 7. Export to MDX WEB/API - Upload to Web API
- **Purpose**: Uploads calibration data to Metropolis Web API server
- **Output**: Direct upload to configured server
- **Content**: Calibration JSON and metadata
- **Supported Calibration Types**: All
- **Availability**: Only when there are calibrated and validated sensors

## Technical Implementation

### File Structure
```
vst-ui-ts/src/pages/vst/calibration-steps/
├── CalibrationDataManagement.tsx     # Main component
├── hooks/
│   └── useExportFunctions.ts         # Export functionality hooks
├── utils/
│   └── downloadUtils.ts              # File download utilities
└── README.md                         # This documentation
```

### Key Features
1. **Unified Error/Success Handling**: All export functions provide consistent user feedback
2. **Loading States**: Visual indicators during export operations
3. **Conditional Rendering**: Export options only show when relevant (e.g., warped images for cartesian)
4. **Responsive Design**: Modern Material-UI layout matching the ReactJS design
5. **Type Safety**: Full TypeScript implementation with proper type definitions

### API Endpoints Used
- `GET /api/projects/{id}/` - Project and sensor data
- `GET /api/getWarpedFiles/{id}/` - Warped images ZIP
- `GET /api/getImageFiles/{id}/` - All images ZIP  
- `GET /api/uploadWebApi/{id}/` - Web API upload

### Dependencies
- Material-UI components for UI
- Axios for HTTP requests
- Custom download utilities for file handling
- Project type definitions

## Usage Example

```typescript
import { CalibrationDataManagement } from './CalibrationDataManagement';

<CalibrationDataManagement 
    project={selectedProject}
/>
```

The component automatically handles all export functionalities based on the project's calibration type and sensor states. 