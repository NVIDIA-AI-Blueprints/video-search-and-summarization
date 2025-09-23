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

.PHONY: up down byov logs shell

up:
	docker compose up -d graph-db

down:
	docker compose down

byov:
	docker compose run --rm -it byov

logs:
	docker compose logs -f

shell: up
	docker compose run --rm -it --entrypoint bash byov -c "pip install --no-cache-dir -r /opt/nvidia/via/via-engine/eval/byov/requirements_byov.txt && bash"
