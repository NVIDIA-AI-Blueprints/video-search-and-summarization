# Opt-in native PAS evaluation image

Build from this directory's pinned RT-CV base with:

```bash
docker build -t vss-rt-cv-pas:branch deploy/docker/services/rtvi/rtvi-cv/pas-eval
```

`source-identity-before-batching.patch` attaches an owned source identity to each
decoded buffer before it enters the mux. `source_identity_meta.h` copies and
releases the metadata through DeepStream's frame metadata lifecycle.
`frame-sensor-identity.patch` publishes the frame's identity instead of looking
up a reused numeric source slot. Unattributed dynamic frames are skipped.
`test_source_identity.cpp` checks that the probe's owned copy survives source
teardown and string reuse, and that the transform, read and release helpers
round-trip; the Docker build compiles and runs it. It calls those helpers
directly and does not run a source pad, `nvstreammux` or slot reuse in a live
pipeline. Verify the deployed pipeline after ingestion with
`ingest_clip_gallery.py --verify-source-attribution`, which fails if any RT-CV
raw or behavior document names an unregistered source.

`eval-config-i1-i4.patch` is applied to a runtime copy of the search profile
configs by `prepare_pas_eval_profile.py`; stock product configs stay intact.
See the complete branch-contained recipe in
[pas-native-reproduction.md](../../../../../../skills/benchmarking/benchmark-video-search/references/pas-native-reproduction.md).
