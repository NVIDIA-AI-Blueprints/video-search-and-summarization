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

import subprocess
import sys
import time
from statistics import mean


def get_gpu_info():
    try:
        output = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=name,persistence_mode,display_active,display_mode,fan.speed,temperature.gpu,power.draw,power.limit,memory.used,memory.total,utilization.gpu,utilization.memory,compute_mode",  # noqa: E501
                "--format=csv,noheader,nounits",
            ],
            universal_newlines=True,
        )
        gpu_info = output.strip().split("\n")
        return gpu_info
    except subprocess.CalledProcessError as e:
        print(f"Error: {e.output}")
        sys.exit(1)


def parse_gpu_info(gpu_info):
    gpu_data = []
    for gpu in gpu_info:
        gpu_fields = gpu.split(", ")
        gpu_dict = {
            "name": gpu_fields[0],
            "persistence_mode": gpu_fields[1],
            "display_active": gpu_fields[2],
            "display_mode": gpu_fields[3],
            "fan_speed": int(gpu_fields[4]),
            "temperature": int(gpu_fields[5]),
            # 'power_draw': int(gpu_fields[6]),
            # 'power_limit': int(gpu_fields[7]),
            "memory_used": int(gpu_fields[8]),
            "memory_total": int(gpu_fields[9]),
            "gpu_util": int(gpu_fields[10]),
            "memory_util": int(gpu_fields[11]),
            "compute_mode": gpu_fields[12],
        }
        gpu_data.append(gpu_dict)
    return gpu_data


def main():
    interval = int(input("Enter the sampling interval (in seconds): "))
    memory_util_list = []

    try:
        while True:
            gpu_info = get_gpu_info()
            gpu_data = parse_gpu_info(gpu_info)

            for gpu in gpu_data:
                if gpu["gpu_util"] == 100:
                    memory_util_list.append(gpu["memory_used"])

            print(f"GPU Utilization: {[gpu['gpu_util'] for gpu in gpu_data]}%")
            print(f"Memory Utilization: {[gpu['memory_used'] for gpu in gpu_data]} MiB")

            time.sleep(interval)

    except KeyboardInterrupt:
        if memory_util_list:
            avg_memory_util = mean(memory_util_list)
            print(
                "\nAverage Memory Utilization (when GPU Utilization was 100%):"
                f" {avg_memory_util:.2f} MiB"
            )
        else:
            print("\nNo instances of 100% GPU Utilization found.")


if __name__ == "__main__":
    main()
