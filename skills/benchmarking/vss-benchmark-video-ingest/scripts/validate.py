# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Validate inputs, CLI connectivity, deployed version, and corpus before upload.

Run standalone to check a configuration without executing anything::

    python scripts/validate.py --vss-repo "$VSS_REPO_ROOT" \
      --elasticsearch-url "$ELASTICSEARCH_URL" \
      --corpus ./ingest-corpus --profile standard

Exit codes: ``0`` valid, ``2`` invalid inputs, ``1`` indeterminate version,
``3`` incompatible version.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
import sys
from typing import Callable

from check_compatibility import add_arguments as add_compatibility_arguments
from check_compatibility import check_compatibility
from check_compatibility import CompatibilityError
from check_compatibility import default_repo
from config import DEFAULT_CONFIG_PATH
from config import DEFAULT_RESULTS_DIR
from config import ConfigError
from config import apply_lists
from config import drop_profile_owned_lists
from config import load_config
from config import profile_given_on_cli
from config import resolve_defaults
from corpus import PREDEFINED_VIDEO_CLASSES
from corpus import CorpusError
from corpus import class_dir
from corpus import collect_video_paths
from corpus import content_type_for
from corpus import ffprobe_available
from corpus import probe_video
from corpus import USER_CLASS_NAME
from corpus import validate_class_name
from httpio import auth_configured
from httpio import parse_endpoint
from httpio import redact_url
from vss_cli import VssCli

#: Benchmark profiles for client-generated concurrency.
PROFILES: dict[str, dict] = {
    "smoke": {
        "classes": ["50MB"],
        "concurrencies": [1],
        "purpose": "Validate endpoints, one upload, Elasticsearch readiness, and charts",
    },
    "standard": {
        "classes": ["50MB", "500MB"],
        "concurrencies": [1, 5, 10, 20],
        "purpose": "Produce a throughput curve under normal load",
    },
    "stress": {
        "classes": ["2GB"],
        "concurrencies": [20],
        "purpose": "Find client-observed degradation, rejections, and timeouts",
    },
    "custom": {
        "classes": [],
        "concurrencies": [],
        "purpose": "User-provided corpus and matrix",
    },
}

#: Sweep dimensions a Phase 1 run may vary. All are client-side.
ALLOWED_SWEEP_DIMENSIONS = frozenset(
    {
        "concurrency",
        "video_class",
        "corpus_subset",
        "limit",
        "es_request_timeout",
        "readiness_timeout",
        "warmup",
        "cleanup",
        "repeat",
        "stagger_sec",
        "max_ramp_sec",
    }
)

#: Each of these mutates the system under test and changes what the
#: measurement means. This benchmark never redeploys between points.
DISALLOWED_SWEEP_DIMENSIONS = {
    "rt_set": "RTVI replica counts change the deployment shape",
    "rt_sets": "RTVI replica counts change the deployment shape",
    "ba_set": "behavior analytics cadence changes the deployment shape",
    "ba_sets": "behavior analytics cadence changes the deployment shape",
    "ba_enabled": "behavior analytics cadence changes the deployment shape",
    "wan_profile": "network shaping runs tc in a cluster sidecar",
    "wan_profiles": "network shaping runs tc in a cluster sidecar",
    "storage_mode": "corpus storage mode needs a cluster-mounted volume",
    "storage_modes": "corpus storage mode needs a cluster-mounted volume",
    "storage_class": "storage class is infrastructure sizing",
    "replicas": "replica counts require Helm or Kubernetes access",
    "stream_limit": "per-pod stream limits are a server-side cap",
    "batch_size": "per-pod batch limits are a server-side cap",
    "muxer_limit": "per-pod muxer limits are a server-side cap",
    "nim_model": "NIM model selection changes the deployment",
    "gpu_profile": "GPU profile selection changes the deployment",
    "es_heap": "search index heap is infrastructure sizing",
    "es_shards": "search index shards are infrastructure sizing",
    "shards": "search index shards are infrastructure sizing",
    "consumer_replicas": "queue consumer replicas are infrastructure sizing",
    "retention_policy": "retention policy is infrastructure sizing",
    "namespace": "namespace targeting implies Kubernetes access",
    "helm_release": "Helm release targeting implies Helm access",
    "kubeconfig": "kubeconfig implies Kubernetes access",
    "prometheus_url": "Prometheus is cluster telemetry, out of scope in Phase 1",
    "kafka_url": "reading the queue directly is out of scope in Phase 1",
}


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    cli: VssCli | None = None
    deployment: dict = field(default_factory=dict)
    elasticsearch_url: str = ""
    version_compatibility: dict = field(default_factory=dict)
    measurements: dict[Path, dict] = field(default_factory=dict)
    error_exit_code: int = 2

    @property
    def ok(self) -> bool:
        return not self.errors

    def report(self) -> str:
        lines = []
        for note in self.notes:
            lines.append(f"  ok      {note}")
        for warning in self.warnings:
            lines.append(f"  WARN    {warning}")
        for error in self.errors:
            lines.append(f"  ERROR   {error}")
        return "\n".join(lines)


