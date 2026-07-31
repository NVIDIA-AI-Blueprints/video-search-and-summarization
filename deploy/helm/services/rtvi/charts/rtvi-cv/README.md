# RTVI CV Helm chart

## Developer startup contract

The `alerts`, `search`, `standalone-2d`, and `standalone-3d` profile modes use the
chart-owned `files/ds-start.sh`. Profile ConfigMaps contain configuration data
only. The StatefulSet mounts that data read-only at `mounted-configs/` (developer
profiles) or profile config paths (warehouse); the startup script stages writable
copies before applying changes.

Supported `DS_MODEL_FAMILY` values are:

- `rtdetr-gdino`
- `rtdetr-warehouse`
- `sparse4d-warehouse`

When `downloadModelsFromNgc` is true, `ds-start.sh` phase 0 runs
`download-models.sh` against a ConfigMap-rendered `models-download.json`. The
download script creates a marker named `.${destPath//\//__}.done` beside the
model tree only after the destination artifact has been copied and its ownership
and modes have been applied. There is no separate model-download Job or
`wait-for-models` initContainer.

The marker body records the `model`, `sourcePath`, `destPath`, and `org` that
produced the artifact, and an entry is skipped only when that record still matches
the manifest. Because several `destPath` values carry no version, a `model`
version bump or a `sourcePath` move would otherwise look already-satisfied and
keep serving stale weights; comparing the recorded tuple re-downloads instead.
Markers from before tuple recording are empty, so the first run after upgrading
re-fetches each artifact once. To inspect what a volume holds:

```bash
grep . /opt/storage/.*.done
```

`standalone-mv3dt` keeps its dedicated `ds-start-mv3dt.sh` and calls the same
download phase before MQTT generation.

For standalone warehouse deployment instructions, see
`README-standalone-warehouse.md`.
