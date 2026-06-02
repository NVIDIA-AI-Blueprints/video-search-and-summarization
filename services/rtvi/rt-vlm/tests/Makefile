# SPDX-FileCopyrightText: Copyright (c) 2024-2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

.PHONY: rtvi_embed rtvi_vlm test-ci help

# Default target
help:
	@echo "Available targets:"
	@echo "  make rtvi_embed         - Run tests for rtvi_embed microservice"
	@echo "  make rtvi_vlm           - Run tests for rtvi_vlm microservice"
	@echo "  make test-ci       	 - Run only simple CI test (test_ci.py)"

# Run all pytest tests
rtvi_embed:
	python3 -m pytest -v -k rtvi_embed -p no:cacheprovider --ignore=byov --ignore=model --ignore rtvi_vlm
# Add other options here as necessary, currently ignoring unused test folders that need to be updated as well as rtvi_vlm tests which have different dependencies

# Run all pytest tests with verbose output and print statements
rtvi_vlm:
	echo "Skipping unit tests temporarily."
	# python3 rtvi_vlm/run_rtvi_tests.py --all
# Add other options here as necessary

# Run only simple CI test
test-ci:
	python3 -m pytest test_ci.py::test_nvidia_smi_runs -v -s --noconftest -p no:cacheprovider
