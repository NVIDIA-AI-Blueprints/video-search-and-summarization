# Cosmos 3 Nano image/video reranking

Zero-shot Cosmos 3 Nano (CR3) reranking, the bundled fine-tuned CR3 checkpoint,
and its fine-tuning recipe.

## 1. Get evaluation numbers

Use the same fine-tuned retriever for both models. Follow the shared setup,
then the steps for the model you need. Training a new checkpoint is in section 3.

### Install and configure inputs

Use Linux, Python 3.12, and an NVIDIA GPU with a compatible PyTorch/CUDA
installation. Install the inference environment:

```bash
cd tools/cosmos-reranker  # From the VSS checkout root.
python3.12 -m venv .venv-eval
.venv-eval/bin/python -m pip install -e '.[eval]'
```

Inference uses vLLM 0.23.0 and Transformers 5.14.1. Model preparation and
training use Transformers 4.57.5 and PEFT 0.17.1.

Supply PAS V3.1 test data and the fine-tuned retriever checkpoint. Test evaluation
needs `test_pairs.json` and `images/` under `TEST_ROOT`. Optional fusion also needs
`val_pairs.json` and `images/` under `PAS_ROOT`, and the fixed validation query
subset. Fine-tuning also needs
`train_pairs.json` and `attribute_vocab.json` under `PAS_ROOT`. These inputs
are supplied separately.

For a new setup, create a private, gitignored `.env` with your paths.
If you already have one, load it with `source .env`:

```bash
if [[ ! -f .env ]]
then
cat > .env <<'ENV'
export PAS_ROOT=/path/to/PAS
export RETRIEVER=/path/to/finetuned_retriever_checkpoint.pth
export VAL_QUERY_SUBSET=/path/to/validation_query_subset/query_subset.json
export TEST_ROOT=/path/to/test_export
export DATA_ROOT="$PWD/artifacts/reproduction/data"
export EMBEDDING_ROOT="$PWD/artifacts/reproduction/embeddings"
export CACHE_ROOT="$PWD/artifacts/reproduction/cache"
export RUN_ROOT="$PWD/artifacts/reproduction/train"
export ADAPTER="$PWD/checkpoints/finetuned-cr3"
export MERGED_MODEL="$PWD/artifacts/merged-vision"
export TEST_EMBED="$PWD/artifacts/evaluation-embeddings/test"
export VAL_EMBED="$PWD/artifacts/evaluation-embeddings/validation"
ENV
fi
source .env
```

Model assets download automatically: Nano language/vision weights are exported
to `artifacts/models/Cosmos3-Nano-VLM`, and the tokenizer comes from
`google/siglip2-so400m-patch16-256`. Downloads reuse the Hugging Face cache.
Optionally set `BASE_MODEL` and `SIGLIP_TOKENIZER` in `.env` to reuse local
assets. Missing directories are populated automatically. A partial base-model
export raises an error. Use a fresh destination.

### Generate retrieval embeddings

Generate test embeddings once. If matching arrays already exist for the same
inputs, set `TEST_EMBED` to their directory and skip this command:

```bash
.venv-eval/bin/python -m cosmos_reranker.evaluation.generate_embeddings \
  --pairs-file "$TEST_ROOT/test_pairs.json" --image-root "$TEST_ROOT/images" \
  --checkpoint "$RETRIEVER" --output-dir "$TEST_EMBED" --device cuda:0 \
  --dtype float16 --image-batch-size 128 --text-batch-size 512
```

For optional fusion, also generate full-validation embeddings once, or reuse
matching arrays through `VAL_EMBED`:

```bash
.venv-eval/bin/python -m cosmos_reranker.evaluation.generate_embeddings \
  --pairs-file "$PAS_ROOT/val_pairs.json" --image-root "$PAS_ROOT/images" \
  --checkpoint "$RETRIEVER" --output-dir "$VAL_EMBED" --device cuda:0 \
  --dtype float16 --image-batch-size 128 --text-batch-size 512
```

### Evaluate on eight GPUs

[`evaluate_8gpu.sh`](evaluate_8gpu.sh) uses GPUs 0–7, with one model instance
per GPU. Each worker evaluates a disjoint query subset against the full gallery.
The script merges metrics and query rankings after all eight workers succeed.
Load `.env` before running it. Run one model's evaluation at a time.

Startup logs, tqdm progress, and metric updates every 32 queries are saved under
`artifacts/evaluation/<variant>/<split>-shards/gpu-*.log`.
Follow a worker's log from another terminal:

```bash
tail -f artifacts/evaluation/zero-shot/test-shards/gpu-0.log
```

