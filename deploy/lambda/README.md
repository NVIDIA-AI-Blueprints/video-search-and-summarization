# Deploy VSS on Lambda GPU Cloud

Stand up the **Video Search & Summarization (VSS) `base` profile** — Quickstart Q&A + report
generation — on a single 80 GB GPU instance from [Lambda GPU Cloud](https://cloud.lambda.ai/),
with the LLM and VLM **self-hosted** on the instance.

This directory only adds Lambda-specific provisioning + host prep. The actual bring-up is
delegated to the repo's existing helper, `deploy/docker/scripts/dev-profile.sh`.

| File | Runs on | Purpose |
|------|---------|---------|
| `launch-instance.sh` | your laptop | Provision / terminate a Lambda GPU instance via the Cloud API |
| `setup-lambda.sh` | the instance | Reconcile prerequisites, then bring up the `base` profile |

---

## Prerequisites (two separate keys)

1. **Lambda Cloud API key** — rents the GPU box. Create one at
   <https://cloud.lambda.ai/api-keys>. Used only by `launch-instance.sh`.
2. **NGC / NVIDIA API key** (`nvapi-...`) — pulls the NIM model containers from `nvcr.io`.
   Requires an NVIDIA AI Enterprise / developer account. Get one at
   <https://ngc.nvidia.com/> (or <https://build.nvidia.com/>). Used by `setup-lambda.sh`.

> **Self-hosting the models is impossible without the NGC key** — it is what authorizes the
> `nvcr.io/nim/...` pulls. If you only have a remote `build.nvidia.com` key, you'd instead run
> a small/cheap box and point at hosted endpoints with `dev-profile.sh --use-remote-llm
> --use-remote-vlm` (not covered here).

Local tools for step 1: `curl` and `jq`.

## What you get

- **GPU:** 1× H100 (80 GB). Base runs the LLM (Nemotron‑Nano‑9B) and VLM (Cosmos3 Nano Reasoner)
  **shared on one GPU** (`local_shared`, device 0).
- **VLM backend (optional):** by default the VLM runs as an in-process Cosmos NIM (`VLM_BACKEND=nim_cosmos`).
  Set `VLM_BACKEND=rtvlm` to instead self-host the same Cosmos3 Nano weights in the `vss-rtvi-vlm`
  container (OpenAI-compatible API on **:8018**). See [Optional: use the RT‑VLM backend](#optional-use-the-rt-vlm-rtvlm-vlm-backend).
- **Also deployed:** Elasticsearch, Kafka, Redis, the VSS agent, the Next.js UI, VST, and HAProxy
  ingress on port **7777**.
- **Cost:** roughly **$3–4/hr** for a single H100 on Lambda — **terminate when you're done.**
- **Disk:** the model caches (~50 GB LLM + up to ~200 GB VLM) live in Docker named volumes under
  the Docker data-root, so you need **~400 GB free** there. Lambda GPU instances ship large local
  NVMe; `setup-lambda.sh` verifies free space and relocates the Docker data-root if needed. This
  storage is **ephemeral** — it's gone when the instance is terminated.

---

## Step-by-step

### 1. Launch the instance (from your laptop)

```bash
export LAMBDA_API_KEY='secret_...'
./deploy/lambda/launch-instance.sh          # auto-picks an available 1x 80GB GPU + region
```

Useful variants:

```bash
./deploy/lambda/launch-instance.sh --list                          # show what has capacity now
./deploy/lambda/launch-instance.sh --instance-type gpu_1x_h100_pcie --region us-east-1
./deploy/lambda/launch-instance.sh --ssh-key-name my-existing-key
```

Lambda H100 capacity is intermittent. If nothing is available, retry, try `--list`, or pass a
different `--instance-type`/`--region`. The script prints the instance's public IP and the exact
next commands when it becomes active.

### 2. Copy the repo to the instance

```bash
# from the repo root on your laptop
rsync -az --exclude .git ./ ubuntu@<INSTANCE_IP>:~/video-search-and-summarization/
# or clone it directly on the instance:
# ssh ubuntu@<INSTANCE_IP> 'git clone <this-repo-url> ~/video-search-and-summarization'
```

### 3. Run setup on the instance

```bash
ssh ubuntu@<INSTANCE_IP>
cd ~/video-search-and-summarization
export NGC_CLI_API_KEY='nvapi-...'
./deploy/lambda/setup-lambda.sh
```

`setup-lambda.sh` will: verify the OS/driver/GPU, install & pin **Docker** into the supported
`28.3.3 ≤ x < 29.5.0` range, install the **NVIDIA Container Toolkit** (≥1.17.8) and wire it into
Docker, ensure enough disk for the model caches, `docker login nvcr.io`, then run
`dev-profile.sh up --profile base --hardware-profile H100` and wait for the LLM to report ready.

> **First run is slow** (20–60+ min) — it downloads multi-hundred-GB model images and weights.

### 4. Open the UI

HAProxy listens on **7777**. Tunnel it to your laptop:

```bash
ssh -L 7777:localhost:7777 ubuntu@<INSTANCE_IP>
# then browse to  http://localhost:7777
```

Upload a short clip and run a Q&A / summary to confirm end-to-end.

### 5. Tear down

```bash
# stop the stack but keep the instance:
ssh ubuntu@<INSTANCE_IP> 'cd ~/video-search-and-summarization && ./deploy/docker/scripts/dev-profile.sh down'

# destroy the instance (STOPS BILLING):
./deploy/lambda/launch-instance.sh --terminate
```

---

## Optional: use the RT‑VLM (`rtvlm`) VLM backend

By default the VLM is served in-process by the Cosmos NIM on **:30082**. Passing
`VLM_BACKEND=rtvlm` instead self-hosts the same `cosmos3-nano-reasoner` weights in the
`vss-rtvi-vlm` container, which exposes an OpenAI-compatible API on **:8018** and becomes the VLM
the agent talks to. This is the same engine `alerts`/`lvs` use, brought to the single-GPU `base`
profile.

```bash
# step 3, on the instance:
export NGC_CLI_API_KEY='nvapi-...'
VLM_BACKEND=rtvlm ./deploy/lambda/setup-lambda.sh
```

Under the hood `setup-lambda.sh` passes `--vlm-backend rtvi` to `dev-profile.sh`, which sets
`VLM_MODEL_TYPE=rtvi`, disables the Cosmos NIM, and starts `vss-rtvi-vlm` on the shared GPU with
Kafka captioning disabled (base has no Kafka broker — interactive Q&A/summary uses the synchronous
endpoint). Notes:

- **Shared-GPU memory:** the LLM and the self-hosted VLM share the 80 GB H100. The VLM's vLLM
  memory fraction defaults to **0.4** on H100. On OOM, re-run with a lower value exported:
  `RTVI_VLLM_GPU_MEMORY_UTILIZATION=0.3 VLM_BACKEND=rtvlm ./deploy/lambda/setup-lambda.sh`.
- **First boot is slow:** `vss-rtvi-vlm` has a ~20 min `start_period` (vLLM warmup) on top of the
  weight download — "not ready" early is expected.
- **Health:** the VLM check moves to `curl -f http://127.0.0.1:8018/v1/health/ready`.
- Only supported for the `base` profile on non-edge GPUs (H100/RTXPRO6000BW).

## Health checks (on the instance)

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
curl -f http://127.0.0.1:30081/v1/health/ready   # LLM  (Nemotron-Nano-9B)
curl -f http://127.0.0.1:30082/v1/health/ready   # VLM  (Cosmos3 NIM;  default VLM_BACKEND=nim_cosmos)
curl -f http://127.0.0.1:8018/v1/health/ready    # VLM  (vss-rtvi-vlm; when VLM_BACKEND=rtvlm)
```

## Troubleshooting

- **NIM restarts with `No available memory for the cache blocks`** — the shared GPU is tight.
  Lower the LLM context/sequence budget via an override env file and redeploy:
  ```bash
  printf 'NIM_MAX_MODEL_LEN=65536\nNIM_MAX_NUM_SEQS=2\n' > /tmp/nim-low-mem.env
  ./deploy/docker/scripts/dev-profile.sh up --profile base --hardware-profile H100 \
    --llm-env-file /tmp/nim-low-mem.env
  ```
  (See `deploy/docker/README.md` for details.)
- **NGC image pulls fail** — usually Docker Engine ≥ 29.5.0 (not supported) or a bad NGC key.
  `docker version -f '{{.Server.Version}}'` should be in `[28.3.3, 29.5.0)`; re-run
  `setup-lambda.sh` to pin it, and confirm `docker login nvcr.io` succeeds.
- **Elasticsearch won't start** — needs `vm.max_map_count=262144`. `dev-profile.sh` sets this via
  `/etc/sysctl.d`; if it didn't apply, run `sudo sysctl -w vm.max_map_count=262144`.
- **`docker` needs sudo** — `setup-lambda.sh` adds you to the `docker` group; log out/in (or use
  `sudo docker`) for a fresh shell.
- **Out of disk during pulls** — the VLM cache alone can be ~200 GB. Ensure the Docker data-root is
  on the large NVMe (`docker info -f '{{.DockerRootDir}}'`) with ≥400 GB free.
- **(rtvlm) `vss-rtvi-vlm` restarts / OOMs** — the shared GPU is tight with both models resident.
  Lower the VLM's vLLM memory fraction and redeploy:
  `RTVI_VLLM_GPU_MEMORY_UTILIZATION=0.3 VLM_BACKEND=rtvlm ./deploy/lambda/setup-lambda.sh`. Watch
  `docker logs vss-rtvi-vlm` — first boot can take ~20 min (vLLM warmup) before `:8018` is ready.
- **Video analysis fails with `libnvcuvid.so.1: cannot open shared object`** — Lambda's default GPU
  image ships a compute-only driver without the NVDEC decode library, which the Cosmos VLM needs to
  decode H.264/H.265 on the GPU. `setup-lambda.sh` now installs the exact-version `libnvcuvid` from
  NVIDIA's driver installer; if you hit this on an already-running box, re-run `setup-lambda.sh` (or
  install the matching `libnvcuvid.so.<driver>` manually) and recreate the VLM container
  (`docker compose ... up -d --force-recreate --no-deps cosmos3-reasoner-shared-gpu`). Do **not** use
  apt's `libnvidia-decode-*` unless its version exactly matches the running kernel driver.
- **UI says "WebSocket connection failed"** — the browser must reach the WS URL in `/__ENV.js`
  (`ws://<VSS_PUBLIC_HOST>:<VSS_PUBLIC_PORT>/websocket`). Over an SSH tunnel this must match your
  local forwarded port and host. If local `7777` is taken (e.g. Conductor's `lume`), advertise a
  free port instead: set `VSS_PUBLIC_PORT` in `generated.env` (keep `HAPROXY_PORT=7777`), recreate
  `vss-ui` + `vss-haproxy-ingress`, tunnel `-L <port>:localhost:7777`, and open
  `http://127.0.0.1:<port>` (use the exact host in the ACL, not `localhost` vs `127.0.0.1`).

## Other profiles (later)

`base` is single-GPU. To run **Search** (~3 GPUs) or **Alerts** (~2 GPUs), launch a larger node
(e.g. `gpu_8x_h100_sxm5`) and swap the profile:
`./deploy/docker/scripts/dev-profile.sh up --profile search --hardware-profile H100`.
See `deploy/docker/README.md`.
