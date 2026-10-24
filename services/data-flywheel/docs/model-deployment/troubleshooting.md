# Troubleshooting

Common issues and debugging steps for model deployments.

> **Note:** DataRobot kubectl commands use the **LRS ID**, not the user-facing Deployment ID. To resolve it, run:
> ```bash
> kubectl get deployment -n dr-app \
>   -l datarobot-deployment-id=<DEPLOYMENT_ID> \
>   -o jsonpath='{.items[0].metadata.labels.datarobot-lrs-name}'
> ```
> Strip the `lrs-` prefix to get `<LRS_ID>`. See [Find your DEPLOYMENT_ID](datarobot.md#find-your-deployment_id) for details.

---

## Deployment stuck in `deploying`

The Celery task is running but hasn't completed. Common causes:

**Model download taking too long**
- Check the download Job logs:

```bash
kubectl logs -n dr-app job/download-sft-<LRS_ID> --tail=50
```

- If the Job is still running, wait. Large models can take 30+ minutes.
- If the Job has failed, check the error (see sections below).

**LRS rollout not completing**
- Check the LRS pod status:

```bash
kubectl get pods -n dr-app -l datarobot-lrs-name=lrs-<LRS_ID>
```

- Describe the pod for events:

```bash
kubectl describe pod -n dr-app -l datarobot-lrs-name=lrs-<LRS_ID>
```

- Common causes: insufficient GPU resources, image pull errors, OOMKilled.

---

## Download Job fails with 503

The DataStore returned **503 Service Unavailable** when the download Job tried to fetch the model.

**Check DataStore health:**

```bash
kubectl get pods -n nv-nvidia-blueprint-data-flywheel | grep data-store
```

The data-store pod should show `Running` and `1/1 Ready`.

**Check DataStore logs:**

```bash
kubectl logs -n nv-nvidia-blueprint-data-flywheel \
  $(kubectl get pods -n nv-nvidia-blueprint-data-flywheel -l app=data-store -o jsonpath='{.items[0].metadata.name}') \
  --tail=50
```

**Fix:** If the store is healthy, retry the deployment. If the store is down, restart it and then retry.

---

## Download Job fails with 404

The model or revision was not found in the DataStore.

**Verify the model exists:**

```bash
# From a pod with network access to the data-store, or use port-forward
curl -s https://dataflywheel-data-store.prd.astra.nvidia.com/v1/hf/api/models/<NAMESPACE>/<MODEL>/revision/<VERSION>
```

**Common causes:**
- The customization/training job has not completed yet.
- The model name in the customizations collection doesn't match what was requested.
- Wrong datastore URL (check `datastore_base_url` in `config/config.yaml`).

---

## "Deployment already in progress" error

Only one active deployment per target `deployment_id` (DataRobot) is allowed at a time.

**Options:**
1. Wait for the existing deployment to finish (check status via the API).
2. Delete the existing deployment first:

```bash
curl -X DELETE 'https://dataflywheel-api.prd.astra.nvidia.com/api/model-deployments/<DEPLOYMENT_RECORD_ID>'
```

3. If the deployment is stuck in a terminal state (`failed`, `deleted`) and still blocking, this may indicate a database inconsistency. Check the MongoDB `model_deployments` collection directly.

---

## Model not found in customizations

The API returned:
> *"Model 'namespace/model@version' not found in customizations."*

**Cause:** The `model_name` must match a `customized_model` field in the MongoDB `customizations` collection.

**Verify:**

```bash
# From a pod with MongoDB access, or via port-forward
mongosh --eval "db.customizations.findOne({customized_model: 'dfwbp/my-model@cust-abc123'})"
```

**Fix:** Ensure the DFW training/customization job has completed successfully before deploying.

---

## Invalid model name format

The API returned:
> *"Invalid model name. Expected format: namespace/model@version"*

The `model_name` must follow the format `namespace/model@version`, for example:
- `dfwbp/my-model@cust-abc123`
- `nvidia/nv-embedqa-e5-v5@cust-bugnemo-v1`

All three parts (namespace, model, version) must be non-empty, with exactly one `/` and one `@`.

---

## Verify the model a DataRobot deployment is serving

After deployment reaches `ready`, exec into the NIM container and query the local models endpoint:

```bash
# Replace <LRS_ID> with the resolved LRS ID (see note at top)
kubectl exec \
  $(kubectl get pods -n dr-app \
      -l datarobot-lrs-name=lrs-<LRS_ID> \
      -o jsonpath='{.items[0].metadata.name}') \
  -n dr-app \
  -c lrs-<LRS_ID>-additional-container-0 \
  -- curl -s http://localhost:8000/v1/models 2>/dev/null | jq
```

**Expected:** A JSON response listing the loaded model(s).

**If empty or fails:**
- The model volume may not be mounted. Check the pod's volume mounts:

```bash
kubectl get pod -n dr-app \
  -l datarobot-lrs-name=lrs-<LRS_ID> \
  -o jsonpath='{.items[0].spec.containers[*].volumeMounts}' | jq
```

- Check the download Job completed successfully:

```bash
kubectl get jobs -n dr-app | grep download-sft-<LRS_ID>
```

---

## NMP deployment not becoming ready

**Check NMP deployment status:**

```bash
curl -X GET 'https://dataflywheel-api.prd.astra.nvidia.com/api/model-deployments/<DEPLOYMENT_RECORD_ID>/status'
```

**Check DMS/NeMo side:**
- Verify the NeMo Deployment Management service is running in `nv-nvidia-blueprint-data-flywheel`.
- Check the NeMo operator and NIM operator pods for errors.

**Common causes:**
- Insufficient GPU resources in the cluster.
- NIM container image not available or pull secret missing.
- Customization job produced invalid model artifacts.

---

## Check deployment logs via the API

The deployment API records timestamped logs throughout the lifecycle:

```bash
curl -X GET \
  'https://dataflywheel-api.prd.astra.nvidia.com/api/model-deployments/<DEPLOYMENT_RECORD_ID>/logs?limit=200' \
  -H 'accept: application/json' | jq '.logs[]'
```

These logs include progress messages from the adapter (for example, "Creating PVC", "Downloading model", "Patching deployment", "Waiting for rollout").

---

## See Also

- [Getting Started & API Reference](index.md)
- [DataRobot Adapter](datarobot.md)
- [NMP Adapter](nmp.md)
- [Architecture](architecture.md)
