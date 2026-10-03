# Corpus and Datasets

## Layout

```text
ingest-corpus/
├── LICENSE
├── 50MB/
│   └── clip-001.mp4
├── 500MB/
├── 2GB/
└── 10GB/
```

The class folder name is the selector. `--video-class 2GB` reads `ingest-corpus/2GB/`
and nothing else. Only `.mp4` and `.mkv` are read: the VSS ingest route accepts
`video/mp4` and `video/x-matroska` only, and any other extension fails validation
rather than being silently skipped.

## Bringing your own video

The four size classes are the shipped convenience, not a constraint. There are two
ways to benchmark footage of your own, and both produce identical metrics, CSVs, and
charts — a user clip is a first-class input, not a special case.

### 1. `--video` — no corpus layout at all

```bash
python scripts/run.py --profile custom --concurrency 1 --concurrency 5 \
  --video ~/footage/warehouse.mp4 --video-class-name warehouse-4k
```

- Repeatable. Each `--video` is a file **or** a directory of them; a directory is read
  one level deep, so nothing is pulled in from a subfolder you did not mean to
  include.
- No `--corpus` is required when every class in the run comes from `--video`.
- A named file with an unsupported extension is an error, not a silent skip. Skipping
  would quietly change the video-minute denominator.
- Duplicates across several `--video` arguments are collapsed by resolved path.

### 2. A named folder under the corpus root

```text
ingest-corpus/
└── warehouse-4k/
    ├── clip-a.mp4
    └── clip-b.mp4
```

```bash
python scripts/run.py --profile custom --concurrency 1 \
  --corpus ./ingest-corpus --video-class warehouse-4k
```

Any folder name works, not just the four predefined sizes. The name must be 1–32
characters of `A-Z a-z 0-9 . - _`, starting with a letter or digit, because it becomes
a directory name, a CSV cell, and a chart tick label.

### How a user class interacts with the sweep

- `--video` **on its own replaces** the profile's classes. Someone who names their own
  footage wants that footage benchmarked, not that footage plus a 500MB class they
  never asked for.
- `--video` **combined with an explicit `--video-class`** adds one more class. That is
  how you put your own clip and a predefined size on the same chart.
- The class name flows into `ingest_corpus.csv`, `ingest_summary.csv`, `summary.md`,
  and the chart legends. Name it after the content.
- CLI owns upload timeouts; ES readiness budgets use measured duration/fps.

### What the footage contains still decides whether the run confirms

During Elasticsearch readiness, RT-CV writes an `mdx-raw` document only
for frames in which it detected an object. Footage with no people or vehicles —
colour bars, a static test pattern, an empty corridor — produces **zero** raw
documents. Every upload returns HTTP 200, `embed` reaches `N/N` on its timer, and
`raw` stays at `0/N` until the readiness timeout expires.

That is a property of the video, not a fault in the deployment. Before concluding
anything from an all-unconfirmed run on user footage, check the clip actually contains
detectable objects.

## One file per class

Prefer one file per class. Uploads per sweep point scale with the file count:

```text
uploads = concurrency x videos in the class
```

A 2GB class holding three clips at concurrency 20 is 60 uploads and 120 GB, not 20
uploads and 40 GB. That is a legitimate configuration, but it is rarely what someone
asking for "2GB at concurrency 20" has in mind. Show the projection before starting.

`--limit N` takes the first N files in sorted order — the "corpus subset" sweep
dimension. It is recorded in `run-metadata.json` because it changes the upload count.

## Every file is measured, none is skipped

Throughput is counted in video-minutes, so a guessed length corrupts every number in
the run. Before the sweep starts, every file in every selected class is measured with
`ffprobe`:

```bash
ffprobe -v error -print_format json \
  -show_entries format=format_name,duration,bit_rate:stream=codec_type,codec_name,avg_frame_rate,width,height \
  clip-001.mp4
```

A file that cannot be read, carries no video stream, or reports a non-positive
duration **fails validation** rather than being skipped, because skipping changes the
denominator.

The result is `csv/ingest_corpus.csv`, written before the first upload. It is part of
the run's evidence: two runs with different corpora are not comparable, and this file
is how that is shown.

## Class sizing

The class name is a label, not an assertion the tooling checks. A 50MB class holding a
2 GB file will run, and every number will be correct — but the chart legend will read
`50MB` and the run will not be comparable to anything. Size the files to the label, or
give the class a name of its own — that is exactly what `--video-class-name` is for.

Nominal targets, matching the harness:

| Class | Nominal file size |
|---|---|
| `50MB` | ~50 MB |
| `500MB` | ~500 MB |
| `2GB` | ~2 GB |
| `10GB` | ~10 GB |

## Datasets must be versioned and licensed

Phase 1 datasets must be available and versioned for download — NGC or GitHub — and
must **not** be included in the skill. `validate.py` warns when there is no `LICENSE`
next to the corpus root; every dataset needs one describing source and usability
rules.

The NVIDIA VSS sample bundle is one option:

```bash
ngc registry resource download-version \
  nvidia/vss-developer/dev-profile-sample-data:3.2.0 \
  --org nvidia --team vss-developer
tar -xzf dev-profile-sample-data_v3.2.0/dev-profile-sample-data.tar.gz
```

That bundle is governed by the NVIDIA Asset License (`LICENSE.DATA` in the VSS repo):
trial and demo use only, no redistribution, no model training, expiring twelve months
after download. Its clips are short demo videos, so they suit the `50MB` class and a
smoke run — they are not large enough for a `2GB` or `10GB` class.

For the larger classes, use content the user is licensed to hold, and record its
provenance in the corpus `LICENSE`. Do not synthesize a large file by concatenating a
small one without saying so: the resulting GOP structure and scene statistics are not
representative, and the embedding path behaves differently on them.

## Never commit corpus files

Recommended `.gitignore` entries wherever a corpus lives beside code:

```text
ingest-corpus/
benchmark-results/
*.mp4
*.mkv
```

## Storage read cost is inside the measurement

Workers stream chunks straight from the corpus path as they send, so a slow local disk
or a network mount shows up in `latency_sec` and in `aggregate_mb_per_sec`. That is
deliberate — it is what the client experienced. It is also a reason not to compare a
run from an NFS-mounted corpus against a run from a local NVMe corpus and call the
difference a deployment change.

Corpus storage mode is one of the harness dimensions Phase 1 drops (`storage_modes`),
precisely because varying it properly needs a cluster-mounted volume. Keep the corpus
on one medium for all points in a comparison, and note the medium in the summary.
