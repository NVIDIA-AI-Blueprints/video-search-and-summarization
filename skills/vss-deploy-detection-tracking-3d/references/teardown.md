# Teardown — MV3DT stack

Parent: [`../SKILL.md`](../SKILL.md). Stop the MV3DT stack, optionally clear data, leave the host clean for redeploy.

This teardown is scoped to whatever this skill brought up — the same compose file the deploy used. It's safe to run repeatedly.

## Step 1 — Stop containers

```bash
cd "${VSS_APPS_DIR}"
docker compose -f compose.yml \
  --env-file industry-profiles/warehouse-operations/.env \
  down
```

This removes the MV3DT containers (perception, fusion, mosquitto, broker, VST sensor stack, configurator, nvstreamer, auto-calibration) but **preserves** named docker volumes (Kafka data, Redis data, Postgres state, etc.).

## Step 2 — Prune dangling volumes (recommended)

```bash
docker volume prune -f
docker system prune -f
```

`volume prune -f` removes only unreferenced volumes — safe after `down`. `system prune -f` clears stopped containers, dangling images, and unused networks. Skip both if you have other docker workloads on this host that share the docker volume namespace.

## Step 3 — Clear data logs and state

```bash
bash "${VSS_APPS_DIR}/scripts/cleanup_all_datalog.sh" \
  -e industry-profiles/warehouse-operations/.env
```

The shipped cleanup script drops data dirs the warehouse stack writes to (Elasticsearch state, Kafka logs, VST sensor state, etc.). Same env file you used with `up`. Sudo may prompt for some paths.

## Step 4 — Tear down AMC (only if you deployed it)

If [`calibration-workflow.md`](calibration-workflow.md) deployed `auto_calib` separately and you didn't tear it down already, do it now:

```bash
cd "${VSS_APPS_DIR}"
COMPOSE_PROFILES=auto-calib docker compose \
  --env-file industry-profiles/warehouse-operations/.env \
  down
```

When AMC was deployed under the warehouse profile gating (i.e. it came up because `bp_wh_*_mv3dt` includes auto-calibration), Step 1 already removed it — no separate teardown needed.

## What is preserved across teardown

These are intentionally not deleted:

- **Calibration outputs** — `${VSS_APPS_DIR}/industry-profiles/warehouse-operations/warehouse-mv3dt-app/calibration/sample-data/<slug>/` (bind-mounted, not a docker volume). Your next deploy reuses them.
- **AMC project state** — `${VSS_APPS_DIR}/services/auto-calibration/projects/project_<id>/` (bind-mounted). Lets you re-run VGGT or fetch logs after teardown.
- **`.env` file** — never touched. Edit it before the next `up`.
- **NGC images** in `nvcr.io` — local docker image cache is preserved. Next deploy uses cached images unless you `--pull always`.

## Nuke option (you're really sure)

When you want to wipe everything including bind-mounted state:

```bash
cd "${VSS_APPS_DIR}"

# Stop everything first
docker compose -f compose.yml \
  --env-file industry-profiles/warehouse-operations/.env down -v --rmi local

# Clear bind-mounted state — DESTRUCTIVE
DATASET="${SAMPLE_VIDEO_DATASET:?}"
sudo rm -rf "${VSS_APPS_DIR}/services/auto-calibration/projects/"
# Only delete your own calibration outputs, not the ship-with-repo sample!
if [ "${DATASET}" != "warehouse-4cams-20mx20m-synthetic" ]; then
  sudo rm -rf "${VSS_APPS_DIR}/industry-profiles/warehouse-operations/warehouse-mv3dt-app/calibration/sample-data/${DATASET}"
fi

bash "${VSS_APPS_DIR}/scripts/cleanup_all_datalog.sh" -e industry-profiles/warehouse-operations/.env
docker volume prune -f
```

Don't run this if you have AMC project state you want to keep — calibration projects under `services/auto-calibration/projects/` are wiped.

## After teardown — common next steps

- Edit `.env` and redeploy: [`deploy-rtvi-cv-3d-stack.md`](deploy-rtvi-cv-3d-stack.md).
- Re-calibrate from scratch: walk [`calibration-workflow.md`](calibration-workflow.md) again.
- Switch to the full warehouse blueprint (with agents / ELK): [`../../vss-deploy-profile/references/warehouse.md`](../../vss-deploy-profile/references/warehouse.md).
