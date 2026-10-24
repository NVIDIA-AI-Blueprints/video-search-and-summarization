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

from unittest.mock import MagicMock, Mock, patch

import pytest
from nemo_platform import APIStatusError

from src.backends.nmp.dms_client import DMSClient

# Global sleep mocking to speed up tests
pytestmark = [pytest.mark.usefixtures("mock_sleep_globally")]


@pytest.fixture(autouse=True)
def mock_sleep_globally():
    """Mock time.sleep globally to speed up tests."""
    with patch("time.sleep"), patch("src.backends.nmp.dms_client.time.sleep"):
        yield


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_dms_config_payload(extra: dict | None = None) -> dict:
    """Build a payload matching what NIMConfig.to_dms_config() returns under NMP 26.03.

    The DMSClient source reads ``workspace``, ``name``, ``description`` and
    ``nim_deployment`` from this payload.
    """
    payload = {
        "name": "test-deployment",
        "workspace": "test-workspace",
        "description": "Test deployment",
        "nim_deployment": {"image_name": "nvcr.io/test", "image_tag": "1.0"},
    }
    if extra:
        payload.update(extra)
    return payload


def _make_customized_payload(extra: dict | None = None) -> dict:
    """Build a payload matching what NIMConfig.to_customized_dms_config() returns."""
    payload = {
        "name": "test-deployment",
        "workspace": "test-workspace",
        "description": "Test deployment",
        "nim_deployment": {"image_name": "nvcr.io/test-custom", "image_tag": "1.0"},
    }
    if extra:
        payload.update(extra)
    return payload


def _make_api_error(status_code: int, message: str = "error") -> APIStatusError:
    """Construct an APIStatusError with a usable status_code attribute."""
    mock_response = Mock()
    mock_response.status_code = status_code
    mock_response.json.return_value = {"message": message}
    err = APIStatusError(
        message=f"Error code: {status_code} - {message}",
        response=mock_response,
        body=None,
    )
    # APIStatusError stores status_code from the response, but be defensive
    err.status_code = status_code
    return err


def _build_dms_client(mock_nim_config: Mock) -> tuple[DMSClient, MagicMock]:
    """Construct a DMSClient with NeMoPlatform patched and settings.workspace populated.

    Returns the client and the underlying mock NeMoPlatform instance.
    """
    mock_nemo_client = MagicMock()
    with (
        patch("src.backends.nmp.dms_client.NeMoPlatform", return_value=mock_nemo_client),
        patch("src.backends.nmp.dms_client.settings") as mock_settings,
    ):
        mock_settings.system_config.nmp_config.workspace = "test-workspace"
        mock_settings.system_config.nmp_config.nemo_base_url = "http://nemo"
        mock_settings.system_config.nmp_config.nim_base_url = "http://nim"
        client = DMSClient(nim=mock_nim_config)
    # Replace the nemo_client just in case the patched object went out of scope.
    client.nemo_client = mock_nemo_client
    # Pin a known workspace for downstream assertions.
    client.workspace = "test-workspace"
    return client, mock_nemo_client


@pytest.fixture
def mock_nim_config():
    """Shared fixture: a NIMConfig mock with sensible default payloads."""
    mock_config = Mock()
    mock_config.to_dms_config.return_value = _make_dms_config_payload()
    mock_config.to_customized_dms_config.return_value = _make_customized_payload()
    mock_config.nmp_model_name.return_value = "test-model-name"
    mock_config.target_model_for_evaluation.return_value = "test-target-model"
    return mock_config


@pytest.fixture
def dms_client(mock_nim_config):
    """Shared fixture: returns a DMSClient with mocked NeMoPlatform."""
    client, _ = _build_dms_client(mock_nim_config)
    return client


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------


class TestDMSClient:
    """Test class for DMSClient initialization and basic functionality."""

    def test_init(self, mock_nim_config):
        """Test DMSClient initialization."""
        with (
            patch("src.backends.nmp.dms_client.NeMoPlatform") as mock_nemo_class,
            patch("src.backends.nmp.dms_client.settings") as mock_settings,
        ):
            mock_settings.system_config.nmp_config.workspace = "test-workspace"
            mock_settings.system_config.nmp_config.nemo_base_url = "http://nemo"
            mock_settings.system_config.nmp_config.nim_base_url = "http://nim"
            mock_nemo_client = Mock()
            mock_nemo_class.return_value = mock_nemo_client

            client = DMSClient(nim=mock_nim_config)

            assert client.nim == mock_nim_config
            assert client.nemo_client == mock_nemo_client
            assert client.workspace == "test-workspace"
            mock_nemo_class.assert_called_once()


