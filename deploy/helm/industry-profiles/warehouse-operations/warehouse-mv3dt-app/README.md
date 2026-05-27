# Warehouse MV3DT App Helm Chart

This profile chart wraps `deploy/helm/services/infra` and `deploy/helm/services/rtvi`, enabling Kafka, Redis, shared-infra Mosquitto, MV3DT BEV fusion, and `vss-rtvi-cv.profileMode=standalone-mv3dt`.

```bash
helm dependency build deploy/helm/industry-profiles/warehouse-operations/warehouse-mv3dt-app
helm lint deploy/helm/industry-profiles/warehouse-operations/warehouse-mv3dt-app
helm template warehouse-mv3dt deploy/helm/industry-profiles/warehouse-operations/warehouse-mv3dt-app
```

Set `rtvi.vss-rtvi-cv.ngcAppDataResourceVersion` to the NGC warehouse app-data resource before installing.