#: Bounds a corpus clip has to sit inside for the numbers built on it to answer
#: the question the user asked. Outside them the sweep still runs and still
#: prints a throughput figure -- which is exactly why they are checked here.
#:
#: fps and duration are not cosmetic: throughput is counted in video-minutes and
#: the readiness check derives expected frames from duration x fps, so a clip
#: that misreports either produces a confident number about nothing.
_FPS_BOUNDS = (1.0, 121.0)
_MIN_DURATION_SEC = 10.0
_MAX_DURATION_SEC = 4 * 60 * 60
_MAX_CONCURRENCY = 200
#: What the VSS decode path is known to handle. Anything else may attach and
#: then emit no detections at all.
_KNOWN_CODECS = {"h264", "hevc", "h265"}
#: Nominal bytes behind each predefined class label, used only to catch a class
#: folder whose contents do not match its own name.
_CLASS_NOMINAL_BYTES = {
    "50MB": 50_000_000,
    "500MB": 500_000_000,
    "2GB": 2_000_000_000,
    "10GB": 10_000_000_000,
}


def probe_class(video_class: str, files: list[Path], result: "ValidationResult") -> None:
    """Measure every file in one class and record what the numbers will mean.

    ``validate.py`` used to confirm only that ffprobe existed and that the folder
    held at least one ``.mp4``. That accepts a corpus of zero-length files, of a
    codec the decoder cannot open, or of synthetic clips containing nothing to
    detect -- each of which uploads with HTTP 200 and then never confirms,
    because no objects means no ``mdx-raw`` documents. The failure surfaces
    hours later as ``unconfirmed``, with no way to tell it apart from a
    deployment fault. Measuring first turns that into a message before any bytes
    move.
    """
    specs: list[dict] = []
    for path in files:
        try:
            content_type_for(path)
        except CorpusError as exc:
            result.errors.append(str(exc))
            continue
        try:
            size = path.stat().st_size
        except OSError as exc:
            result.errors.append(f"{path} cannot be read: {exc}")
            continue
        if size == 0:
            result.errors.append(f"{path} is zero bytes")
            continue
        try:
            spec = probe_video(path)
            result.measurements[path.resolve()] = dict(spec)
        except CorpusError as exc:
            result.errors.append(str(exc))
            continue
        spec["path"] = path
        spec["bytes"] = size
        specs.append(spec)

        if not (_FPS_BOUNDS[0] <= spec["fps"] < _FPS_BOUNDS[1]):
            result.errors.append(
                f"{path.name} reports {spec['fps']} fps, outside "
                f"{_FPS_BOUNDS[0]}-{_FPS_BOUNDS[1]}. Frame rate scales the expected frame "
                "count, so the readiness check cannot be trusted against it."
            )
        if spec["duration_sec"] < _MIN_DURATION_SEC:
            result.errors.append(
                f"{path.name} is {spec['duration_sec']:.1f}s. Below ~{_MIN_DURATION_SEC:.0f}s "
                "the fixed per-upload overhead dominates and the throughput figure "
                "describes the handshake rather than the ingest path."
            )
        elif spec["duration_sec"] > _MAX_DURATION_SEC:
            result.errors.append(
                f"{path.name} is {spec['duration_sec'] / 3600:.1f}h. The maximum supported "
                f"benchmark clip duration is {_MAX_DURATION_SEC / 3600:g}h; longer clips "
                "inflate transfer volume and completion time enough to make the sweep "
                "impractical. Split or shorten the clip first."
            )
        if spec["codec"] and spec["codec"].lower() not in _KNOWN_CODECS:
            result.warnings.append(
                f"{path.name} is {spec['codec']}, not one of {', '.join(sorted(_KNOWN_CODECS))}. "
                "Confirm the deployment decodes it; an unsupported codec attaches and then "
                "produces no detections, which reads as an ingest failure."
            )
        if not (spec["width"] and spec["height"]):
            result.errors.append(f"{path.name} reports no resolution")

    if not specs:
        return

    # --- class-level coherence ------------------------------------------
    # video-minutes only aggregate across clips that cost the same to process.
    for label, key in (("codec", "codec"), ("resolution", "resolution")):
        distinct = sorted({str(sp[key]) for sp in specs if sp[key]})
        if len(distinct) > 1:
            result.warnings.append(
                f"class {video_class!r} mixes {label}s ({', '.join(distinct)}). One "
                "video-minute is not one unit of work across them, so the class average "
                "hides the difference instead of measuring it."
            )
    fps_values = sorted({round(sp["fps"], 1) for sp in specs})
    if len(fps_values) > 1:
        result.warnings.append(
            f"class {video_class!r} mixes frame rates ({', '.join(str(v) for v in fps_values)}); "
            "a video-minute carries a different frame count per file"
        )

    nominal = _CLASS_NOMINAL_BYTES.get(video_class)
    if nominal:
        largest = max(sp["bytes"] for sp in specs)
        if largest < nominal / 4 or largest > nominal * 4:
            result.warnings.append(
                f"class {video_class!r} holds files of ~{largest / 1_000_000:.3g} MB, which "
                f"does not match the ~{nominal / 1_000_000:.3g} MB its name claims. Charts "
                "and comparisons key off the class label, so the axis would be wrong."
            )

    fingerprints = {(sp["bytes"], round(sp["duration_sec"], 1)) for sp in specs}
    if len(specs) > 1 and len(fingerprints) == 1:
        result.warnings.append(
            f"class {video_class!r} holds {len(specs)} files with identical size and duration "
            "-- probably one clip copied. That is valid for a load test and invalid for a "
            "content-diversity claim; say which one this run is."
        )

    total_min = sum(sp["duration_sec"] for sp in specs) / 60.0
    total_gb = sum(sp["bytes"] for sp in specs) / 1_000_000_000
    sample = specs[0]
    result.notes.append(
        f"{video_class}: {len(specs)} file(s), {total_gb:.3f} GB, {total_min:.1f} video-min, "
        f"{sample['codec']} {sample['resolution']} @ {sample['fps']:g}fps"
    )


