---
name: vss-prerequisites
description: Check VSS system prerequisites — GPU driver, Docker, NVIDIA Container Toolkit, and NGC access. Use when troubleshooting a deploy failure, after a system change, or to verify the system is ready for VSS.
---

# VSS Prerequisites Check

Verifies system readiness for any VSS developer profile. For NGC CLI setup specifically, use the `ngc` skill.

## When to Use

Use this skill when:

- A VSS deploy failed and you need to diagnose why
- User asks to verify GPU, Docker, or system setup
- After a driver or Docker update
- Called from BOOTSTRAP during first-time setup

## Read TOOLS.md First

Check `TOOLS.md` for the VSS section. If missing, the environment isn't configured yet — run BOOTSTRAP first.

---

## Sudo Access

Most prerequisite steps require `sudo` (Docker install, NVIDIA toolkit, kernel settings, systemctl). On cloud instances (Brev, Colossus, DGX Cloud) the default user typically has passwordless sudo. On bare-metal machines, the user may need to enter a password or be in the `sudo` group.

Check before proceeding:

```bash
sudo -n true 2>/dev/null && echo "passwordless sudo" || echo "sudo requires password"
```

If sudo requires a password, ask the user to run privileged commands manually or configure passwordless sudo for the session.

## Kernel Settings

Required for Elasticsearch and Kafka. Apply before deploying:

```bash
sudo sysctl -w vm.max_map_count=262144
sudo sysctl -w net.core.rmem_max=5242880
sudo sysctl -w net.core.wmem_max=5242880
```

To persist across reboots, write to `/etc/sysctl.d/99-vss.conf`:

```bash
cat <<'EOF' | sudo tee /etc/sysctl.d/99-vss.conf
vm.max_map_count = 262144
net.core.rmem_max = 5242880
net.core.wmem_max = 5242880
net.ipv4.tcp_rmem = 4096 87380 16777216
net.ipv4.tcp_wmem = 4096 65536 16777216
net.ipv6.conf.all.disable_ipv6 = 1
net.ipv6.conf.default.disable_ipv6 = 1
net.ipv6.conf.lo.disable_ipv6 = 1
EOF
sudo sysctl --system
```

## GPU Module Loading

If `nvidia-smi` fails with "NVIDIA-SMI has failed" but the driver is installed, load the kernel modules:

```bash
sudo modprobe nvidia && sudo modprobe nvidia_uvm
```

This works without a reboot on Brev and Colossus instances.

## Checks

Run in order, report pass/fail for each.

### 1. GPU Detection

```bash
nvidia-smi --query-gpu=index,name,driver_version,memory.total --format=csv,noheader
```

Expected for this machine: 2× RTX PRO 6000 Blackwell, devices 0 and 1.

If `nvidia-smi` fails → driver not installed or not loaded. Pin the exact build for the OS / platform:

