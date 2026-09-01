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
  --set vios.vss-vios-nvstreamer.syncFileCount=<N> \
  -f values-stream-cap.generated.yaml
# <N> is the effective (possibly capped) count printed in step 1, not necessarily
# the --num-streams you requested
```

The non-streams overrides above (`global.vssIngress.enabled`, `externalHost`, `storageClass`,
Grafana/Kibana URLs) are the same host-specific values documented in
`warehouse-2d-app/README.md` §3 Install — this skill doesn't change any of that, it only adds the
stream-cap layer.

## If your install customizes `bp-configurator.env`

The script patches `NUM_STREAMS`/`HARDWARE_PROFILE` into the chart's own `bp-configurator.env`
list. If you skip what's below, its output — built from chart defaults, then layered last —
overwrites whatever you had there, since Helm replaces list-typed values wholesale instead of
merging entries.

If the customization is in a values file (`-f my-values.yaml`), pass that file to the script too,
via `-f`/`--values`, so it merges your customizations in before patching:

```bash
python3 deploy/helm/industry-profiles/warehouse-operations/scripts/compute_stream_cap.py \
  --mode 2d --num-streams 8 -f my-values.yaml -o values-stream-cap.generated.yaml
```

That covers `bp-configurator.env` only — the script's output has nothing else in it. If
`my-values.yaml` also sets other things (storage class, ingress, monitoring, alerts flags), still
pass it to `helm` directly, with the generated file layered after it so it only overrides
`bp-configurator.env`:

```bash
helm upgrade --install wh <chart-dir> -n <namespace> \
  -f my-values.yaml \
  -f values-stream-cap.generated.yaml   # wins on bp-configurator.env only, rest of my-values.yaml stays
```

If the customization is inline (`--set`/`--set-json` on `bp-configurator.env`), the script can't
read it — it only takes YAML files. Move it into a values file first. If a release is already
installed, `helm get values wh -n <namespace> -o yaml` only returns what was explicitly set, not
the full merged list — `-a` dumps the release's full computed values, secrets included, so pick the
file's location and permissions carefully: `(umask 077; helm get values wh -n <namespace> -a -o
yaml > /tmp/my-values.yaml) || rm -f /tmp/my-values.yaml` (outside the repo checkout so it can't
get committed, mode 600 so it's not world-readable, and removed on failure so a truncated dump
can't be mistaken for a real one). Keep its `bp-configurator` block untrimmed, don't print/paste
its contents, and `rm /tmp/my-values.yaml` once you've pulled that block into the real values file
used below. Then use the values-file case above.

## Without the skill

The script has no agent dependency. Run it directly and hand the output file to `helm` yourself:

```bash
python3 deploy/helm/industry-profiles/warehouse-operations/scripts/compute_stream_cap.py \
  --mode 3d --num-streams 15 --hardware-profile H100 -o values-streams.yaml
helm upgrade --install wh .../warehouse-3d-app -n <namespace> ... -f values-streams.yaml
```
(`...` stands for any other `-f` your install needs — put those files *before* the generated
`-f values-streams.yaml`, not after, or they can clobber the `bp-configurator.env` it just set.
`--set` is different: Helm always applies it after every `-f` file no matter where it sits on
the command line, so a `--set` on `bp-configurator.env` clobbers the generated file regardless
of position — convert it to a values file first, per "if your install customizes
`bp-configurator.env`" above.)

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

A known `HARDWARE_PROFILE` with no tuned section gets **no cap applied** — a warning, requested
count passed through unchanged. An unrecognized `nvidia-smi` name is different: hard error, exits
asking for `--hardware-profile` explicitly, not a warning.
