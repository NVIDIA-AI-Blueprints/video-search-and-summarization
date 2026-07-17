# Video-summarization unit tests

Public unit suite for `services/video-summarization`.

```bash
# From services/video-summarization (with src/ on PYTHONPATH)
mkdir -p "${VIA_LOG_DIR:-/tmp/via-logs}"
pytest tests/unit -m unit -vv
```

Functional, integration, and CI harness tests live in the internal
[ci-vss-oss](https://gitlab-master.nvidia.com/metromind/ci-vss-oss) tree under
`ci/lvs/tests/`.
