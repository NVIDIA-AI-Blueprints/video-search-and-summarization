# Source lifecycle CLI

Run source mutations from the repository root through the guarded host script:

```bash
bash skills/vss-search-archive/scripts/manage_search_source.sh
```

Set exactly one `ACTION` and the corresponding inputs. Do not enable shell
xtrace because RTSP credentials and API request bodies may be present.

## Deployment selectors

- Docker: `DEPLOYMENT=docker PROFILE=search`. The selected profile's
  `generated.env` supplies its published loopback ports and runtime index names.
- Kubernetes: `DEPLOYMENT=kubernetes NAMESPACE=<namespace> RELEASE=<release>`
  and optional `KUBE_CONTEXT=<context>`. The script opens loopback-only managed
  port-forwards and closes them on normal exit, failure, or interruption. It
  follows the live file-ingest RTVI-CV URL exactly, including an internal
  HAProxy path prefix. A direct RTVI-CV Service is accepted only when it has one
  ready backend (which requires Endpoints read access); multi-replica cleanup
  requires an affinity-routed endpoint whose live Ingress annotations and path
  can be read and verified.
- Explicit host-reachable `VSS_AGENT_URL`, `VST_URL`, `ES_URL`,
  `BEHAVIOR_ES_URL`, `RAW_ES_URL`, `RTVI_CV_URL`, and `VST_FORWARD_URL` override discovered
  transport URLs. The three Elasticsearch URLs correspond to embed, behavior,
  and raw-frame cleanup respectively; set all three when discovery cannot read
  an operator-managed cleanup endpoint. Management URLs must use HTTP(S),
  include a hostname, and must not embed userinfo, query credentials, or URL
  fragments; pass authentication through an operator-managed workflow instead.

## Actions

### File ingestion

```bash
ACTION=file-ingest \
FILE_PATH=/absolute/path/clip.mp4 \
FILENAME=clip.mp4 \
DEPLOYMENT=docker PROFILE=search \
bash skills/vss-search-archive/scripts/manage_search_source.sh
```

`FILENAME` must be a safe, whitespace-free basename. The script performs the
agent handshake, chunked upload, completion call, VST readiness check, and
transactional cleanup on failure. The completion response must identify the
same VST sensor. If the completion request disconnects or times out, cleanup is
attempted but never certified: server-side embedding may still be running, so
operator verification is required.

### RTSP ingestion

```bash
ACTION=rtsp-ingest \
RTSP_URL=rtsp://camera.example/live \
SOURCE_NAME=loading-dock \
DEPLOYMENT=docker PROFILE=search \
bash skills/vss-search-archive/scripts/manage_search_source.sh
```

Set optional `RTSP_USERNAME`. Leave `RTSP_PASSWORD` unset for a hidden prompt,
or set `RTSP_PASSWORD_FILE` to an operator-managed file with no group/other
permissions. `SOURCE_NAME` must not already exist in VST; this keeps the
name-addressed rollback unambiguous after an agent-confirmed add. The rollback
key is armed before the POST, but a failed or transport-unknown add never sends
a name-only delete because ownership cannot be proved against a concurrent
caller. Source names and video IDs must be non-dot single path segments. A
successful registration is not reported as complete until an exact
embedding becomes searchable; a readiness failure is rolled back.

### File deletion

```bash
ACTION=file-delete \
VIDEO_ID=<exact-vst-id> SOURCE_NAME=<exact-name> \
DEPLOYMENT=docker PROFILE=search \
bash skills/vss-search-archive/scripts/manage_search_source.sh
```

### RTSP deletion

```bash
ACTION=rtsp-delete \
VIDEO_ID=<exact-vst-id> SOURCE_NAME=<exact-name> \
DEPLOYMENT=kubernetes NAMESPACE=vss RELEASE=search \
bash skills/vss-search-archive/scripts/manage_search_source.sh
```

Both deletion actions verify that the ID/name pair identifies one live source
of the expected kind before mutation. RTSP deletion requires agent `success`
before exact history cleanup, because a `partial` result can leave a producer
active. File deletion may reconcile agent `partial`: it independently retries
VST storage and sensor deletion, sends the RTVI-CV removal with the sensor ID
routing header, cleans the discovered embed/behavior/raw index contracts, and
then requires an empty VST timeline, empty physical media-file listing, absent
sensor, and zero exact Elasticsearch counts. Missing exact indexes count as
already clean only for optional behavior/raw families; the deployed embed index
must exist. A 404 from RTVI-CV is accepted only through a live, verified
x-stream-id HAProxy Ingress or a verified singleton route. External URL path
shape and an overall agent success are not treated as proof of affinity, and
the 404 body must be the structured `NotFound` response naming the exact camera
ID rather than a generic route-level 404.

## Timing and explicit index overrides

`INGEST_WAIT_SECONDS`, `DELETE_WAIT_SECONDS`, `CHUNK_SIZE_BYTES`, and
`CHUNK_TIMEOUT_SECONDS` accept positive integers. RTSP rollback polls for a
late post-mutation VST registration for `RTSP_ROLLBACK_DISCOVERY_SECONDS`
(default 30) and refuses a name-addressed delete when the name is missing,
ambiguous, or maps to a different sensor. `VIDEO_INDEX`,
`VIDEO_INDEX_WILDCARD`, `BEHAVIOR_INDEX`, `BEHAVIOR_INDEX_WILDCARD`,
`RAW_INDEX`, and `RAW_INDEX_WILDCARD` may override discovered values for an
operator-managed deployment; unsafe index expressions are rejected.
