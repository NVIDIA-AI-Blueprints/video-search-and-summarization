# Video Embedding Downsampling

## Overview

This document explains video embedding downsampling algorithms implemented in the analytics stream pipeline. These are lossy compression techniques that intelligently reduce time-series embedding data while preserving important patterns and transitions within configurable tolerance bounds.

Two complementary algorithms are provided:
- **Swinging Door Trending (SDT)**: Interpolation-based compression for smooth trends
- **Sliding Window**: Neighbor-based novelty detection for cyclical patterns

## Common Concepts

### Adaptation to High-Dimensional Vectors

Both algorithms were originally designed for 1D scalar time-series data. Our implementations adapt them for high-dimensional video embedding vectors (typically 512-2048 dimensions).

#### Key Adaptations

1. **Vector Normalization**: All embedding vectors are normalized to unit length before comparison, ensuring fair geometric comparison regardless of magnitude.

2. **Vector Operations**: Mathematical operations extend naturally to high-dimensional space:
   - Linear interpolation: `expected = anchor_v + alpha * (new_v - anchor_v)`
   - Distance computation: `||v1 - v2||` in n-dimensional space
   - Similarity: Dot product of normalized vectors

3. **Tolerance Metrics**: Two options for measuring deviation in normalized vector space.

### Tolerance Metrics Comparison

Both algorithms support two metrics for measuring similarity between embedding vectors.

#### Distance Mode

**Metric**: Euclidean distance in normalized vector space
```python
distance = ||candidate - expected||
within_tolerance = distance <= threshold
```

**Geometric Interpretation**: Creates a spherical tolerance region around the expected/reference point.

**Advantages**:
- More faithful to classical compression algorithm semantics
- Creates a bounded deviation region (sphere)
- Clearer error guarantees
- Direct geometric meaning: "points within distance d"
- Consistent spatial interpretation

**Threshold Guidelines**:
| Threshold | Angular Deviation | Use Case |
|-----------|------------------|----------|
| 0.05 | ~2.9° | Very strict - high fidelity, minimal compression |
| 0.10 | ~5.7° | Strict - good for critical data |
| 0.15 | ~8.6° | **Recommended for SDT** - balanced |
| 0.20 | ~11.5° | Loose - higher compression |
| 0.30 | ~17.5° | Very loose - aggressive compression |
| 0.32 | ~18.2° | **Recommended for Window** - strict transitions |
| 0.45 | ~25.8° | **Recommended for Window** - balanced |
| 0.55 | ~32.0° | Very loose for Window |

#### Cosine Mode

**Metric**: Cosine similarity between vectors
```python
similarity = (candidate · expected) / (||candidate|| * ||expected||)
within_tolerance = similarity >= threshold
```

**Geometric Interpretation**: Creates a conical tolerance region based on angular deviation.

**Characteristics**:
- Measures angular difference, not spatial deviation
- Two vectors at different distances but same angle have same similarity
- Less geometrically faithful to classical algorithms
- May not preserve bounded error properties

**Threshold Guidelines**:
| Threshold | Angular Deviation | Notes |
|-----------|------------------|-------|
| 0.99 | ~8.1° | Very strict |
| 0.95 | ~18.2° | Moderate |
| 0.91 | ~24.2° | Current default for SDT |
| 0.90 | ~25.8° | Recommended for Window |
| 0.85 | ~31.8° | Very loose |


### Mathematical Correctness

#### Why Distance is Preferred

**Distance-based tolerance**:
- Maintains bounded error: all skipped points are within distance `d` of the interpolated/reference point
- Geometric guarantee: reconstructed trajectory stays within `d` of actual trajectory
- More faithful to classical compression deviation band concepts
- Clear spatial error bounds

**Cosine-based tolerance**:
- Maintains bounded angular error: all skipped points have angle <= `arccos(threshold)`
- Does NOT guarantee bounded spatial error for vectors at different scales
- Works well for normalized embeddings where direction is more important than magnitude

#### Computational Cost

Both metrics have similar O(1) complexity per comparison:

```python
# Distance: one vector subtraction + one norm
distance = np.linalg.norm(v1 - v2)  # sqrt(sum of squares)

# Cosine: one dot product (for normalized vectors)
similarity = np.dot(v1, v2)  # already normalized
```

Nearly identical performance in practice.

## Swinging Door Trending (SDT)

