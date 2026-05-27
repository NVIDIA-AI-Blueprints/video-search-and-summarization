# Analytics Components - Coordinate Transformation System

## Overview

This system handles coordinate transformations between **world coordinates** (real-world measurements in meters) and **image coordinates** (pixel coordinates on camera frames) for ROI and Tripwire management in computer vision applications.

## Two Calibration Scenarios

The system supports two different calibration scenarios:

### 1. **2D Calibration (No Homography Matrix)**
- **Condition**: `calibrationType === 'cartesian' && !sensorData.homography`
- **Data Source**: Uses `imageCoordinates` and `globalCoordinates` arrays (4 point correspondences)
- **Method**: Calculates homography matrix on-the-fly using 4-point perspective transformation
- **Use Case**: When calibration data only provides reference point correspondences

### 2. **3D Calibration (Pre-computed Homography)**
- **Condition**: `sensorData.homography` exists
- **Data Source**: Uses pre-calculated 3x3 homography matrix from calibration system
- **Method**: Uses existing `Calibration` and `ReverseCalibration` classes
- **Use Case**: When full 3D calibration with homography matrix is available

**This README focuses on Case 1 (2D Calibration)** - the mathematical implementation for when no homography matrix is provided.

## Mathematical Foundation

### Problem Statement

Camera systems capture real-world scenes and map them to 2D image planes. Due to camera position, angle, and lens characteristics, this mapping involves:
- **Translation** (camera position offset)
- **Rotation** (camera orientation)
- **Scaling** (distance and zoom effects)
- **Perspective distortion** (non-perpendicular viewing angle)

### Coordinate Systems

1. **World Coordinates**: Real-world measurements in meters
   - Origin: Defined calibration point in physical space
   - Units: Meters (x, y, z)
   - Example: `(4.75, 1.55)` = 4.75m east, 1.55m north from origin

2. **Image Coordinates**: Pixel positions on camera frame
   - Origin: Top-left corner of image (0, 0)
   - Units: Pixels (x, y)
   - Frame size: Typically 1920×1080 pixels
   - Example: `(1103.7, 810.8)` = pixel at column 1103, row 810

## 4-Point Perspective Transformation (Homography)

### Mathematical Model

The transformation uses a **3×3 homography matrix** that maps between coordinate systems:

```
[x']   [h11  h12  h13] [x]
[y'] = [h21  h22  h23] [y]
[1 ]   [h31  h32  h33] [1]
```

Where:
- `(x, y)` = source coordinates
- `(x', y')` = destination coordinates
- `h11` through `h33` = homography matrix elements

### Perspective Division

The actual transformation includes perspective division:

```
x' = (h11×x + h12×y + h13) / (h31×x + h32×y + h33)
y' = (h21×x + h22×y + h23) / (h31×x + h32×y + h33)
```

### Why 4 Points?

A homography matrix has **8 degrees of freedom** (9 elements, but scale-invariant). Each point correspondence provides 2 equations:

- 4 points × 2 equations = 8 equations
- 8 equations solve for 8 unknowns (h11 through h32, with h33 = 1)

### Calibration Data Structure

#### Case 1: 2D Calibration Data (No Homography Matrix)

The system requires exactly 4 corresponding points between world and image coordinates:

```json
{
  "calibrationType": "cartesian",
  "sensors": [{
    "id": "sensor_id",
    "imageCoordinates": [
      {"x": 23.7, "y": 610.8},    // Image pixel: bottom-left
      {"x": 1103.7, "y": 810.8},  // Image pixel: bottom-right
      {"x": 1252.4, "y": 311.6},  // Image pixel: top-right
      {"x": 511.1, "y": 226.4}    // Image pixel: top-left
    ],
    "globalCoordinates": [
      {"x": 4.75, "y": 1.55},     // World meter: bottom-left
      {"x": 7.95, "y": 1.55},     // World meter: bottom-right
      {"x": 7.95, "y": 4.75},     // World meter: top-right
      {"x": 4.75, "y": 4.75}      // World meter: top-left
    ],
    "scaleFactor": 100,            // Legacy parameter (not used in homography)
    "homography": null             // No pre-computed homography matrix
  }]
}
```

#### Case 2: 3D Calibration Data (With Homography Matrix)

```json
{
  "calibrationType": "cartesian",
  "sensors": [{
    "id": "sensor_id",
    "origin": {
      "lat": 37.3543,
      "lng": -121.9522
    },
    "homography": [              // Pre-computed 3x3 homography matrix
      [h11, h12, h13],
      [h21, h22, h23],
      [h31, h32, h33]
    ]
  }]
}
```

**This implementation handles Case 1** - where no homography matrix is provided and must be calculated from reference points.

## Algorithm Implementation

### 1. System of Equations Setup

For each point correspondence `i`, we create two equations:

```
h11×xi + h12×yi + h13 - x'i×h31×xi - x'i×h32×yi = x'i
h21×xi + h22×yi + h23 - y'i×h31×xi - y'i×h32×yi = y'i
```

This creates an 8×8 linear system: **A × h = b**

### 2. Matrix Assembly

```typescript
const A: number[][] = [];
const b: number[] = [];

for (let i = 0; i < 4; i++) {
    const { x: sx, y: sy } = srcPoints[i];
    const { x: dx, y: dy } = dstPoints[i];
    
    // First equation
    A.push([sx, sy, 1, 0, 0, 0, -dx * sx, -dx * sy]);
    b.push(dx);
    
    // Second equation  
    A.push([0, 0, 0, sx, sy, 1, -dy * sx, -dy * sy]);
    b.push(dy);
}
```

