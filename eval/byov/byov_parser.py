######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
######################################################################################################

import yaml


class ByovParser:
    def __init__(self, config_file):
        print("===PARSING CONFIG FILE...===")
        self.config_file = config_file
        self.config = self.parse_config()
        print("===CONFIG FILE PARSED===")

    def parse_config(self):
        try:
            with open(self.config_file, "r") as file:
                data = yaml.safe_load(file)
                try:
                    self.videos = data["videos"]
                except Exception as e:
                    print(f"Error parsing videos: {e}")
                    return e
        except Exception as e:
            print(f"Error parsing config file: {e}")
            return e

        try:
            self.llm_judge = data["LLM_Judge_Model"]
        except Exception as e:
            print(f"Error parsing LLM_Judge_Model: {e}")
            return e
        try:
            self.vss_configs = data["VSS_Configurations"]
            return data  # Return the parsed data
        except Exception as e:
            print(f"Error parsing VSS_Configurations: {e}")
            return e

    def get_llm_judge(self):
        return self.llm_judge

    def get_videos(self):
        videos = []
        for video in self.videos:
            videos.append(video)
        return videos

    def get_vss_configs(self):
        return self.vss_configs