### What is Swinging Door Trending?

Swinging Door Trending was originally developed for industrial process control systems to compress sensor data while maintaining bounded error guarantees.

#### Key Insight: Look-Ahead Strategy

Unlike greedy algorithms that compare consecutive points, SDT uses a **one-point look-ahead** strategy:

- **Anchor**: The last committed/stored point
- **Candidate**: A pending point whose fate is undecided
- **New Point**: The current incoming point being evaluated

The algorithm asks: "If I draw a line from the anchor to the new point, does my candidate fall within a tolerance band around that line?" This look-ahead allows SDT to detect trend changes more accurately than simple consecutive-point comparison.

```
Visual Representation:

     value
       │
    v2 ──┤              ●  new_point
       │           ╱
    v1 ──┤      ●  candidate ← Is this within tolerance?
       │   ╱
    v0 ──┤●  anchor (last saved)
       │
       └─────┴─────┴───→ time
           t0    t1    t2

Question: Does candidate lie close enough to the line (anchor → new)?
```

### Algorithm Description

#### State Machine

The SDT algorithm maintains three key states:

1. **Anchor Point** `(t0, v0)`: The last point committed to storage
2. **Candidate Point** `(t1, v1)`: A look-ahead point awaiting decision
3. **New Point** `(t2, v2)`: Current incoming point being processed

#### Decision Process

For each new point arriving:

1. **Interpolation**: Calculate where the candidate *should* be if it lies on the line from anchor to new point:
   ```
   alpha = (t_candidate - t_anchor) / (t_new - t_anchor)
   expected_candidate = anchor + alpha * (new - anchor)
   ```

2. **Deviation Measurement**: Compare actual candidate to expected position using tolerance metric (distance or cosine similarity)

3. **Decision**:
   - **Within tolerance**: Skip the candidate, move new point to candidate position
   - **Outside tolerance**: Save the candidate, promote it to anchor, new point becomes candidate

#### Pseudocode

```python
def process_point(new_point):
    if anchor is None:
        anchor = new_point
        save(new_point)
        return
    
    if candidate is None:
        candidate = new_point
        return
    
    # Compute where candidate should be on line from anchor to new
    expected_candidate = interpolate(anchor, new_point, candidate.timestamp)
    
    # Measure deviation
    deviation = measure_distance(candidate, expected_candidate)
    
    if deviation <= threshold:
        # Within tolerance - skip candidate
        candidate = new_point
    else:
        # Outside tolerance - save candidate
        save(candidate)
        anchor = candidate
        candidate = new_point
```

### Linear Interpolation Correctness

The interpolation formula is **mathematically correct** for computing the expected candidate position:

```
Given:
  - anchor at time t0 with vector v0
  - new point at time t2 with vector v2
  - candidate at time t1 with vector v1 (where t0 < t1 < t2)

Expected candidate on line from anchor to new:
  alpha = (t1 - t0) / (t2 - t0)        [time ratio: 0 to 1]
  expected = v0 + alpha * (v2 - v0)    [linear interpolation]
  expected = normalize(expected)        [project to unit sphere]
```

This correctly computes the point on the line segment (anchor → new) at the candidate's timestamp.

### Performance Characteristics

#### Compression Ratios

Typical compression ratios observed with SDT:

| Data Characteristics | Distance=0.15 | Cosine=0.91 |
|---------------------|---------------|-------------|
| Smooth motion (circular) | 30-40x | 30-40x |
| Moderate changes | 10-20x | 10-20x |
| Frequent transitions | 5-10x | 5-10x |
| Rapid/chaotic motion | 2-5x | 2-5x |

#### Computational Complexity

- **Per-point cost**: O(1) operations
  - One vector interpolation: O(d) where d = embedding dimension
  - One distance/similarity computation: O(d)
  - Constant overhead for state management

- **Memory**: O(1) - stores only 2 points (anchor and candidate)

- **Latency**: One-point delay (candidate is held pending)

### When to Use SDT

**Good fit**:
- Smooth, continuous data streams with gradual changes
- Video embeddings with slow scene transitions
- Want to preserve trends and trajectories
- Memory constrained (minimal state)
- Bounded error requirements

**Poor fit**:
- Data with frequent sudden jumps or discontinuities
- Cyclical/repetitive patterns that return to previous states
- Need to detect when patterns repeat
- Ultra-low latency critical (SDT has 1-point delay)