def resolve_matrix(
    profile: str,
    classes: list[str] | None,
    concurrencies: list[int] | None,
    user_videos: list[str] | None = None,
    user_class: str = USER_CLASS_NAME,
) -> tuple[list[str], list[int]]:
    """Return (classes, concurrencies) for the selected profile.

    Explicit ``--video-class`` / ``--concurrency`` always override the profile
    shape; ``custom`` requires both.

    ``--video`` contributes a class of its own. On its own it *replaces* the
    profile's classes -- someone who names their own footage wants that footage
    benchmarked, not that footage plus a 500MB class they never asked for.
    Combined with an explicit ``--video-class`` it is added as one more series,
    which is how you compare your own clip against a predefined size.
    """
    spec = PROFILES.get(profile)
    if spec is None:
        raise ValueError(f"unknown profile {profile!r}; valid values are {', '.join(PROFILES)}")
    resolved_classes = list(classes) if classes else list(spec["classes"])
    if user_videos:
        if not classes:
            resolved_classes = [user_class]
        elif user_class not in resolved_classes:
            resolved_classes = resolved_classes + [user_class]
    resolved_concurrencies = list(concurrencies) if concurrencies else list(spec["concurrencies"])
    if not resolved_classes or not resolved_concurrencies:
        raise ValueError(f"profile {profile!r} needs an explicit --video-class and --concurrency")
    return resolved_classes, resolved_concurrencies


def check_overrides(overrides: dict[str, str]) -> tuple[list[str], list[str]]:
    """Split user ``--set key=value`` overrides into (errors, accepted keys)."""
    errors: list[str] = []
    accepted: list[str] = []
    for key in overrides:
        normalized = key.strip().lower().replace("-", "_")
        if normalized in DISALLOWED_SWEEP_DIMENSIONS:
            errors.append(
                f"disallowed sweep dimension {key!r}: {DISALLOWED_SWEEP_DIMENSIONS[normalized]}. "
                "Phase 1 benchmarks the deployed endpoint; it does not tune it."
            )
        elif normalized not in ALLOWED_SWEEP_DIMENSIONS:
            errors.append(
                f"unrecognized sweep dimension {key!r}; allowed dimensions are "
                f"{', '.join(sorted(ALLOWED_SWEEP_DIMENSIONS))}"
            )
        else:
            errors.append(
                f"--set {key}=... is not applied by this runner; use the explicit benchmark "
                "flags instead (for example --concurrency, --video-class, --limit, --warmup, or --cleanup)."
            )
    return errors, accepted