For the fine-tuned model, replace `zero-shot` with `fine-tuned`. For validation,
replace `test-shards` with `validation-shards`. Use `nvidia-smi` to inspect GPU activity.

### Zero-shot CR3

1. Evaluate the full test set. Use scalar-plus-accessories relevance, K20
   candidates, and deduplicated queries with matching ground truths unioned:

```bash
./evaluate_8gpu.sh zero-shot test
```

2. Read `artifacts/evaluation/zero-shot/test/scalar_plus_accessories/nvidia_pas_metrics_weighted_aggregate.csv`
   for Easy/Medium/Hard reranking metrics. Use the snippet below for Overall.

### Fine-tuned CR3 checkpoint

The fine-tuned language and visual adapters are included in this repository at
`tools/cosmos-reranker/checkpoints/finetuned-cr3/`.

1. Install the preparation environment, assemble and verify the bundled adapters,
   and merge the visual adapter. Base weights download and export automatically
   when missing:

```bash
python3.12 -m venv --system-site-packages .venv-train
.venv-train/bin/python -m pip install -e '.[train]'
export ADAPTER="$PWD/checkpoints/finetuned-cr3"
export MERGED_MODEL="$PWD/artifacts/merged-vision"
.venv-train/bin/python -m cosmos_reranker.finetuning.recipe checkpoint --adapter "$ADAPTER"
.venv-train/bin/python -m cosmos_reranker.finetuning.recipe merge-vision \
  --adapter "$ADAPTER" --output "$MERGED_MODEL"
```

2. Evaluate the same test inputs, using the merged visual weights and language adapter:

```bash
./evaluate_8gpu.sh fine-tuned test
```

3. Read `artifacts/evaluation/fine-tuned/test/scalar_plus_accessories/nvidia_pas_metrics_weighted_aggregate.csv`
   for the same metrics.

4. Optional score fusion: evaluate the validation query subset, then apply the
   checkpoint's frozen validation calibration to the test rankings:

```bash
./evaluate_8gpu.sh fine-tuned validation &&
.venv-eval/bin/python -m cosmos_reranker.evaluation.fit_val_apply_test_fusion \
  --validation-rankings artifacts/evaluation/fine-tuned/validation/scalar_plus_accessories/query_rankings.jsonl \
  --test-rankings artifacts/evaluation/fine-tuned/test/scalar_plus_accessories/query_rankings.jsonl \
  --calibration-summary "$ADAPTER/fusion_calibration.json" \
  --output-dir artifacts/evaluation/fine-tuned/fusion
```

Read `test_at_frozen_validation_alpha` in
`artifacts/evaluation/fine-tuned/fusion/summary.json`. For a newly trained
checkpoint, omit `--calibration-summary` to fit normalization and weight on
validation. Keep both frozen when evaluating test.

### Read metrics for either model

Set `variant` to `zero-shot` or `fine-tuned`. This prints mAP, Rank-1, and
Rank-5 as percentages. Overall weights each query type by its query count:

```python
import csv
from pathlib import Path

variant = "zero-shot"
path = Path("artifacts/evaluation") / variant / "test/scalar_plus_accessories/nvidia_pas_metrics_weighted_aggregate.csv"
with path.open() as handle:
    rows = list(csv.DictReader(handle))
metrics = ("mAP", "Rank-1", "Rank-5")
for row in rows:
    print(row["QueryType"], {m: 100 * float(row[m]) for m in metrics})
count = sum(int(row["num_queries"]) for row in rows)
print("overall", {m: 100 * sum(float(r[m]) * int(r["num_queries"]) for r in rows) / count for m in metrics})
```

## 2. Image/video API examples

Use `.venv-eval/bin/python` for these examples. Create the reranker once and
pass candidates returned by your retriever.

### Zero-shot model

```python
from pathlib import Path
from cosmos_reranker.reranking import (
    Candidate, Segment, RerankerConfig, CosmosReasonReranker,
)

reranker = CosmosReasonReranker(RerankerConfig(
    rerank_depth=20, fps=2, max_frames=32,
    options={"model_id": "nvidia/Cosmos3-Nano", "max_model_len": 32768},
))

# Images: each retrieved image is one candidate.
paths = [Path("/path/image1.jpg"), Path("/path/image2.jpg")]
images = [
    Candidate(Segment(i, str(p), None, None), stage1_score=-float(i), image_path=p)
    for i, p in enumerate(paths)
]
ranked_images = reranker.rerank("a person wearing a red shirt", images)

# Videos: None bounds score the whole video. Numeric bounds select seconds.
video = Path("/path/video.mp4")
videos = [
    Candidate(Segment(0, str(video), None, None), stage1_score=0.9, video_path=video),
    Candidate(Segment(1, str(video), 10, 15), stage1_score=0.8, video_path=video),
]
ranked_videos = reranker.rerank("a person opens the door", videos)
for candidate in ranked_videos:
    print(candidate.segment.segment_id, candidate.rerank_score)
```

