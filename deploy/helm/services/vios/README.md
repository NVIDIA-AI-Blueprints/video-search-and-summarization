# VIOS Helm configuration

## Sensor admission limit

The sensor and stream-processing charts default to 100 sensors. Sensor management
checks this limit when adding sensors; stream processing also checks it when
uploading video files. Set both limits to the same positive integer.

For a developer profile, add the following to the deployment's Helm values file
to allow up to 500 sensors:

```yaml
vios:
  vss-vios-sensor:
    maxSensorsSupported: 500
  vss-vios-streamprocessing:
    maxSensorsSupported: 500
```

When installing the VIOS umbrella chart directly, omit the outer `vios` key.
Each chart renders the value as the integer `onvif.max_devices_supported` in
its `vst_config.json` ConfigMap entry. Accepted values are 1 through 2147483647,
matching the service's signed integer setting.

Changing the limit changes the pod configuration checksum and rolls the affected
VIOS workloads. Schedule the update around active video requests. It does not
delete existing sensor records or videos in persistent storage, reserve more
GPUs, or preallocate resources for that many sensors. If the sensor count already
exceeds the configured limit, existing sensors remain but additions are rejected.

For GitOps deployments, commit these values and select a chart revision that
supports them. ArgoCD can then enforce the intended limit without ignore rules.
