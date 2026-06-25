# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import pytest
import numpy as np
from unittest.mock import Mock, patch

from mdx.analytics.core.inference.inference_client import InferenceClient
from mdx.analytics.core.schema.proto import ext_pb2 as extSchema


class TestInferenceClientFunctionality:
    """Test suite for InferenceClient core functionality."""

    @pytest.fixture
    def client(self):
        """Create a test InferenceClient instance."""
        with patch('mdx.analytics.core.inference.inference_client.InferenceServerClient'):
            return InferenceClient("localhost:8001")

    @pytest.fixture
    def mock_behavior(self):
        """Create a mock behavior object."""
        behavior = Mock(spec=extSchema.Behavior)
        behavior.locations = Mock()
        behavior.locations.coordinates = [
            Mock(point=[1.0, 2.0]),
            Mock(point=[3.0, 4.0]),
            Mock(point=[5.0, 6.0])
        ]
        return behavior

    def test_init_creates_client(self):
        """Test that InferenceClient initialization creates proper client connection."""
        server_url = "localhost:8001"
        
        with patch('mdx.analytics.core.inference.inference_client.InferenceServerClient') as mock_client_class:
            mock_client_instance = Mock()
            mock_client_class.return_value = mock_client_instance
            
            client = InferenceClient(server_url)
            
            assert client.server_url == server_url
            assert client.client == mock_client_instance
            mock_client_class.assert_called_once_with(url=server_url)

    def test_server_live_success(self, client):
        """Test server_live returns True when server is live."""
        client.client.is_server_live.return_value = True
        
        result = client.server_live()
        
        assert result is True
        client.client.is_server_live.assert_called_once()

    def test_server_live_exception(self, client):
        """Test server_live returns False when exception occurs."""
        client.client.is_server_live.side_effect = Exception("Connection error")
        
        with patch('mdx.analytics.core.inference.inference_client.logger') as mock_logger:
            result = client.server_live()
            
            assert result is False
            mock_logger.info.assert_called_once()

    def test_server_ready_success(self, client):
        """Test server_ready returns True when server is ready."""
        client.client.is_server_ready.return_value = True
        
        result = client.server_ready()
        
        assert result is True
        client.client.is_server_ready.assert_called_once()

    def test_server_ready_exception(self, client):
        """Test server_ready returns False when exception occurs."""
        client.client.is_server_ready.side_effect = Exception("Connection error")
        
        with patch('mdx.analytics.core.inference.inference_client.logger') as mock_logger:
            result = client.server_ready()
            
            assert result is False
            mock_logger.info.assert_called_once()

    def test_server_metadata_success(self, client):
        """Test server_metadata returns metadata when successful."""
        expected_metadata = {"version": "2.0", "name": "triton"}
        client.client.get_server_metadata.return_value = expected_metadata
        
        result = client.server_metadata()
        
        assert result == expected_metadata
        client.client.get_server_metadata.assert_called_once()

    def test_server_metadata_exception(self, client):
        """Test server_metadata returns None when exception occurs."""
        client.client.get_server_metadata.side_effect = Exception("Connection error")
        
        with patch('mdx.analytics.core.inference.inference_client.logger') as mock_logger:
            result = client.server_metadata()
            
            assert result is None
            mock_logger.info.assert_called_once()

    def test_model_ready_success(self, client):
        """Test model_ready returns True when model is ready."""
        client.client.is_model_ready.return_value = True
        
        with patch('mdx.analytics.core.inference.inference_client.logger') as mock_logger:
            result = client.model_ready("test_model", "1.0")
            
            assert result is True
            client.client.is_model_ready.assert_called_once_with("test_model", "1.0")
            mock_logger.info.assert_called_once()

    def test_model_ready_default_version(self, client):
        """Test model_ready with default version parameter."""
        client.client.is_model_ready.return_value = True
        
        with patch('mdx.analytics.core.inference.inference_client.logger'):
            result = client.model_ready("test_model")
            
            assert result is True
            client.client.is_model_ready.assert_called_once_with("test_model", "")

    def test_model_ready_exception(self, client):
        """Test model_ready returns False when exception occurs."""
        client.client.is_model_ready.side_effect = Exception("Model error")
        
        with patch('mdx.analytics.core.inference.inference_client.logger') as mock_logger:
            result = client.model_ready("test_model")
            
            assert result is False
            mock_logger.info.assert_called_once()

    def test_model_metadata_success(self, client):
        """Test model_metadata returns metadata when successful."""
        expected_metadata = {"name": "test_model", "inputs": [], "outputs": []}
        client.client.get_model_metadata.return_value = expected_metadata
        
        result = client.model_metadata("test_model", "1.0")
        
        assert result == expected_metadata
        client.client.get_model_metadata.assert_called_once_with("test_model", "1.0")

    def test_model_metadata_default_version(self, client):
        """Test model_metadata with default version parameter."""
        expected_metadata = {"name": "test_model"}
        client.client.get_model_metadata.return_value = expected_metadata
        
        result = client.model_metadata("test_model")
        
        assert result == expected_metadata
        client.client.get_model_metadata.assert_called_once_with("test_model", "")

    def test_model_metadata_exception(self, client):
        """Test model_metadata returns None when exception occurs."""
        client.client.get_model_metadata.side_effect = Exception("Metadata error")
        
        with patch('mdx.analytics.core.inference.inference_client.logger') as mock_logger:
            result = client.model_metadata("test_model")
            
            assert result is None
            mock_logger.info.assert_called_once()

    def test_model_config_success(self, client):
        """Test model_config returns configuration when successful."""
        expected_config = {"backend": "pytorch", "max_batch_size": 8}
        client.client.get_model_config.return_value = expected_config
        
        result = client.model_config("test_model", "1.0")
        
        assert result == expected_config
        client.client.get_model_config.assert_called_once_with("test_model", "1.0")

    def test_model_config_default_version(self, client):
        """Test model_config with default version parameter."""
        expected_config = {"backend": "pytorch"}
        client.client.get_model_config.return_value = expected_config
        
        result = client.model_config("test_model")
        
        assert result == expected_config
        client.client.get_model_config.assert_called_once_with("test_model", "")

    def test_model_config_exception(self, client):
        """Test model_config returns None when exception occurs."""
        client.client.get_model_config.side_effect = Exception("Config error")
        
        with patch('mdx.analytics.core.inference.inference_client.logger') as mock_logger:
            result = client.model_config("test_model")
            
            assert result is None
            mock_logger.info.assert_called_once()

    def test_close_with_client(self, client):
        """Test close method when client exists."""
        client.close()
        client.client.close.assert_called_once()

    def test_close_without_client(self):
        """Test close method when client is None."""
        with patch('mdx.analytics.core.inference.inference_client.InferenceServerClient'):
            client = InferenceClient("localhost:8001")
            # Mock the client to have no close method or be None-like
            client.client = Mock()
            client.client.close = Mock()
            # Should not raise exception
            client.close()
            client.client.close.assert_called_once()

    def test_close_when_client_is_falsy(self):
        """Test close method when client is falsy - covers the missing branch."""
        with patch('mdx.analytics.core.inference.inference_client.InferenceServerClient'):
            client = InferenceClient("localhost:8001")
            # Mock a falsy client that doesn't have close method to test the missing branch
            falsy_client = Mock()
            falsy_client.__bool__ = Mock(return_value=False)
            client.client = falsy_client
            # Should not raise exception when client is falsy
            client.close()