Supply candidates from your retriever and reuse the reranker across queries.
`stage1_score` breaks ties. Images use the PAS person-attribute matching prompt.
Videos use a clip-matching prompt and native ordered frames sampled at about
2 FPS, capped at 32. Shorter segments help capture brief events in long videos.
Score is `log P(yes) - log P(no)`. Higher means a stronger match. Only the top
`rerank_depth` candidates are rescored. Failed scores are `None`. Inspect
`reranker.last_failures`. Keep each batch to one media type.

Use `rerank_batch(queries, candidate_lists)` for multiple queries. Set
`score_chunk_size` in `options` to control scoring batches. For two-GPU model
parallelism, set `tensor_parallel_size=2` and `CUDA_VISIBLE_DEVICES=0,1`.

### Fine-tuned checkpoint

The base model is downloaded and exported automatically when missing. The
visual adapter is merged in FP32 and saved in FP16. The language adapter is
loaded during inference. Supply both:

```python
import os

reranker = CosmosReasonReranker(RerankerConfig(
    rerank_depth=20, fps=2, max_frames=32,
    options={
        "model_id": os.environ["MERGED_MODEL"],
        "lora_path": str(Path(os.environ["ADAPTER"]) / "language"),
        "max_lora_rank": 32, "max_model_len": 32768,
    },
))
ranked_images = reranker.rerank("a person wearing a red shirt", images)
```

The same API accepts videos and segments. The fine-tuned checkpoint was trained
on PAS images. The reported PAS metrics measure image retrieval.

## 3. Fine-tuning recipe and configuration

Use the training environment prepared above with PyTorch 2.11. Install the
training runtime and FlashAttention. Building FlashAttention requires a C++
compiler and a compatible CUDA toolkit with `nvcc` on `PATH`:

```bash
.venv-train/bin/python -m cosmos_reranker.finetuning.recipe runtime
MAX_JOBS=8 .venv-train/bin/python -m pip install --no-build-isolation flash-attn==2.7.4.post1
```

