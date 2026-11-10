# Bootstrap a fresh VSS skill-eval GPU worker

Use [`bootstrap-vss-skill-eval-worker.sh`](bootstrap-vss-skill-eval-worker.sh) for a **new, unused** x86 GPU worker before adding it to the `vss-eval-*` pool. This is host preparation, not a CI trial. The script defaults to a read-only `--check`; `--apply` installs `uv` for the normal user if missing. On a host with a separate mounted `/data` disk it merges Docker `data-root=/data/docker` into the existing daemon configuration and adds a systemd mount dependency. On a host with at least 500 GB of root disk and no separate `/data`, it leaves Docker on `/var/lib/docker`. It does not format disks, delete old Docker data, install drivers/Docker/toolkit, create trial directories, or register the node in the pool. The trial harness creates `/logs`, `/tests`, `/solution` and `/skills` itself.

## Host prerequisites

- Ubuntu 24.04 x86_64, GPU driver at least 595.58.03, one or more visible GPUs. VSS's [x86 prerequisites](https://docs.nvidia.com/vss/getting-started/prerequisites) set this OS/driver baseline. The one-skill L40S canary requires one GPU.
- At least 200 GB root disk, matching the current eval task metadata. Docker storage needs either a separate mounted `/data` filesystem of at least 500 GB or a root disk of at least 500 GB. Mount a separate volume by persistent UUID in the instance image or `/etc/fstab` **before** running this script. The script never chooses or formats a block device.
- A VSS-supported Docker Engine version and NVIDIA Container Toolkit, with Docker usable by the normal Brev login user. The script preserves any existing NVIDIA runtime, MTU, and daemon options.
- No existing Docker containers, images, or volumes if Docker still uses `/var/lib/docker`. This script does not migrate a populated Docker store. No running containers if the script must restart Docker to add its systemd drop-in.
- Passwordless `sudo` for `--apply`. Run as the normal user so `uv` lands in that user's `~/.local/bin` rather than root's.

The repository already includes [`deploy/docker/scripts/pin_docker_version.sh`](../../deploy/docker/scripts/pin_docker_version.sh) for Docker package pinning and the required `cgroupfs` setting. On a fresh node whose Docker version is out of range, use that existing script **before** the bootstrap check, during a maintenance window; it may downgrade packages and restart Docker. Review the host's apt preferences and simulate the package transaction first, especially on provider images. Driver installation is separate and follows NVIDIA's [Ubuntu driver guide](https://docs.nvidia.com/datacenter/tesla/driver-installation-guide/latest/ubuntu.html).

## Operator sequence

From the VSS checkout on your control machine, once `vss-eval-l40s-1g-1` is ready:

```bash
brev ls
brev copy .github/scripts/bootstrap-vss-skill-eval-worker.sh vss-eval-l40s-1g-1:/tmp/bootstrap-vss-skill-eval-worker.sh
brev exec vss-eval-l40s-1g-1 'bash /tmp/bootstrap-vss-skill-eval-worker.sh --check'
```

Review the OS, GPU driver, root size, optional `/data` mount, Docker root and NVIDIA runtime result. If both root and `/data` are too small, arrange a persistent mount for the **identified** data volume; do not assume `/dev/vdb` or auto-format an unknown device. If Docker or the toolkit is missing or out of the documented version range, follow the VSS prerequisites. If the host checks pass and the worker is not in use, apply:

```bash
brev exec vss-eval-l40s-1g-1 'bash /tmp/bootstrap-vss-skill-eval-worker.sh --apply'
brev exec vss-eval-l40s-1g-1 "docker info --format '{{.DockerRootDir}}'"
brev exec vss-eval-l40s-1g-1 'nvidia-smi --query-gpu=name,driver_version --format=csv,noheader'
```

Expected Docker root: `/data/docker` when a separate data disk exists, otherwise `/var/lib/docker` on a sufficiently large root disk. Check `df -h /`, optional `findmnt /data`, and `docker info` after any reboot. Re-run `--check` or `--apply` safely on an already configured idle worker; the script should not restart Docker when storage is already configured. The original `/var/lib/docker` is not deleted. On failure during configuration, the script restores its saved daemon JSON and restarts Docker with the previous settings; inspect the emitted backup path before further action.

### Current Nebius L40S instance (2026-11-10)

Read-only inspection of `vss-eval-l40s-1g-1` found Ubuntu 24.04.5 and one L40S, but its driver is **580.173.02** (below VSS's 595.58.03 x86 baseline) and Docker Engine is **29.9.2** (above VSS's `<29.5.0` bound). Docker uses the `systemd` cgroup driver; VSS requires `cgroupfs`. `lsblk` shows one 1.3-TB `vda1` ext4 root volume and no data disk; `findmnt -T /var/lib/docker` resolves to that same root filesystem with about 1.2 TB free. Keep Docker at `/var/lib/docker`; creating a `/data` directory or moving Docker there would not add capacity. Nebius apt preferences pin the installed 580 driver and assign `-1` to the NVIDIA and Docker upstream repositories, so do not blindly run an upstream install or downgrade command. Do not run `--apply` or a canary until the driver and Docker version are corrected and `--check` passes. The bootstrap script does not make those package changes.

Before a canary, confirm the node is visible in the Brev pool, no other eval has leased it, and CI's `L40S` selector can choose it. The `--apply` step is a host mutation and should be performed only during a maintenance window or before pool registration. Neither this script nor the manual canary changes ordinary nightly automatically.