class TestDMSClientModelDeployment:
    """Test class for model deployment functionality."""

    def test_deploy_model_success(self, dms_client, mock_nim_config):
        """Test successful model deployment via the NMP 26.03 inference API."""
        # deployment_configs.create succeeds with no-op return (we don't inspect it)
        dms_client.nemo_client.inference.deployment_configs.create.return_value = MagicMock()

        mock_deployment = Mock()
        mock_deployment.name = "test-deployment"
        mock_deployment.model_dump.return_value = {
            "deployment_id": "test-deployment-id",
            "status": "PENDING",
        }
        dms_client.nemo_client.inference.deployments.create.return_value = mock_deployment

        result = dms_client.deploy_model()

        # The source first creates the deployment_config, then creates the deployment.
        dms_client.nemo_client.inference.deployments.create.assert_called_once_with(
            workspace="test-workspace",
            name="test-deployment",
            config="test-deployment",
        )
        assert result == {"deployment_id": "test-deployment-id", "status": "PENDING"}

    def test_deploy_model_already_exists(self, dms_client):
        """Test model deployment when deployment already exists (500 + 'already exists')."""
        # deployment_configs.create can be a no-op
        dms_client.nemo_client.inference.deployment_configs.create.return_value = MagicMock()
        # deployments.create raises an "already exists" 500
        api_error = _make_api_error(500, "model deployment already exists")
        dms_client.nemo_client.inference.deployments.create.side_effect = api_error

        result = dms_client.deploy_model()
        assert result is None

    def test_deploy_model_other_error(self, dms_client):
        """Test model deployment with a generic (non-API) exception."""
        dms_client.nemo_client.inference.deployment_configs.create.return_value = MagicMock()
        dms_client.nemo_client.inference.deployments.create.side_effect = Exception(
            "Some other error"
        )

        with pytest.raises(Exception, match="Some other error"):
            dms_client.deploy_model()


class TestDMSClientDeploymentStatus:
    """Test class for deployment status checking functionality."""

    def test_does_deployment_exist_true(self, dms_client):
        """Test deployment existence check when deployment exists."""
        mock_deployment = Mock()
        dms_client.nemo_client.inference.deployments.retrieve.return_value = mock_deployment

        result = dms_client.does_deployment_exist()
        assert result is True

    def test_does_deployment_exist_false(self, dms_client):
        """Test deployment existence check when deployment doesn't exist."""
        dms_client.nemo_client.inference.deployments.retrieve.side_effect = Exception("Not found")

        result = dms_client.does_deployment_exist()
        assert result is False

    def test_get_deployment_status_success(self, dms_client):
        """Status comes from deployment.status, translated via _LEGACY_STATUS_MAP."""
        mock_deployment = Mock()
        mock_deployment.status = "READY"  # NMP 26.03 uppercase status
        dms_client.nemo_client.inference.deployments.retrieve.return_value = mock_deployment

        result = dms_client.get_deployment_status()
        # READY -> "ready" via _LEGACY_STATUS_MAP
        assert result == "ready"

    def test_get_deployment_status_error(self, dms_client):
        """Test deployment status retrieval with error."""
        dms_client.nemo_client.inference.deployments.retrieve.side_effect = Exception("Not found")

        with pytest.raises(Exception, match="Not found"):
            dms_client.get_deployment_status()

    def test_is_deployed_true(self, dms_client):
        """Test is_deployed when model is deployed."""
        with patch.object(dms_client, "get_deployment_status", return_value="ready"):
            result = dms_client.is_deployed()
            assert result is True

    def test_is_deployed_false(self, dms_client):
        """Test is_deployed when model is not deployed."""
        with patch.object(dms_client, "get_deployment_status", return_value="pending"):
            result = dms_client.is_deployed()
            assert result is False

    def test_is_deployed_exception(self, dms_client):
        """Test is_deployed when exception occurs."""
        with patch.object(
            dms_client, "get_deployment_status", side_effect=Exception("Network error")
        ):
            result = dms_client.is_deployed()
            assert result is False


