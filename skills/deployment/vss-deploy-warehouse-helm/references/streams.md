# Warehouse Helm — Stream Sizing and Install

## Why `syncFileCount` has to match `NUM_STREAMS`

`vios.vss-vios-nvstreamer.syncFileCount` controls how many sample video files NVStreamer syncs
into its own volume. If it's lower than the effective `NUM_STREAMS`, `bp-configurator` will find
fewer streams than it expects and under-register cameras with VST; if it's higher, NVStreamer
wastes time/storage syncing files nothing will consume. `compute_stream_cap.py` prints the
effective (capped) stream count specifically so this can be set to match — see
`warehouse-2d-app/README.md` line ~168: "Keep in step with `bp-configurator` `NUM_STREAMS`."

## Full install sequence

```bash
# 1. Compute the GPU-capped stream count and patch bp-configurator.env
python3 deploy/helm/industry-profiles/warehouse-operations/scripts/compute_stream_cap.py \
  --mode 2d --num-streams 8 -o values-stream-cap.generated.yaml
# -> prints effective stream count N to stderr, e.g. "using 8 stream(s) (cap: 52)"

# 2. Chart dependencies
helm dependency update deploy/helm/industry-profiles/warehouse-operations/warehouse-2d-app

# 3. Install/upgrade — layer the generated file after every other -f/--set override
helm upgrade --install wh deploy/helm/industry-profiles/warehouse-operations/warehouse-2d-app \
  -n <namespace> --create-namespace \
  --set global.vssIngress.enabled=true \
  --set global.externalHost=<NODE_IP> \
  --set global.storageClass=<STORAGE_CLASS> \
  --set monitoring.grafana.rootUrl=http://<NODE_IP>/grafana \
  --set infra.kibana.kibanaPublicUrl=http://<NODE_IP>/kibana \
  --set vios.vss-vios-nvstreamer.syncFileCount=8 \
  -f values-stream-cap.generated.yaml    # last: match the effective count from step 1
```

The non-streams overrides above (`global.vssIngress.enabled`, `externalHost`, `storageClass`,
Grafana/Kibana URLs) are the same host-specific values documented in
`warehouse-2d-app/README.md` §3 Install — this skill doesn't change any of that, it only adds the
stream-cap layer.

## If your install customizes `bp-configurator.env`

The script builds its output by reading the chart's `values.yaml` and patching `NUM_STREAMS` /
`HARDWARE_PROFILE` into that env list. Skip the steps below and the generated file — built from
chart defaults only, then layered last — silently replaces your customized `bp-configurator.env`
with the defaults, since Helm replaces list-typed values wholesale rather than merging them
entry-by-entry. Which steps apply depends on how the customization is set:

**Values file (`-f my-values.yaml`).** Pass that same file to the *script* with `-f`/`--values` so
it merges your customizations in before patching:

```bash
python3 deploy/helm/industry-profiles/warehouse-operations/scripts/compute_stream_cap.py \
  --mode 2d --num-streams 8 -f my-values.yaml -o values-stream-cap.generated.yaml
```

That only handles `bp-configurator.env` — the script's output contains nothing else. If
`my-values.yaml` also sets unrelated things (storage class, ingress, monitoring, alerts flags),
still pass it to `helm` too, generated file layered after it so it wins only on
`bp-configurator.env`:

```bash
helm upgrade --install wh <chart-dir> -n <namespace> \
  -f my-values.yaml \
  -f values-stream-cap.generated.yaml   # last: wins on bp-configurator.env, leaves the rest of my-values.yaml intact
```

**Inline (`--set`/`--set-json` touching `bp-configurator.env`).** The script only reads YAML
files, not `--set` strings, so there's no direct way to feed it an inline override. Write the
equivalent into a small values file and use that instead — either by hand, or by dumping the
current release's values if one is already installed (`helm get values wh -n <namespace> -o yaml >
my-values.yaml`, then trim to just the `bp-configurator` block) — and follow the values-file path
above.

## Without the skill

The script has no agent dependency. Run it directly and hand the output file to `helm` yourself:

```bash
python3 deploy/helm/industry-profiles/warehouse-operations/scripts/compute_stream_cap.py \
  --mode 3d --num-streams 15 --hardware-profile H100 -o values-streams.yaml
helm upgrade --install wh .../warehouse-3d-app -n <namespace> ... -f values-streams.yaml
```
(`...` stands for any other `-f`/`--set` your install needs — put them *before* the generated
`-f values-streams.yaml`, not after, or they can clobber the `bp-configurator.env` it just set.)

## Re-running after a hardware or stream-count change

The generated file is a point-in-time computation, not a live binding — if you move to a different
GPU or want a different stream count, re-run the script and re-`helm upgrade` with the new file.
There's no drift detection; `helm upgrade` will happily keep serving the old cap otherwise.

## GPU → `HARDWARE_PROFILE` mapping and stream caps

Canonical source: `deploy/docker/industry-profiles/warehouse-operations/blueprint-configurator/blueprint_config.yml`
(`max_streams_supported` per profile × mode) and
[`vss-deploy-profile`'s warehouse reference §Supported Hardware](../../vss-deploy-profile/references/warehouse.md#supported-hardware)
for the `nvidia-smi` name → `HARDWARE_PROFILE` table. `compute_stream_cap.py` reads the YAML file
directly rather than duplicating the table, so it can't drift from what Compose enforces.

A `HARDWARE_PROFILE` (or a GPU `nvidia-smi` name) with no tuned section in `blueprint_config.yml`
gets **no cap applied** — the script warns on stderr and passes the requested count through
unchanged, matching Compose's own fallback behavior for untuned profiles.