This installs a pinned [Cosmos-RL](https://github.com/nvidia-cosmos/cosmos-rl)
build from NVIDIA's official repository and applies the bundled training
adaptations. The recipe launches training with the installed `cosmos-rl` command
and stops after each stage's target checkpoint is saved. Model weights, optimizer
state, and the learning-rate schedule are preserved between stages.

Set CUDA paths for your host if needed. If BF16 Conv3d fails in cuDNN, set
`PAS_DISABLE_CUDNN_CONV=1` to use the native CUDA projection.
Run the following stages in order on eight GPUs.

### 1. Mine training candidates

Remove training queries whose caption text also appears in the validation query
subset, so those captions are held out for evaluation. Sample 750,000 training queries
with seed 880821 and select the disjoint remaining 1,131,764 queries with seed
880822. Encode both selections and 222,217 images with the fine-tuned retriever,
then normalize the 1,152-dimensional embeddings and write eight shards.

```bash
source .env
export CUDA_VISIBLE_DEVICES=0,1,2,3,4,5,6,7
prepare_args=(
  --pas-root "$PAS_ROOT" --retriever "$RETRIEVER"
  --validation-subset "$VAL_QUERY_SUBSET" --data-root "$DATA_ROOT"
  --embedding-root "$EMBEDDING_ROOT" --cache-root "$CACHE_ROOT"
)
.venv-train/bin/python -m cosmos_reranker.finetuning.recipe prepare indices "${prepare_args[@]}"
.venv-train/bin/python -m cosmos_reranker.finetuning.recipe prepare embeddings "${prepare_args[@]}"
.venv-train/bin/python -m cosmos_reranker.finetuning.recipe prepare mine "${prepare_args[@]}"
```

Mining retrieves the top 20 candidates within each dataset gallery and labels
matches using scalar attributes and accessories. It retains groups with both
positive and negative candidates. Expected group counts are 669,871 and
1,017,346. Both training stages use these same two annotation sets.

### 2. Build attribute targets and the vision cache

Create structured attribute targets, then cache the frozen vision prefix through
block 23 in BF16. The cache includes deep-stack features, offsets, and token grids.
Training runs vision blocks 24–26 and the merger using these cached inputs.
Caching speeds up training by avoiding repeated computation of the frozen vision prefix.

```bash
.venv-train/bin/python -m cosmos_reranker.finetuning.recipe prepare attributes "${prepare_args[@]}"
.venv-train/bin/python -m cosmos_reranker.finetuning.recipe prepare cache "${prepare_args[@]}"
```

Eight GPUs write the primary and supplement safetensors shards.
Supplied image lists in `artifacts/recipe/` are reused. Otherwise, preparation
creates complete primary and supplement lists in first-seen order. This affects
shard layout without changing image coverage. Cache keys use absolute image
paths, so rebuild the cache after moving images. Completed preparation outputs
are reused. Use fresh output directories after changing inputs.

### 3. Train the two stages

Both stages use the same objective:

`L = L_rank + L_binary + 0.25 * L_attribute`

- `L_rank` increases the total probability assigned to positive candidates
  within each K20 group.
- `L_binary` is binary cross-entropy with equal weight for the mean positive
  loss and the mean negative loss within each query.
- `L_attribute` is masked cross-entropy over structured attribute choices.

The loss implementation is in `src/cosmos_reranker/finetuning/loss.py` and
`structured_attribute_aux.py`. Retriever scores select candidates and do not supervise the loss.

| Setting | Stage 0 | Stage 1 |
| --- | --- | --- |
| Initialization | Base model with fresh LoRA adapters | Resume stage 0 with model, optimizer, and scheduler state |
| Updates | 2,000 | 6,000 additional updates |
| Training data and loss weights | Both mined annotation sets, ranking 1.0, binary 1.0, attribute 0.25 | Same |
| Learning rates | Configured base LR 2e-5 for language and 5e-6 for vision | Continue the saved cosine schedule without resetting the learning rates |

Render the configuration, then train each stage:

```bash
.venv-train/bin/python -m cosmos_reranker.finetuning.recipe configure \
  --pas-root "$PAS_ROOT" --data-root "$DATA_ROOT" --cache-root "$CACHE_ROOT" \
  --run-root "$RUN_ROOT" --gpus 8
.venv-train/bin/python -m cosmos_reranker.finetuning.recipe train stage0 --run-root "$RUN_ROOT"
.venv-train/bin/python -m cosmos_reranker.finetuning.recipe train stage1 --run-root "$RUN_ROOT"
```

The templates are in `src/cosmos_reranker/finetuning/configs/`. Rendered configs
are saved under `$RUN_ROOT/configs/`. Shared settings are:

| Setting | Value |
| --- | --- |
| Language/visual LoRA | Rank 32, alpha 64, no dropout, BF16 frozen weights, FP32 trainable weights |
| Sampling | K20 groups, global candidate batch 640, microbatch 20, seed 160901 |
| Optimizer | AdamW, betas 0.9/0.999, weight decay 0.01, gradient clip 1.0 |
| Schedule | Cosine, 500 warmup steps, minimum LR factor 0.1 |

Keep `max_num_steps=200000` in both configs so the stage boundaries do not
shorten the configured schedule. Training validation is disabled. W&B defaults
to offline mode. Different GPU counts or kernels can cause small numerical
differences. Training also supports 16 or 32 GPUs through `configure --gpus`.

### 4. Export the fine-tuned reranker

Split the trained adapter into language and visual weights, then merge the visual
adapter into a base model for inference. The language adapter loads during inference.

```bash
.venv-train/bin/python -m cosmos_reranker.finetuning.recipe export --run-root "$RUN_ROOT"
export ADAPTER="$RUN_ROOT/deploy"
export MERGED_MODEL="$RUN_ROOT/merged-vision"
.venv-train/bin/python -m cosmos_reranker.finetuning.recipe merge-vision \
  --adapter "$ADAPTER" --output "$MERGED_MODEL"
```

Use these exported paths with `./evaluate_8gpu.sh fine-tuned test` or the
image/video API above.

## Package layout

| Folder | Contents |
| --- | --- |
| `src/cosmos_reranker/reranking/` | Direct Python API for image, video, and segment scoring. |
| `src/cosmos_reranker/evaluation/` | Shared SigLIP2 embeddings, PAS evaluation, and score fusion. |
| `src/cosmos_reranker/finetuning/` | Mining, vision caching, training losses, configuration, and adapter export. |
| `evaluate_8gpu.sh` | Eight-GPU evaluation and result merging. |
| `checkpoints/finetuned-cr3/` | Bundled language and visual adapters. |
| `artifacts/` | Gitignored data, caches, generated checkpoints, and results. |
