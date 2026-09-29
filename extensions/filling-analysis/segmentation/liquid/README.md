# Custom liquid segmenter

The custom Seg Nano checkpoint is adapted to the reviewed camera. Obtain it separately and verify the hash in [the QA recipe](../../deployment/qa/README.md).

Training helpers are optional and never run during build/runtime. Set FILLING_TRAINING_DIR and FILLING_PRETRAIN_CHECKPOINT to external assets. Supply the reviewed media used by build_dataset.py. Weak labels and repeated synthetic compositions limit independence. Inference uses a neural mask without HSV fallback; exterior overflow is separate.