## Sliding Window Downsampling

### What is Sliding Window Downsampling?

The sliding window algorithm uses a fixed-size buffer of recent embeddings and neighbor-based novelty detection to identify which points represent new patterns or state transitions. Unlike SDT's interpolation approach, it directly compares new points to historical context.

#### Key Insight: Novelty Detection

The algorithm asks: "How many of my recent neighbors are similar to this new point?" Points with few similar neighbors are considered novel or transitional and are stored.

```
Visual Representation:

    Window Buffer (last N points):
    [v_n-5, v_n-4, v_n-3, v_n-2, v_n-1]
                                    ↑
                                    Search backward
    
    New point: v_new
    
    Count consecutive similar neighbors from end:
    - v_n-1: similar ✓ (count=1)
    - v_n-2: similar ✓ (count=2)
    - v_n-3: dissimilar ✗ → STOP
    
    If count < min_neighbours → SAVE (novel)
    If count >= min_neighbours → SKIP (redundant)
```

### Algorithm Description

#### State Machine

The sliding window algorithm maintains:

1. **Window Buffer**: Fixed-size deque of last N embedding points (FIFO)
2. **Last Saved Time**: Timestamp of most recently stored point (for max interval)

When window is full, oldest points are automatically evicted.

#### Decision Process

For each new point arriving:

1. **Add to Window**: Append new point to window buffer (auto-evicts oldest if full)

2. **Max Interval Check**: If time since last save >= max_interval → force save

3. **Neighbor Counting**: Search backward through window:
   - Count consecutive similar points (within tolerance)
   - Stop when encountering dissimilar point
   - Stop when reaching min_neighbours count

4. **Decision**:
   - **Few neighbors** (< min_neighbours): Save point → Novel pattern
   - **Many neighbors** (>= min_neighbours): Skip point → Redundant

#### Pseudocode

```python
def process_point(new_point):
    # Add to window
    window.append(new_point)
    
    # Check max interval
    if time_since_last_save >= max_interval:
        save(new_point)
        return
    
    # Count consecutive similar neighbors
    count = 0
    for neighbor in reversed(window):
        if is_similar(new_point, neighbor):
            count += 1
        else:
            break  # Stop on first dissimilar
        
        if count == min_neighbours:
            break  # Found enough
    
    # Decide based on count
    if count < min_neighbours:
        save(new_point)  # Novel/transitional
    else:
        skip(new_point)  # Redundant
```

### Rationale and Correctness

#### Why Consecutive Neighbors?

The algorithm counts only **consecutive** similar neighbors from the most recent point, not all similar points in the window. This design choice is intentional:

**Consecutive search**:
- Ensures neighbors are recent and clustered
- Detects transitions between patterns
- Point similar to old pattern but not recent = transitional state

**What if we counted all similar points?**:
- Would miss transitions (point might match old pattern but not recent one)
- Less sensitive to pattern changes
- Loses temporal locality information

#### Decision Rules Explained

**Novel Point** (count < min_neighbours):
```
Recent window: [A, A, B, B, B]
New point: C
Backward search from B: C≠B → count=0 → SAVE

Interpretation: C is different from recent pattern (B's)
Action: Store as novel pattern
```

**Redundant Point** (count >= min_neighbours):
```
Recent window: [A, A, B, B, B]
New point: B
Backward search: B=B(✓), B=B(✓), B=B(✓) → count=3 → SKIP

Interpretation: B matches recent pattern
Action: Skip as redundant
```

**Transitional Point** (partial match):
```
Recent window: [A, A, A, B, B]
New point: B
Backward search: B=B(✓), B=B(✓), B≠A(✗) → count=2 < 3 → SAVE

Interpretation: B is establishing as new pattern (transition from A)
Action: Store to capture transition point
```

#### Break vs Reset Strategy

The current implementation uses **break** when encountering dissimilar point:

```python
for neighbor in reversed(window):
    if is_similar(new_point, neighbor):
        count += 1
    else:
        break  # Stop immediately
```

**Alternative: reset** (commented out in code):
```python
for neighbor in reversed(window):
    if is_similar(new_point, neighbor):
        count += 1
    else:
        count = 0  # Reset and continue
```

**Break strategy** (current):
- Counts consecutive neighbors only
- Stops at first dissimilar point
- Better for transition detection
- Preserves temporal locality

