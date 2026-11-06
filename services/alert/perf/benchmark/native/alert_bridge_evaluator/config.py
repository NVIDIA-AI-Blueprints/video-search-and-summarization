"""
Configuration settings for Alert Bridge Evaluator

Configuration is loaded in this order (later sources override earlier):
1. Default values (hardcoded)
2. YAML config file (configs/config.yml)
3. Environment variables
4. Command line arguments (via Settings.override())
"""

import os
import yaml
import logging
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass, field
from urllib.parse import urlparse

try:
    import redis  # type: ignore
except ImportError:  # pragma: no cover - only in lightweight test envs
    redis = None

logger = logging.getLogger(__name__)

# Default config file path (relative to this file)
DEFAULT_CONFIG_PATH = Path(__file__).parent / "configs" / "config.yml"


def _expand_env_vars(value: Any) -> Any:
    """
    Recursively expand environment variables in config values.

    Supports format: ${VAR_NAME:-default_value}

    Args:
        value: Config value (string, dict, list, or other)

    Returns:
        Value with environment variables expanded
    """
    import re

    if isinstance(value, str):
        # Pattern: ${VAR_NAME:-default_value} or ${VAR_NAME}
        pattern = r'\$\{([^}:]+)(?::-([^}]*))?\}'

        def replace_env(match):
            var_name = match.group(1)
            default_value = match.group(2) if match.group(2) is not None else ''
            return os.getenv(var_name, default_value)

        return re.sub(pattern, replace_env, value)

    elif isinstance(value, dict):
        return {k: _expand_env_vars(v) for k, v in value.items()}

    elif isinstance(value, list):
        return [_expand_env_vars(item) for item in value]

    return value


