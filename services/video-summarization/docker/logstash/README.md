<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

# Logstash sidecar for the RTVI-VLM Kafka -> Elasticsearch path

This image consumes `nv.VisionLLM` protobuf messages from the two Kafka
topics produced by the streaming RTVI-VLM flow
(`mdx-vlm-captions` and `mdx-structured-events-summary`) and writes
documents into Elasticsearch in the **exact shape** that
`ElasticsearchDBTool.add_summary` produces in via-ctx-rag.

## Why this exists

In the streaming-only architecture, RTVI-VLM publishes raw VLM events
directly to Kafka instead of streaming SSE chunks back to via-engine.
via-engine adds structured event batches and the aggregated narrative
summary to a second Kafka topic. Logstash is the sole writer to
Elasticsearch; the previous Python `services/kafka_consumer/` is retired.

## How it runs

There is **no custom Dockerfile**. The sidecar uses the stock
`docker.elastic.co/logstash/logstash:8.15.3` image and installs the
`logstash-codec-protobuf` plugin on first container boot, caching the
gem tree in a Docker named volume so subsequent recreates skip the
install. All configuration (`config/logstash.yml`,
`pipeline/visionllm.conf`, `pb_definitions/nv_pb.rb`) is bind-mounted
into the container from this directory.

Because `config.reload.automatic: true` is set in `config/logstash.yml`
(interval 10s), editing `pipeline/visionllm.conf` on the host hot-
reloads the pipeline inside the running container — no rebuild and no
`docker compose restart` needed.

### Running as part of the LVS stack (common case)

```bash
cd long-video-summarization/compose/BlueprintBuilderGenerated
docker compose --profile rtvi --profile kafka up -d
```

Logstash is wired to the stack's `app-network` and depends on healthy
`elasticsearch`, `kafka`, and `lvs` services. First boot on a fresh
volume takes ~1-2 min for the plugin install; warm restarts boot in
seconds.

### Running standalone (narrow case)

Against an externally-provided Kafka + ES (e.g. the rtvi-microservices
compose broker + a managed ES endpoint), use the standalone file in
this directory:

```bash
cd long-video-summarization/docker/logstash
KAFKA_BOOTSTRAP_SERVERS=host.docker.internal:9094 \
  ES_HOST=host.docker.internal ES_PORT=9200 \
  docker compose -f logstash-compose.yml up -d
```

See the header of `logstash-compose.yml` for the Linux-host variants
(without `host.docker.internal`). The standalone file uses its own
compose project name (`lvs-logstash-standalone`) so its volume does
not collide with the main stack's.

### Regenerating `nv_pb.rb`

When `src/protos/nv.proto` changes, regenerate the Ruby class
definitions used by the protobuf codec:

```bash
protoc \
  --proto_path=long-video-summarization/src/protos \
  --ruby_out=long-video-summarization/docker/logstash/pb_definitions \
  long-video-summarization/src/protos/nv.proto

python3 long-video-summarization/ci/utils/add_generated_protobuf_headers.py
```

The helper uses the current year for a newly-created generated file and
preserves the original year as a range when an existing generated file
is refreshed in a later year.

Then restart the logstash container so the new `.rb` file loads
(`docker compose restart logstash`). No image rebuild is involved.

## Gotcha 1: the two service definitions must stay in sync

The logstash service is defined in **both**
[`compose/BlueprintBuilderGenerated/docker-compose.yml`](../../compose/BlueprintBuilderGenerated/docker-compose.yml)
(inline, for the LVS stack) and in
[`docker/logstash/logstash-compose.yml`](logstash-compose.yml)
(standalone). Changes to image, startup `command`, plugin version, env
vars, healthcheck, or bind-mount layout must be applied to both.

Audit with:

```bash
cd long-video-summarization
diff \
  <(COMPOSE_PROFILES=rtvi,kafka docker compose \
      -f compose/BlueprintBuilderGenerated/docker-compose.yml \
      --env-file compose/BlueprintBuilderGenerated/.env \
      config --format json 2>/dev/null | \
    jq '.services.logstash | del(.environment,.volumes,.labels)') \
  <(docker compose -f docker/logstash/logstash-compose.yml \
      config --format json 2>/dev/null | \
    jq '.services.logstash | del(.environment,.volumes,.labels)')
```

Expected differences only:

| Field | Main stack | Standalone |
|---|---|---|
| `container_name` | `logstash` | `lvs-logstash` |
| `depends_on` | `elasticsearch`, `kafka`, `lvs` healthy | (absent) |
| `networks` | `app-network` | `default` |
| `profiles` | `[kafka]` | (absent) |

Bind-mount path roots also differ (`../../docker/logstash/...` in the
main file, `./...` in the standalone file) and `environment` default
values are slightly different (standalone exposes `ES_PORT` as
env-overridable). Those do not show up in the `jq` diff because the
recipe strips `environment` and `volumes` to focus on structural
differences only.

## Gotcha 2: bumping Logstash version = reset the plugin volume

