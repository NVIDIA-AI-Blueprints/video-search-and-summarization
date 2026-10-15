# Sample Embeddings Script

This script samples embeddings from a dumped CSV and writes the sampled rows to `sampled_embeddings.csv`.

## Key Design: Per-Object Processing

**Important**: The script processes each `object_id` **independently** through the entire sampling pipeline. This is crucial because:

1. **Statistical Independence**: Each object (person/vehicle) has its own feature distribution and cluster properties
2. **Fair Sampling**: Objects with 1000 samples get proportional representation compared to objects with 100 samples
3. **Accurate Outlier Detection**: Outliers are detected relative to each object's own centroid, not a global centroid
4. **Cluster Integrity**: Each object's cluster maintains its statistical properties during pruning
5. **Scalability**: Can handle datasets with vastly different sample counts per object

Without per-object processing, objects with more samples would dominate the sampling, and outlier detection would be skewed.

## Features

1. **Data Loading**: Reads cluster data from a CSV file
2. **Per-Object Processing**: Processes each object_id independently for better sampling
3. **Outlier Removal**: Removes extreme outliers based on distance to centroid (per object)
4. **Cosine Pruning**: Removes highly similar vectors (cosine similarity > threshold)
5. **Farthest Point Sampling (FPS)**: Selects K=min(max_k, sqrt(N)) diverse samples per object

## Prerequisites

```bash
pip install numpy faiss-cpu
```

For GPU acceleration (optional):
```bash
pip install faiss-gpu
```

## Usage

### Step 1: Dump Data from ReID Service

First, dump the embeddings:

```bash
# Dump embeddings (with vectors)
curl -X POST "http://localhost:8000/dump/file?filename=embeddings.csv&include_vectors=true&format=csv"
```

### Step 2: Run the Sampling Script

**Process ALL objects (recommended):**
```bash
python sample_and_extract_patches.py \
    --embeddings embeddings.csv \
    --output-dir sampled/
```

**Process a single object only:**
```bash
python sample_and_extract_patches.py \
    --embeddings embeddings.csv \
    --output-dir sampled/ \
    --object-id 25
```

### Full Example with All Options (All Objects)

```bash
python sample_and_extract_patches.py \
    --embeddings embeddings.csv \
    --output-dir sampled/ \
    --outlier-threshold 3.0 \
    --cosine-threshold 0.9995 \
    --max-samples 100
```

## Command Line Arguments

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--embeddings` | Yes | - | Path to embeddings CSV file from `/dump/file` |
| `--output-dir` | Yes | - | Directory for `sampled_embeddings.csv` |
| `--object-id` | No | None | Filter by specific object_id. **If not specified, processes ALL objects in the CSV file independently** |
| `--outlier-threshold` | No | 3.0 | Standard deviation threshold for outlier removal |
| `--cosine-threshold` | No | 0.999 | Cosine similarity threshold (0.999-0.9995 recommended) |
| `--max-samples` | No | 7 | Maximum number of samples per object |

## Algorithm Details

**Important**: Each object_id is processed **independently** through the entire pipeline. This ensures that:
- Each object's cluster maintains its own statistical properties
- Outlier detection is relative to each object's centroid
- Sampling is proportional and fair across different objects
- Different objects with varying sample sizes are handled correctly

### 1. Data Loading & Grouping
- Loads all embeddings from CSV
- Groups embeddings by object_id
- Each object_id maintains its own metadata (entry_ids, sensor_ids, frame_ids, vectors)

### 2. Outlier Removal (Per Object)
- Calculates centroid for each object's vectors
- Computes distance of each vector to its object's centroid
- Removes vectors beyond `mean + threshold * std` distance
- Applied independently to each object_id

### 3. Cosine Pruning (Per Object)
- Normalizes vectors for cosine similarity computation
- Uses FAISS IndexFlatIP for fast similarity search within each object
- Removes vectors with cosine similarity > threshold
- Keeps first occurrence, removes subsequent similar vectors
- Applied independently to each object_id

### 4. Farthest Point Sampling (Per Object)
- Selects K = min(max_k, sqrt(N)) samples **for each object**
- Iteratively selects points that are farthest from already selected set
- Uses FAISS IndexFlatL2 for efficient distance computation
- Ensures diverse sampling across each object's feature space
- Each object gets up to max_k samples (or sqrt(N) if smaller)

## Output

Sampled rows are written to `sampled_embeddings.csv` in `--output-dir`.

## Example Workflow

```bash
# 1. Run ReID service and collect data
python src/main.py --schema-type protobuf