def validate(
    *,
    vss_repo: Path,
    cli_config_home: Path | None,
    elasticsearch_url: str,
    corpus_root: Path,
    profile: str,
    classes: list[str],
    concurrencies: list[int],
    readiness_poll_interval_sec: float,
    readiness_timeout_sec: float | None,
    es_request_timeout_sec: float,
    results_dir: Path,
    overrides: dict[str, str] | None = None,
    check_health: bool = True,
    user_videos: list[str] | None = None,
    user_class: str = "",
    cli_executable: str | None = None,
    version_url: str = "",
    version_timeout_sec: float = 10.0,
    progress: Callable[[str], None] | None = None,
) -> ValidationResult:
    """Validate inputs and deployed version before measuring any corpus file."""
    result = ValidationResult()
    overrides = overrides or {}
    user_videos = list(user_videos or [])
    # Classes whose files come from --video, not from a corpus class folder.
    # They are checked against the paths the user named instead of the layout.
    ad_hoc_classes = {user_class} if (user_videos and user_class) else set()

    # Read the configured deployment; never construct a VIOS URL or invoke Agent.
    try:
        result.cli = VssCli(vss_repo, cli_config_home, executable=cli_executable)
        result.deployment = result.cli.deployment(check_health=check_health)
        services = result.deployment.get("services", {})
        es_service = services.get("elasticsearch", {})
        elasticsearch_url = elasticsearch_url or (es_service.get("url", "") if isinstance(es_service, dict) else "")
        result.elasticsearch_url = elasticsearch_url
        result.notes.append(f"CLI configured against {redact_url(result.deployment.get('base_url', ''))}")
    except (OSError, ValueError) as exc:
        result.errors.append(str(exc))

    # --- auth (presence only; the value is never read into an artifact) -
    result.notes.append(
        "VSS_AUTH_TOKEN is set for ES reads only (value not printed)"
        if auth_configured()
        else "VSS_AUTH_TOKEN is not set for ES reads; CLI authentication is independent"
    )

    # --- profile and matrix --------------------------------------------
    if profile not in PROFILES:
        result.errors.append(f"unknown profile {profile!r}; valid values are {', '.join(PROFILES)}")
    else:
        result.notes.append(f"profile {profile!r}: {PROFILES[profile]['purpose']}")

    for video_class in classes:
        try:
            validate_class_name(video_class)
        except CorpusError as exc:
            result.errors.append(str(exc))
            continue
        if video_class not in PREDEFINED_VIDEO_CLASSES and video_class not in ad_hoc_classes:
            # Allowed, but called out: a custom class carries no known size, so
            # the timeout floor below is derived from the files rather than
            # looked up, and the run is not one of the predefined shapes.
            result.notes.append(
                f"video class {video_class!r} is user-defined (not one of "
                f"{', '.join(PREDEFINED_VIDEO_CLASSES)}); its size is taken from the files"
            )
    if len(set(concurrencies)) != len(concurrencies):
        result.errors.append(
            f"concurrency values must be unique, got {concurrencies}; duplicate points "
            "repeat the same load and can overwrite or double-count artifacts"
        )
    for concurrency in concurrencies:
        if concurrency < 1:
            result.errors.append(f"concurrency must be >= 1, got {concurrency}")
        elif concurrency > _MAX_CONCURRENCY:
            result.errors.append(
                f"concurrency must be <= {_MAX_CONCURRENCY}, got {concurrency}. "
                "Larger client fan-out is outside this benchmark's safety bound."
            )

    # --- sweep dimensions ----------------------------------------------
    override_errors, accepted = check_overrides(overrides)
    result.errors.extend(override_errors)
    if accepted:
        result.notes.append(f"allowed sweep overrides: {', '.join(sorted(set(accepted)))}")

    corpus_files: dict[str, list[Path]] = {}

    # --- user-supplied videos -------------------------------------------
    # ``--video`` is checked against the paths themselves; there is no class
    # folder to look in, and no corpus root is required for it.
    user_video_bytes = 0
    if user_videos:
        try:
            files = collect_video_paths(user_videos)
            user_video_bytes = sum(f.stat().st_size for f in files)
            result.notes.append(
                f"{user_class}: {len(files)} user-provided file(s), "
                f"{user_video_bytes / 1_000_000_000:.3f} GB, from --video"
            )
            corpus_files[user_class] = files
        except CorpusError as exc:
            result.errors.append(str(exc))

    # --- corpus ---------------------------------------------------------
    folder_classes = [c for c in classes if c not in ad_hoc_classes]
    if not folder_classes:
        # Every class in this run came from --video, so the corpus root is not
        # consulted at all and its absence is not an error.
        result.notes.append("corpus root not used: every class was supplied with --video")
    elif not corpus_root.is_dir():
        result.errors.append(f"corpus directory not found: {corpus_root}")
    else:
        for video_class in folder_classes:
            directory = class_dir(corpus_root, video_class)
            if not directory.is_dir():
                result.errors.append(f"video class folder not found: {directory}")
                continue
            files = [p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in {".mp4", ".mkv"}]
            if not files:
                result.errors.append(f"{directory} contains no .mp4 or .mkv files")
            else:
                corpus_files[video_class] = sorted(files)
        license_path = corpus_root / "LICENSE"
        if not license_path.exists():
            result.warnings.append(
                f"no LICENSE next to the corpus at {corpus_root}; every Phase 1 dataset "
                "must carry a license describing source and usability rules"
            )

    # Do not probe footage or proceed on an unknown/incompatible deployment.
    # --no-health-check skips the VIOS read only, never this compulsory gate.
    if result.errors:
        return result
    if progress is not None:
        progress("[2/8] Deployed VSS version compatibility check")
    try:
        result.version_compatibility = check_compatibility(
            result.deployment, version_url=version_url, timeout_sec=version_timeout_sec, vss_repo=vss_repo,
        )
        result.notes.append(
            f"Version compatible: VSS {result.version_compatibility['deployed_vss_version']} "
            f"with skill {result.version_compatibility['skill_version']} "
            f"(requires-vss: {result.version_compatibility['requires_vss']})"
        )
    except CompatibilityError as exc:
        result.errors.append(str(exc))
        result.error_exit_code = exc.exit_code
        return result
    except (OSError, ValueError) as exc:
        result.errors.append(str(exc))
        return result

    if progress is not None:
        progress("[3/8] Measuring the corpus and projecting transfer volume")

    if not ffprobe_available():
        result.errors.append(
            "ffprobe is not available. Throughput is counted in video-minutes, so every "
            "video must be measured before the sweep starts; a guessed length corrupts "
            "every number in the run. Without it none of the per-file characteristic "
            "checks above ran either, so the corpus is entirely unverified."
        )
    else:
        for video_class, files in corpus_files.items():
            probe_class(video_class, files, result)
        result.notes.append("ffprobe is available; corpus measurement completed")

    # --- Elasticsearch readiness ---------------------------------------
    if not elasticsearch_url:
        result.errors.append("--elasticsearch-url is required")
    else:
        try:
            parse_endpoint(elasticsearch_url, label="--elasticsearch-url")
            result.notes.append(f"Elasticsearch readiness endpoint parses: {redact_url(elasticsearch_url)}")
        except ValueError as exc:
            result.errors.append(str(exc))
    if readiness_poll_interval_sec <= 0:
        result.errors.append(f"--readiness-poll-interval must be > 0, got {readiness_poll_interval_sec:g}")
    if readiness_timeout_sec is not None and readiness_timeout_sec <= 0:
        result.errors.append(f"--readiness-timeout must be > 0 when set, got {readiness_timeout_sec:g}")

    if es_request_timeout_sec <= 0:
        result.errors.append("--es-request-timeout must be positive")

    # --- output ----------------------------------------------------------
    try:
        results_dir.mkdir(parents=True, exist_ok=True)
        probe = results_dir / ".write-probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        result.notes.append(f"results directory is writable: {results_dir}")
    except OSError as exc:
        result.errors.append(f"results directory is not writable: {results_dir} ({exc})")

    return result