### 3. Linear System Solution

The system is solved using **Gaussian elimination** with partial pivoting:

1. **Forward elimination**: Convert matrix to upper triangular form
2. **Back substitution**: Solve for homography parameters
3. **Matrix construction**: Assemble final 3×3 homography matrix

### 4. Transformation Application

```typescript
const applyHomography = (point, homography) => {
    const [h11, h12, h13, h21, h22, h23, h31, h32, h33] = homography;
    
    const w = h31 * point.x + h32 * point.y + h33;
    const x = (h11 * point.x + h12 * point.y + h13) / w;
    const y = (h21 * point.x + h22 * point.y + h23) / w;
    
    return { x, y };
};
```

## Transformation Types

### Forward Transformation: Image → World
- **Use case**: User draws ROI/tripwire on UI, converts to world coordinates for API submission
- **Process**: `imageCoords → homography → worldCoords`
- **Example**: Pixel `(800, 400)` → World `(6.2, 3.1)`

### Reverse Transformation: World → Image  
- **Use case**: API returns world coordinates, converts to pixels for visualization
- **Process**: `worldCoords → inverse_homography → imageCoords`
- **Example**: World `(5.5, 2.8)` → Pixel `(750, 350)`

## Advantages Over Simple Linear Transformation

### Previous Approach (Limited)
```typescript
// Only used first reference point + scale factor
const deltaX = coord.x - referencePoint.x;
const deltaY = coord.y - referencePoint.y;
const worldX = referenceWorld.x + (deltaX / scaleFactor);
const worldY = referenceWorld.y + (deltaY / scaleFactor);
```

**Problems:**
- Ignores 3 out of 4 reference points
- No perspective correction
- Assumes rectangular mapping (rarely true)
- Results in negative/invalid coordinates

### Current Approach (Robust)
```typescript
// Uses all 4 reference points with full perspective transformation
const homography = calculateHomographyMatrix(imagePoints, worldPoints);
const worldCoord = applyHomography(imageCoord, homography);
```

**Benefits:**
- Utilizes all calibration data
- Handles perspective distortion
- Accommodates irregular quadrilaterals
- Higher accuracy across entire calibrated area

## Coordinate Validation

The system validates transformations by:

1. **Reference Point Testing**: Ensures calculated homography correctly transforms all 4 calibration points
2. **Boundary Checking**: Warns when coordinates fall outside expected ranges
3. **Error Calculation**: Measures transformation accuracy with RMS error

## Error Handling

- **Singular Matrix Detection**: Checks for degenerate reference point configurations
- **Numerical Stability**: Uses partial pivoting in Gaussian elimination
- **Fallback Mechanisms**: Graceful degradation when calculations fail

## Code Implementation Logic

The system automatically detects which calibration type to use:

```typescript
// Check calibration type
const is2DCalibration = calibrationData.calibrationType === 'cartesian' && !sensorData.homography;

if (is2DCalibration) {
    // Case 1: 2D Calibration - Calculate homography from reference points
    if (!sensorData.imageCoordinates || !sensorData.globalCoordinates) {
        throw new Error('2D calibration requires imageCoordinates and globalCoordinates');
    }

    worldCoords = transform2DImageToWorld(
        roiImageCoords,
        sensorData.imageCoordinates,      // 4 image reference points
        sensorData.globalCoordinates      // 4 world reference points
    );
} else {
    // Case 2: 3D Calibration - Use pre-computed homography matrix
    if (!calibrationInstance) {
        throw new Error('Calibration instance required for 3D transformation');
    }

    const transformedCoords = transformImageROIToWorld(
        roiImageCoords,
        selectedSensor.sensorId,
        calibrationInstance              // Contains pre-computed homography
    );
}
```

## Usage Example (2D Calibration Case)

```typescript
// Setup reference points (from calibration data)
const imageRefs = [
    { x: 23.7, y: 610.8 },    // From sensorData.imageCoordinates
    { x: 1103.7, y: 810.8 },
    { x: 1252.4, y: 311.6 },
    { x: 511.1, y: 226.4 }
];

const worldRefs = [
    { x: 4.75, y: 1.55 },     // From sensorData.globalCoordinates
    { x: 7.95, y: 1.55 },
    { x: 7.95, y: 4.75 },
    { x: 4.75, y: 4.75 }
];

// Transform ROI coordinates
const roiImageCoords = [
    { x: 100, y: 200 },
    { x: 300, y: 200 },
    { x: 300, y: 400 },
    { x: 100, y: 400 }
];

const roiWorldCoords = transform2DImageToWorld(
    roiImageCoords,
    imageRefs,      // Reference image points
    worldRefs       // Reference world points
);

// Result: Real-world coordinates for API submission
console.log(roiWorldCoords);
// [{ x: 4.85, y: 2.1, z: 0 }, { x: 6.2, y: 2.1, z: 0 }, ...]
```

## Files Structure

- **Analytics.tsx**: Main component with transformation logic
- **VisualizationCanvas.tsx**: Canvas rendering for coordinate visualization
- **ReverseTransformationResults.tsx**: Display component for reverse transformations
- **types.ts**: TypeScript interfaces for coordinate data structures

## Mathematical References

- **Computer Vision**: Multiple View Geometry in Computer Vision (Hartley & Zisserman)
- **Homography**: Perspective transformation theory and applications
- **Linear Algebra**: Gaussian elimination and matrix operations
- **Numerical Methods**: Stability and accuracy in coordinate transformations 