# 2. After collecting data, dump to CSV
curl -X POST "http://localhost:8000/dump/file?filename=embeddings.csv&format=csv"

# 3. Sample embeddings for object_id 25
python sample_and_extract_patches.py \
    --embeddings embeddings.csv \
    --output-dir sampled_obj25/ \
    --object-id 25 \
    --cosine-threshold 0.9995 \
    --max-samples 50

# 4. Check results
ls -lh sampled_obj25/
```

## Expected Output

### Processing All Objects (Default)

```
============================================================
SAMPLE EMBEDDINGS
============================================================

[STEP 1] Loading data...
Loading embeddings from embeddings.csv...
Loaded 1250 embeddings across 3 object(s)

============================================================
Processing Object ID: 25
============================================================
Initial vectors: 500

[STEP 2] Removing outliers for object 25...
  Original count: 500
  Mean distance: 0.1234
  Std distance: 0.0456
  Threshold: 0.2602
  Removed: 25 outliers
  Remaining: 475 vectors

[STEP 3] Cosine pruning for object 25...
  Original count: 475
  Removed: 120 similar vectors
  Remaining: 355 vectors

[STEP 4] Farthest Point Sampling for object 25...
  N=355, sqrt(N)=18, max_k=50
  K=min(max_k, sqrt(N))=18
  Sampled 18 points

============================================================
Processing Object ID: 37
============================================================
Initial vectors: 400

[STEP 2] Removing outliers for object 37...
  Removed: 15 outliers
  Remaining: 385 vectors

[STEP 3] Cosine pruning for object 37...
  Removed: 80 similar vectors
  Remaining: 305 vectors

[STEP 4] Farthest Point Sampling for object 37...
  N=305, sqrt(N)=17, max_k=50
  K=min(max_k, sqrt(N))=17
  Sampled 17 points

============================================================
Processing Object ID: 42
============================================================
Initial vectors: 350

[STEP 2] Removing outliers for object 42...
  Removed: 20 outliers
  Remaining: 330 vectors

[STEP 3] Cosine pruning for object 42...
  Removed: 95 similar vectors
  Remaining: 235 vectors

[STEP 4] Farthest Point Sampling for object 42...
  N=235, sqrt(N)=15, max_k=50
  K=min(max_k, sqrt(N))=15
  Sampled 15 points

============================================================
SUMMARY
============================================================
Objects processed: 3
Total original vectors: 1250
Total sampled points: 50
Output directory: sampled/

Per-object breakdown:
  Object 25: 18 samples
  Object 37: 17 samples
  Object 42: 15 samples
============================================================
```

### Processing Single Object

When using `--object-id 25`, only that object is processed.

## Troubleshooting

### "No embeddings found!"
- Check that CSV file has correct format
- Verify object_id exists in the data
- Check that `--object-id` parameter is correct

## Performance Tips

1. **Use GPU FAISS**: Install `faiss-gpu` for faster similarity search
2. **Adjust batch size**: For large datasets, process in batches
3. **Filter by object_id**: Process one object at a time for better memory usage
4. **Cosine threshold**: Higher threshold (0.9995) = more aggressive pruning
5. **Max samples**: Reduce for faster processing, increase for more coverage

## Sampled CSV

```bash
python sample_and_extract_patches.py \
    --embeddings embeddings.csv \
    --output-dir sampled/
```

