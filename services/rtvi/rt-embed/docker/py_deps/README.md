#Why use pdm?:
pdm is a package manager like poetry. We can maintain fixed versions
in pyproject/lock files for reproducible builds.
However, some of the packages we need have conflicting dependency versions.
pdm allows to resolve such conflicts by manually overriding versions while
poetry does not.

# using pytorch docker (In case host is Ubuntu 22.04):
```sh
export WORKSPACE_DIR=$(git rev-parse --show-toplevel)
docker run -it --rm --gpus 'device=0' -e WORKSPACE_DIR=$WORKSPACE_DIR -v $WORKSPACE_DIR:$WORKSPACE_DIR -v $WORKSPACE_DIR/docker/base/binaries:/tmp/binaries -w $WORKSPACE_DIR nvcr.io/nvidia/pytorch:25.01-py3
cd ./docker/base/py_deps
```

# Install pdm using:
```sh
curl https://raw.githubusercontent.com/pdm-project/pdm/75156a09d7e710d8e10117c2c7c88e8ce5097e7d/install-pdm.py | python3 -
export PATH=$HOME/.local/bin:$PATH
```

# Inside pytorch docker, venv is not required regarding torch dependencies.
# disable venv creation.
# this step is for container only
```sh
pdm config python.use_venv false
pdm config python.use_pyenv false
pdm use /usr/bin/python3.12

# Add / upgrade new package:
```sh
export NVIDIA_TENSORRT_DISABLE_INTERNAL_PIP=true
pdm add --update-reuse <pkg>==<ver>
```
OR manually edit pyproject.toml

# RT-VLM performance-sensitive vLLM notes:
RTVI VLM uses the current `vllm==0.11.1+9114fd76.nv25.12.cu131` pin with an
RTVI image-build patch for Qwen2.5-VL vision attention. The patch keeps Cosmos
Reason 2's head_dim=72 vision encoder on upstream FlashAttention instead of
falling back to TORCH_SDPA. Validate vLLM stack updates with the cache-disabled
H100 `max_live_streams_test_1_token` benchmark.

# Update lock file:
Remove following lines from pdm.lock:
```sh
[[metadata.targets]]
requires_python = "==3.12.*"
platform = "linux_aarch64"
```
Run following commands:
```sh
pdm lock --update-reuse -G amd64
pdm lock --update-reuse -G arm64 --append --platform linux_aarch64
# Update local requirements file for source code CVE scan
pdm export --no-hashes > requirements.txt
```


# Troubleshooting:
## Try to remove pdm.lock and retry the steps.
```sh
rm pdm.lock
```
