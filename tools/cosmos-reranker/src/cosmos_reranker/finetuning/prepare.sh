#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Rebuild the PAS step-03906 K20 annotations, attribute records, and vision cache.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
recipe="$repo/artifacts/recipe"
pas_root="${PAS_DATA_ROOT:?Set PAS_DATA_ROOT through the preparation script}"
retriever="${RETRIEVER_CHECKPOINT:?Set RETRIEVER_CHECKPOINT through the preparation script}"
tokenizer="${SIGLIP_TOKENIZER:?Set SIGLIP_TOKENIZER through the preparation script}"
val_denylist="${VAL_CAPTION_DENYLIST:?Set VAL_CAPTION_DENYLIST through the preparation script}"
output_root="${PAS_DATA_OUTPUT:-$repo/artifacts/reproduction/data/step03906}"
embed_root="${PAS_EMBED_OUTPUT:-$repo/artifacts/reproduction/data/step03906_embeddings}"
cache_root="${PAS_CACHE_OUTPUT:-$repo/artifacts/reproduction/cache}"
cache_model="${CR3_BASE_MODEL:?Set CR3_BASE_MODEL through the preparation script}"
python_bin="${PYTHON_BIN:-python}"
torchrun_bin="${TORCHRUN_BIN:-torchrun}"
world_size="${WORLD_SIZE:-8}"
master_port="${PAS_TORCHRUN_MASTER_PORT:-29600}"
mode="${1:-all}"
if [[ "$world_size" != 8 ]]; then
  echo "This recipe requires WORLD_SIZE=8 to match the saved embedding and cache shards" >&2
  exit 2
fi

balanced="$output_root/query_indices_trainonly_balanced250k_seed880821.json"
remaining="$output_root/query_indices_trainonly_remaining_after750k_seed880822.json"
balanced_embed="$embed_root/trainonly_balanced750k_seed880821"
remaining_embed="$embed_root/trainonly_remaining_after750k_seed880822"
gallery="${TRAIN_IMAGE_EMBEDDINGS:-$balanced_embed/image_embeddings.npy}"
balanced_annotations="$output_root/train_deployed_top20_k20_trainonly750k_seed880821.json"
remaining_annotations="$output_root/train_deployed_top20_k20_remaining_after750k_seed880822.json"

export PYTHONPATH="$repo/src${PYTHONPATH:+:$PYTHONPATH}"
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
export TOKENIZERS_PARALLELISM=false
if [[ -d /usr/local/cuda-13.2/compat/lib.real ]]; then
  export LD_LIBRARY_PATH="/usr/local/cuda-13.2/compat/lib.real${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
cd "$repo"
mkdir -p "$output_root" "$embed_root" "$cache_root"

indices() {
  if [[ ! -s "$balanced" ]]; then
    "$python_bin" src/cosmos_reranker/finetuning/sample_trainonly_query_indices.py \
      --pairs-file "$pas_root/train_pairs.json" --output "$balanced" \
      --manifest "${balanced%.json}.manifest.json" --per-type 250000 \
      --seed 880821 --exclude-caption-file "$val_denylist"
  fi
  if [[ ! -s "$remaining" ]]; then
    "$python_bin" src/cosmos_reranker/finetuning/sample_trainonly_query_indices.py \
      --pairs-file "$pas_root/train_pairs.json" --output "$remaining" \
      --manifest "${remaining%.json}.manifest.json" --all-eligible \
      --seed 880822 --exclude-indices-file "$balanced" \
      --exclude-caption-file "$val_denylist"
  fi
}

embed_one() {
  local indices_file="$1" destination="$2" image_mode="$3"
  local complete=1 rank stem
  for ((rank=0; rank<world_size; rank++)); do
    printf -v stem 'text_embeddings.shard_%05d_of_%05d' "$rank" "$world_size"
    [[ -s "$destination/$stem.npy" && -s "$destination/$stem.json" ]] || complete=0
  done
  if [[ "$image_mode" == images-and-text ]]; then
    [[ -s "$destination/image_embeddings.npy" && -s "$destination/image_embeddings.json" ]] || complete=0
  fi
  [[ "$complete" == 1 ]] && return
  local flags=()
  if [[ "$image_mode" == text-only ]]; then flags+=(--text-only); fi
  "$torchrun_bin" --master-addr=127.0.0.1 --master-port="$master_port" --nproc-per-node="$world_size" \
    src/cosmos_reranker/finetuning/generate_siglip2_selected_embeddings_distributed.py \
    --pairs-file "$pas_root/train_pairs.json" --image-root "$pas_root/images" \
    --checkpoint "$retriever" --tokenizer-dir "$tokenizer" \
    --query-indices "$indices_file" --output-dir "$destination" \
    --image-batch-size 128 --text-batch-size 512 --dtype float16 "${flags[@]}"
}

