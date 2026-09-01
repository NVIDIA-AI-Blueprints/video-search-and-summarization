# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Patch FlashInfer CUDA runtime discovery to avoid TileLang's libcudart stub.

vLLM's non-eager compile path imports flashinfer.comm. If TileLang has already
loaded libcudart_stub.so, FlashInfer's map scan can bind to that stub instead
of the real CUDA runtime and then fail on symbols such as cudaDeviceReset.
"""

from __future__ import annotations

import site
import sys
import sysconfig
from pathlib import Path

PATCH_MARKER = "RTVI_FLASHINFER_CUDART_PATCH"


OLD_IMPORT = "import ctypes\n"
NEW_IMPORT = "import ctypes\nimport glob\nimport os\n"

OLD_FUNCTION = '''def find_loaded_library(lib_name) -> Optional[str]:
    """
    According to according to https://man7.org/linux/man-pages/man5/proc_pid_maps.5.html,
    the file `/proc/self/maps` contains the memory maps of the process, which includes the
    shared libraries loaded by the process. We can use this file to find the path of the
    a loaded library.
    """  # noqa
    found = False
    with open("/proc/self/maps") as f:
        for line in f:
            if lib_name in line:
                found = True
                break
    if not found:
        # the library is not loaded in the current process
        return None
    # if lib_name is libcudart, we need to match a line with:
    # address /path/to/libcudart-hash.so.11.0
    start = line.index("/")
    path = line[start:].strip()
    filename = path.split("/")[-1]
    assert filename.rpartition(".so")[0].startswith(lib_name), (
        f"Unexpected filename: {filename} for library {lib_name}"
    )
    return path
'''

NEW_FUNCTION = f'''def _find_real_cudart_from_filesystem() -> Optional[str]:
    """Find a real CUDA runtime shared object, excluding stub libraries.

    {PATCH_MARKER}: TileLang ships libcudart_stub.so for build/runtime helpers.
    FlashInfer's CudaRTLibrary needs symbols that the stub does not provide.
    """
    candidates: List[str] = []
    for directory in os.environ.get("LD_LIBRARY_PATH", "").split(":"):
        if directory:
            candidates.extend(glob.glob(os.path.join(directory, "libcudart.so*")))
    candidates.extend(glob.glob("/usr/local/cuda*/targets/*/lib/libcudart.so*"))
    candidates.extend(glob.glob("/usr/local/cuda*/lib64/libcudart.so*"))
    candidates.extend(glob.glob("/usr/lib/*/libcudart.so*"))

    for path in candidates:
        filename = os.path.basename(path)
        if "stub" not in filename and os.path.exists(path):
            return path
    return None


def find_loaded_library(lib_name) -> Optional[str]:
    """
    According to https://man7.org/linux/man-pages/man5/proc_pid_maps.5.html,
    the file `/proc/self/maps` contains the memory maps of the process, which includes the
    shared libraries loaded by the process. We can use this file to find the path of
    a loaded library.
    """  # noqa
    with open("/proc/self/maps") as f:
        for line in f:
            if lib_name not in line or "/" not in line:
                continue
            start = line.index("/")
            path = line[start:].strip()
            filename = os.path.basename(path)
            if not filename.rpartition(".so")[0].startswith(lib_name):
                continue
            if lib_name == "libcudart" and (
                "stub" in filename or "/tilelang/" in path
            ):
                continue
            return path

    if lib_name == "libcudart":
        return _find_real_cudart_from_filesystem()
    return None
'''


def candidate_roots() -> list[Path]:
    roots: list[Path] = []
    for value in (
        sysconfig.get_paths().get("purelib"),
        sysconfig.get_paths().get("platlib"),
        *site.getsitepackages(),
        *sys.path,
        "/opt/nvidia/vllm-0.17.0",
    ):
        if value:
            root = Path(value)
            if root not in roots:
                roots.append(root)
    return roots


def patch_file(path: Path) -> bool:
    content = path.read_text()
    if PATCH_MARKER in content:
        print(f"  ✓ {path} already patched, skipping.")
        return True

    if OLD_FUNCTION not in content:
        print(f"  - {path} does not match expected FlashInfer cuda_ipc layout, skipping.")
        return False

    if NEW_IMPORT not in content:
        content = content.replace(OLD_IMPORT, NEW_IMPORT, 1)
    content = content.replace(OLD_FUNCTION, NEW_FUNCTION, 1)
    path.write_text(content)
    print(f"  ✓ patched FlashInfer CUDA runtime discovery in {path}")
    return True


def main() -> None:
    patched = 0
    seen: set[Path] = set()
    for root in candidate_roots():
        path = root / "flashinfer" / "comm" / "cuda_ipc.py"
        if path in seen or not path.exists():
            continue
        seen.add(path)
        if patch_file(path):
            patched += 1

    if patched == 0:
        raise RuntimeError("Could not find a compatible FlashInfer cuda_ipc.py to patch")
    print(f"Done. Patched {patched} FlashInfer cuda_ipc.py file(s).")


if __name__ == "__main__":
    main()
