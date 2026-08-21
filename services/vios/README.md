# Media Service (VIOS)

VIOS (Video I/O Service) is the video ingest and storage layer of the [NVIDIA VSS blueprint](../../README.md). It connects to cameras rtsp/onvif/milestone, VMS systems, or NVStreamer sources, manages sensor registration and recording, routes streams to the downstream pipeline, and serves recorded/live video plus a web dashboard (at `:30888/vst`) to the rest of the stack.

## Build and Launch

### Step 1 — Build the images

Pick whichever fits. **Option A & B on a fresh clone** — the compile toolchain and base image are built automatically the first time, then reused on every later run (never rebuilt unless you delete them or pass `no-cache`).
Option C reuses an already-built toolchain.

```bash
# Option A — build all VIOS modules (sensor + streamprocessing) plus NVStreamer. Also auto-builds the toolchain + runtime-base image if not present.
./build.sh all

# Option B — build only the specified module(s) / NVStreamer. Also auto-builds the toolchain + runtime-base image if not present.
./build.sh container module=streamprocessing          # one module
./build.sh container module=sensor,streamprocessing   # several modules
./build.sh container nvstreamer                       # NVStreamer container only

# Option C — reuse a toolchain/runtime-base you already have (pulled from a registry, or built earlier locally)
# Point at it via the toolchain-image= flag OR the X86_BUILD_IMAGE / AARCH64_CC_IMAGE env var.
./build.sh container module=sensor,streamprocessing no-auto-deps \
  toolchain-image=my-registry.example.com/vios-build:x86-24.04-cuda13.0.0 \
  image-registry=my-registry.example.com/vios
```

| | First run | Later runs |
| --- | --- | --- |
| **`./build.sh all`** | Builds toolchain + base + all modules + NVStreamer | Reuses cached toolchain + base; rebuilds modules |
| **`./build.sh container module=…`** | Auto-builds toolchain + base, then just that module | Reuses toolchain + base; rebuilds just that module (fast) |
| **`./build.sh container nvstreamer`** | Auto-builds toolchain + base, then NVStreamer | Reuses toolchain + base; rebuilds just NVStreamer |

**Good to know:**

- **What they are.** The *toolchain* is the image with the compiler + CUDA/build deps used to compile VIOS; the *runtime base* (`vios-base`) is the slim runtime image (system libraries, no compiler) that the service containers are layered on. Both are cached and reused so builds stay fast.
- **Toolchain + base are built once, then reused.** `build.sh` detects them if they already exist — built earlier, or pulled from a registry — and **skips** them. They are never rebuilt on a repeat run; force a rebuild only by deleting the image or adding `no-cache`.
- **You can start with a single module.** Jump straight to `./build.sh container module=streamprocessing` (or `nvstreamer`) on a fresh clone — the toolchain + base are auto-built that first time, then reused. No need to run `all` first.
- **Bringing your own toolchain/base?** Under the default tags, nothing changes — they're detected and skipped. Under custom registry tags, use **Option C** above to point at them and skip the local build entirely.
- **aarch64:** prefix any build with `arch=arm64` (e.g. `./build.sh arch=arm64 container module=streamprocessing`).

### Step 2 — Deploy

```bash
# VST + NVStreamer together (most common)
python3 deployment/stream-processing/oneclick_dc_deployment.py deploy --target all --force

# VST only
python3 deployment/stream-processing/oneclick_dc_deployment.py deploy --force

# NVStreamer only
python3 deployment/stream-processing/oneclick_dc_deployment.py deploy --target nvstreamer --force
```

Then open the dashboards:

```text
VST UI     : http://<HOST_IP>:30888/vst/
NVStreamer : http://<HOST_IP>:31000/
```

For aarch64, run the same deploy commands on the arm64 target host. That's the whole local-dev flow.

> Full command surface — lives in [`deployment/stream-processing/README.md`](deployment/stream-processing/README.md).

---

## Going further

Everything below is optional. Reach for it only when the steps above aren't enough.

<details>
<summary><b>Publish images to your own registry</b></summary>

Image tags are baked in **at build time**, so configure the registry *before* you build. Use CLI flags (no shell state) or env vars — CLI flag wins if both are set.

| CLI flag (preferred) | Env var equivalent | Default (local-only) |
| --- | --- | --- |
| `image-registry=<ref>` | `IMAGE_REGISTRY` | `vios` → `vios/vst-sensor:latest` |
| `nvstreamer-image=<ref>` | `NVSTREAMER_IMAGE_REGISTRY` | `nvstreamer` → `nvstreamer:latest` |
| `toolchain-image=<ref>` | `X86_BUILD_IMAGE` (x86) / `AARCH64_CC_IMAGE` (arm64) | `vios-build:x86-24.04-cuda13.0.0` |

```bash
# Build with your registry baked in
./build.sh all \
  image-registry=my-registry.example.com/vios \
  nvstreamer-image=my-registry.example.com/nvstreamer \
  toolchain-image=my-registry.example.com/vios-build:x86-24.04-cuda13.0.0

# Push (add the same flags so the right tag is published)
./build.sh toolchain push=1            toolchain-image=my-registry.example.com/vios-build:x86-24.04-cuda13.0.0
./build.sh base-container push=1       image-registry=my-registry.example.com/vios
./build.sh container module=sensor,streamprocessing push=1 image-registry=my-registry.example.com/vios
```

**Already built with defaults?** Either retag (`docker tag … && docker push …`) or re-run the push commands above with the registry flags set (layer cache makes the rebuild near-instant; produces new tags).

**Push safety:** auto-built toolchain/base are **never** pushed implicitly — only the explicit `./build.sh toolchain push=1` / `base-container push=1` commands publish them.