**Reset strategy** (alternative):
- Would find largest consecutive group in window
- Continues through dissimilar points
- Could miss rapid transitions
- Less temporally local

The break strategy is recommended as it's more sensitive to recent pattern changes.

### Performance Characteristics

#### Compression Ratios

Typical compression ratios observed with sliding window:

| Data Characteristics | Distance=0.45, min_neighbours=3 | Cosine=0.90, min_neighbours=3 |
|---------------------|--------------------------------|-------------------------------|
| Cyclical patterns | 20-30x | 20-30x |
| Repetitive states | 15-25x | 15-25x |
| Frequent transitions | 8-15x | 8-15x |
| Random/chaotic | 3-8x | 3-8x |

#### Computational Complexity

- **Per-point cost**: O(W) operations where W = window size
  - Backward search through window: O(W)
  - Each comparison: O(d) where d = embedding dimension
  - Early termination possible (often < W comparisons)

- **Memory**: O(W) - stores window of W points

- **Latency**: No look-ahead delay (immediate decision)

### When to Use Sliding Window

**Good fit**:
- Cyclical or repetitive patterns (video loops)
- Data that returns to previous states
- Need novelty detection (seen before vs new)
- Want to detect state transitions
- Transition points are important (pattern A → pattern B)

**Poor fit**:
- Pure smooth trends (SDT better)
- Memory heavily constrained
- Window size unclear (need to know pattern duration)
- Patterns longer than practical window size

### Parameter Tuning

#### Window Size

Choose based on pattern duration:

- **Small (10-30)**: Fast response, minimal memory, short patterns
- **Medium (30-60)**: Balanced, good default
- **Large (60-120)**: Long pattern recognition, higher memory

**Rule of thumb**: Window size ≈ typical pattern duration (in frames)

#### Min Neighbours

Controls novelty sensitivity:

- **min_neighbours=1**: Very sensitive, stores many points (high fidelity)
- **min_neighbours=3**: Balanced (recommended default)
- **min_neighbours=5**: Less sensitive, aggressive compression

**Rule of thumb**: min_neighbours = 2-3 for most cases

## Algorithm Comparison

### SDT vs Sliding Window

| Aspect | SDT | Sliding Window |
|--------|-----|----------------|
| **Strategy** | Interpolation-based | Neighbor-based |
| **State** | 2 points (anchor, candidate) | N points (window) |
| **Memory** | O(1) | O(N) |
| **Comparison** | To interpolated line | To historical neighbors |
| **Detection** | Trend deviation | Novelty/transition |
| **Best For** | Smooth trends | Cyclical patterns |
| **Latency** | 1-point delay | No delay |
| **Complexity** | O(1) per point | O(N) per point |
| **Transition Sensitivity** | Moderate | High |

### Choosing the Right Algorithm

**Use SDT when**:
- Embeddings change smoothly over time
- Video has slow scene transitions
- Memory is constrained
- Want bounded error guarantees with interpolation
- Data has clear trends/trajectories

**Use Sliding Window when**:
- Embeddings have cyclical/repetitive patterns
- Video has loops or repeated actions
- Want to detect when patterns repeat or change
- Need immediate decisions (no look-ahead delay)
- Transition detection is important
- Can afford O(N) memory


### Why Different Thresholds?

SDT and Sliding Window use significantly different threshold values (e.g., 0.15 vs 0.45 for distance mode) because they use the thresholds in fundamentally different ways:

#### SDT - Comparing Against Interpolated Expected Position

**What SDT does:**
- Takes anchor point and new point
- Computes where candidate SHOULD be via linear interpolation
- Compares actual candidate to this mathematically precise expected position

**Why tighter thresholds work (0.15 typical):**
- The expected position is a very precise reference point computed from the trend
- Even small deviations indicate the candidate doesn't follow the trend line
- Asking: "Does this point lie on the linear trajectory?"
- Like checking if you're staying on a straight road - small deviations matter

#### Sliding Window - Direct Comparison to Historical Neighbors

**What Window does:**
- Compares new point DIRECTLY to actual historical points in the window
- No interpolation - raw point-to-point similarity
- Counts how many consecutive recent points are "similar enough"

**Why looser thresholds are needed (0.45 typical):**
- Natural variation exists even within the "same pattern"
- Video embeddings for similar (but not identical) scenes have inherent differences
- Asking: "Have I seen something LIKE this recently?"
- Like face recognition - need tolerance for variations in lighting, angle, expression
- If threshold too tight, won't find enough consecutive neighbors → saves everything (poor compression)

