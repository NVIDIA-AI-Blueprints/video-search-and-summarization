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

`standalone-mv3dt` keeps its dedicated `ds-start-mv3dt.sh` and calls the same
download phase before MQTT generation.

For standalone warehouse deployment instructions, see
`README-standalone-warehouse.md`.