def load_yaml_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Load configuration from YAML file.

    Supports environment variable expansion in format: ${VAR_NAME:-default_value}

    Args:
        config_path: Path to config file. If None, uses default path.

    Returns:
        Dictionary with configuration values (with env vars expanded)
    """
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH

    if not path.exists():
        logger.warning(f"Config file not found: {path}, using defaults")
        return {}

    try:
        with open(path, 'r') as f:
            config = yaml.safe_load(f) or {}

        # Expand environment variables
        config = _expand_env_vars(config)

        logger.info(f"✓ Loaded config from {path}")
        return config
    except Exception as e:
        logger.error(f"Failed to load config from {path}: {e}")
        return {}


def _get_nested(config: Dict, *keys, default=None):
    """Get nested value from config dict."""
    value = config
    for key in keys:
        if isinstance(value, dict):
            value = value.get(key)
        else:
            return default
    return value if value is not None else default


@dataclass
class Settings:
    """
    Application settings loaded from YAML config and environment variables.

    Priority: ENV vars > YAML config > defaults

    All hosts default to localhost for local development.
    In production, configure via config.yml or environment variables.
    """

    # Config file path
    config_file: Optional[str] = None

    # File paths
    gt_file_path: str = field(default='gt.json')
    reports_dir: str = field(default='reports')

    # Service configuration
    host: str = field(default='0.0.0.0')
    port: int = field(default=5001)

    # Redis configuration
    redis_host: str = field(default='localhost')
    redis_port: int = field(default=6379)
    redis_db: int = field(default=0)
    redis_password: Optional[str] = field(default=None)
    job_ttl: int = field(default=86400)  # 24 hours

    # Alert Bridge configuration
    alert_bridge_url: str = field(default='http://localhost:9080')
    alert_bridge_timeout: int = field(default=180)
    # Endpoint path for the on-demand verification API. Kept configurable so
    # the same evaluator can target a future revision of the AB API without a
    # code change. Default matches alert-agent-web's
    # `verification_routes.py::POST /api/v1/verification/ondemand`.
    alert_bridge_endpoint_path: str = field(default='/api/v1/verification/ondemand')
    # Incident ingestion endpoint used by synthetic latency message-bus mode.
    # Alert Agent accepts NvSchema incident JSON here and publishes it to the
    # configured Kafka incident topic, so the normal worker/Prometheus path is
    # exercised without requiring the evaluator to produce Kafka protobufs.
    alert_bridge_incident_endpoint_path: str = field(default='/api/v1/incidents')

    # Seed AB's prompt registry (`POST /api/v1/verification/config`) from the
    # GT file at evaluator startup. Keep disabled by default so metrics-only
    # latency evals do not write prompt config; run_eval.sh enables it for
    # alerts verification synthetic injection.
    auto_seed_ab: bool = field(default=False)
    # Max time (seconds) to wait for AB to become healthy before seeding.
    # Startup proceeds even after timeout — seeding is best-effort and the
    # per-incident path will retry-with-seed on UnknownCategoryError.
    ab_health_timeout: int = field(default=60)
    # Thread-pool size for parallel seed POSTs (and the matching cleanup
    # DELETEs). 8 is conservative; bump if AB's Redis can take more.
    seed_ab_concurrency: int = field(default=8)

    # Media configuration — required to build `info.media_urls` for
    # /api/v1/verification/ondemand. Each GT entry's `video` is mapped to
    # `f"{media_base_url}/{video}{media_file_ext}"` unless the GT entry
    # already provides an absolute URL via `media_url` or `media_urls`.
    media_base_url: str = field(default='http://127.0.0.1:30888/vst/sim/media')
    media_file_ext: str = field(default='.mp4')
    media_type: str = field(default='video')
    # Optional VST storage URL generation for Helm synthetic latency. When set,
    # the evaluator asks VST for a temporary /vst/storage/temp_files/*.mp4 URL
    # per runtime sensor before falling back to media_base_url.
    vst_base_url: str = field(default='')
    vst_media_expiry_minutes: int = field(default=60)
    vst_media_window_seconds: int = field(default=30)
    vst_media_disable_audio: bool = field(default=False)
    vst_media_timeout: int = field(default=30)
    vst_request_attempts: int = field(default=3)
    vst_request_backoff_seconds: float = field(default=1.0)
    # Before continuous synthetic latency injection starts, wait for VST to
    # report every runtime stream online and, by default, with storage
    # timelines. This avoids injecting synthetic alerts before BP configurator
    # has finished registering the replicated streams.
    vst_sensor_ready_timeout_seconds: int = field(default=900)
    vst_sensor_ready_poll_seconds: int = field(default=5)
    vst_require_timelines: bool = field(default=True)

    # Elasticsearch configuration
    elasticsearch_url: str = field(default='http://localhost:9200')
    elasticsearch_index_incidents: str = field(default='mdx-vlm-incidents*')
    elasticsearch_index_alerts: str = field(default='mdx-vlm-alerts*')
    elasticsearch_timeout: int = field(default=30)

    # Data source selection for v2 evaluation
    data_source: str = field(default='elasticsearch')

    # Prometheus configuration (used when data_source == "prometheus")
    prometheus_host: str = field(default='localhost')
    prometheus_port: int = field(default=9090)
    prometheus_url: str = field(default='http://localhost:9090')
    prometheus_timeout: int = field(default=30)
    prometheus_summary_buffer_seconds: int = field(default=0)
    prometheus_metric_profile: str = field(default='realtime')
    _prometheus_metric_profile_explicit: bool = field(default=False, init=False, repr=False)

    # Polling configuration for waiting VLM results
    result_poll_interval: float = field(default=2.0)
    result_poll_timeout: int = field(default=10)  # seconds
    result_poll_max_attempts: int = field(default=3)  # max attempts (0 = use timeout)

    # Evaluation workflow: "verification" (alert-bridge) | "real-time" (rtvi-vlm)
    eval_workflow: str = field(default='verification')

    # Evaluation mode: "accuracy" (default) | "latency"
    eval_mode: str = field(default='accuracy')
    setup_indexed_at: bool = field(default=False)
    latency_duration: str = field(default='1h')
    default_incident_category: str = field(default='')
    synthetic_alerts_injection: bool = field(default=False)
    synthetic_alert_submission_mode: str = field(default='message_bus')
    synthetic_alerts_per_minute: Optional[float] = field(default=None)
    synthetic_alert_event_duration_ms: Optional[int] = field(default=None)
    synthetic_e2e_min_coverage_ratio: float = field(default=0.99)

    # Deployment metadata (for summary reports)
    platform: str = field(default='')
    model: str = field(default='')

    # Stream concurrency (from pipeline config; 0 = auto-detect from ES data)
    max_stream_concurrency: int = field(default=0)

    # Kafka (for counting actual VLM output messages)
    kafka_bootstrap_servers: str = field(default='')
    kafka_incident_topic: str = field(default='mdx-incidents')
    kafka_alert_topic: str = field(default='mdx-alerts')
    kafka_vlm_topic: str = field(default='mdx-vlm-incidents')
    kafka_produce_flush_timeout: float = field(default=10.0)

    # Logging
    log_level: str = field(default='INFO')

    def __post_init__(self):
        """Load config from YAML and environment variables after init."""
        self._load_from_yaml()
        self._load_from_env()
        self._sync_prometheus_url_from_host_port()
        self._validate()

    def _load_from_yaml(self) -> None:
        """Load settings from YAML config file."""
        config = load_yaml_config(self.config_file)

        if not config:
            return

        # Service
        self.host = _get_nested(config, 'service', 'host', default=self.host)
        self.port = _get_nested(config, 'service', 'port', default=self.port)

        # Ground truth
        self.gt_file_path = _get_nested(config, 'ground_truth', 'file_path', default=self.gt_file_path)

        # Reports
        self.reports_dir = _get_nested(config, 'reports', 'directory', default=self.reports_dir)

        # Redis
        self.redis_host = _get_nested(config, 'redis', 'host', default=self.redis_host)
        self.redis_port = _get_nested(config, 'redis', 'port', default=self.redis_port)
        self.redis_db = _get_nested(config, 'redis', 'db', default=self.redis_db)
        self.redis_password = _get_nested(config, 'redis', 'password', default=self.redis_password)
        self.job_ttl = _get_nested(config, 'redis', 'job_ttl', default=self.job_ttl)

        # Alert Bridge
        self.alert_bridge_url = _get_nested(config, 'alert_bridge', 'base_url', default=self.alert_bridge_url)
        self.alert_bridge_timeout = _get_nested(config, 'alert_bridge', 'timeout', default=self.alert_bridge_timeout)
        self.alert_bridge_endpoint_path = _get_nested(
            config, 'alert_bridge', 'endpoint_path',
            default=self.alert_bridge_endpoint_path,
        )
        self.alert_bridge_incident_endpoint_path = _get_nested(
            config, 'alert_bridge', 'incident_endpoint_path',
            default=self.alert_bridge_incident_endpoint_path,
        )
        auto_seed_raw = _get_nested(config, 'alert_bridge', 'auto_seed', default=self.auto_seed_ab)
        self.auto_seed_ab = str(auto_seed_raw).lower() in ('true', '1', 'yes')
        self.ab_health_timeout = int(_get_nested(
            config, 'alert_bridge', 'health_timeout', default=self.ab_health_timeout,
        ))
        self.seed_ab_concurrency = int(_get_nested(
            config, 'alert_bridge', 'seed_concurrency', default=self.seed_ab_concurrency,
        ))

        # Media (for /api/v1/verification/ondemand `info.media_urls`)
        self.media_base_url = _get_nested(config, 'media', 'base_url', default=self.media_base_url)
        self.media_file_ext = _get_nested(config, 'media', 'file_ext', default=self.media_file_ext)
        self.media_type = _get_nested(config, 'media', 'media_type', default=self.media_type)
        self.vst_base_url = str(_get_nested(config, 'media', 'vst_base_url', default=self.vst_base_url) or '').rstrip('/')
        self.vst_media_expiry_minutes = int(_get_nested(
            config, 'media', 'vst_expiry_minutes', default=self.vst_media_expiry_minutes,
        ))
        self.vst_media_window_seconds = int(_get_nested(
            config, 'media', 'vst_window_seconds', default=self.vst_media_window_seconds,
        ))
        disable_audio_raw = _get_nested(
            config, 'media', 'vst_disable_audio', default=self.vst_media_disable_audio,
        )
        self.vst_media_disable_audio = str(disable_audio_raw).lower() in ('true', '1', 'yes')
        self.vst_media_timeout = int(_get_nested(
            config, 'media', 'vst_timeout', default=self.vst_media_timeout,
        ))
        self.vst_request_attempts = int(_get_nested(
            config, 'media', 'vst_request_attempts', default=self.vst_request_attempts,
        ))
        self.vst_request_backoff_seconds = float(_get_nested(
            config, 'media', 'vst_request_backoff_seconds', default=self.vst_request_backoff_seconds,
        ))
        self.vst_sensor_ready_timeout_seconds = int(_get_nested(
            config, 'media', 'vst_sensor_ready_timeout_seconds',
            default=_get_nested(
                config,
                'media',
                'vst_sensor_ready_timeout',
                default=self.vst_sensor_ready_timeout_seconds,
            ),
        ))
        self.vst_sensor_ready_poll_seconds = int(_get_nested(
            config, 'media', 'vst_sensor_ready_poll_seconds',
            default=self.vst_sensor_ready_poll_seconds,
        ))
        require_timelines_raw = _get_nested(
            config, 'media', 'vst_require_timelines', default=self.vst_require_timelines,
        )
        self.vst_require_timelines = str(require_timelines_raw).lower() in ('true', '1', 'yes')

        # Elasticsearch
        es_hosts = _get_nested(config, 'elasticsearch', 'hosts', default=[])
        if es_hosts and len(es_hosts) > 0:
            self.elasticsearch_url = es_hosts[0]
        self.elasticsearch_index_incidents = _get_nested(config, 'elasticsearch', 'index_incidents', default=self.elasticsearch_index_incidents)
        self.elasticsearch_index_alerts = _get_nested(config, 'elasticsearch', 'index_alerts', default=self.elasticsearch_index_alerts)
        # Backward compatibility: "index" in YAML sets incidents
        if _get_nested(config, 'elasticsearch', 'index') is not None:
            self.elasticsearch_index_incidents = _get_nested(config, 'elasticsearch', 'index', default=self.elasticsearch_index_incidents)
        self.elasticsearch_timeout = _get_nested(config, 'elasticsearch', 'timeout', default=self.elasticsearch_timeout)

        # Data source + Prometheus
        self.data_source = str(
            _get_nested(config, 'data_source', default=self.data_source)
        ).lower()
        prom_url = _get_nested(config, 'prometheus', 'url', default=None)
        if prom_url:
            self.prometheus_url = str(prom_url)
            self._set_prometheus_host_port_from_url(self.prometheus_url)
        prom_host = _get_nested(config, 'prometheus', 'host', default=None)
        if prom_host:
            self.prometheus_host = str(prom_host)
        prom_port = _get_nested(config, 'prometheus', 'port', default=None)
        if prom_port is not None:
            self.prometheus_port = int(prom_port)
        self._sync_prometheus_url_from_host_port()
        self.prometheus_timeout = int(
            _get_nested(config, 'prometheus', 'timeout', default=self.prometheus_timeout)
        )
        self.prometheus_summary_buffer_seconds = int(
            _get_nested(
                config,
                'prometheus',
                'summary_buffer_seconds',
                default=self.prometheus_summary_buffer_seconds,
            )
        )
        prom_metric_profile = _get_nested(config, 'prometheus', 'metric_profile', default=None)
        if prom_metric_profile:
            self.prometheus_metric_profile = str(prom_metric_profile).strip().lower()
            self._prometheus_metric_profile_explicit = True

        # Polling
        self.result_poll_interval = _get_nested(config, 'polling', 'interval', default=self.result_poll_interval)
        self.result_poll_timeout = _get_nested(config, 'polling', 'timeout', default=self.result_poll_timeout)
        self.result_poll_max_attempts = _get_nested(config, 'polling', 'max_attempts', default=self.result_poll_max_attempts)

        # Evaluation workflow & mode
        self.eval_workflow = str(_get_nested(config, 'evaluation', 'workflow', default=self.eval_workflow)).lower()
        self.eval_mode = str(_get_nested(config, 'evaluation', 'mode', default=self.eval_mode)).lower()
        setup_raw = _get_nested(config, 'evaluation', 'setup_indexed_at', default=self.setup_indexed_at)
        self.setup_indexed_at = str(setup_raw).lower() in ('true', '1', 'yes')
        self.latency_duration = _get_nested(config, 'evaluation', 'duration', default=self.latency_duration)
        self.default_incident_category = str(
            _get_nested(
                config,
                'evaluation',
                'default_incident_category',
                default=self.default_incident_category,
            )
            or ''
        ).strip()
        synthetic_raw = _get_nested(
            config,
            'evaluation',
            'synthetic_alerts_injection',
            default=self.synthetic_alerts_injection,
        )
        self.synthetic_alerts_injection = str(synthetic_raw).lower() in ('true', '1', 'yes')
        self.synthetic_alert_submission_mode = _get_nested(
            config,
            'evaluation',
            'synthetic_alert_submission_mode',
            default=self.synthetic_alert_submission_mode,
        )
        self.synthetic_alerts_per_minute = self._parse_optional_positive_float(
            _get_nested(
                config,
                'evaluation',
                'synthetic_alerts_per_minute',
                default=self.synthetic_alerts_per_minute,
            ),
            field_name='synthetic_alerts_per_minute',
        )
        self.synthetic_alert_event_duration_ms = self._parse_optional_positive_int(
            _get_nested(
                config,
                'evaluation',
                'synthetic_alert_event_duration_ms',
                default=self.synthetic_alert_event_duration_ms,
            ),
            field_name='synthetic_alert_event_duration_ms',
        )
        self.synthetic_e2e_min_coverage_ratio = self._parse_coverage_ratio(
            _get_nested(
                config,
                'evaluation',
                'synthetic_e2e_min_coverage_ratio',
                default=self.synthetic_e2e_min_coverage_ratio,
            ),
            field_name='synthetic_e2e_min_coverage_ratio',
        )

        # Deployment metadata
        self.platform = _get_nested(config, 'metadata', 'platform', default=self.platform)
        self.model = _get_nested(config, 'metadata', 'model', default=self.model)

        # Kafka
        self.kafka_bootstrap_servers = _get_nested(config, 'kafka', 'bootstrap_servers', default=self.kafka_bootstrap_servers)
        self.kafka_incident_topic = _get_nested(config, 'kafka', 'incident_topic', default=self.kafka_incident_topic)
        self.kafka_alert_topic = _get_nested(config, 'kafka', 'alert_topic', default=self.kafka_alert_topic)
        self.kafka_vlm_topic = _get_nested(config, 'kafka', 'vlm_topic', default=self.kafka_vlm_topic)
        self.kafka_produce_flush_timeout = _get_nested(config, 'kafka', 'produce_flush_timeout', default=self.kafka_produce_flush_timeout)

        # Logging
        self.log_level = _get_nested(config, 'logging', 'level', default=self.log_level)

    def _load_from_env(self) -> None:
        """Load settings from environment variables (overrides YAML)."""
        # File paths
        if os.getenv('GT_FILE_PATH'):
            self.gt_file_path = os.getenv('GT_FILE_PATH')
        # REPORTS_DIRECTORY takes priority (used in config.yml), REPORTS_DIR for backwards compatibility
        if os.getenv('REPORTS_DIRECTORY'):
            self.reports_dir = os.getenv('REPORTS_DIRECTORY')
        elif os.getenv('REPORTS_DIR'):
            self.reports_dir = os.getenv('REPORTS_DIR')

        # Service
        if os.getenv('HOST'):
            self.host = os.getenv('HOST')
        if os.getenv('PORT'):
            self.port = int(os.getenv('PORT'))

        # Redis
        if os.getenv('REDIS_HOST'):
            self.redis_host = os.getenv('REDIS_HOST')
        if os.getenv('REDIS_PORT'):
            self.redis_port = int(os.getenv('REDIS_PORT'))
        if os.getenv('REDIS_DB'):
            self.redis_db = int(os.getenv('REDIS_DB'))
        if os.getenv('REDIS_PASSWORD'):
            self.redis_password = os.getenv('REDIS_PASSWORD')
        if os.getenv('JOB_TTL'):
            self.job_ttl = int(os.getenv('JOB_TTL'))

        # Alert Bridge
        if os.getenv('ALERT_BRIDGE_URL'):
            self.alert_bridge_url = os.getenv('ALERT_BRIDGE_URL')
        if os.getenv('ALERT_BRIDGE_TIMEOUT'):
            self.alert_bridge_timeout = int(os.getenv('ALERT_BRIDGE_TIMEOUT'))
        if os.getenv('ALERT_BRIDGE_ENDPOINT_PATH'):
            self.alert_bridge_endpoint_path = os.getenv('ALERT_BRIDGE_ENDPOINT_PATH')
        if os.getenv('ALERT_BRIDGE_INCIDENT_ENDPOINT_PATH'):
            self.alert_bridge_incident_endpoint_path = os.getenv('ALERT_BRIDGE_INCIDENT_ENDPOINT_PATH')
        if os.getenv('AUTO_SEED_AB'):
            self.auto_seed_ab = os.getenv('AUTO_SEED_AB', '').lower() in ('true', '1', 'yes')
        if os.getenv('AB_HEALTH_TIMEOUT'):
            self.ab_health_timeout = int(os.getenv('AB_HEALTH_TIMEOUT'))
        if os.getenv('SEED_AB_CONCURRENCY'):
            self.seed_ab_concurrency = int(os.getenv('SEED_AB_CONCURRENCY'))

        # Media
        if os.getenv('MEDIA_BASE_URL'):
            self.media_base_url = os.getenv('MEDIA_BASE_URL')
        if os.getenv('MEDIA_FILE_EXT'):
            self.media_file_ext = os.getenv('MEDIA_FILE_EXT')
        if os.getenv('MEDIA_TYPE'):
            self.media_type = os.getenv('MEDIA_TYPE')
        if os.getenv('VST_BASE_URL'):
            self.vst_base_url = os.getenv('VST_BASE_URL', '').rstrip('/')
        if os.getenv('VST_MEDIA_EXPIRY_MINUTES'):
            self.vst_media_expiry_minutes = int(os.getenv('VST_MEDIA_EXPIRY_MINUTES'))
        if os.getenv('VST_MEDIA_WINDOW_SECONDS'):
            self.vst_media_window_seconds = int(os.getenv('VST_MEDIA_WINDOW_SECONDS'))
        if os.getenv('VST_MEDIA_DISABLE_AUDIO'):
            self.vst_media_disable_audio = os.getenv('VST_MEDIA_DISABLE_AUDIO', '').lower() in ('true', '1', 'yes')
        if os.getenv('VST_MEDIA_TIMEOUT'):
            self.vst_media_timeout = int(os.getenv('VST_MEDIA_TIMEOUT'))
        if os.getenv('VST_REQUEST_ATTEMPTS'):
            self.vst_request_attempts = int(os.getenv('VST_REQUEST_ATTEMPTS'))
        if os.getenv('VST_REQUEST_BACKOFF_SECONDS'):
            self.vst_request_backoff_seconds = float(os.getenv('VST_REQUEST_BACKOFF_SECONDS'))
        if os.getenv('VST_SENSOR_READY_TIMEOUT_SECONDS'):
            self.vst_sensor_ready_timeout_seconds = int(os.getenv('VST_SENSOR_READY_TIMEOUT_SECONDS'))
        if os.getenv('VST_SENSOR_READY_POLL_SECONDS'):
            self.vst_sensor_ready_poll_seconds = int(os.getenv('VST_SENSOR_READY_POLL_SECONDS'))
        if os.getenv('VST_REQUIRE_TIMELINES'):
            self.vst_require_timelines = os.getenv('VST_REQUIRE_TIMELINES', '').lower() in ('true', '1', 'yes')

        # Elasticsearch
        if os.getenv('ELASTICSEARCH_URL'):
            self.elasticsearch_url = os.getenv('ELASTICSEARCH_URL')
        if os.getenv('ELASTICSEARCH_INDEX_INCIDENTS'):
            self.elasticsearch_index_incidents = os.getenv('ELASTICSEARCH_INDEX_INCIDENTS')
        if os.getenv('ELASTICSEARCH_INDEX_ALERTS'):
            self.elasticsearch_index_alerts = os.getenv('ELASTICSEARCH_INDEX_ALERTS')
        if os.getenv('ELASTICSEARCH_TIMEOUT'):
            self.elasticsearch_timeout = int(os.getenv('ELASTICSEARCH_TIMEOUT'))

        # Data source + Prometheus
        if os.getenv('DATA_SOURCE'):
            self.data_source = os.getenv('DATA_SOURCE', 'elasticsearch').lower()
        if os.getenv('PROMETHEUS_URL'):
            self.prometheus_url = os.getenv('PROMETHEUS_URL')
            self._set_prometheus_host_port_from_url(self.prometheus_url)
        if os.getenv('PROMETHEUS_HOST'):
            self.prometheus_host = os.getenv('PROMETHEUS_HOST')
        if os.getenv('PROMETHEUS_PORT'):
            self.prometheus_port = int(os.getenv('PROMETHEUS_PORT'))
        if os.getenv('PROMETHEUS_URL') or os.getenv('PROMETHEUS_HOST') or os.getenv('PROMETHEUS_PORT'):
            self._sync_prometheus_url_from_host_port()
        if os.getenv('PROMETHEUS_TIMEOUT'):
            self.prometheus_timeout = int(os.getenv('PROMETHEUS_TIMEOUT'))
        if os.getenv('PROMETHEUS_SUMMARY_BUFFER_SECONDS'):
            self.prometheus_summary_buffer_seconds = int(
                os.getenv('PROMETHEUS_SUMMARY_BUFFER_SECONDS')
            )
        prom_metric_profile = (
            os.getenv('ALERT_BRIDGE_EVALUATOR_METRIC_PROFILE')
            or os.getenv('PROMETHEUS_METRIC_PROFILE')
        )
        if prom_metric_profile:
            self.prometheus_metric_profile = prom_metric_profile.strip().lower()
            self._prometheus_metric_profile_explicit = True

        # Polling
        if os.getenv('RESULT_POLL_INTERVAL'):
            self.result_poll_interval = float(os.getenv('RESULT_POLL_INTERVAL'))
        if os.getenv('RESULT_POLL_TIMEOUT'):
            self.result_poll_timeout = int(os.getenv('RESULT_POLL_TIMEOUT'))
        if os.getenv('RESULT_POLL_MAX_ATTEMPTS'):
            self.result_poll_max_attempts = int(os.getenv('RESULT_POLL_MAX_ATTEMPTS'))

        # Evaluation workflow & mode
        if os.getenv('EVAL_WORKFLOW'):
            self.eval_workflow = os.getenv('EVAL_WORKFLOW', 'verification').lower()
        if os.getenv('EVAL_MODE'):
            self.eval_mode = os.getenv('EVAL_MODE', 'accuracy').lower()
        if os.getenv('SETUP_INDEXED_AT'):
            self.setup_indexed_at = os.getenv('SETUP_INDEXED_AT', '').lower() in ('true', '1', 'yes')
        if os.getenv('LATENCY_DURATION'):
            self.latency_duration = os.getenv('LATENCY_DURATION')
        if os.getenv('ALERT_BRIDGE_INCIDENT_CATEGORY'):
            self.default_incident_category = os.getenv('ALERT_BRIDGE_INCIDENT_CATEGORY', '').strip()
        if os.getenv('SYNTHETIC_ALERTS_INJECTION'):
            self.synthetic_alerts_injection = os.getenv('SYNTHETIC_ALERTS_INJECTION', '').lower() in ('true', '1', 'yes')
        synthetic_submission_mode = (
            os.getenv('ALERT_BRIDGE_SYNTHETIC_SUBMISSION_MODE')
            or os.getenv('SYNTHETIC_ALERT_SUBMISSION_MODE')
        )
        if synthetic_submission_mode:
            self.synthetic_alert_submission_mode = synthetic_submission_mode
        if os.getenv('SYNTHETIC_ALERTS_PER_MINUTE'):
            self.synthetic_alerts_per_minute = self._parse_optional_positive_float(
                os.getenv('SYNTHETIC_ALERTS_PER_MINUTE'),
                field_name='SYNTHETIC_ALERTS_PER_MINUTE',
            )
        if os.getenv('SYNTHETIC_ALERT_EVENT_DURATION_MS'):
            self.synthetic_alert_event_duration_ms = self._parse_optional_positive_int(
                os.getenv('SYNTHETIC_ALERT_EVENT_DURATION_MS'),
                field_name='SYNTHETIC_ALERT_EVENT_DURATION_MS',
            )
        if os.getenv('SYNTHETIC_E2E_MIN_COVERAGE_RATIO'):
            self.synthetic_e2e_min_coverage_ratio = self._parse_coverage_ratio(
                os.getenv('SYNTHETIC_E2E_MIN_COVERAGE_RATIO'),
                field_name='SYNTHETIC_E2E_MIN_COVERAGE_RATIO',
            )

        # Deployment metadata
        if os.getenv('PLATFORM'):
            self.platform = os.getenv('PLATFORM')
        if os.getenv('MODEL'):
            self.model = os.getenv('MODEL')

        # Stream concurrency
        if os.getenv('MAX_STREAM_CONCURRENCY'):
            self.max_stream_concurrency = int(os.getenv('MAX_STREAM_CONCURRENCY'))

        # Kafka
        if os.getenv('KAFKA_BOOTSTRAP_SERVERS'):
            self.kafka_bootstrap_servers = os.getenv('KAFKA_BOOTSTRAP_SERVERS')
        if os.getenv('KAFKA_INCIDENT_TOPIC'):
            self.kafka_incident_topic = os.getenv('KAFKA_INCIDENT_TOPIC')
        if os.getenv('KAFKA_ALERT_TOPIC'):
            self.kafka_alert_topic = os.getenv('KAFKA_ALERT_TOPIC')
        if os.getenv('KAFKA_VLM_TOPIC'):
            self.kafka_vlm_topic = os.getenv('KAFKA_VLM_TOPIC')
        if os.getenv('KAFKA_PRODUCE_FLUSH_TIMEOUT'):
            self.kafka_produce_flush_timeout = float(os.getenv('KAFKA_PRODUCE_FLUSH_TIMEOUT'))

        # Logging
        if os.getenv('LOG_LEVEL'):
            self.log_level = os.getenv('LOG_LEVEL')

    @property
    def redis_url(self) -> str:
        """Construct Redis URL from components"""
        if self.redis_password:
            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    def _set_prometheus_host_port_from_url(self, url: str) -> None:
        """Best-effort parse of host/port from a Prometheus URL."""
        if not url:
            return
        normalized = url if "://" in url else f"http://{url}"
        parsed = urlparse(normalized)
        if parsed.hostname:
            self.prometheus_host = parsed.hostname
        if parsed.port:
            self.prometheus_port = parsed.port

    def _sync_prometheus_url_from_host_port(self) -> None:
        """Build Prometheus URL from host/port settings."""
        host = (self.prometheus_host or "localhost").strip()
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        self.prometheus_url = f"http://{host}:{int(self.prometheus_port)}"

    def override(self, **kwargs) -> 'Settings':
        """
        Create a new Settings instance with overridden values.

        Args:
            **kwargs: Settings to override (e.g., gt_file_path='/path/to/gt.json')

        Returns:
            New Settings instance with overridden values
        """
        for key, value in kwargs.items():
            if value is not None and hasattr(self, key):
                setattr(self, key, value)
        self._validate()
        return self

    def _validate(self) -> None:
        self.data_source = str(self.data_source or 'elasticsearch').strip().lower()
        self.eval_mode = str(self.eval_mode or 'accuracy').strip().lower()
        self.synthetic_alert_submission_mode = self._normalize_synthetic_alert_submission_mode(
            self.synthetic_alert_submission_mode
        )
        self.synthetic_alerts_per_minute = self._parse_optional_positive_float(
            self.synthetic_alerts_per_minute,
            field_name='synthetic_alerts_per_minute',
        )
        self.synthetic_alert_event_duration_ms = self._parse_optional_positive_int(
            self.synthetic_alert_event_duration_ms,
            field_name='synthetic_alert_event_duration_ms',
        )
        self.synthetic_e2e_min_coverage_ratio = self._parse_coverage_ratio(
            self.synthetic_e2e_min_coverage_ratio,
            field_name='synthetic_e2e_min_coverage_ratio',
        )
        self.prometheus_metric_profile = str(self.prometheus_metric_profile or 'realtime').strip().lower()
        auto_profile = self.prometheus_metric_profile in {'', 'auto'}
        synthetic_prometheus_latency = (
            self.data_source == 'prometheus'
            and self.eval_mode == 'latency'
            and self.synthetic_alerts_injection
        )
        if auto_profile or (not self._prometheus_metric_profile_explicit and synthetic_prometheus_latency):
            if synthetic_prometheus_latency and self.synthetic_alert_submission_mode == 'ondemand_api':
                self.prometheus_metric_profile = 'ondemand'
            else:
                self.prometheus_metric_profile = 'realtime'

        self.vst_request_attempts = int(self.vst_request_attempts)
        self.vst_request_backoff_seconds = float(self.vst_request_backoff_seconds)
        if self.vst_request_attempts <= 0:
            raise ValueError("vst_request_attempts must be positive")
        if self.vst_request_backoff_seconds < 0:
            raise ValueError("vst_request_backoff_seconds must be non-negative")

        valid_sources = {'elasticsearch', 'prometheus'}
        if self.data_source not in valid_sources:
            raise ValueError(
                f"Invalid data_source={self.data_source!r}. "
                f"Supported values: {sorted(valid_sources)}"
            )
        valid_metric_profiles = {'realtime', 'ondemand'}
        if self.prometheus_metric_profile not in valid_metric_profiles:
            raise ValueError(
                f"Invalid prometheus_metric_profile={self.prometheus_metric_profile!r}. "
                f"Supported values: {sorted(valid_metric_profiles)}"
            )

    @staticmethod
    def _normalize_synthetic_alert_submission_mode(value: Any) -> str:
        normalized = str(value or 'message_bus').strip().lower().replace('-', '_')
        aliases = {
            'message_bus': 'message_bus',
            'messagebus': 'message_bus',
            'bus': 'message_bus',
            'kafka': 'message_bus',
            'incident_api': 'incident_api',
            'http_incident_api': 'incident_api',
            'http_incident': 'incident_api',
            'ondemand_api': 'ondemand_api',
            'ondemand': 'ondemand_api',
            'on_demand': 'ondemand_api',
            'api': 'ondemand_api',
        }
        if normalized in aliases:
            return aliases[normalized]
        raise ValueError(
            "Invalid synthetic_alert_submission_mode="
            f"{value!r}. Supported values: ['message_bus', 'incident_api', 'ondemand_api']"
        )

    @staticmethod
    def _parse_optional_positive_float(value: Any, *, field_name: str) -> Optional[float]:
        if value is None:
            return None
        if isinstance(value, str) and value.strip() == '':
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name} must be a positive number") from exc
        if parsed <= 0:
            raise ValueError(f"{field_name} must be a positive number")
        return parsed


    @staticmethod
    def _parse_coverage_ratio(value: Any, *, field_name: str) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f'{field_name} must be a number greater than 0 and at most 1') from exc
        if isinstance(value, bool) or parsed <= 0 or parsed > 1:
            raise ValueError(f'{field_name} must be a number greater than 0 and at most 1')
        return parsed

    @staticmethod
    def _parse_optional_positive_int(value: Any, *, field_name: str) -> Optional[int]:
        if value is None or (isinstance(value, str) and value.strip() == ''):
            return None
        if isinstance(value, bool):
            raise ValueError(f'{field_name} must be a positive integer')
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f'{field_name} must be a positive integer') from exc
        if parsed <= 0 or str(value).strip() != str(parsed):
            raise ValueError(f'{field_name} must be a positive integer')
        return parsed

    def print_config(self) -> None:
        """Print current configuration for debugging."""
        print("=" * 50)
        print("EVALUATOR CONFIGURATION")
        print("=" * 50)
        print(f"Service:       {self.host}:{self.port}")
        print(f"GT File:       {self.gt_file_path}")
        print(f"Reports Dir:   {self.reports_dir}")
        print(f"Redis:         {self.redis_host}:{self.redis_port}/{self.redis_db}")
        print(f"Alert Bridge:  {self.alert_bridge_url}{self.alert_bridge_endpoint_path}")
        print(f"AB Incident API:{self.alert_bridge_url}{self.alert_bridge_incident_endpoint_path}")
        print(f"AB Auto Seed:  {self.auto_seed_ab} (health_timeout={self.ab_health_timeout}s, concurrency={self.seed_ab_concurrency})")
        print(f"Media:         {self.media_base_url} (ext={self.media_file_ext}, type={self.media_type})")
        if self.vst_base_url:
            print(
                f"VST Media:     enabled base={self.vst_base_url} "
                f"window={self.vst_media_window_seconds}s "
                f"timeout={self.vst_media_timeout}s "
                f"request_attempts={self.vst_request_attempts} "
                f"request_backoff={self.vst_request_backoff_seconds}s "
                f"expiry={self.vst_media_expiry_minutes}m "
                f"disable_audio={self.vst_media_disable_audio} "
                f"ready_timeout={self.vst_sensor_ready_timeout_seconds}s "
                f"ready_poll={self.vst_sensor_ready_poll_seconds}s "
                f"require_timelines={self.vst_require_timelines}"
            )
        else:
            print("VST Media:     disabled")
        print(f"Elasticsearch: {self.elasticsearch_url}")
        print(f"ES Index (incidents): {self.elasticsearch_index_incidents}")
        print(f"ES Index (alerts):    {self.elasticsearch_index_alerts}")
        print(f"Data Source:   {self.data_source}")
        if self.data_source == "prometheus":
            print(
                f"Prometheus:    {self.prometheus_url} "
                f"(host={self.prometheus_host}, port={self.prometheus_port}, timeout={self.prometheus_timeout}s, "
                f"summary_buffer={self.prometheus_summary_buffer_seconds}s)"
            )
            print(f"Metric Profile:{self.prometheus_metric_profile}")
        print(f"Poll Timeout:  {self.result_poll_timeout}s")
        print(f"Eval Workflow: {self.eval_workflow}")
        print(f"Eval Mode:     {self.eval_mode}")
        print(f"Latency Duration: {self.latency_duration}")
        print(f"Synthetic:     {self.synthetic_alerts_injection}")
        print(f"Synthetic Submit Mode: {self.synthetic_alert_submission_mode}")
        print(f"Synthetic Alerts/min: {self.synthetic_alerts_per_minute or ''}")
        print(f"Synthetic Event Duration ms: {self.synthetic_alert_event_duration_ms or ''}")
        print(f"Synthetic E2E Min Coverage: {self.synthetic_e2e_min_coverage_ratio}")
        print(f"Default Category: {self.default_incident_category or '(unset)'}")
        print(f"Setup IndexedAt: {self.setup_indexed_at}")
        print(f"Max Stream Concurrency: {self.max_stream_concurrency} (0=auto)")
        print(f"Kafka:         {self.kafka_bootstrap_servers or '(disabled)'}")
        print(f"Kafka Incident Topic: {self.kafka_incident_topic}")
        print(f"Kafka Alert Topic: {self.kafka_alert_topic}")
        print(f"Kafka VLM Topic: {self.kafka_vlm_topic}")
        print(f"Log Level:     {self.log_level}")
        print("=" * 50)


class RedisClient:
    """
    Redis client wrapper with connection management and fallback support.
    """

    def __init__(self, settings: Settings):
        self._settings = settings
        self._client: Optional[Any] = None
        self._available: bool = False
        self._jobs: dict = {}

        self._connect()

    def _connect(self) -> None:
        """Attempt to connect to Redis server"""
        if redis is None:
            print("⚠ redis package not installed")
            print("⚠ Falling back to in-memory storage (not recommended for production)")
            self._available = False
            self._client = None
            return
        try:
            self._client = redis.from_url(
                self._settings.redis_url,
                decode_responses=True
            )
            self._client.ping()
            print(f"✓ Connected to Redis at {self._settings.redis_host}:{self._settings.redis_port}")
            self._available = True
        except Exception as e:
            print(f"⚠ Redis connection failed: {e}")
            print("⚠ Falling back to in-memory storage (not recommended for production)")
            self._available = False

    @property
    def client(self) -> Optional[Any]:
        """Get Redis client instance"""
        return self._client if self._available else None

    @property
    def is_available(self) -> bool:
        return self._available

    @property
    def fallback_storage(self) -> dict:
        return self._jobs

    def ping(self) -> bool:
        if not self._available or not self._client:
            return False
        try:
            self._client.ping()
            return True
        except Exception:
            return False
