# Monitoring the eight distributed coordinators

This package provides:

- Telegraf collection every 15 seconds on
  `vss-skill-validator-distributed-{1..8}`;
- push-based delivery to InfluxDB 2.x, suitable for Brev hosts behind NAT;
- one Grafana dashboard covering CPU, RAM, root disk, disk throughput,
  load, swap, and host-reporting status;
- no changes to GitHub runner labels or services.

## Dashboard

Import `vss-skill-eval-coordinators.json` into Grafana and select an InfluxDB
2.x datasource configured for Flux. The datasource's default bucket must be
the same bucket used by Telegraf.

The dashboard refreshes every 30 seconds and defaults to the previous six
hours. The `Coordinator` selector shows all eight machines or any subset.
The first tile must report `8`; a lower number means a host has not sent a
`system.uptime` metric within two minutes.

Regenerate the JSON after modifying the generator:

```bash
python3 .github/skill-eval/ops/monitoring/generate_grafana_dashboard.py
```

For an immediate operator view without InfluxDB, run the bundled SSH/Brev
dashboard:

```bash
python3 .github/skill-eval/ops/monitoring/live_dashboard.py \
  --host 127.0.0.1 \
  --port 8765
```

Open `http://127.0.0.1:8765`. The server copies its read-only metrics probe to
`/tmp` on a coordinator when needed, including after a host reboot. Keep it
bound to loopback and use an authenticated SSH tunnel when sharing access.
The Grafana/InfluxDB path remains the persistent production dashboard and
alerting backend.

## InfluxDB setup

Create a dedicated bucket, for example `vss-skill-eval-hosts`, with enough
retention for operational troubleshooting (30 days is a reasonable start).
Create a token with write permission to that bucket only.

On the operator workstation, create a mode-0600 file that is never committed:

```bash
install -m 600 /dev/null /tmp/vss-monitoring.env
```

Its content is:

```dotenv
INFLUX_URL=https://influx.example.com
INFLUX_TOKEN=replace-with-bucket-write-token
INFLUX_ORG=your-org
INFLUX_BUCKET=vss-skill-eval-hosts
```

The staging script transfers this file as a temporary mode-0600 file. Each
host installs it as `/etc/telegraf/vss-skill-eval.env`, owned by
`root:telegraf` with mode `0640`, then deletes the temporary copy.

## Deployment

The default command performs connectivity checks only:

```bash
.github/skill-eval/ops/monitoring/stage_monitoring.sh \
  --env-file /tmp/vss-monitoring.env
```

After verifying all eight host names, start monitoring:

```bash
.github/skill-eval/ops/monitoring/stage_monitoring.sh \
  --env-file /tmp/vss-monitoring.env \
  --apply
```

This starts only `telegraf.service`. It does not start, enable, or relabel any
GitHub runner, so the machines remain unable to accept skill-eval jobs.

## Suggested Grafana alerts

Create alert rules from the dashboard queries:

- **Host missing:** reporting host count below 8 for 2 minutes.
- **Disk warning:** root disk usage above 80% for 10 minutes.
- **Disk critical:** root disk usage above 90% for 5 minutes.
- **RAM pressure:** RAM usage above 90% for 10 minutes.
- **CPU saturation:** active CPU above 90% for 15 minutes.
- **Swap growth:** swap usage above 20% for 10 minutes.

Route host-missing and disk-critical alerts to the team's paging channel;
the remaining alerts are usually ticket or chat severity.

## Verification

On each host:

```bash
systemctl is-active telegraf.service
journalctl -u telegraf.service --since '10 minutes ago'
```

In InfluxDB, confirm exactly eight `coordinator_id` tag values. In Grafana,
select `All` and verify that CPU, RAM, and root-disk panels contain one series
per coordinator.
