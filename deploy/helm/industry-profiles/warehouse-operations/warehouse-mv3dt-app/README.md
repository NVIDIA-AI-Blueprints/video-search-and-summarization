# Warehouse MV3DT App Helm Chart

This profile chart wraps `deploy/helm/services/infra` and `deploy/helm/services/rtvi`, enabling Kafka, Redis, shared-infra Mosquitto, MV3DT BEV fusion, and `vss-rtvi-cv.profileMode=standalone-mv3dt`.

Warehouse inputs use two independent NGC download paths on the shared models PVC:

- `downloadNgcAppData` supplies videos, playback, and calibration under `vss-warehouse-app-data/`.
- `downloadModelsFromNgc` supplies RT-DETR and BodyPose3DNet at the flattened paths `/opt/storage/rtdetr_warehouse_v1.0.2.fp16.onnx` and `/opt/storage/BodyPose3DNet/bodypose3dnet_accuracy.onnx`.

The RT-CV StatefulSet waits for both model artifacts and their completion markers. It mounts the models PVC directly at `/opt/storage`, so generated TensorRT engines persist across pod restarts.

```bash
helm dependency build deploy/helm/industry-profiles/warehouse-operations/warehouse-mv3dt-app
helm lint deploy/helm/industry-profiles/warehouse-operations/warehouse-mv3dt-app
helm template warehouse-mv3dt deploy/helm/industry-profiles/warehouse-operations/warehouse-mv3dt-app
```

Override `rtvi.vss-rtvi-cv.ngcAppDataResourceVersion` and `vios.vss-vios-nvstreamer.ngcVideoSeed.resourceVersion` when using a different NGC warehouse app-data resource. Keep the versioned entries under `rtvi.vss-rtvi-cv.ngcModelsToDownload` aligned with the RT-CV model release.
