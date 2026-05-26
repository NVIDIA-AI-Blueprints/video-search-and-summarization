# Warehouse 3D App Helm Chart

This profile chart wraps `deploy/helm/services/rtvi` and enables `vss-rtvi-cv.profileMode=standalone-3d`.

```bash
helm dependency build deploy/helm/industry-profiles/warehouse-operations/warehouse-3d-app
helm lint deploy/helm/industry-profiles/warehouse-operations/warehouse-3d-app
helm template warehouse-3d deploy/helm/industry-profiles/warehouse-operations/warehouse-3d-app
```

Set `rtvi.vss-rtvi-cv.ngcAppDataResourceVersion` to the NGC warehouse app-data resource before installing.