class TestDMSClientWaitForDeployment:
    """Test class for wait_for_deployment functionality."""

    def test_wait_for_deployment_success(self, dms_client):
        """Test successful deployment wait."""
        with (
            patch("src.backends.nmp.dms_client.check_cancellation") as mock_check_cancellation,
            patch.object(dms_client, "is_model_available", return_value=False),
            patch.object(
                dms_client, "get_deployment_status", return_value="ready"
            ) as mock_get_status,
        ):
            mock_callback = Mock()

            dms_client.wait_for_deployment(
                flywheel_run_id="test-run-id", progress_callback=mock_callback, timeout=10
            )

            mock_check_cancellation.assert_called_with("test-run-id")
            mock_get_status.assert_called()
            mock_callback.assert_called_with({"status": "ready"})

    def test_wait_for_deployment_progress_updates(self, dms_client):
        """Test deployment wait with progress updates."""
        status_sequence = ["pending", "pending", "ready"]

        with (
            patch("src.backends.nmp.dms_client.check_cancellation"),
            patch.object(dms_client, "is_model_available", return_value=False),
            patch.object(dms_client, "get_deployment_status", side_effect=status_sequence),
        ):
            mock_callback = Mock()

            dms_client.wait_for_deployment(
                flywheel_run_id="test-run-id", progress_callback=mock_callback, timeout=10
            )

            # Source emits a callback every loop iteration AND an extra one when status==ready.
            assert mock_callback.call_count == 4

    def test_wait_for_deployment_no_callback(self, dms_client):
        """Test deployment wait without progress callback."""
        with (
            patch("src.backends.nmp.dms_client.check_cancellation"),
            patch.object(dms_client, "is_model_available", return_value=False),
            patch.object(dms_client, "get_deployment_status", return_value="ready"),
        ):
            dms_client.wait_for_deployment(flywheel_run_id="test-run-id", timeout=10)

    def test_wait_for_deployment_cancellation(self, dms_client):
        """Test deployment wait with cancellation."""
        with (
            patch(
                "src.backends.nmp.dms_client.check_cancellation", side_effect=Exception("Cancelled")
            ),
            patch.object(dms_client, "is_model_available", return_value=False),
            patch.object(dms_client, "get_deployment_status", return_value="pending"),
        ):
            with pytest.raises(Exception, match="Cancelled"):
                dms_client.wait_for_deployment(flywheel_run_id="test-run-id", timeout=10)

    def test_wait_for_deployment_timeout(self, dms_client):
        """Test deployment wait timeout."""
        # First time() call is start_time=0, subsequent calls return >timeout to break loop
        time_values = [0] * 10 + [3700] * 10

        with (
            patch("src.backends.nmp.dms_client.check_cancellation"),
            patch.object(dms_client, "is_model_available", return_value=False),
            patch.object(dms_client, "get_deployment_status", return_value="pending"),
            patch("src.backends.nmp.dms_client.time.time", side_effect=time_values),
            patch("src.backends.nmp.dms_client.logger.info"),
        ):
            mock_callback = Mock()

            with pytest.raises(
                TimeoutError, match="Deployment did not complete within 3600 seconds"
            ):
                dms_client.wait_for_deployment(
                    flywheel_run_id="test-run-id", progress_callback=mock_callback, timeout=3600
                )

            mock_callback.assert_called_with(
                {"status": "pending", "error": "Deployment did not complete within 3600 seconds"}
            )

    def test_wait_for_deployment_model_already_available(self, dms_client):
        """Test deployment wait returns early when target model is already available."""
        with (
            patch.object(dms_client, "is_model_available", return_value=True) as mock_is_available,
            patch.object(dms_client, "get_deployment_status") as mock_get_status,
        ):
            mock_callback = Mock()

            dms_client.wait_for_deployment(
                flywheel_run_id="test-run-id", progress_callback=mock_callback, timeout=10
            )

            mock_is_available.assert_called_once()
            mock_get_status.assert_not_called()
            mock_callback.assert_called_once_with({"status": "ready"})


