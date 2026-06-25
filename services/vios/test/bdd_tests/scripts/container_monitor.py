# SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Container resource monitoring for BDD tests.

Collects docker stats periodically during test execution and saves to reports.

Standalone usage (run from test/bdd_tests/):
    # Monitor all containers, default 10s interval
    python3 scripts/container_monitor.py

    # Custom interval (5 seconds)
    python3 scripts/container_monitor.py -i 5

    # Monitor specific containers only
    python3 scripts/container_monitor.py -c my_container_1 my_container_2

    # Both: specific containers with 3s interval, custom output dir
    python3 scripts/container_monitor.py -i 3 -c nginx redis -o /tmp/my_reports

Press Ctrl+C to stop and save reports (JSON, CSV, summary) to reports/stats/.
"""

import json
import logging
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_monitor_instance: Optional['ContainerMonitor'] = None


class ContainerMonitor:
    """Monitor container resource usage during test execution."""

    def __init__(
        self,
        reports_dir: str,
        interval_seconds: int = 300,
        container_names: Optional[List[str]] = None
    ):
        """Initialize the ContainerMonitor.

        Args:
            reports_dir: Base reports directory (stats will be saved in <reports_dir>/stats/).
            interval_seconds: Interval between stats collection in seconds.
            container_names: Optional list of container names to monitor. None means all.

        Note:
            Container stats are saved to <reports_dir>/stats/ subdirectory automatically.
        """
        self.reports_dir = Path(reports_dir) / "stats"
        self.interval_seconds = interval_seconds
        self.container_names = container_names
        self.stats_history: List[Dict] = []
        self._stop_event = threading.Event()
        self._monitor_thread: Optional[threading.Thread] = None
        self._test_start_time: Optional[datetime] = None
        self._test_end_time: Optional[datetime] = None

    def _parse_docker_stats(self) -> List[Dict]:
        """Run docker stats and parse the output.

        Returns:
            List of container stats dictionaries.
        """
        try:
            result = subprocess.run(
                ['docker', 'stats', '--no-stream', '--format',
                 '{{.Container}}\t{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.NetIO}}\t{{.BlockIO}}\t{{.PIDs}}'],
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode != 0:
                logger.warning("docker stats failed: %s", result.stderr)
                return []

            containers = []
            for line in result.stdout.strip().split('\n'):
                if not line:
                    continue

                parts = line.split('\t')
                if len(parts) >= 8:
                    name = parts[1]
                    if self.container_names and name not in self.container_names:
                        continue
                    containers.append({
                        'container_id': parts[0],
                        'name': name,
                        'cpu_percent': parts[2],
                        'mem_usage': parts[3],
                        'mem_percent': parts[4],
                        'net_io': parts[5],
                        'block_io': parts[6],
                        'pids': parts[7]
                    })

            return containers

        except subprocess.TimeoutExpired:
            logger.warning("docker stats timed out")
            return []
        except FileNotFoundError:
            logger.warning("docker command not found")
            return []
        except Exception as e:
            logger.warning("Error collecting docker stats: %s", e)
            return []

    def _collect_stats(self, print_to_console: bool = False) -> None:
        """Collect a single stats snapshot.

        Args:
            print_to_console: If True, print a live table to stdout.
        """
        timestamp = datetime.now().isoformat()
        containers = self._parse_docker_stats()

        if containers:
            snapshot = {
                'timestamp': timestamp,
                'containers': containers
            }
            self.stats_history.append(snapshot)
            logger.debug("Collected stats for %d containers", len(containers))

            if print_to_console:
                self._print_stats_table(timestamp, containers)

    @staticmethod
    def _print_stats_table(timestamp: str, containers: List[Dict]) -> None:
        """Print a formatted stats table to stdout."""
        header = (
            f"{'CONTAINER':<20} {'CPU %':>8} {'MEM USAGE':>20} {'MEM %':>8} "
            f"{'NET I/O':>24} {'BLOCK I/O':>24} {'PIDS':>6}"
        )
        print(f"\n[{timestamp}]")
        print(header)
        print("-" * len(header))
        for c in containers:
            print(
                f"{c['name']:<20} {c['cpu_percent']:>8} {c['mem_usage']:>20} "
                f"{c['mem_percent']:>8} {c['net_io']:>24} {c['block_io']:>24} "
                f"{c['pids']:>6}"
            )

    def _monitoring_loop(self, print_to_console: bool = False) -> None:
        """Background monitoring loop.

        Args:
            print_to_console: If True, print live stats to stdout each cycle.
        """
        logger.info("Container monitoring started (interval: %ds)", self.interval_seconds)

        self._collect_stats(print_to_console)

        while not self._stop_event.wait(timeout=self.interval_seconds):
            self._collect_stats(print_to_console)

        self._collect_stats(print_to_console)
        logger.info("Container monitoring stopped")

    def log_test_start(self) -> None:
        """Log the test start time."""
        self._test_start_time = datetime.now()
        logger.info("Test session started at %s", self._test_start_time.isoformat())

    def log_test_end(self) -> None:
        """Log the test end time and save reports."""
        self._test_end_time = datetime.now()
        logger.info("Test session ended at %s", self._test_end_time.isoformat())
        self._save_reports()

    def start_background_monitoring(self, print_to_console: bool = False) -> None:
        """Start background stats collection thread.

        Args:
            print_to_console: If True, print live stats to stdout each cycle.
        """
        if self._monitor_thread is not None and self._monitor_thread.is_alive():
            logger.warning("Monitor thread already running")
            return

        self._stop_event.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitoring_loop,
            args=(print_to_console,),
            name="ContainerMonitor",
            daemon=True
        )
        self._monitor_thread.start()

    def stop_background_monitoring(self) -> None:
        """Stop the background monitoring thread."""
        if self._monitor_thread is None:
            return

        self._stop_event.set()
        self._monitor_thread.join(timeout=10)

        if self._monitor_thread.is_alive():
            logger.warning("Monitor thread did not stop gracefully")

    def _save_reports(self) -> None:
        """Save collected stats to report files."""
        if not self.stats_history:
            logger.info("No stats collected, skipping report generation")
            return

        self.reports_dir.mkdir(parents=True, exist_ok=True)

        # Save detailed JSON report
        json_report = {
            'test_start': self._test_start_time.isoformat() if self._test_start_time else None,
            'test_end': self._test_end_time.isoformat() if self._test_end_time else None,
            'collection_interval_seconds': self.interval_seconds,
            'total_snapshots': len(self.stats_history),
            'snapshots': self.stats_history
        }

        json_path = self.reports_dir / 'container_stats.json'
        with open(json_path, 'w') as f:
            json.dump(json_report, f, indent=2)
        logger.info("Saved container stats JSON report: %s", json_path)

        # Save CSV summary for easy analysis
        csv_path = self.reports_dir / 'container_stats.csv'
        with open(csv_path, 'w') as f:
            f.write('timestamp,container_id,name,cpu_percent,mem_usage,mem_percent,net_io,block_io,pids\n')
            for snapshot in self.stats_history:
                ts = snapshot['timestamp']
                for container in snapshot['containers']:
                    f.write(f"{ts},{container['container_id']},{container['name']},"
                           f"{container['cpu_percent']},{container['mem_usage']},"
                           f"{container['mem_percent']},{container['net_io']},"
                           f"{container['block_io']},{container['pids']}\n")
        logger.info("Saved container stats CSV report: %s", csv_path)

        # Generate summary statistics
        self._save_summary()

    def _save_summary(self) -> None:
        """Generate and save a summary of container stats."""
        if not self.stats_history:
            return

        # Aggregate stats by container name
        container_stats: Dict[str, List[Dict]] = {}
        for snapshot in self.stats_history:
            for container in snapshot['containers']:
                name = container['name']
                if name not in container_stats:
                    container_stats[name] = []
                container_stats[name].append(container)

        summary_path = self.reports_dir / 'container_stats_summary.txt'
        with open(summary_path, 'w') as f:
            f.write("Container Resource Usage Summary\n")
            f.write("=" * 60 + "\n\n")

            if self._test_start_time and self._test_end_time:
                duration = (self._test_end_time - self._test_start_time).total_seconds()
                f.write(f"Test Duration: {duration:.1f} seconds\n")
                f.write(f"Collection Interval: {self.interval_seconds} seconds\n")
                f.write(f"Total Snapshots: {len(self.stats_history)}\n\n")

            for name, stats in sorted(container_stats.items()):
                f.write(f"\n{name}\n")
                f.write("-" * 40 + "\n")

                # Parse CPU percentages
                cpu_values = []
                for s in stats:
                    try:
                        cpu_str = s['cpu_percent'].rstrip('%')
                        cpu_values.append(float(cpu_str))
                    except (ValueError, AttributeError):
                        pass

                # Parse memory percentages
                mem_values = []
                for s in stats:
                    try:
                        mem_str = s['mem_percent'].rstrip('%')
                        mem_values.append(float(mem_str))
                    except (ValueError, AttributeError):
                        pass

                if cpu_values:
                    f.write(f"  CPU:  min={min(cpu_values):.2f}%  max={max(cpu_values):.2f}%  "
                           f"avg={sum(cpu_values)/len(cpu_values):.2f}%\n")

                if mem_values:
                    f.write(f"  MEM:  min={min(mem_values):.2f}%  max={max(mem_values):.2f}%  "
                           f"avg={sum(mem_values)/len(mem_values):.2f}%\n")

                f.write(f"  Samples: {len(stats)}\n")

        logger.info("Saved container stats summary: %s", summary_path)


def get_monitor(reports_dir: str, interval_seconds: int = 300) -> ContainerMonitor:
    """Get or create the singleton monitor instance.

    Args:
        reports_dir: Directory to save monitoring reports.
        interval_seconds: Interval between stats collection in seconds.

    Returns:
        The ContainerMonitor instance.
    """
    global _monitor_instance
    if _monitor_instance is None:
        _monitor_instance = ContainerMonitor(reports_dir, interval_seconds)
    return _monitor_instance


def cleanup_monitor() -> None:
    """Clean up the monitor instance."""
    global _monitor_instance
    _monitor_instance = None


def _run_standalone() -> None:
    """CLI entry point for standalone container monitoring."""
    import argparse
    import signal

    parser = argparse.ArgumentParser(
        description="Monitor Docker container resource usage. Runs until Ctrl+C."
    )
    parser.add_argument(
        "-i", "--interval",
        type=int,
        default=10,
        help="Stats collection interval in seconds (default: 10)"
    )
    parser.add_argument(
        "-c", "--containers",
        nargs="+",
        default=None,
        metavar="NAME",
        help="Container names to monitor (default: all running containers)"
    )
    parser.add_argument(
        "-o", "--output-dir",
        default="reports",
        help="Base reports directory (default: reports)"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )

    monitor = ContainerMonitor(
        reports_dir=args.output_dir,
        interval_seconds=args.interval,
        container_names=args.containers
    )

    filter_msg = ", ".join(args.containers) if args.containers else "all"
    print(f"Monitoring containers: {filter_msg}")
    print(f"Collection interval : {args.interval}s")
    print(f"Reports directory   : {Path(args.output_dir) / 'stats'}")
    print("Press Ctrl+C to stop and save reports.\n")

    monitor.log_test_start()
    monitor.start_background_monitoring(print_to_console=True)

    stop_event = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop_event.set())
    signal.signal(signal.SIGTERM, lambda *_: stop_event.set())

    stop_event.wait()

    print("\n\nStopping monitor...")
    monitor.stop_background_monitoring()
    monitor.log_test_end()
    print("Reports saved. Exiting.")


if __name__ == "__main__":
    _run_standalone()