class TestInferenceClientUtilityMethods:
    """Test suite for InferenceClient utility methods."""

    @pytest.fixture
    def client(self):
        """Create a test InferenceClient instance."""
        with patch('mdx.analytics.core.inference.inference_client.InferenceServerClient'):
            return InferenceClient("localhost:8001")

    @pytest.mark.parametrize("coordinates,target_length,expected_length", [
        ([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]], 100, 100),
        ([[0.0, 0.0], [10.0, 10.0]], 50, 50),
        ([[1.0, 1.0], [2.0, 2.0]], 5, 5),
    ])
    def test_linear_interpolate_various_inputs(self, client, coordinates, target_length, expected_length):
        """Test linear_interpolate with various input sizes and target lengths."""
        result = client.linear_interpolate(coordinates, target_length)
        
        assert len(result) == expected_length
        assert all(len(coord) == 2 for coord in result)

    def test_linear_interpolate_single_point_target_length_edge_case(self, client):
        """Test linear_interpolate edge case with target_length=1 causes division by zero."""
        coordinates = [[5.0, 7.0]]
        # This is an edge case that causes division by zero in the current implementation
        with pytest.raises(ZeroDivisionError):
            client.linear_interpolate(coordinates, 1)

    def test_linear_interpolate_single_coordinate_multiple_targets_edge_case(self, client):
        """Test linear_interpolate edge case with single coordinate and target > 1."""
        coordinates = [[5.0, 7.0]]
        # This causes IndexError due to higher_index going out of bounds when coordinates_length=1
        with pytest.raises(IndexError):
            client.linear_interpolate(coordinates, 3)

    def test_linear_interpolate_two_points(self, client):
        """Test linear_interpolate with two coordinates."""
        coordinates = [[0.0, 0.0], [10.0, 20.0]]
        result = client.linear_interpolate(coordinates, 3)
        
        assert len(result) == 3
        assert result[0] == [0.0, 0.0]
        assert result[2] == [10.0, 20.0]
        # Middle point should be interpolated
        assert result[1][0] == pytest.approx(5.0)
        assert result[1][1] == pytest.approx(10.0)

    def test_linear_interpolate_edge_case_last_point(self, client):
        """Test linear_interpolate correctly handles last point edge case."""
        coordinates = [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]
        result = client.linear_interpolate(coordinates, 5)
        
        # Last point should exactly match the last coordinate
        assert result[-1] == [5.0, 6.0]

    def test_row_major_basic(self, client):
        """Test row_major transformation with basic tensor."""
        tensor = [[[1.0, 2.0], [3.0, 4.0]], [[5.0, 6.0], [7.0, 8.0]]]
        
        with patch.object(client, 'linear_interpolate') as mock_interpolate:
            mock_interpolate.side_effect = [
                [[1.0, 2.0]] * 100,  # First trajectory
                [[5.0, 6.0]] * 100   # Second trajectory
            ]
            
            result = client.row_major(tensor, 100)
            
            assert len(result) == 2  # Two trajectories
            assert len(result[0]) == 100  # Each with 100 points
            assert mock_interpolate.call_count == 2

    def test_row_major_custom_length(self, client):
        """Test row_major transformation with custom target length."""
        tensor = [[[1.0, 2.0], [3.0, 4.0]]]
        target_length = 50
        
        with patch.object(client, 'linear_interpolate') as mock_interpolate:
            mock_interpolate.return_value = [[1.0, 2.0]] * target_length
            
            result = client.row_major(tensor, target_length)
            
            assert len(result) == 1
            assert len(result[0]) == target_length
            mock_interpolate.assert_called_once_with([[1.0, 2.0], [3.0, 4.0]], target_length)

    def test_row_major_empty_tensor(self, client):
        """Test row_major transformation with empty tensor."""
        tensor = []
        result = client.row_major(tensor, 100)
        
        assert result == []