class TestDMSClientWaitForModelSync:
    """Test class for wait_for_model_sync functionality."""

    def test_wait_for_model_sync_success(self, dms_client):
        """Test successful model sync wait."""
        mock_models = Mock()
        mock_models.data = [Mock(id="other-model"), Mock(id="test-model"), Mock(id="another-model")]

        dms_client.nemo_client.inference.models.list.return_value = mock_models

        with patch("src.backends.nmp.dms_client.check_cancellation"):
            result = dms_client.wait_for_model_sync(
                "test-model", "test-run-id", check_interval=1, timeout=10
            )

            dms_client.nemo_client.inference.models.list.assert_called()
            assert result == {"status": "synced", "model_id": "test-model"}

    def test_wait_for_model_sync_multiple_checks(self, dms_client):
        """Test model sync wait with multiple checks."""
        mock_models_1 = Mock()
        mock_models_1.data = [Mock(id="other-model")]

        mock_models_2 = Mock()
        mock_models_2.data = [Mock(id="test-model")]

        dms_client.nemo_client.inference.models.list.side_effect = [mock_models_1, mock_models_2]

        with patch("src.backends.nmp.dms_client.check_cancellation"):
            result = dms_client.wait_for_model_sync(
                "test-model", "test-run-id", check_interval=1, timeout=10
            )

            assert result == {"status": "synced", "model_id": "test-model"}

    def test_wait_for_model_sync_cancellation(self, dms_client):
        """Test model sync wait with cancellation."""
        with patch(
            "src.backends.nmp.dms_client.check_cancellation", side_effect=Exception("Cancelled")
        ):
            with pytest.raises(Exception, match="Cancelled"):
                dms_client.wait_for_model_sync(
                    "test-model", "test-run-id", check_interval=1, timeout=10
                )

    def test_wait_for_model_sync_api_error(self, dms_client):
        """Test model sync wait with API error."""
        dms_client.nemo_client.inference.models.list.side_effect = Exception("API Error")

        with patch("src.backends.nmp.dms_client.check_cancellation"):
            with pytest.raises(Exception, match="API Error"):
                dms_client.wait_for_model_sync(
                    "test-model", "test-run-id", check_interval=1, timeout=10
                )

    def test_wait_for_model_sync_timeout(self, dms_client):
        """Test model sync wait timeout."""
        mock_models = Mock()
        mock_models.data = [Mock(id="other-model")]

        dms_client.nemo_client.inference.models.list.return_value = mock_models

        time_values = [0] * 10 + [15] * 10

        with (
            patch("src.backends.nmp.dms_client.check_cancellation"),
            patch("src.backends.nmp.dms_client.time.time", side_effect=time_values),
            patch("src.backends.nmp.dms_client.logger.error"),
        ):
            with pytest.raises(
                TimeoutError, match="Model test-model did not sync within 10 second"
            ):
                dms_client.wait_for_model_sync(
                    "test-model", "test-run-id", check_interval=1, timeout=10
                )

    def test_wait_for_model_sync_empty_data(self, dms_client):
        """Test model sync wait with empty data response.

        Source uses ``getattr(models, "data", None) or list(models)`` — when
        ``data`` is an empty list (falsy), the fallback iterates the response.
        We provide a MagicMock whose iteration yields nothing.
        """
        mock_models = MagicMock()
        mock_models.data = []
        mock_models.__iter__.return_value = iter([])

        dms_client.nemo_client.inference.models.list.return_value = mock_models

        time_values = [0] * 10 + [15] * 10

        with (
            patch("src.backends.nmp.dms_client.check_cancellation"),
            patch("src.backends.nmp.dms_client.time.time", side_effect=time_values),
            patch("src.backends.nmp.dms_client.logger.error"),
        ):
            with pytest.raises(TimeoutError):
                dms_client.wait_for_model_sync(
                    "test-model", "test-run-id", check_interval=1, timeout=10
                )