| Platform | Required driver |
|---|---|
| x86 — Ubuntu 24.04 | **`580.105.08`** (https://www.nvidia.com/en-us/drivers/) |
| x86 — Ubuntu 22.04 | **`580.65.06`** |
| DGX-SPARK | **`580.95.05`** (ships with DGX OS 7.4.0) |
| IGX-THOR / AGX-THOR | **`580.00`** (ships with Jetson Linux BSP Rel 38.5 / 38.4) |

After install, load the kernel modules instead of rebooting:

```bash
sudo modprobe nvidia && sudo modprobe nvidia_uvm
```

> **Multi-GPU H100 SXM HBM3 only — NVIDIA Fabric Manager `580.105.08`** is also required to host a local LLM. Single-GPU and multi-GPU PCIe-only systems do **not** need Fabric Manager — installing it will conflict with the standard `nvidia-driver-580` package. See [`warehouse.md` § Fabric Manager](warehouse.md#nvidia-fabric-manager-when-required) for the full install guide.

> **Workaround:** If GPU is present but detection fails during a deploy, prepend `SKIP_HARDWARE_CHECK=true` — but investigate root cause.

### 2. Docker

```bash
docker --version        # need 28.3.3+ and earlier than 29.5.0
docker compose version  # need v2.39.1+
docker ps               # verify runs without sudo
```

If Docker needs to be installed: https://docs.docker.com/engine/install/ubuntu/

> **Docker upper bound — `< 29.5.0`.** Docker Engine `29.5.0` and later fail to pull some NGC-hosted image tags after the layers download with `error from registry: Incorrect Repository Format`. Pin a supported version below `29.5.0` (canonical reference: `28.3.3`). If you must run `29.5.0`+, disable the containerd snapshotter daemon-side — see [Docker 29.5.0+ workaround](#docker-2950-workaround) below.

If `docker ps` requires sudo → add user to docker group:
```bash
sudo usermod -aG docker $USER && newgrp docker
```

Also verify cgroupfs driver:
```bash
cat /etc/docker/daemon.json | grep cgroupfs
# Should contain: "exec-opts": ["native.cgroupdriver=cgroupfs"]
```

#### Docker 29.5.0+ workaround

If the host is locked to Docker `29.5.0` or later (e.g. distro-managed), add or merge the following daemon-side override and restart Docker to fall back to the legacy graphdriver image store:

```bash
sudo bash -c 'cat > /etc/docker/daemon.json << EOF
{
  "exec-opts": ["native.cgroupdriver=cgroupfs"],
  "features": {
    "containerd-snapshotter": false
  }
}
EOF'
sudo systemctl daemon-reload && sudo systemctl restart docker
```

Preserve any other daemon settings already present (e.g. registry-mirrors, log-driver). The `exec-opts` cgroup driver line is required regardless of the snapshotter override.

### 3. NVIDIA Container Toolkit

Required minimum: **`1.17.8+`**.

```bash
# Check installed version
dpkg -s nvidia-container-toolkit 2>/dev/null | grep -E '^Version:' || \
  rpm -q nvidia-container-toolkit 2>/dev/null

# Check runtime is registered
docker info 2>/dev/null | grep -i "runtimes"

# Check it works end-to-end
docker run --rm --gpus all ubuntu:22.04 nvidia-smi 2>&1 | head -8
```

Should print GPU info from inside the container. If `runtimes` line doesn't show `nvidia`, or the run fails with `unknown or invalid runtime name: nvidia`:

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit

# Configure Docker and restart
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

Re-run the `docker run` check to confirm before continuing.

> Full guide: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html

### 4. NGC CLI + Access

Required minimum: **`4.10.0+`**. Use the `ngc` skill to check NGC CLI and API key access.

---

## Canonical version matrix

Single source of truth for **every** dependency the deploy assumes. Sourced from the [VSS prerequisites page](https://docs.nvidia.com/vss/3.2.0/prerequisites.html); update this table when the upstream blueprint docs change.

| Component | Required version | Notes |
|---|---|---|
| OS — x86 host | Ubuntu 22.04 or 24.04 | |
| OS — DGX-SPARK | DGX OS 7.4.0 | |
| OS — IGX-THOR | Jetson Linux BSP Rel 38.5 | |
| OS — AGX-THOR | Jetson Linux BSP Rel 38.4 | |
| NVIDIA Driver — Ubuntu 24.04 | `580.105.08` | exact pin |
| NVIDIA Driver — Ubuntu 22.04 | `580.65.06` | exact pin |
| NVIDIA Driver — DGX-SPARK | `580.95.05` | exact pin |
| NVIDIA Driver — IGX-THOR / AGX-THOR | `580.00` | exact pin |
| NVIDIA Fabric Manager | `580.105.08` | **only** for multi-GPU NVLink/NVSwitch hosts running local LLM (H100 SXM HBM3, NVSwitch, HGX) |
| NVIDIA Container Toolkit | `1.17.8+` | |
| Docker | `28.3.3+` **and** `< 29.5.0` | upper bound: `29.5.0`+ breaks NGC image pulls — see [Docker 29.5.0+ workaround](#docker-2950-workaround) |
| Docker Compose | `v2.39.1+` | |
| NGC CLI | `4.10.0+` | use `ngc` skill |

---

## Summary

- All pass → "System ready. You can deploy base, lvs, search, or alerts."
- Any fail → report the item, provide the fix, re-run that check before continuing.