**Multi-arch (amd64 + arm64) in one command.** For a single tag that runs on both x86 and aarch64 hosts, `multiarch` builds each arch, pushes per-arch tags, and assembles one multi-arch manifest. Needs Docker Buildx, a pushable `IMAGE_REGISTRY`, and a prior `docker login`.

```bash
export IMAGE_REGISTRY=my-registry.example.com/vios

# Full deployable set (sensor + streamprocessing + NVStreamer), both arches:
./build.sh arch=multiarch all tag=2.1.0-26.05.4

# Or a specific subset:
./build.sh multiarch tag=2.1.0-26.05.4 module=sensor,streamprocessing
./build.sh multiarch tag=2.1.0-26.05.4 nvstreamer

# Ingress is nginx + static UI — already a single multi-arch manifest, built separately:
./build.sh container ingress push=1 tag=2.1.0-26.05.4
```

Like `./build.sh all`, `arch=multiarch all` does **not** include ingress; build it with the one-liner above.

</details>

<details>
<summary><b>Individual build subcommands (CI / partial rebuilds)</b></summary>

```bash
./build.sh toolchain                    # compile toolchain only (x86_64)
./build.sh arch=arm64 toolchain         # compile toolchain (aarch64 cross)
./build.sh base-container               # runtime base image only
./build.sh nvstreamer container         # NVStreamer container only
./build.sh container module=… no-cache       # force rebuild from scratch (incl. toolchain/base)
./build.sh container module=… no-auto-deps   # fail fast if toolchain/base missing (CI)
./build.sh debug module=sensor          # debug build
./build.sh help                         # full option list
```

</details>

<details>
<summary><b>Build &amp; deploy with an AI coding agent</b></summary>

The repo ships agent skills under `services/vios/.claude/sqa/` that map natural-language requests to `build.sh` and the deploy script. The agent handles the toolchain/base automatically — you don't need to mention them.

**Build prompts**

| Say this | What it does |
| --- | --- |
| *"build streamprocessing"* / *"build sensor and streamprocessing"* | Runs `./build.sh container module=…`; auto-builds the toolchain + base on first run. |
| *"build streamprocessing with tag v2.1.0"* | Same, with `tag=v2.1.0`. |
| *"build everything"* / *"build all containers"* | Runs `./build.sh all`. |
| *"build the nvstreamer container"* | Runs `./build.sh container nvstreamer`. |
| *"build the toolchain for x86 / arm64"* | Runs `./build.sh toolchain` (add `arch=arm64` for aarch64). |
| *"build the toolchain and push to my registry"* | Runs `./build.sh toolchain push=1` with a registry-qualified `toolchain-image=…`; the agent asks for the registry if you didn't name one. |
| *"build the base image"* / *"rebuild base and push"* | Runs `./build.sh base-container` (with `push=1 image-registry=…` to publish; asks for the registry if not named). |
| *"force rebuild …"* / *"rebuild with no cache"* | Adds `no-cache` so cached layers (incl. toolchain/base) are rebuilt from scratch. |
| *"build sensor in debug mode"* | Runs `./build.sh debug module=sensor`. |
| *"rebuild sensor and redeploy"* | Builds the module, then redeploys just that container. |

**Deploy prompts**

| Say this | What it does |
| --- | --- |
| *"deploy vios"* | Default deploy — VIOS only (NVStreamer is opt-in; not deployed unless requested). |
| *"deploy vios + nvstreamer"* / *"deploy full stack"* | Brings up both. |
| *"deploy vios without nvstreamer"* | VIOS only (RTSP from an external camera/VMS). |
| *"deploy vios in milestone adaptor mode"* | Sets `VST_ADAPTOR=milestone_onvif` + `NGINX_MODE=mms`; prompts for VMS `ip`/`user`/`password`. |
| *"recreate sensor"* / *"recreate nvstreamer"* | Surgical per-container restart (aliases accepted). |
| *"stop vios"* | Stops containers; preserves data. |
| *"clean stop"* / *"wipe data"* | Stops and removes persistent data (no host `sudo`). |

Every prompt maps to a documented `build.sh` / `oneclick_dc_deployment.py` invocation — see `deployment/stream-processing/README.md`.

</details>

For the full deployment flag surface (single-container recreate, sysctl pre-flight, MMS adaptor mode), see `deployment/stream-processing/README.md`.

---

## Quick Start — verify it's up

Open the dashboard in any browser:

- URL: `http://<HOST_IP>:30888/vst/#/dashboard`
- Sample: <http://localhost:30888/vst>

The Media Service dashboard should load.

## Troubleshooting

### `docker pull` fails with "Incorrect Repository Format" / unsupported manifest

Published images are multi-arch OCI indexes carrying BuildKit attestation manifests (`unknown/unknown` platform entries). Some Docker/containerd versions fail to resolve them. Fix: pull for an explicit platform.

```bash
# x86_64 hosts
docker pull --platform linux/amd64 <image>:<tag>

# Arm hosts (Grace / Jetson)
docker pull --platform linux/arm64 <image>:<tag>
```

## Git LFS

Some files under `services/vios/` are stored with [Git LFS](https://git-lfs.com/); see `.gitattributes` for paths such as `LICENSE.3rdparty`, prebuilt `*.so` and `*.a` files, bundled UI `*.js` files, and `*.tar.gz` source archives. Install Git LFS before cloning or building so these resolve to real files instead of pointer stubs.

When adding an LFS-tracked file:

1. Ensure `services/vios/.gitattributes` covers its path or pattern.
2. If CI source-artifact consumers need the real content, add its path to the `git lfs pull --include=` list in `.github/workflows/ci.yml` (the **Fetch required LFS source notices** step). Other `services/vios/**` LFS objects intentionally remain pointer stubs in the source artifact.
