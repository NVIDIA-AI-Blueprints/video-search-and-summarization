# Corpus inputs

Provide either a root with class folders or explicit `--video` files/directories:

```text
ingest-corpus/
├── LICENSE
├── 50MB/clip-001.mp4
├── 500MB/clip-001.mp4
└── warehouse-4k/clip-001.mkv
```

```bash
python scripts/run.py --profile custom --concurrency 1 --concurrency 5 \
  --video ~/footage/warehouse.mp4 --video-class-name warehouse-4k

python scripts/run.py --profile custom --concurrency 1 \
  --corpus ./ingest-corpus --video-class warehouse-4k
```

Directory scans select `.mp4` and `.mkv` one level deep. An explicitly named file
with an unsupported extension fails validation. Repeated user-video sources are
deduplicated by resolved path. `--video` alone replaces configured/profile classes;
with `--video-class`, it adds its own class. No corpus root is needed if every
selected class comes from explicit videos.

Class names are labels, not file-size assertions. Nominal classes are `50MB`,
`500MB`, `2GB` and `10GB`; custom names contain 1–32 letters, digits, dots,
underscores or hyphens and start with a letter or digit.

## Workload size and measurement

Prefer one file per class. Every worker uploads every selected file, so three
2 GB files at concurrency 20 mean 60 uploads and 120 GB of payload. Projected
transfer includes warmup. `--limit N` selects the first N files in sorted order
and is recorded in metadata.

ffprobe measures duration, average frame rate, geometry, codec and container before
uploads. An unreadable file, missing video stream or non-positive duration fails
validation; it is not silently dropped. The corpus CSV preserves measured source
volume used in throughput calculations.

Use footage with detectable objects. Sparse detections can produce fewer raw
documents than this benchmark requires even if media upload and embedding succeed.
An unconfirmed result does not by itself distinguish corpus content from a
processing or indexing problem.

## Provenance and storage

Use content the user is permitted to process. Record its source, version and usage
terms with the corpus; validation warns if the corpus root lacks `LICENSE`.
Do not commit video files or results with the skill. If footage is synthesized or
looped, disclose that because scene statistics and encoding can affect results.

Source reads and transfer are inside client latency. Keep the corpus medium
consistent across comparisons and report whether it is local or network-mounted;
a storage change can alter results without any deployment change.
