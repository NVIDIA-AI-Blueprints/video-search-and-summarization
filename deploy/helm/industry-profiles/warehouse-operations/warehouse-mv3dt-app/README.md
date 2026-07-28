# Warehouse MV3DT App Helm Chart

This profile chart wraps `deploy/helm/services/infra` and `deploy/helm/services/rtvi`, enabling Kafka, Redis, shared-infra Mosquitto, MV3DT BEV fusion, and `vss-rtvi-cv.profileMode=standalone-mv3dt`.

```bash
helm dependency build deploy/helm/industry-profiles/warehouse-operations/warehouse-mv3dt-app
helm lint deploy/helm/industry-profiles/warehouse-operations/warehouse-mv3dt-app
helm template warehouse-mv3dt deploy/helm/industry-profiles/warehouse-operations/warehouse-mv3dt-app
```

Override `rtvi.vss-rtvi-cv.ngcAppDataResourceVersion` and `vios.vss-vios-nvstreamer.ngcVideoSeed.resourceVersion` when using a different NGC warehouse app-data resource.