The `logstash-plugins` named volume persists the installed
`logstash-codec-protobuf` gem tree across container recreates. When
you bump `LOGSTASH_VERSION` (in `.env` or inline), the cached gems
were compiled for the old JRuby runtime and will fail to load on the
new image with an error like `cannot load such file -- google/protobuf_java`
or a version-mismatch stack trace at boot.

Fix by removing the volume so the plugin re-installs on next boot:

```bash
# Main stack
docker compose -f compose/BlueprintBuilderGenerated/docker-compose.yml \
  --profile rtvi --profile kafka down
docker volume rm h100-integrated-cr2-nemotron-9b-8gpu_logstash-plugins

# Standalone
docker compose -f docker/logstash/logstash-compose.yml down
docker volume rm lvs-logstash-standalone_logstash-plugins
```

The project name prefix (`h100-...` / `lvs-logstash-standalone`) comes
from each file's top-level `name:` field. If you use a non-default
project name, `docker volume ls --format '{{.Name}}' | grep logstash-plugins`
lists the candidates.

## Pipeline outline

| Stage | What it does |
|---|---|
| Kafka input | Subscribes to both topics; consumer group `logstash-vlm-es-writer`; protobuf codec decodes `nv.VisionLLM`. |
| `mutate` | Promotes `llm.queries[0].response` to top-level `text`. |
| `ruby` | Builds `metadata.source` + `metadata.content_metadata.*`, normalises empties to `nil`, derives ES `_id`, attaches a zero `vector` of length `LVS_EMB_DIMENSIONS`. |
| `mutate.convert` | Restores native int/float/bool types for the enumerated metadata fields (proto wire is `map<string,string>`). |
| `mutate.remove_field` | Drops `info`, `llm`, `@version`, `event`. |
| Elasticsearch output | `index = info[collection_name]`, `document_id = (collection):(uuid):(doc_type):(chunkIdx\|batch_i\|doc_i)`. Idempotent on Kafka redeliveries. |

## Document shape

```json
{
  "text":   "<llm.queries[0].response, verbatim>",
  "vector": [0.0, 0.0, ...],
  "metadata": {
    "source": "<info[source] or info[file] or 'N/A'>",
    "content_metadata": {
      "doc_type": "raw_events|structured_events|aggregated_summary",
      "uuid": "...",
      "...": "..."
    }
  }
}
```

This matches `ElasticsearchDBTool.add_summary` so existing read queries
(`metadata.content_metadata.<field>.keyword` term filters,
`metadata.content_metadata.start_ntp_float` range filters, etc.) work
unchanged.

## Embedding vector

`LVS_EMB_ENABLE=false` always for this path. The Ruby filter writes a
fixed-length zero vector matching `NullEmbedding` (default
`LVS_EMB_DIMENSIONS=1024`). Similarity search on the resulting documents
returns indistinguishable results, but every other query path
(`retrieve_docs`, `filter_chunks`, `aget_text_data`,
`aget_max_batch_index`) uses exact-term filters that don't need the
vector.

## Environment variables

| Var | Default | Purpose |
|---|---|---|
| `KAFKA_BOOTSTRAP_SERVERS` | `kafka:9092` | Broker address (use the host:port of the rtvi-microservices Kafka broker). |
| `KAFKA_CONSUMER_GROUP` | `logstash-vlm-es-writer` | Single shared group across replicas. |
| `ES_HOST` | `elasticsearch` | Elasticsearch host. |
| `ES_PORT` | `9200` | Elasticsearch port. |
| `LVS_EMB_DIMENSIONS` | `1024` | Length of the zero vector written into `vector`. Must match the index template's `dense_vector` `dims`. |

## Audit: Ruby reserved words in `nv.proto`

`logstash-codec-protobuf` 1.3.0 fails to load Ruby files whose protobuf
field names collide with Ruby reserved words AT THE SYMBOL LEVEL
(`extend`, `class`, `module`, etc., see
[logstash-codec-protobuf#71](https://github.com/logstash-plugins/logstash-codec-protobuf/issues/71)).

### Result of the audit (last run on this branch)

```bash
grep -nE '\b(end|class|extend|module|def|begin)\b\s*=\s*[0-9]+' \
  long-video-summarization/src/protos/nv.proto
# 661:  google.protobuf.Timestamp end = 3;
```

The single hit is `VisionLLM.end` on line 661. This **does not** break
the codec's load step because `protoc --ruby_out` emits field names as
**Ruby symbols inside a DSL block** (`optional :end, :message, 3, ...`)
and accessors via `define_method`, neither of which collides with the
`end` keyword at parse time. Decoding `info[end]` from a wire payload
goes through the descriptor pool's hash lookup — also unaffected.

Re-run this audit any time `nv.proto` is updated:

```bash
grep -nE '\b(end|class|extend|module|def|begin|alias|do|for|while|if|return|rescue|ensure|case|when|then|self|nil|true|false|in|next|not|or|and|redo|retry|undef|unless|until|yield|BEGIN|END|__LINE__|__FILE__|__ENCODING__)\b\s*=\s*[0-9]+' \
  long-video-summarization/src/protos/nv.proto
```

Any new field whose name appears in the list and which the codec
actually has trouble loading would need to be renamed and the codec
config updated.
