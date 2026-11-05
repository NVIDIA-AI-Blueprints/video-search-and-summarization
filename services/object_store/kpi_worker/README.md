# kpi_worker

Data-plane for the KPI-set pipeline — the consumer side of `POST /v1/kpi`
(the backend fans out slices; this service exports them). Structurally mirrors
`snapshot_worker`, minus the "since last" deltas / latest pointer (a KPI set is
a one-shot export).

Two containers off one image:

- **kpi-slice-consumer** — consumes `kpi-slices`, runs the sliced `search_after`
  loop over the shared PIT, streams `manifest<i>.jsonl` to the object store, and
  reports each slice's outcome to `kpi-summary`. Scale up to the slice count:
  `docker compose ... up -d --scale kpi-slice-consumer=3`.
- **kpi-finalizer** — consumes `kpi-summary`; once all N per-slice status markers
  are present it writes `kpi.json` (`ready`/`failed`) and closes the shared PIT.

Object-store layout (`KPI_PREFIX`, default `kpi`):

    kpi/<kpi_id>/
    ├── manifest0.jsonl ... manifest<N-1>.jsonl
    ├── _status/slice0.json ... slice<N-1>.json   (durable per-slice markers)
    └── kpi.json                                   (finalized; read by GET /v1/kpi)

## Tests

    uv run pytest tests -q
