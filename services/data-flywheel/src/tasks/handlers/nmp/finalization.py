# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Handler for finalization tasks."""

import time
from datetime import datetime

from src.api.models import TaskResult
from src.tasks.base import BaseTaskHandler


class FinalizeFlywheelRunHandler(BaseTaskHandler):
    """Handler for finalizing the flywheel run."""

    def execute(self, previous_results: list[TaskResult] | TaskResult) -> TaskResult:
        """Finalize the Flywheel run by setting its finished_at timestamp.

        This is the final step of the workflow. It will mark the flywheel run as
        completed and update the flywheel run document with the finished_at timestamp.

        Args:
            previous_results: Either a single TaskResult or a list returned by a group

        Returns:
            Final TaskResult
        """
        previous_result: TaskResult | None = None

        try:
            previous_result = self.extract_previous_result(
                previous_results,
                validator=lambda r: bool(r.flywheel_run_id),
                error_msg="Could not determine flywheel_run_id when finalizing Flywheel run",
            )

            # Sleep for 1 minute to allow the deployment to be deleted
            # We need to sleep here to allow time for all deployment resources to be fully cleaned up.
            # We cannot poll the deployment status because the endpoint returns "not found" immediately
            # after the deletion is initiated, but the actual cleanup of k8s resources (pods, services, etc.)
            # takes longer. This sleep ensures those resources are fully removed before proceeding.
            time.sleep(60)

            # Mark the flywheel run as completed
            # This will update only if there is no error recorded on the flywheel run document
            self.db_manager.mark_flywheel_run_completed(
                previous_result.flywheel_run_id, datetime.utcnow()
            )

            self.logger.info(
                "Flywheel run %s marked as finished at %s",
                previous_result.flywheel_run_id,
                datetime.utcnow(),
            )
            return previous_result

        except Exception as e:
            error_msg = f"Error finalizing Flywheel run: {e!s}"
            self.logger.error(error_msg)

            if not previous_result:
                return TaskResult(error=error_msg)

            # Update existing previous_result with error to preserve all context
            previous_result.error = error_msg
            self.db_manager.mark_flywheel_run_error(
                previous_result.flywheel_run_id, error_msg, finished_at=datetime.utcnow()
            )

            # Sleep for cleanup
            time.sleep(60)

            # Return the enriched previous_result with all context preserved
            return previous_result
