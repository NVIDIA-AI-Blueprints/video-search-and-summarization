# Performance Benchmarks

This directory contains performance benchmark scripts for various algorithms in the spatialai_data_utils package.

## Available Benchmarks

### benchmark_frustum.py

Benchmarks the camera frustum calculation algorithm (`calculate_camera_frustum_polygon`).

**Features:**
- Single camera frustum calculation performance
- Multiple camera scaling tests
- Scene bounds clipping overhead analysis
- Uses real calibration data from `data/mtmc/scene_001/calibration.json`

**Usage:**
```bash
python benchmarks/benchmark_frustum.py
```

**Sample Output:**
- Mean/median/min/max execution times
- Performance scaling with number of cameras
- Estimated FPS for real-time applications
- Scene bounds clipping overhead

**Requirements:**
- NumPy
- Real calibration data at `data/mtmc/scene_001/calibration.json`

## Running Benchmarks

All benchmark scripts can be run directly from the repository root:

```bash
# Run frustum calculation benchmark
python benchmarks/benchmark_frustum.py
```

## Adding New Benchmarks

When adding new benchmark scripts:

1. Use the `benchmark_*.py` naming convention
2. Include a docstring explaining what is being benchmarked
3. Use real data when possible to ensure realistic performance measurements
4. Output clear statistics (mean, median, std dev, min, max)
5. Include scaling tests if applicable
6. Update this README with the new benchmark details

