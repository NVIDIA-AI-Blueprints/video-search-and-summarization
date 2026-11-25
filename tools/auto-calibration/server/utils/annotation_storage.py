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

"""
Storage and management of ROI and Tripwire annotations.
Handles saving/loading annotation data for calibration projects.
"""

import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .calibration_exporter import (
    GLOBAL_ROIS_TRIPWIRES_KEY,
    strip_video_filename_extension,
)


def resolve_camera_annotation_block(all_annotations: Dict, video_name: str) -> Dict:
    """
    Return the annotation object for ``video_name``.
    Top-level keys use the internal camera id (no video file extension).
    """
    empty: Dict = {"rois": [], "tripwireLines": [], "tripwireDirections": []}
    if not all_annotations:
        return empty
    if video_name == GLOBAL_ROIS_TRIPWIRES_KEY:
        block = all_annotations.get(video_name)
        return block if isinstance(block, dict) else empty
    key = strip_video_filename_extension(video_name)
    block = all_annotations.get(key)
    return block if isinstance(block, dict) else empty


def _annotation_bucket_key(video_name: str) -> str:
    if video_name == GLOBAL_ROIS_TRIPWIRES_KEY:
        return video_name
    return strip_video_filename_extension(video_name)


def _normalize_camera_id_fields(
    items: List[Dict], bucket_key: str
) -> List[Dict]:
    """Ensure each annotation item's ``camera_id`` matches the storage bucket (no video extension)."""
    out: List[Dict] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        if bucket_key == GLOBAL_ROIS_TRIPWIRES_KEY:
            row["camera_id"] = GLOBAL_ROIS_TRIPWIRES_KEY
        else:
            row["camera_id"] = bucket_key
        out.append(row)
    return out


