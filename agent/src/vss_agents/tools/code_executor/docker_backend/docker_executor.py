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
import builtins
import contextlib
from datetime import datetime
import io
import logging
import os
import tarfile
import time

import docker
from vss_agents.tools.code_executor.docker_backend.image_builder import ImageBuilder

logger = logging.getLogger(__name__)


class DockerExecutor:
    def __init__(self, gpu: bool = False):
        self.client = docker.from_env()
        self.gpu = gpu
        self.builder = ImageBuilder()

    def _pack_files(self, files: dict[str, str | bytes]) -> bytes:
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w") as tar:
            created_dirs = set()

            for path, content in files.items():
                # Normalize path
                path = path.lstrip("/")

                # Create parent directories
                dir_path = os.path.dirname(path)
                if dir_path and dir_path not in created_dirs:
                    parts = dir_path.split("/")
                    for i in range(1, len(parts) + 1):
                        parent = "/".join(parts[:i])
                        if parent not in created_dirs:
                            dir_info = tarfile.TarInfo(name=parent)
                            dir_info.type = tarfile.DIRTYPE
                            dir_info.mode = 0o755
                            dir_info.mtime = int(time.time())
                            tar.addfile(dir_info)
                            created_dirs.add(parent)

                # Handle both string and bytes content
                if isinstance(content, str):
                    file_data = content.encode("utf-8")
                    # Make scripts executable if they look like scripts
                    mode = 0o755 if content.startswith("#!") else 0o644
                else:
                    file_data = content
                    mode = 0o644

                # Add the file
                info = tarfile.TarInfo(name=path)
                info.size = len(file_data)
                info.mtime = int(time.time())
                info.mode = mode
                info.uid = 1000
                info.gid = 1000
                tar.addfile(info, io.BytesIO(file_data))

        data.seek(0)
        return data.getvalue()

    def run_code(
        self,
        code: str,
        files: dict[str, str] | None = None,
        image: str = "python",
        cmd: list[str] | None = None,
        debug: bool = False,
        timeout_sec: int = 10,
        cpu_limit: float = 1.0,  # 1 vCPU
        mem_limit: str = "1g",
        network: bool = False,
    ) -> dict[str, str | int]:
        image_tag = self.builder.get_image_tag(image)
        workdir = f"/job-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        # Default command to run python code
        if cmd is None:
            if debug:
                cmd = [
                    "bash",
                    "-c",
                    f"echo '=== Initial {workdir} contents ===' && ls -la {workdir} && "
                    f"echo '=== Running Python ===' && python {workdir}/main.py && "
                    f"echo '=== Final {workdir} contents ===' && ls -la {workdir}",
                ]
            else:
                cmd = ["bash", "-lc", f"python {workdir}/main.py"]

        # Write code into /work
        all_files: dict[str, str | bytes] = {"main.py": code, **(files or {})}
        tar_stream = self._pack_files(all_files)

        # Device requests for GPU
        device_requests = None
        if self.gpu:
            device_requests = [docker.types.DeviceRequest(count=-1, capabilities=[["gpu"]])]

        container = self.client.containers.create(
            image=image_tag,
            command=cmd,
            working_dir=workdir,
            stdin_open=False,
            tty=False,
            detach=True,
            # Isolation knobs
            network_disabled=not network,
            mem_limit=mem_limit,
            nano_cpus=int(cpu_limit * 1e9),  # e.g., 1.0 -> 1 core
            pids_limit=128,
            tmpfs={"/tmp": "size=1G", "/home": "size=1G"},
            security_opt=[
                "no-new-privileges:true",
            ],
            cap_drop=["ALL"],
            device_requests=device_requests,
            user="1000:1000",  # non-root; ensure image has this uid or add it
        )

        try:
            # Put files into the container
            container.put_archive(workdir, tar_stream)

            # Start & wait with timeout
            container.start()
            exit_code = container.wait(timeout=timeout_sec).get("StatusCode", 124)

            # Gather output
            stdout = container.logs(stdout=True, stderr=False).decode("utf-8", "replace")
            stderr = container.logs(stdout=False, stderr=True).decode("utf-8", "replace")

            return {"exit_code": exit_code, "stdout": stdout, "stderr": stderr}
        except Exception as e:
            # Attempt to stop if it is still running
            with contextlib.suppress(builtins.BaseException):
                container.kill()
            return {"exit_code": 124, "stdout": "", "stderr": f"{type(e).__name__}: {e}"}
        finally:
            with contextlib.suppress(builtins.BaseException):
                container.remove(force=True)

    def build_image(self, image: str, base_image: str, language_packages: list[str] | None = None) -> str:
        image_tag = self.builder.build_image(image, base_image, language_packages=language_packages)
        return image_tag


if __name__ == "__main__":
    executor = DockerExecutor()
    executor.build_image("python", "python:3.10-slim", language_packages=["numpy"])
    output = executor.run_code("print('hi')", debug=True)
    print("exit_code", output["exit_code"])
    print("stdout", output["stdout"])
    print("stderr", output["stderr"])
