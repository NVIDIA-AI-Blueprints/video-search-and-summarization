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

import logging
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta
from elasticsearch import Elasticsearch

from mdx.anomaly.source.source_base import SourceBase


class ElasticsearchSource(SourceBase):
    """Client for interacting with ElasticSearch to fetch object data."""
    
    def __init__(self, config: dict):
        """Initialize the ElasticSearch source with config."""
        super().__init__(config)
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # Extract Elasticsearch connection parameters from config
        self.es_host = config.get('elasticsearch', {}).get('host', 'localhost')
        self.es_port = config.get('elasticsearch', {}).get('port', 9200)
        
        # Initialize Elasticsearch client
        self.es = Elasticsearch([f'http://{self.es_host}:{self.es_port}'])
    
    def search_objects(self, sensor_id: str, timestamp_str: str, object_id: str) -> List[Dict]:
        """
        Search for objects in ElasticSearch around the given timestamp.
        
        Args:
            sensor_id (str): The sensor ID to search for
            timestamp_str (str): The timestamp to search around in ISO format
            object_id (str): The object ID to search for
            
        Returns:
            List[Dict]: List of objects found
        """
        try:
            dt_timestamp = self._parse_timestamp(timestamp_str)
            index_pattern = f"mdx-raw-{dt_timestamp.strftime('%Y-%m-%d')}"
            query = self._create_query(sensor_id, dt_timestamp, object_id)
            
            result = self._search(index_pattern, query)
            if result['hits']['total']['value'] > 0:
                return self._find_best_match(result['hits']['hits'], dt_timestamp, object_id)
            return None
            
        except Exception as e:
            self.logger.error(f"Error in search_objects: {str(e)}")
            return []
        
    def _search(self, index_pattern: str, body: Dict) -> Dict:
        """
        Search for documents in ElasticSearch using the provided query.
        
        Args:
            index_pattern (str): The Elasticsearch index pattern to search
            body (Dict): The Elasticsearch query body
            
        Returns:
            Dict: The search results from Elasticsearch
        """
        try:
            self.logger.debug(f"Searching Elasticsearch index '{index_pattern}' with query: {body}")
            return self.es.search(index=index_pattern, body=body)
        except Exception as e:
            self.logger.error(f"Error searching Elasticsearch: {str(e)}")
            return {"error": str(e)}
    
    def _parse_timestamp(self, timestamp_str: str) -> datetime:
        """Parse the timestamp string into a datetime object."""
        return datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
    
    def _create_query(self, sensor_id: str, timestamp: datetime, object_id: str) -> Dict:
        """Create the ElasticSearch query for the given sensor_id and timestamp."""
        start_time = timestamp.replace(microsecond=0)
        end_time = start_time + timedelta(seconds=1)
        
        return {
            "query": {
                "bool": {
                    "must": [
                        {"match": {"sensorId": sensor_id}},
                        {"nested": {
                            "path": "objects",
                            "query": {
                                "match": {"objects.id": object_id}
                            }   
                        }},
                        {
                            "range": {
                                "timestamp": {
                                    "gte": start_time.isoformat(),
                                    "lt": end_time.isoformat()
                                }
                            }
                        }
                    ]
                }
            },
            "sort": [
                {"timestamp": {"order": "asc"}}
            ],
            "size": 100
        }
    
    def _find_best_match(self, results: List[Dict[str, Any]], target_timestamp: datetime, object_id: str) -> Optional[Dict[str, Any]]:
        """Find exact match or next closest timestamp and return only the specified object."""
        if not results:
            return None

        target_ts = target_timestamp.timestamp()
        next_closest = None
        smallest_diff = float('inf')

        for doc in results:
            doc_timestamp = datetime.fromisoformat(
                doc['_source']['timestamp'].replace('Z', '+00:00')
            )
            doc_ts = doc_timestamp.timestamp()
            time_diff = doc_ts - target_ts

            # Find the matching object and extract only needed fields
            matching_objects = [
                {
                    "id": obj["id"],
                    "bbox": obj["bbox"]
                }
                for obj in doc['_source']['objects'] 
                if obj['id'] == object_id
            ]

            if not matching_objects:
                continue

            # Create simplified result
            filtered_result = {
                "timestamp": doc['_source']['timestamp'],
                "object": matching_objects[0]  # We know there's exactly one match
            }

            # Check for exact match
            if time_diff == 0:
                return filtered_result

            # Track next closest timestamp
            if time_diff > 0 and time_diff < smallest_diff:
                smallest_diff = time_diff
                next_closest = filtered_result

        return next_closest
    
    def close(self) -> None:
        """Close the Elasticsearch client connection."""
        pass  # Elasticsearch client doesn't require explicit closing 