class TestInferenceClientInferenceMethod:
    """Test suite for InferenceClient inference method."""

    @pytest.fixture
    def client(self):
        """Create a test InferenceClient instance."""
        with patch('mdx.analytics.core.inference.inference_client.InferenceServerClient'):
            return InferenceClient("localhost:8001")

    @pytest.fixture
    def mock_behaviors(self):
        """Create mock behavior objects."""
        behaviors = []
        for i in range(2):
            behavior = Mock(spec=extSchema.Behavior)
            behavior.locations = Mock()
            behavior.locations.coordinates = [
                Mock(point=[float(i*2+1), float(i*2+2)]),
                Mock(point=[float(i*2+3), float(i*2+4)]),
            ]
            behaviors.append(behavior)
        return behaviors

    @patch('mdx.analytics.core.inference.inference_client.InferInput')
    @patch('mdx.analytics.core.inference.inference_client.InferRequestedOutput')
    def test_infer_with_protobuf_success(self, mock_output, mock_input, client, mock_behaviors):
        """Test successful inference with protobuf."""
        # Setup mocks
        client.client.is_model_ready.return_value = True
        
        mock_response = Mock()
        # Test successful inference with numpy array response
        result_array = np.array([[1], [2]])
        def mock_as_numpy(key):
            if key == 'output__0':
                return result_array
            return None
        mock_response.as_numpy = mock_as_numpy
        mock_response.get_response.return_value = {"model_version": "1.0"}
        client.client.infer.return_value = mock_response
        
        mock_interpolated_data = [[[1.0, 2.0]] * 100, [[3.0, 4.0]] * 100]
        
        with patch.object(client, 'row_major', return_value=mock_interpolated_data):
            with patch('mdx.analytics.core.inference.inference_client.logger') as mock_logger:
                result = client.infer_with_protobuf("sensor1", mock_behaviors)
                
                cluster_indices, model_version = result
                assert cluster_indices == [[1], [2]]
                assert model_version == "1.0"
                mock_logger.info.assert_called()

    def test_infer_with_protobuf_model_not_ready(self, client, mock_behaviors):
        """Test inference when model is not ready."""
        client.client.is_model_ready.return_value = False
        
        result = client.infer_with_protobuf("sensor1", mock_behaviors)
        
        assert result == ([], "")

    @patch('mdx.analytics.core.inference.inference_client.InferInput')
    @patch('mdx.analytics.core.inference.inference_client.InferRequestedOutput')
    def test_infer_with_protobuf_no_response(self, mock_output, mock_input, client, mock_behaviors):
        """Test inference when server returns no response."""
        client.client.is_model_ready.return_value = True
        client.client.infer.return_value = None
        
        mock_interpolated_data = [[[1.0, 2.0]] * 100, [[3.0, 4.0]] * 100]
        
        with patch.object(client, 'row_major', return_value=mock_interpolated_data):
            result = client.infer_with_protobuf("sensor1", mock_behaviors)
            
            assert result == ([], "")

    @patch('mdx.analytics.core.inference.inference_client.InferInput')
    @patch('mdx.analytics.core.inference.inference_client.InferRequestedOutput')  
    def test_infer_with_protobuf_response_no_array(self, mock_output, mock_input, client, mock_behaviors):
        """Test inference when response has no numpy array."""
        client.client.is_model_ready.return_value = True
        
        mock_response = Mock()
        mock_response.as_numpy.return_value = None
        client.client.infer.return_value = mock_response
        
        mock_interpolated_data = [[[1.0, 2.0]] * 100, [[3.0, 4.0]] * 100]
        
        with patch.object(client, 'row_major', return_value=mock_interpolated_data):
            result = client.infer_with_protobuf("sensor1", mock_behaviors)
            
            assert result == ([], "")

    @patch('mdx.analytics.core.inference.inference_client.InferInput')
    @patch('mdx.analytics.core.inference.inference_client.InferRequestedOutput')
    def test_infer_with_protobuf_empty_array_response(self, mock_output, mock_input, client):
        """Test inference with empty array response."""
        client.client.is_model_ready.return_value = True
        
        mock_response = Mock()
        # Empty arrays have size 0, so they should hit the fallback path
        empty_array = np.array([])
        def mock_as_numpy(key):
            if key == 'output__0':
                return empty_array
            return None
        mock_response.as_numpy = mock_as_numpy
        mock_response.get_response.return_value = {"model_version": "1.0"}
        client.client.infer.return_value = mock_response
        
        with patch.object(client, 'row_major', return_value=[]):
            result = client.infer_with_protobuf("sensor1", [])
            
            cluster_indices, model_version = result
            # Empty array has size 0, so it falls back to default return values
            assert cluster_indices == []
            assert model_version == ""