class TestDMSClientShutdown:
    """Test class for deployment shutdown functionality."""

    def test_shutdown_deployment_success(self, dms_client):
        """Test successful deployment shutdown."""
        mock_result = Mock()
        mock_result.model_dump.return_value = {
            "status": "shutdown",
            "message": "Deployment stopped",
        }

        dms_client.nemo_client.inference.deployments.delete.return_value = mock_result

        result = dms_client.shutdown_deployment()

        # NMP 26.03 delete signature: name=, workspace=
        dms_client.nemo_client.inference.deployments.delete.assert_called_once_with(
            name="test-model-name", workspace="test-workspace"
        )
        assert result == {"status": "shutdown", "message": "Deployment stopped"}

    def test_shutdown_deployment_no_model_dump(self, dms_client):
        """Test deployment shutdown when result has no model_dump method."""
        mock_result = Mock(spec=[])  # No attributes, including model_dump

        dms_client.nemo_client.inference.deployments.delete.return_value = mock_result

        result = dms_client.shutdown_deployment()
        assert result == {"status": "deleted"}

    def test_shutdown_deployment_error(self, dms_client):
        """Test deployment shutdown with generic error."""
        dms_client.nemo_client.inference.deployments.delete.side_effect = Exception("Not found")

        with pytest.raises(Exception, match="Not found"):
            dms_client.shutdown_deployment()


class TestDMSClientDeploymentConfiguration:
    """Test deployment config creation and model deployment with various configurations."""

    def test_create_deployment_config_with_base_model(self, dms_client):
        """Test creating deployment config with base_model_for_image."""
        mock_config = Mock()
        mock_config.name = "test-config"
        mock_config.model_dump.return_value = {"name": "test-config"}

        dms_client.nemo_client.inference.deployment_configs.create.return_value = mock_config

        result = dms_client.create_deployment_config(base_model_for_image="base-model")

        assert result["name"] == "test-config"

    def test_create_deployment_config_already_exists(self, dms_client):
        """Test creating deployment config when it already exists (500 + 'already exists')."""
        api_error = _make_api_error(500, "already exists")
        dms_client.nemo_client.inference.deployment_configs.create.side_effect = api_error

        result = dms_client.create_deployment_config()
        assert result is None

    def test_create_deployment_config_other_error(self, dms_client):
        """Test creating deployment config with other error (500 without 'already exists')."""
        api_error = _make_api_error(500, "some other error")
        dms_client.nemo_client.inference.deployment_configs.create.side_effect = api_error

        with pytest.raises(APIStatusError):
            dms_client.create_deployment_config()

    def test_deploy_model_with_hf_token(self, dms_client, mock_nim_config):
        """Test deploying a model whose payload includes ``hf_token``.

        Under NMP 26.03 the DMSClient.deploy_model() does not forward an
        explicit ``hf_token`` kwarg to ``inference.deployments.create``.  The
        token, if present in the payload, is expected to flow through the
        deployment_config (``nim_deployment``).  We assert that the deployment
        still succeeds end-to-end and that the deployment_configs.create() call
        was made with our payload.
        """
        mock_nim_config.to_dms_config.return_value = _make_dms_config_payload(
            {"hf_token": "hf_test_token"}
        )

        dms_client.nemo_client.inference.deployment_configs.create.return_value = MagicMock()

        mock_deployment = Mock()
        mock_deployment.name = "test-deployment"
        mock_deployment.model_dump.return_value = {"name": "test-deployment"}
        dms_client.nemo_client.inference.deployments.create.return_value = mock_deployment

        result = dms_client.deploy_model()

        assert result == {"name": "test-deployment"}
        # deployment_configs.create() should have been called with our payload (no exception raised).
        dms_client.nemo_client.inference.deployment_configs.create.assert_called_once()

    def test_deploy_model_already_deployed_variations(self, dms_client):
        """Test deploy_model handles various 'already deployed' messages."""
        test_messages = [
            "model deployment already exists",
            "already deployed",
            "model already deployed",
        ]

        for msg in test_messages:
            dms_client.nemo_client.reset_mock()
            dms_client.nemo_client.inference.deployment_configs.create.return_value = MagicMock()
            api_error = _make_api_error(500, msg)
            # Source checks message via str(e.message); we set the message text accordingly.
            api_error.message = msg
            dms_client.nemo_client.inference.deployments.create.side_effect = api_error

            result = dms_client.deploy_model()
            assert result is None, f"Expected None for message: {msg}"

    def test_shutdown_deployment_already_deleted(self, dms_client):
        """Test shutdown when deployment is already deleted (404)."""
        api_error = _make_api_error(404, "model deployment not found")
        api_error.message = "model deployment not found"
        dms_client.nemo_client.inference.deployments.delete.side_effect = api_error

        result = dms_client.shutdown_deployment()
        assert result["status"] == "already_deleted"

    def test_shutdown_deployment_api_error(self, dms_client):
        """Test shutdown deployment with API error other than 404."""
        api_error = _make_api_error(500, "Some other error")
        api_error.message = "Some other error"
        dms_client.nemo_client.inference.deployments.delete.side_effect = api_error

        with pytest.raises(APIStatusError):
            dms_client.shutdown_deployment()