embeddings() {
  indices
  if [[ -s "$gallery" ]]; then
    embed_one "$balanced" "$balanced_embed" text-only
  else
    if [[ "$gallery" != "$balanced_embed/image_embeddings.npy" ]]; then
      echo "Missing TRAIN_IMAGE_EMBEDDINGS: $gallery" >&2; exit 2
    fi
    embed_one "$balanced" "$balanced_embed" images-and-text
  fi
  [[ -s "$gallery" ]] || { echo "Missing gallery array: $gallery" >&2; exit 2; }
  embed_one "$remaining" "$remaining_embed" text-only
}

mine_one() {
  local indices_file="$1" shards="$2" destination="$3" selection="$4"
  if [[ -s "$destination" && -s "${destination%.json}.manifest.json" ]]; then return; fi
  local flags=()
  if [[ "$selection" == deployed ]]; then flags+=(--deployed-topk-group); fi
  "$torchrun_bin" --master-addr=127.0.0.1 --master-port="$master_port" --nproc-per-node="$world_size" \
    src/cosmos_reranker/finetuning/mine_siglip_hard_negatives_distributed.py \
    --pairs-file "$pas_root/train_pairs.json" --image-embeddings "$gallery" \
    --text-embedding-shards "$shards" --query-indices "$indices_file" \
    --checkpoint "$retriever" --output "$destination" --batch-size 512 \
    --topk 20 --group-size 20 --ground-truth scalar_plus_accessories "${flags[@]}"
}

mine() {
  embeddings
  mine_one "$balanced" "$balanced_embed" "$balanced_annotations" deployed
  mine_one "$remaining" "$remaining_embed" "$remaining_annotations" rotating
}

attributes() {
  "$python_bin" src/cosmos_reranker/finetuning/build_attribute_replay_records.py \
    --train-pairs "$pas_root/train_pairs.json" \
    --val-pairs "$pas_root/val_pairs.json" \
    --output-dir "$output_root/attribute_replay"
}

cache_one() {
  local image_list="$1" destination="$2"
  local complete=1 rank manifest
  for ((rank=0; rank<world_size; rank++)); do
    printf -v manifest 'manifest_rank%02d.json' "$rank"
    [[ -s "$destination/$manifest" ]] || complete=0
  done
  [[ "$complete" == 1 ]] && return
  "$torchrun_bin" --master-addr=127.0.0.1 --master-port="$master_port" --nproc-per-node="$world_size" \
    src/cosmos_reranker/finetuning/build_visual_cache.py \
    --image-lists "$image_list" --image-root "$pas_root/images" \
    --output-dir "$destination" --model "$cache_model" \
    --batch-size 32 --decode-workers 8 --max-pixels 81920 \
    --linear-patch-embed --prefix-block 24
}

cache() {
  if [[ ! -s "$recipe/cache_primary_images.txt" || ! -s "$recipe/cache_supplement_images.txt" ]]; then
    mkdir -p "$recipe"
    "$python_bin" - "$pas_root/train_pairs.json" "$recipe" <<'PY_CACHE'
import sys
from pathlib import Path
from cosmos_reranker.finetuning.prepare_annotations import cache_image_names

pairs, recipe = Path(sys.argv[1]), Path(sys.argv[2])
ordered = cache_image_names(pairs)
if len(ordered) != 222217:
    raise SystemExit(f"Expected 222217 training images, found {len(ordered)}")
for name, selection in (("primary", ordered[:217743]), ("supplement", ordered[217743:])):
    (recipe / f"cache_{name}_images.txt").write_text("\n".join(selection) + "\n")
PY_CACHE
  fi
  cache_one "$recipe/cache_primary_images.txt" \
    "$cache_root/pas_cr3_block24_prefix_step03906_trainonly750k_star"
  cache_one "$recipe/cache_supplement_images.txt" \
    "$cache_root/pas_cr3_block24_prefix_step03906_top50_supplement4474_clean"
}

case "$mode" in
  indices) indices ;;
  embeddings) embeddings ;;
  mine) mine ;;
  attributes) attributes ;;
  cache) cache ;;
  all) mine; attributes; cache ;;
  *) echo "Usage: $0 {indices|embeddings|mine|attributes|cache|all}" >&2; exit 2 ;;
esac

echo "Prepared PAS data under $output_root, $embed_root, and $cache_root"