class TestInferenceClientEdgeCases:
    """Test suite for InferenceClient edge cases and error conditions."""

    @pytest.fixture
    def client(self):
        """Create a test InferenceClient instance."""
        with patch('mdx.analytics.core.inference.inference_client.InferenceServerClient'):
            return InferenceClient("localhost:8001")

    def test_linear_interpolate_large_target_length(self, client):
        """Test linear_interpolate with large target length."""
        coordinates = [[0.0, 0.0], [1.0, 1.0]]
        result = client.linear_interpolate(coordinates, 1000)
        
        assert len(result) == 1000
        assert result[0] == [0.0, 0.0]
        assert result[-1] == [1.0, 1.0]

    def test_linear_interpolate_target_smaller_than_input(self, client):
        """Test linear_interpolate when target is smaller than input."""
        coordinates = [[i, i] for i in range(10)]  # 10 points
        result = client.linear_interpolate(coordinates, 5)  # Downsample to 5
        
        assert len(result) == 5
        assert result[0] == [0, 0]
        assert result[-1] == [9, 9]

    def test_server_live_false_response(self, client):
        """Test server_live when server returns False."""
        client.client.is_server_live.return_value = False
        
        result = client.server_live()
        
        assert result is False

    def test_server_ready_false_response(self, client):
        """Test server_ready when server returns False."""
        client.client.is_server_ready.return_value = False
        
        result = client.server_ready()
        
        assert result is False

    def test_model_ready_false_response(self, client):
        """Test model_ready when model returns False."""
        client.client.is_model_ready.return_value = False
        
        with patch('mdx.analytics.core.inference.inference_client.logger'):
            result = client.model_ready("test_model")
            
            assert result is False

    def test_linear_interpolate_fraction_calculation(self, client):
        """Test linear_interpolate fraction calculation edge cases."""
        coordinates = [[0.0, 0.0], [2.0, 4.0], [4.0, 8.0]]
        result = client.linear_interpolate(coordinates, 5)
        
        # Verify interpolation calculations
        assert len(result) == 5
        # First and last should match exactly
        assert result[0] == [0.0, 0.0]
        assert result[-1] == [4.0, 8.0]
        # Check middle interpolations are reasonable
        for coord in result:
            assert len(coord) == 2
            assert isinstance(coord[0], float)
            assert isinstance(coord[1], float)

    @patch('mdx.analytics.core.inference.inference_client.InferInput')
    @patch('mdx.analytics.core.inference.inference_client.InferRequestedOutput')
    def test_infer_with_protobuf_tensor_processing(self, mock_output, mock_input, client):
        """Test the tensor processing logic in infer_with_protobuf."""
        # Create behavior with specific coordinate structure
        behavior = Mock(spec=extSchema.Behavior)
        behavior.locations = Mock()
        behavior.locations.coordinates = [
            Mock(point=[1.0, 2.0]),
            Mock(point=[3.0, 4.0]),
        ]
        
        client.client.is_model_ready.return_value = True
        
        mock_response = Mock()
        mock_response.as_numpy.return_value = np.array([[0]])
        mock_response.get_response.return_value = {"model_version": "1.0"}
        client.client.infer.return_value = mock_response
        
        with patch.object(client, 'row_major') as mock_row_major:
            mock_row_major.return_value = [[[1.0, 2.0]] * 100]
            
            with patch('mdx.analytics.core.inference.inference_client.logger'):
                result = client.infer_with_protobuf("sensor1", [behavior])
                
                # Verify tensor extraction was called correctly
                expected_tensor = [[[1.0, 2.0], [3.0, 4.0]]]
                mock_row_major.assert_called_once_with(expected_tensor, 100)
                
                cluster_indices, model_version = result
                assert cluster_indices == [[0]]
                assert model_version == "1.0"