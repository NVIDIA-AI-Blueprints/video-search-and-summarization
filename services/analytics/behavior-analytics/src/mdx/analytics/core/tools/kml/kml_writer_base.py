# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

from decimal import ROUND_HALF_UP, Decimal

from lxml import etree
from pykml.factory import KML_ElementMaker as KML

from mdx.analytics.core.schema.trajectory.trajectory_e import TrajectoryE


class KmlWriterBase:
    """
    Base class for writing trajectory data to KML format.

    This class provides core functionality for:
    - Converting trajectory data to KML format
    - Creating KML documents and folders
    - Generating geometric elements (linestrings, points)
    - Handling coordinate transformations
    - Managing trajectory visualization

    Examples::
        >>> writer = KmlWriterBase()
        >>> writer.writeOriginal(trajectories, "output.kml")
        >>> print("KML file created successfully")
    """

    def toDecimal(self, v: float) -> float:
        """
        Convert a float value to a decimal with 2 decimal places.

        This method:
        1. Converts input float to Decimal
        2. Rounds to 2 decimal places using half-up rounding
        3. Converts back to float

        :param float v: The float value to convert.
        :return float: The rounded decimal value.

        Examples::
            >>> writer = KmlWriterBase()
            >>> result = writer.toDecimal(3.14159)
            >>> print(f"Rounded value: {result}")  # Output: 3.14
        """
        return float(Decimal(v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    def writeOriginal1(
        self, trajectories: dict[tuple[str, str], TrajectoryE], filename: str, summary: bool = False
    ) -> None:
        """
        Write trajectories to a KML file with basic information.

        This method:
        1. Creates a new KML document and folder
        2. Processes each trajectory
        3. Creates linestrings for trajectories
        4. Adds points at trajectory starts
        5. Saves the KML file

        :param dict[tuple[str, str], TrajectoryE] trajectories: Dictionary of trajectories to write.
        :param str filename: Path to the output KML file.
        :param bool summary: Whether to use summary mode (affects naming).
        :return: None

        Examples::
            >>> writer = KmlWriterBase()
            >>> trajectories = {(id, name): trajectory}
            >>> writer.writeOriginal1(trajectories, "output.kml")
            >>> print("Basic KML file created")
        """
        # Create the root KML element
        kml = KML.kml(KML.Document(KML.Folder()))
        folder = kml.Document.Folder

        for key, traj in trajectories.items():
            id_part = key[0]
            name_part = key[1]

            trajectory = traj.points
            coords = [(p.y, p.x) for p in trajectory]

            # Create document for this trajectory
            doc = KML.Document(
                KML.name(f"trajectory{id_part}"),
                KML.Placemark(
                    KML.name("trajectory"),
                    KML.LineString(KML.coordinates(" ".join(f"{lat},{lon},0" for lat, lon in coords))),
                ),
                KML.Placemark(KML.name(name_part), KML.Point(KML.coordinates(f"{coords[0][0]},{coords[0][1]},0"))),
            )
            folder.append(doc)

        # Write the KML file
        with open(filename, "wb") as f:
            f.write(etree.tostring(kml, pretty_print=True))
        print(f"File created: {filename}")

    def writeOriginal(
        self, trajectories: dict[tuple[str, str], TrajectoryE], filename: str, summary: bool = False
    ) -> None:
        """
        Write trajectories to a KML file with detailed information.

        This method:
        1. Creates a new KML document and folder
        2. Processes each trajectory
        3. Creates linestrings for trajectories
        4. Adds points at both start and end
        5. Includes speed, distance, and time information
        6. Saves the KML file

        :param dict[tuple[str, str], TrajectoryE] trajectories: Dictionary of trajectories to write.
        :param str filename: Path to the output KML file.
        :param bool summary: Whether to use summary mode (affects naming and information display).
        :return: None

        Examples::
            >>> writer = KmlWriterBase()
            >>> trajectories = {(id, name): trajectory}
            >>> writer.writeOriginal(trajectories, "output.kml", summary=True)
            >>> print("Detailed KML file created")
        """
        kml = KML.kml(KML.Document(KML.Folder()))
        folder = kml.Document.Folder

        for key, traj in trajectories.items():
            trajectory = traj.points
            coords = [(p.y, p.x) for p in trajectory]

            id_suffix = key[1].split("-")[-1] if "-" in key[1] else key[1]

            speed = self.toDecimal(traj.speed)
            distance = self.toDecimal(traj.distance)
            interval = self.toDecimal(traj.time_interval)

            n = "" if summary else f"{id_suffix}: {traj.direction} at {speed} mph"
            e = "" if summary else f"E: {distance} m in {interval} sec"

            doc = KML.Document(
                KML.name(f"trajectory{key[0]}"),
                KML.Placemark(
                    KML.name("trajectory"),
                    KML.LineString(KML.coordinates(" ".join(f"{lat},{lon},0" for lat, lon in coords))),
                ),
                KML.Placemark(KML.name(n), KML.Point(KML.coordinates(f"{coords[0][0]},{coords[0][1]},0"))),
                KML.Placemark(KML.name(e), KML.Point(KML.coordinates(f"{coords[-1][0]},{coords[-1][1]},0"))),
            )
            folder.append(doc)

        with open(filename, "wb") as f:
            f.write(etree.tostring(kml, pretty_print=True))
        print(f"File created: {filename}")

    def writeOriginalAndSmoothed(
        self, trajectories: dict[tuple[str, str], TrajectoryE], filename: str, summary: bool = False
    ) -> None:
        """
        Write both original and smoothed trajectories to a KML file.

        This method:
        1. Creates a new KML document and folder
        2. Processes each trajectory
        3. Creates linestrings for both original and smoothed trajectories
        4. Adds points at both start and end
        5. Includes detailed trajectory information
        6. Saves the KML file

        :param dict[tuple[str, str], TrajectoryE] trajectories: Dictionary of trajectories to write.
        :param str filename: Path to the output KML file.
        :param bool summary: Whether to use summary mode (affects naming and information display).
        :return: None

        Examples::
            >>> writer = KmlWriterBase()
            >>> trajectories = {(id, name): trajectory}
            >>> writer.writeOriginalAndSmoothed(trajectories, "output.kml", summary=True)
            >>> print("KML file with original and smoothed trajectories created")
        """
        kml = KML.kml(KML.Document(KML.Folder()))
        folder = kml.Document.Folder

        for key, traj in trajectories.items():
            smoothed = traj.points
            snapped = traj.smooth_trajectory

            smoothed_coords = [(p.y, p.x) for p in smoothed]
            snapped_coords = [(p.y, p.x) for p in snapped]

            speed = self.toDecimal(traj.speed)
            distance = self.toDecimal(traj.distance)
            ldistance = self.toDecimal(traj.linear_distance)
            interval = self.toDecimal(traj.time_interval)

            n = f"Moving: {traj.direction}" if summary else f"S: {traj.direction} at {speed} mph"
            e = f"distance: {distance}" if summary else f"E: {distance}/{ldistance} m in {interval} sec"

            doc = KML.Document(
                KML.name(f"trajectory{key[0]}"),
                KML.Placemark(
                    KML.name("trajectory"),
                    KML.LineString(KML.coordinates(" ".join(f"{lat},{lon},0" for lat, lon in smoothed_coords))),
                ),
                KML.Placemark(
                    KML.name("snappedTrajectory"),
                    KML.LineString(KML.coordinates(" ".join(f"{lat},{lon},0" for lat, lon in snapped_coords))),
                ),
                KML.Placemark(
                    KML.name(n), KML.Point(KML.coordinates(f"{smoothed_coords[0][0]},{smoothed_coords[0][1]},0"))
                ),
                KML.Placemark(
                    KML.name(e), KML.Point(KML.coordinates(f"{smoothed_coords[-1][0]},{smoothed_coords[-1][1]},0"))
                ),
            )
            folder.append(doc)

        with open(filename, "wb") as f:
            f.write(etree.tostring(kml, pretty_print=True))
        print(f"File created: {filename}")
