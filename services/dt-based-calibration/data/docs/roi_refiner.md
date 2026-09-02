# ROI Simplifier

A web-based tool to simplify complex ROI (Region of Interest) polygons from CARLA simulation calibration files.

## Quick Start

```bash
# Option 1: Python HTTP server
cd roi-simplifier
python3 -m http.server 8080
# Open: http://localhost:8080

# Option 2: Direct file (some features may not work)
# Open index.html in browser
```

**First run:** You'll be prompted to enter a Google Maps API key (saved in localStorage).

## Usage

1. **Load JSON** - Click "Load JSON File" and select a calibration file
2. **Adjust Parameters** - Tune Max Edge and Denoise settings
3. **Simplify** - Click "⚡ Simplify ROI"
4. **Edit** (optional) - Click "Edit Polygon" to manually adjust vertices
5. **Export** - Click "Export JSON" to download simplified ROI

## Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| Max Edge | 0.045 km | Maximum edge length for concave hull. Smaller = more detailed |
| Denoise | 3.0σ | Outlier removal threshold. Smaller = more aggressive filtering |
| Adaptive | ON | Corner-aware smoothing to preserve turns while simplifying straights |

## Algorithm

### Pipeline

```
Input Points → Denoise → Concave Hull → Simplify → Corner Smoothing → Output
```

### 1. Denoise (Outlier Removal)
- Calculates centroid of all points
- Removes points beyond `threshold × σ` from mean distance
- Eliminates GPS noise and errant points

### 2. Concave Hull (Turf.js)
- Uses [Turf.js concave](https://turfjs.org/docs/#concave) algorithm
- Creates a polygon that "hugs" the point cloud
- `maxEdge` controls how concave the result can be
- Falls back to convex hull if concave fails

### 3. Douglas-Peucker Simplification
- Reduces point count while preserving shape
- Applied when result has >30 points
- Tolerance scales with polygon size

### 4. Corner-Aware Smoothing
- Detects corners (angle change >25°)
- Preserves corner points
- Removes redundant points on straight sections
- Keeps intermediate points if distance >50m

## Input Format

Supports nested JSON with `roiCoordinates`:

```json
{
  "sensors": [{
    "rois": [{
      "id": "roi-id-1",
      "roiCoordinates": [
        {"x": -121.96, "y": 37.37},
        ...
      ]
    }]
  }]
}
```

## Output Format

```json
{
  "rois": [{
    "id": "roi-1",
    "roiCoordinates": [
      {"x": -121.96543, "y": 37.36937},
      ...
    ]
  }],
  "_metadata": {
    "totalROIs": 1,
    "originalPoints": 282,
    "simplifiedPoints": 20,
    "exportedAt": "2025-12-05T..."
  }
}
```

## Dependencies

- [Turf.js](https://turfjs.org/) - Geospatial analysis
- [Google Maps API](https://developers.google.com/maps) - Map visualization