class AnnotationStorage:
    """
    Manages storage and retrieval of ROI and Tripwire annotations.
    Saves annotations in JSON format matching CalibrationJsonManager structure.
    """
    
    def __init__(self, project_dir: Path):
        """
        Initialize annotation storage for a project.
        
        Args:
            project_dir: Path to the project directory
        """
        self.project_dir = Path(project_dir)
        self.annotations_file = self.project_dir / "annotations.json"
        self.output_dir = self.project_dir / "output"
        self.alignment_data_dir = self.output_dir / "alignment_data"
        self.alignment_annotations_file = self.alignment_data_dir / "annotations.json"
    
    def load_annotations(self, video_name: str) -> Dict:
        """
        Load annotations for a specific video.
        
        Args:
            video_name: Name of the video file
        
        Returns:
            Dictionary with 'rois', 'tripwireLines', and 'tripwireDirections' keys
        """
        if not self.annotations_file.exists():
            return {'rois': [], 'tripwireLines': [], 'tripwireDirections': []}
        
        try:
            with open(self.annotations_file, 'r') as f:
                all_annotations = json.load(f)

            return resolve_camera_annotation_block(all_annotations, video_name)
        except Exception as e:
            print(f"Error loading annotations: {e}")
            return {'rois': [], 'tripwireLines': [], 'tripwireDirections': []}
    
    def save_annotations(self, video_name: str, rois: List[Dict], 
                        tripwire_lines: List[Dict], tripwire_directions: List[Dict]):
        """
        Save annotations for a specific video.
        
        Args:
            video_name: Name of the video file
            rois: List of ROI polygons with points
            tripwire_lines: List of tripwire lines (without direction)
            tripwire_directions: List of tripwire direction arrows
        """
        # Load existing annotations
        if self.annotations_file.exists():
            try:
                with open(self.annotations_file, 'r') as f:
                    all_annotations = json.load(f)
            except:
                all_annotations = {}
        else:
            all_annotations = {}
        
        bucket_key = _annotation_bucket_key(video_name)

        all_annotations[bucket_key] = {
            "rois": _normalize_camera_id_fields(rois, bucket_key),
            "tripwireLines": _normalize_camera_id_fields(tripwire_lines, bucket_key),
            "tripwireDirections": _normalize_camera_id_fields(
                tripwire_directions, bucket_key
            ),
        }
        
        # Save to both locations
        try:
            # Save to primary location
            with open(self.annotations_file, 'w') as f:
                json.dump(all_annotations, f, indent=2)
            
            # Camera frames shown by the UI are linear, so this copy is already
            # in the coordinate space consumed by calibration.
            self.alignment_data_dir.mkdir(parents=True, exist_ok=True)
            with open(self.alignment_annotations_file, 'w') as f:
                json.dump(all_annotations, f, indent=2)
            
            return True
        except Exception as e:
            print(f"Error saving annotations: {e}")
            return False
    
    def get_all_video_annotations(self) -> Dict:
        """
        Get annotations for all videos in the project.
        
        Returns:
            Dictionary mapping video names to their annotations
        """
        if not self.annotations_file.exists():
            return {}
        
        try:
            with open(self.annotations_file, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading all annotations: {e}")
            return {}

    def remap_camera_annotations(
        self, camera_id_map: Dict[str, str], removed_camera_ids: Iterable[str] = ()
    ) -> bool:
        """Move per-camera annotation buckets after camera files are reindexed.

        Annotation keys and their embedded ``camera_id`` fields use AMC's internal
        camera ids, so both must change together when a video is deleted from the
        middle of a project. Buckets in ``removed_camera_ids`` are discarded;
        global layout annotations deliberately have no entry in either collection
        and are retained unchanged.
        """
        if not self.annotations_file.exists():
            return True

        try:
            with open(self.annotations_file, "r", encoding="utf-8") as f:
                all_annotations = json.load(f)
            if not isinstance(all_annotations, dict):
                return False

            removed_camera_ids = set(removed_camera_ids)
            remapped_annotations: Dict = {}
            for bucket_key, annotation_block in all_annotations.items():
                if bucket_key in removed_camera_ids:
                    continue
                new_bucket_key = camera_id_map.get(bucket_key, bucket_key)
                if not isinstance(annotation_block, dict):
                    remapped_annotations[new_bucket_key] = annotation_block
                    continue

                remapped_block = dict(annotation_block)
                if bucket_key in camera_id_map:
                    for field in ("rois", "tripwireLines", "tripwireDirections"):
                        items = annotation_block.get(field, [])
                        remapped_block[field] = _normalize_camera_id_fields(
                            items, new_bucket_key
                        )
                remapped_annotations[new_bucket_key] = remapped_block

            with open(self.annotations_file, "w", encoding="utf-8") as f:
                json.dump(remapped_annotations, f, indent=2)
            return True
        except Exception as e:
            print(f"Error remapping annotations: {e}")
            return False

    def sync_alignment_annotations(self) -> bool:
        """Copy the source annotations into a freshly invalidated output tree."""
        if not self.annotations_file.exists():
            return True

        try:
            self.alignment_data_dir.mkdir(parents=True, exist_ok=True)
            self.alignment_annotations_file.write_bytes(self.annotations_file.read_bytes())
            return True
        except Exception as e:
            print(f"Error syncing alignment annotations: {e}")
            return False
    
    def delete_annotations(self, video_name: str) -> bool:
        """
        Delete annotations for a specific video.
        
        Args:
            video_name: Name of the video file
        
        Returns:
            True if successful, False otherwise
        """
        if not self.annotations_file.exists():
            return True
        
        try:
            with open(self.annotations_file, 'r') as f:
                all_annotations = json.load(f)
            
            bucket_key = _annotation_bucket_key(video_name)
            if bucket_key not in all_annotations:
                return True
            del all_annotations[bucket_key]
            
            # Update both locations
            with open(self.annotations_file, 'w') as f:
                json.dump(all_annotations, f, indent=2)
            
            if self.alignment_annotations_file.exists():
                with open(self.alignment_annotations_file, 'w') as f:
                    json.dump(all_annotations, f, indent=2)
            
            return True
        except Exception as e:
            print(f"Error deleting annotations: {e}")
            return False


def create_roi_object(points: List[Dict], roi_id: Optional[str] = None) -> Dict:
    """
    Create an ROI object from points in calibration format.
    
    Args:
        points: List of points with 'lat' and 'lng' keys (raw pixels)
        roi_id: Optional ID for the ROI
    
    Returns:
        ROI object dictionary in calibration format
    """
    # Convert from internal format {lng, lat} to calibration format {x, y}
    roi_coordinates = [
        {'x': p['lng'], 'y': p['lat']}
        for p in points
    ]
    
    return {
        'id': roi_id or f'roi-id-{int(datetime.now().timestamp())}',
        'roiCoordinates': roi_coordinates
    }


def create_tripwire_object(points: List[Dict], tripwire_id: Optional[str] = None, 
                          direction_points: Optional[List[Dict]] = None, has_direction: bool = False) -> Dict:
    """
    Create a Tripwire object in calibration format.
    
    Args:
        points: List of 2 points with 'lat' and 'lng' keys for the wire line
        tripwire_id: Optional ID for the tripwire
        direction_points: List of 2 points for the direction arrow (if has_direction=True)
        has_direction: Whether the tripwire has a directional arrow
    
    Returns:
        Tripwire object dictionary in calibration format
    """
    # Ensure exactly 2 points for the wire
    wire_points = points[:2]
    
    tripwire = {
        'id': tripwire_id or f'tripwire-id-{int(datetime.now().timestamp())}',
        'wire': {
            'p1': {'x': wire_points[0]['lng'], 'y': wire_points[0]['lat']},
            'p2': {'x': wire_points[1]['lng'], 'y': wire_points[1]['lat']}
        }
    }
    
    # Add direction if present
    if has_direction and direction_points and len(direction_points) == 2:
        tripwire['direction'] = {
            'p1': {'x': direction_points[0]['lng'], 'y': direction_points[0]['lat']},
            'p2': {'x': direction_points[1]['lng'], 'y': direction_points[1]['lat']}
        }
    
    return tripwire