class TestDMSClientCustomConfigAndExceptions:
    """Test DMS client with custom configurations and exception handling."""

    def test_create_deployment_config_generic_exception(self, dms_client):
        """Test create_deployment_config handles generic exceptions."""
        dms_client.nemo_client.inference.deployment_configs.create.side_effect = Exception(
            "Network error"
        )

        with pytest.raises(Exception, match="Network error"):
            dms_client.create_deployment_config()

    def test_deploy_model_with_custom_config(self, dms_client):
        """Test deploy_model with custom config parameter."""
        mock_deployment = Mock()
        mock_deployment.name = "custom-config"
        mock_deployment.model_dump.return_value = {"name": "custom-config"}

        dms_client.nemo_client.inference.deployments.create.return_value = mock_deployment

        custom_config = {
            "name": "custom-config",
            "namespace": "test-namespace",
            "gpus": 4,
        }
        result = dms_client.deploy_model(config=custom_config)

        assert result == {"name": "custom-config"}
        # With a custom config, the source skips deployment_configs.create and goes
        # straight to deployments.create with workspace+name+config.
        call_args = dms_client.nemo_client.inference.deployments.create.call_args[1]
        assert call_args["name"] == "custom-config"
        assert call_args["config"] == "custom-config"
        assert call_args["workspace"] == "test-workspace"

    def test_deploy_model_generic_exception(self, dms_client):
        """Test deploy_model handles generic exceptions."""
        dms_client.nemo_client.inference.deployment_configs.create.return_value = MagicMock()
        dms_client.nemo_client.inference.deployments.create.side_effect = Exception(
            "Deployment error"
        )

        with pytest.raises(Exception, match="Deployment error"):
            dms_client.deploy_model()


class TestDMSClientExceptionHandling:
    """Test exception handling in DMSClient methods."""

    def test_create_deployment_config_api_error_propagation(self, dms_client):
        """Test create_deployment_config propagates non-500 API errors correctly."""
        api_error = _make_api_error(400, "Bad Request")
        dms_client.nemo_client.inference.deployment_configs.create.side_effect = api_error

        with pytest.raises(APIStatusError):
            dms_client.create_deployment_config()

    def test_deploy_model_api_error_other_5xx(self, dms_client):
        """Test deploy_model handles non-500/409 5xx API errors correctly.

        Under NMP 26.03 the source only swallows 409 and 500 'already exists'
        responses; a 503 with no 'already exists' marker should propagate.
        """
        dms_client.nemo_client.inference.deployment_configs.create.return_value = MagicMock()
        api_error = _make_api_error(503, "Service Unavailable")
        api_error.message = "Service Unavailable"
        dms_client.nemo_client.inference.deployments.create.side_effect = api_error

        with pytest.raises(APIStatusError):
            dms_client.deploy_model()

    def test_deploy_model_with_hf_token_in_config(self, dms_client):
        """Test deploy_model succeeds when payload contains an ``hf_token`` value.

        Same caveat as test_deploy_model_with_hf_token: NMP 26.03's
        inference.deployments.create signature does not include ``hf_token``;
        the token flows through nim_deployment instead.  We assert the
        deployment returns the mocked payload and that deployments.create was
        invoked.
        """
        mock_deployment = Mock()
        mock_deployment.name = "test-deployment"
        mock_deployment.model_dump.return_value = {"name": "test-deployment"}
        dms_client.nemo_client.inference.deployment_configs.create.return_value = MagicMock()
        dms_client.nemo_client.inference.deployments.create.return_value = mock_deployment

        dms_client.nim.to_dms_config.return_value = _make_dms_config_payload(
            {"hf_token": "secret-token-123"}
        )

        result = dms_client.deploy_model()

        assert result == {"name": "test-deployment"}
        dms_client.nemo_client.inference.deployments.create.assert_called_once()
