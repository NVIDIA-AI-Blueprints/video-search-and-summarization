# Lifecycle scripts (in `.scripts/`)

The old `mdx-*` shell aliases / `ngc-env-on` from
[`deploy/docker/developer-profiles/dev-profile-incident/.dotfiles/`](../.dotfiles/README.md) moved here as standalone,
executable scripts, ready to run individually. Since the native split ([architecture](../.docs/architecture.md#native-vs-docker-service-split-policy)), the VM-side scripts cover both halves: native processes through
`native-services.sh`, Docker appliance containers through `docker compose -p mdx`.

| Script | Was | Runs on | What it does |
|---|---|---|---|
| `../start.sh` | — (new) | **laptop** | Laptop entry point: checks and heals the VM backend, opens the tunnel, runs the console |
| `.scripts/tunnel.sh` | `mdx-tunnel-incident` | **laptop** | Open the SSH tunnel in the foreground (Ctrl-C closes it) |
| `.scripts/tunnel-check.sh` | `mdx-tunnel-incident-check` | **laptop** | Check the tunnel from the laptop (agent health, search route, proxy) |
| `.scripts/vm-preflight.sh` | — (new) | **laptop** | Sourced by `start.sh` and `tunnel.sh`: SSH target, SSH probe with the cause of a failure, VM address. Tests: `test-vm-preflight.sh` |
| `.scripts/vm-ensure-docker.sh` | — (new) | VM (piped by `start.sh`) | Bounded Docker deploy on the VM (clone, env check, `HOST_IP` fix, pull, `compose up`). Tests: `test-vm-ensure-docker.sh` |
| `.scripts/native-services.sh` | — (new) | VM | `start`/`stop`/`restart`/`status`/`logs` for the native services. Tests: `test-native-services.sh` |
| `.scripts/media-tools.sh` | — (new) | VM (run by `native-services.sh` before it starts `vss-agent`) | `install`/`check` for `ffmpeg` and `ffprobe`: a SHA-256-checked static build in `<repo>/.tools/ffmpeg/bin` unless both are on `PATH`. Tests: `test-native-services.sh` |
| `.scripts/tracking-stack.sh` | — (new) | VM (started by `start.sh` unless `ENABLE_TRACKING=false`) | `start`/`stop`/`status`/`restart-perception` for the person-tracking stack (stages the RT-DETR model, never removes containers) |
| `.scripts/status.sh` | `mdx-ps` | VM | Containers, native services and `nvidia-smi` |
| `.scripts/down.sh` | `mdx-down` | VM | Stop native services and containers without removing them (never `-v`, never `dev-profile.sh down`) |
| `.scripts/health.sh` | `mdx-health` | laptop/VM | Agent health probe on `:8000/health` |
| `.scripts/logs.sh` | `mdx-logs` | VM | Tail one service's logs (default `vss-agent`) |
| `.scripts/rebuild-svc.sh` | `mdx-rebuild-svc` | VM | Restart a native service, or rebuild one Docker service with the profile overrides |
| `skills/incident-operate-vm/scripts/prune-native-images.sh` | — (new) | VM | Remove the Docker images of services that now run natively |
| `.scripts/ngc-env.sh` | `ngc-env-on` | VM | `source` it to export the shared NGC credentials |

## Laptop-side scripts & start.sh modes

`tunnel.sh`, `tunnel-check.sh`, and top-level `start.sh` are **laptop-side only** (SSH tunnel from developer laptops to the `kwanz-ws` backend); never run them on `kwanz-ws` itself.

`start.sh` modes (`--mode mock|vm`, default `vm`), what each starts, and its cleanup on exit: [`incident-start`](../skills/incident-start/SKILL.md) and [`incident-profile-operations.md`](../.docs/incident-profile-operations.md#3-laptop-to-vm-connectivity--ssh-troubleshooting).
