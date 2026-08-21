# -*- mode: python ; coding: utf-8 -*-
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# PyInstaller spec for run_workloads.py (sdr-mw). Dependencies aligned with pyproject.toml + lib/*.
import os
import pathlib
from PyInstaller.utils.hooks import collect_all, collect_submodules

try:
    _REPO_ROOT = os.path.dirname(os.path.abspath(SPEC))
except NameError:
    _REPO_ROOT = os.getcwd()


def collect_lib_hiddenimports(repo_root: str):
    """Every lib.* name from lib/**/*.py and Cython *.so under lib/ (e.g. k8sclient.*.so)."""
    lib_dir = pathlib.Path(repo_root) / 'lib'
    names = []
    if not lib_dir.is_dir():
        return names

    for path in sorted(lib_dir.rglob('*.py')):
        if '__pycache__' in path.parts:
            continue
        rel = path.relative_to(lib_dir)
        parts = rel.with_suffix('').parts
        if parts[-1] == '__init__':
            if len(parts) == 1:
                names.append('lib')
            else:
                names.append('lib.' + '.'.join(parts[:-1]))
        else:
            names.append('lib.' + '.'.join(parts))

    for path in sorted(lib_dir.rglob('*.so')):
        if '__pycache__' in path.parts:
            continue
        stem = path.name.split('.', 1)[0]
        rel_parent = path.relative_to(lib_dir).parent
        if stem == '__init__':
            if rel_parent.parts:
                names.append('lib.' + '.'.join(rel_parent.parts))
            else:
                names.append('lib')
            continue
        if rel_parent.parts:
            names.append('lib.' + '.'.join(rel_parent.parts) + '.' + stem)
        else:
            names.append('lib.' + stem)

    return names


datas = [('config.py', '.'), ('templates', 'templates'), ('static', 'static')]
binaries = []
hiddenimports = [
    'backports.tarfile',
    'betterproto',
    'betterproto.grpc.grpclib_client',
    'betterproto.grpc.grpclib_server',
    'betterproto.lib.google.protobuf',
    # Lazy import in run_workloads._start_controller_background_watchers
    'lib.controller',
    # Stdlib; easy to miss when analyzing lib.logging.*
    'logging',
    'logging.handlers',
]


def merge_collect(package: str) -> None:
    global datas, binaries, hiddenimports
    ret = collect_all(package)
    datas += ret[0]
    binaries += ret[1]
    hiddenimports += ret[2]


# Third-party: import/top-level names matching pyproject.toml (and common transitive roots).
for _pkg in (
    'backports',
    'certifi',
    'charset_normalizer',
    'dns',
    'docker',
    'dotmap',
    'envoy_data_plane',
    'flask',
    'flask_kafka',
    'flask_swagger_ui',
    'google.protobuf',
    'grpc',
    'grpclib',
    'idna',
    'jinja2',
    'kafka',
    'kubernetes',
    'prometheus_client',
    'redis',
    'redis_lock',
    'requests',
    'simple_settings',
    'stringcase',
    'urllib3',
    'werkzeug',
    'yaml',
):
    merge_collect(_pkg)

# Namespace / large trees (collect_all on dotted names is unreliable for ruamel).
for _tree in ('opentelemetry', 'ruamel', 'lib'):
    try:
        hiddenimports += collect_submodules(_tree)
    except Exception as exc:
        print('warning: collect_submodules(%r) failed: %s' % (_tree, exc))

# lib/ has no top-level __init__.py; walk the tree so lib.controller and peers are bundled.
hiddenimports += collect_lib_hiddenimports(_REPO_ROOT)
hiddenimports = list(dict.fromkeys(hiddenimports))

a = Analysis(
    ['run_workloads.py'],
    pathex=[_REPO_ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='sdr-mw',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
