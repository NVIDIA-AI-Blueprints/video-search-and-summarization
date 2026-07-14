# RTVI CV Helm chart

## Developer startup contract

The `alerts` and `search` profile modes use the chart-owned
`files/ds-start.sh`. Profile ConfigMaps contain configuration data only. The
StatefulSet mounts that data read-only at `mounted-configs/`; the startup
script copies it to the writable `configs/` volume before applying changes.

Supported `DS_MODEL_FAMILY` values are:

- `rtdetr-gdino`
- `rtdetr-warehouse`
- `sparse4d-warehouse`

The model download Job creates a marker named
`.${destPath//\//__}.done` beside the model tree only after the destination
artifact has been copied and its ownership and modes have been applied. The
workload waits for both the marker and destination artifact.

The `standalone-2d`, `standalone-3d`, and MV3DT startup paths remain separate
from this developer-profile contract.

For standalone warehouse deployment instructions, see
`README-standalone-warehouse.md`.