#### Concrete Example

Imagine circular camera motion capturing the same scene repeatedly:

**SDT with 0.15:**
```
Point 1 → Point 2 (smooth motion)
Interpolate expected position for Point 1.5
Compare to actual Point 1.5
Distance = 0.08 (within 0.15) ✓
Skip Point 1.5 - follows trend
```

**Window with 0.15 (too tight):**
```
New point comes in (similar scene but slightly different angle/lighting)
Compare to last 3 points in window
Distances: [0.22, 0.25, 0.19] (all > 0.15) ✗
No neighbors found → SAVE
Result: Saves almost everything - poor compression!
```

**Window with 0.45 (appropriate):**
```
New point comes in
Compare to last points: [0.22, 0.25, 0.19] (all <= 0.45) ✓
Found 3 consecutive neighbors → SKIP
Result: Correctly identifies as redundant pattern
```

#### The Key Insight

**SDT tolerance**: "How much can the point deviate from the expected TREND?"
- Checking geometric alignment with an interpolated line
- Deviations from smooth trends are typically small
- Only comparing one candidate at a time
- Designed for smooth continuous changes

**Window tolerance**: "How DIFFERENT can embeddings be while still representing the SAME PATTERN?"
- Pattern matching, not trend following
- Real-world embeddings have variation even for "same" content
- Needs to accumulate consecutive matches (typically 3+)
- Designed for cyclical/repetitive data where exact matches are rare

They're solving different problems with different semantic meanings for "similar enough", hence **0.15 for SDT** but **0.45 for Window**. - 

## Configuration

Configuration parameters in `AppConfig`:

```python
# Common parameters (both algorithms)
embedDownsampleToleranceMode = "distance"  # or "cosine"
embedDownsampleMaxIntervalSec = "300"  # 5 minutes max gap

# Threshold defaults (algorithm-specific)
# SDT defaults:
embedDownsampleDistanceThreshold = "0.15"  # for distance mode
embedDownsampleSimilarityThreshold = "0.91"  # for cosine mode

# Window defaults:
embedDownsampleDistanceThreshold = "0.45"  # for distance mode
embedDownsampleSimilarityThreshold = "0.90"  # for cosine mode

# Window-specific parameters
embedDownsampleWindowSize = "60"  # number of points in window
embedDownsampleMinNeighbours = "3"  # minimum consecutive similar neighbors
```

## Known Limitations

**SDT**:
1. One-point latency (candidate held pending)
2. No backtracking (skipped points lost forever)
3. Assumes smooth trends (poor for discontinuities)
4. Requires force_save() at stream end
5. May miss cyclical patterns that return to previous states

**Sliding Window**:
1. Memory overhead (stores full window)
2. O(N) per-point cost (vs SDT's O(1))
3. Window size must be tuned to pattern duration
4. Patterns longer than window will not be detected
5. May store too many points during chaotic segments

**Both**:
1. Normalization required for fair comparison
2. Lossy compression (cannot reconstruct exactly)
3. Threshold tuning needed for different data types
4. No adaptation to changing data characteristics

## References

- **SDT Algorithm**: Bristol Babcock (1981) - Original Swinging Door Trending
- **EnOS IoT Documentation**: [Time-Series Compression Algorithms](https://support.enos-iot.com/docs/time-series-data/en/2.3.0/reference/compression_algorithm.html)

## Summary

Video embedding downsampling provides effective compression for streaming analytics while preserving important patterns and transitions:

### Key Insights

**Swinging Door Trending (SDT)**:
- Look-ahead interpolation strategy
- Preserves smooth trends with bounded error
- O(1) memory and complexity
- Best for gradual scene changes
- Requires force_save() at stream end

**Sliding Window**:
- Neighbor-based novelty detection
- Detects pattern repetitions and transitions
- O(N) memory and complexity
- Best for cyclical or repetitive patterns
- Immediate decisions, no pending state

**Tolerance Metrics**:
- Cosine mode - angular deviation, rotation-invariant
- Distance mode - spatial deviation, bounded error

**Configuration**:
- Tunable thresholds for compression-fidelity tradeoff
- Max interval override prevents excessive gaps
- Algorithm selection based on data characteristics