def _parse_set(values: list[str] | None) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for item in values or []:
        key, _, value = item.partition("=")
        overrides[key.strip()] = value.strip()
    return overrides


# The subset of config-backed dests this entry point actually defines. Config
# keys outside it (upload ramp, cleanup policy, es tuning) belong to run.py and
# are ignored here rather than dumped onto the namespace as dead attributes.
_VALIDATE_DESTS = frozenset(
    {
        "vss_repo",
        "cli_config_home",
        "cli_executable",
        "version_url",
        "version_timeout",
        "elasticsearch_url",
        "readiness_poll_interval",
        "readiness_timeout",
        "corpus",
        "profile",
        "video_class_name",
        "es_request_timeout",
        "results_dir",
    }
)


def main(argv: list[str] | None = None) -> int:
    # Same config precedence as run.py: flag > config.yml > built-in default.
    # Validating against a different set of defaults than the run would use
    # would make the check worthless, so both entry points read the same file.
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", type=Path, default=None)
    pre.add_argument("--no-config", action="store_true")
    known, _ = pre.parse_known_args(argv)
    try:
        if known.no_config:
            config_scalars: dict = {}
            config_lists: dict = {}
        else:
            config_path = known.config or DEFAULT_CONFIG_PATH
            flat = load_config(config_path, explicit=known.config is not None)
            config_scalars, config_lists = resolve_defaults(flat, config_path.parent)
    except ConfigError as exc:
        print(f"  ERROR   {exc}")
        return 2

    parser = argparse.ArgumentParser(description="Validate a VSS ingest benchmark configuration.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--no-config", action="store_true")
    parser.add_argument(
        "--vss-repo",
        type=Path,
        default=default_repo(),
    )
    parser.add_argument(
        "--cli-config-home",
        type=Path,
        default=None,
        help="CLI config directory; otherwise use VSS_CONFIG_HOME or ~/.vss.",
    )
    parser.add_argument("--cli-executable", default=None, help="Installed vss executable; defaults to PATH.")
    add_compatibility_arguments(parser)
    parser.add_argument("--elasticsearch-url", default="")
    parser.add_argument("--readiness-poll-interval", type=float, default=5.0)
    parser.add_argument("--readiness-timeout", type=float, default=None)
    parser.add_argument("--corpus", type=Path, default=None)
    parser.add_argument("--profile", default="smoke", choices=sorted(PROFILES))
    parser.add_argument("--video-class", action="append", dest="classes")
    parser.add_argument(
        "--video",
        action="append",
        dest="videos",
        help="Benchmark your own video: a file or a directory of them. Repeatable. "
        "Needs no corpus root and no size-class folder.",
    )
    parser.add_argument(
        "--video-class-name",
        default=USER_CLASS_NAME,
        help="Label for the --video files in the CSVs and charts.",
    )
    parser.add_argument("--concurrency", action="append", type=int, dest="concurrencies")
    parser.add_argument("--es-request-timeout", type=float, default=60.0)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument("--set", action="append", dest="overrides")
    parser.add_argument("--no-health-check", action="store_true")
    parser.set_defaults(**{k: v for k, v in config_scalars.items() if k in _VALIDATE_DESTS})
    args = parser.parse_args(argv)
    if profile_given_on_cli(argv):
        drop_profile_owned_lists(config_lists, args.profile)
    apply_lists(args, config_lists)

    try:
        classes, concurrencies = resolve_matrix(
            args.profile,
            args.classes,
            args.concurrencies,
            user_videos=args.videos,
            user_class=args.video_class_name,
        )
    except ValueError as exc:
        print(f"  ERROR   {exc}")
        return 2

    if args.corpus is None and not args.videos:
        print("  ERROR   --corpus is required unless every class is supplied with --video")
        return 2

    result = validate(
        vss_repo=args.vss_repo,
        cli_config_home=args.cli_config_home,
        cli_executable=args.cli_executable,
        version_url=args.version_url,
        version_timeout_sec=args.version_timeout,
        elasticsearch_url=args.elasticsearch_url,
        corpus_root=args.corpus or Path("."),
        profile=args.profile,
        classes=classes,
        concurrencies=concurrencies,
        readiness_poll_interval_sec=args.readiness_poll_interval,
        readiness_timeout_sec=args.readiness_timeout,
        es_request_timeout_sec=args.es_request_timeout,
        results_dir=args.results_dir,
        overrides=_parse_set(args.overrides),
        check_health=not args.no_health_check,
        user_videos=args.videos,
        user_class=args.video_class_name,
    )
    print(f"Validating VSS ingest benchmark configuration (profile={args.profile})")
    print(result.report())
    print("VALID" if result.ok else "INVALID")
    return 0 if result.ok else result.error_exit_code


if __name__ == "__main__":
    sys.exit(main())
