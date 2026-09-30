#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

usage() {
  echo "Usage: $0 {zero-shot|fine-tuned} [test|validation]"
  echo "Source .env first. Evaluation uses GPUs 0–7 and merges all eight shards."
}
if [[ "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi
if (( $# < 1 || $# > 2 )); then
  usage >&2
  exit 2
fi

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
variant="$1"
split="${2:-test}"
model_options=()
case "$variant" in
  zero-shot) model_options=(--model-id nvidia/Cosmos3-Nano) ;;
  fine-tuned)
    model_options=(--model-id "${MERGED_MODEL:?Source .env and set MERGED_MODEL}"
      --lora-path "${ADAPTER:?Source .env and set ADAPTER}/language" --max-lora-rank 32)
    ;;
  *) usage >&2; exit 2 ;;
esac

query_options=()
case "$split" in
  test)
    pairs_file="${TEST_ROOT:?Source .env and set TEST_ROOT}/test_pairs.json"
    image_root="$TEST_ROOT/images"
    embeddings="${TEST_EMBED:?Source .env and set TEST_EMBED}"
    query_options=(--deduplicate-scalar-plus-accessories)
    ;;
  validation)
    pairs_file="${PAS_ROOT:?Source .env and set PAS_ROOT}/val_pairs.json"
    image_root="$PAS_ROOT/images"
    embeddings="${VAL_EMBED:?Source .env and set VAL_EMBED}"
    query_options=(--query-subset-file "${VAL_QUERY_SUBSET:?Source .env and set VAL_QUERY_SUBSET}")
    ;;
  *) usage >&2; exit 2 ;;
esac

python_bin=.venv-eval/bin/python
if [[ ! -x "$python_bin" ]]; then
  echo "Install .venv-eval as described in README.md." >&2
  exit 2
fi
export OMP_NUM_THREADS=1
export TOKENIZERS_PARALLELISM=false
root="artifacts/evaluation/$variant"
shards="$root/$split-shards"
mkdir -p "$shards"
pids=()
stop_workers() {
  for pid in "${pids[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait || true
}
trap 'stop_workers; exit 130' INT
trap 'stop_workers; exit 143' TERM

echo "Evaluating $variant $split on GPUs 0–7. Logs: $shards/gpu-*.log"
for gpu in {0..7}; do
  CUDA_VISIBLE_DEVICES="$gpu" \
  "$python_bin" -u -m cosmos_reranker.evaluation.evaluate \
    --pairs-file "$pairs_file" --image-root "$image_root" \
    --image-embeddings "$embeddings/image_embeddings.npy" \
    --text-embeddings "$embeddings/text_embeddings.npy" \
    --reranker cosmos_reason --tensor-parallel-size 1 \
    --max-model-len 2048 --reranker-score-chunk-size 64 \
    --modes scalar_plus_accessories "${query_options[@]}" \
    --rerank-depth 20 --k 5 --save-query-rankings \
    --query-batch-size 32 --running-log-every 32 \
    --shard-count 8 --shard-index "$gpu" --run-name "$variant" \
    --output-dir "$shards/$gpu" "${model_options[@]}" \
    > "$shards/gpu-$gpu.log" 2>&1 &
  pids+=("$!")
done

failed=0
for pid in "${pids[@]}"; do
  wait "$pid" || failed=1
done
pids=()
if [[ "$failed" -ne 0 ]]; then
  echo "A worker failed. Check $shards/gpu-*.log" >&2
  exit 1
fi

"$python_bin" -m cosmos_reranker.evaluation.merge_results \
  --run-name "$variant" --k 5 --modes scalar_plus_accessories \
  --output-dir "$root/$split" "$shards"/{0..7}
echo "Merged results: $root/$split/scalar_plus_accessories/"
