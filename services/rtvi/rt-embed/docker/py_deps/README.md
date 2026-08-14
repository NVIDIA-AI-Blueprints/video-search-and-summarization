#Why use pdm?:
pdm is a package manager like poetry. We can maintain fixed versions
in pyproject/lock files for reproducible builds.
However, some of the packages we need have conflicting dependency versions.
pdm allows to resolve such conflicts by manually overriding versions while
poetry does not.

# Using the public PyTorch container (for example, when the host is Ubuntu 22.04):
```sh
export WORKSPACE_DIR=$(git rev-parse --show-toplevel)
docker run -it --rm --gpus 'device=0' -e WORKSPACE_DIR=$WORKSPACE_DIR -v $WORKSPACE_DIR:$WORKSPACE_DIR -w $WORKSPACE_DIR nvcr.io/nvidia/pytorch:26.03-py3
cd services/rtvi/rt-embed/docker/py_deps
```

# Install pdm using:
```sh
PDM_INSTALLER_URL=https://raw.githubusercontent.com/pdm-project/pdm/75156a09d7e710d8e10117c2c7c88e8ce5097e7d/install-pdm.py
PDM_INSTALLER_SHA256=e1c7f6455fa7ffc50cbc13e4d49c06dfaaf8e9b74d0c9b46287bf767f6a4e4fc
PDM_INSTALLER_PATH=/tmp/install-pdm.py
curl --fail --location --output "$PDM_INSTALLER_PATH" "$PDM_INSTALLER_URL" && \
  printf '%s  %s\n' "$PDM_INSTALLER_SHA256" "$PDM_INSTALLER_PATH" | sha256sum -c - && \
  python3 "$PDM_INSTALLER_PATH" && \
  rm -f "$PDM_INSTALLER_PATH"
export PATH=$HOME/.local/bin:$PATH
```

# Inside pytorch docker, venv is not required regarding torch dependencies.
# disable venv creation.
# this step is for container only
```sh
pdm config python.use_venv false
pdm config python.use_pyenv false
pdm use /usr/bin/python3.12

# Add or upgrade a package:
```sh
pdm add --update-reuse <pkg>==<ver>
```
OR manually edit pyproject.toml

# Package sources

RT Embed dependencies must resolve from public PyPI, `pypi.nvidia.com`, and
explicit public source archives only. The optional vLLM-compatible implementation remains in
the source tree but is not packaged in this image.

# Update lock file:
Run following commands:
```sh
pdm lock --update-reuse -G amd64
pdm lock --update-reuse -G arm64 --append --platform linux_aarch64
# Update the generated requirements file for source code CVE scanning.
pdm export --no-hashes --output requirements.txt
```


# Troubleshooting:
## Try to remove pdm.lock and retry the steps.
```sh
rm pdm.lock
```
