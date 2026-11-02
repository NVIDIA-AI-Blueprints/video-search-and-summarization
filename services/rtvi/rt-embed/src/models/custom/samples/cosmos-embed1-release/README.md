# Cosmos ONNX release adapter

Use this opt-in sample for the verified Cosmos v1.2 release with FP32 video input
`[B,3,8,448,448]`, int64 text inputs `[B,128]`, and normalized FP32 embeddings
`[B,768]`. The existing `cosmos-embed1` sample supplies frame selection and resizing.
This adapter arranges/scales those frames; it does not export or retrain weights.

The supplied release root must contain `MANIFEST.sha256`, `onnx/` with the named
vision/text graphs and external vision weights, and the model directory containing
its processor/configuration files. The builder validates checksums, graph contracts,
and the supported batch size before sealing engines and configurations in `build.json`.
It rejects stale or incomplete engine repositories. Use a separate output repository
to rebuild for different assets, adapter code, TensorRT version, or GPU identity.

```bash
python create_triton_model_repo.py --model_path "$MODEL_DIRECTORY" \
  --triton_repo_path "$TRITON_REPOSITORY" --max_batch_size 64
```

Select this sample through the existing custom-model configuration. Runtime engine
lookup follows the existing service convention `/tmp/triton_model_repo`; the
repository supplied to the builder must be mounted there for inference. Engine
compilation requires the service's existing ONNX/TensorRT tools and GPU. Model
assets are not included. Host contract tests validate input/output and stale-artifact
handling; changing checkpoint, hardware, or deployment still requires an integration run.
