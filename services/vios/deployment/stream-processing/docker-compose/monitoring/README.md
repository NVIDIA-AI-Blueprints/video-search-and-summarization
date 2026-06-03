# VST Monitoring Stack

This monitoring stack provides real-time monitoring of VST (Video Streaming Tool) metrics using Prometheus and Grafana.

## Components

- **Prometheus**: Scrapes metrics from VST application running on port 8080
- **Grafana**: Visualizes the metrics with pre-configured dashboards

## Quick Start

1. Make sure your VST application is running and exposing metrics on port 8080
2. Start the monitoring stack:
   ```bash
   cd monitoring
   docker compose up -d
   ```

## Access Points

- **Grafana**: http://localhost:3000
  - Username: `admin`
  - Password: `admin`
- **Prometheus**: http://localhost:9090

## VST Metrics Dashboard

The dashboard includes:
- **Total Packets Received by Camera**: Shows packet reception rate for each camera
- **Packet Loss Percentage by Camera**: Monitors packet loss across cameras
- **Inter-packet Gap (Max) by Camera**: Tracks maximum inter-packet gaps
- **Prometheus Metrics Exported**: Shows volume of metrics being exported

## Configuration

### Prometheus Configuration
- Scrapes VST metrics every 5 seconds from `host.docker.internal:8080/metrics`
- Retains data for 200 hours
- Configuration file: `prometheus.yml`

### Grafana Configuration
- Auto-configured Prometheus datasource
- Pre-loaded VST metrics dashboard
- Dashboard refreshes every 5 seconds for real-time monitoring

## Customization

### Adding New Metrics
1. Check available metrics: `curl http://localhost:8080/metrics`
2. Add new panels to the dashboard via Grafana UI
3. Export the dashboard JSON and update `grafana/provisioning/dashboards/vst-metrics.json`

### Modifying Scrape Intervals
Edit `prometheus.yml` and restart with:
```bash
docker compose restart prometheus
```

## Troubleshooting

### VST Metrics Not Showing
1. Verify VST is running and metrics are enabled in config
2. Check VST metrics endpoint: `curl http://localhost:8080/metrics`
3. Check Prometheus targets: http://localhost:9090/targets

### Container Issues
```bash
# Check container status
docker compose ps

# View logs
docker compose logs prometheus
docker compose logs grafana

# Restart services
docker compose restart
```

## Stopping the Stack

```bash
docker compose down
```

To remove all data:
```bash
docker compose down -v
``` 