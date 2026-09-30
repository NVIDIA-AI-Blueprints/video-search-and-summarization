Metropolis Camera Calibration Toolkit
=======================================

Calibration Overview
----------------------

The MDX Camera Calibration Toolkit provides instructions on configuring and calibrating sensors for use in MDX. The MDX Camera Calibration Toolkit is a React/Python web application that enables NVIDIA developers to onboard sensors onto the MDX. Developers can export the calibrated sensors to use with the MDX.

This software is used to onboard sensors, calibrate them, validate, and configure the UI for sensor groups. Camera calibration allows us to map the output from the sensor-processing layer onto a coordinate system such as Global Information System or Cartesian Coordinate map in MDX, and ultimately the Visualization.


Architecture
-------------------

**Module Overview**


.. image:: /content/Calibration-Module_Overview.png
        :align: center
        :alt: Calibration Toolkit Module Overview

**Architecture**

.. image:: /content/Calibration-Tool_Architecture.png
        :align: center
        :alt: Calibration Toolkit Architecture


Requirements
-------------

Hardware Requirements
~~~~~~~~~~~~~~~~~~~~~~~~

* 4 GB system RAM
* Single core CPU
* 50 GB of HDD space

**Recommended**

* 8 GB system RAM
* 2 core CPU
* 100 GB of SSD space

Software Requirements
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* OS Independent -- Recommended: Ubuntu 18.04 LTS

* Google Maps Javascript API Key

* Create an account with Google if you don't have one already, and enable `Google Maps Javascript API. <https://console.cloud.google.com/google/maps-apis/start?pli=1>`_


* OpenStreetMaps Link

   * Locate the appropriate Open Street Map Link from `here <https://download.geofabrik.de/>`_.
   * We will need this in generating the Intersection Road links. Try to find the map most local to the city you are calibrating, as this will quicken the computation. For example, if calibrating, the city of San Jose, choose the map for the state of California.

* Browser - Recommended: Google Chrome

Installation Prerequisites
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* RTSP links to sensors, or screenshot views of sensors

* Global Coordinates of sensor locations

* Global Coordinates of Place Locations

* NGC Pre-requisites

Installation
-----------------------

Pull the docker from ngc: ::

     docker pull nvcr.io/metropolis/metropolis-analytic/mdx-calibration:1.0

Make a directory to store your data: ::

     mkdir <path/to/dir>/data

Run the docker container using host's port 8003, and mounting the aforementioned directory as your data folder. ::

     docker run  -p 8003:8003 -v <path to data dir>/data:/calibration/server/data/ -it nvcr.io/metropolis/metropolis-analytic/mdx-calibration:1.0 deploy.sh

.. note:: You may add network=host to the docker command if you have problem running the containers

Navigate to https://localhost:8003, where the application will be live.

For more information on using the sensor calibration see :doc:`MDX_Camera_Calibration_User_Guide`.



Configuration
-------------------

In this section we will learn how to setup the structures that will eventually create the configs for the MDX.

Project Configuration
~~~~~~~~~~~~~~~~~~~~~~~
First, we will create a project. Each project has a different calibration type:

 - GIS - GIS based calibration
 - Approximate - Approximate Cartesian Calibration
 - MultiCamera Tracking - Calibration for MultiCamera Tracking based on a single floorplan
 - Image Calibration - Only ROI/Tripwire Calibration for Images


Discover Cameras via MMS Import
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If you have set up MDX media-service (MMS), you can import the sensors available in MMS and propagate some of the sensors details available. If possible, it will also try and take screenshots of the current sensor view. This will save time from importing it  manually.

.. image:: ../content/Calibration-mms_config.png
        :align: center
        :alt: MMS Modal Config

Sensor Configuration
~~~~~~~~~~~~~~~~~~~~~~

.. image:: ../content/Calibration-sensor_config_bryant.png
        :align: center
        :alt: Camera Modal Config

A sensor consists of the metadata that we will be calibrating. These are the common sensors:

- Sensor Id\* - Sensor ID is the unique name of the sensor, which will be used throughout MDX.
- Camera Name\* - A name that can be used to find the sensor in Media Server or ONVIF id
- Camera Latitude\* - Latitude of Sensor.
- Camera Longitude\* - Longitude of Sensor.
- Cardinal Direction - Direction of the cars, the Camera's Field of View sees. For example, if a sensor is facing south, the sensor's Field of View is `North Bound` or `NB`.
- FPS\* - Integer referring to Sensor's Frame per second
- Direction - Integer between 0 and 360, measured in degrees, with 0 as North.
- Depth\* - Integer referring to Sensor's Depth.
- FOV\* - Angle of the Sensor
- Video URL\* - hls or webRTC URL used by mdx UI
- MMS Protocol\* - 2 options "webrtc" or "hls".  Please use these exact spelling
- MMS type\* - 2 options "nvMms" if you are using mdx media-service or "wowza". Please use this exact spelling.
- MMS Host\* - http url that points to the Media Server. Please be sure to include "http://" and if you are using mdx media-service, that you include port 81.
- Intersection - Choose an intersection that this sensor is associated with
- Corridor - Choose a corridor that this sensor is associated with
- Place\* - Choose any PlaceType, and it's corresponding Place that the sensor may be associated with.



\*required

Group Configuration
~~~~~~~~~~~~~~~~~~~~

PlaceType
^^^^^^^^^^^^^

`PlaceType` is a generic grouping term. For example, a building or a room could be a PlaceType. To create a new PlaceType click on ``New PlaceType`` in the tabs.

.. image:: ../content/Calibration-new_placeType_config.png
        :align: center
        :alt: New PlaceType Modal Config

Within each PlaceType tab, we can create Places, such as Building R or Room G148. Each Place has a name and a location.

.. image:: ../content/Calibration-new_place_config.png
        :align: center
        :alt: New Place Modal Config

Intersection
#############

Overview
++++++++++

Intersection is a crossing of two roads. In this, we try to group sensors that cover the same two roads. This grouping allows us to configure “Intersection View” within the UI.

.. image:: ../content/Calibration-intersection_config_bryant.png
        :align: center
        :alt: Intersection Modal Config


Properties
+++++++++++++++

- Name (Name of Intersection) - often this is the common Major Road and Minor roads of a group of sensors
- Description
- Intersection Latitude - Latitude of center of Intersection
- Intersection Longitude - Longitude of center of Intersection

Corridor
##########

Overview
+++++++++++

A corridor is a group of sensors that might fall under a large artery, such as a highway. For a broader definition, FRC (federal road class) level 1-3 can be called a corridor. (Total 6 levels - Interstate, Other Freeways & Expressways, Other Principal Arterials, Minor Arterials, Major and Minor Collectors, Local Roads. (`source <https://www.fhwa.dot.gov/planning/processes/statewide/related/highway_functional_classifications/section03.cfm>`_). This grouping allows us to configure “Corridor View” within the UI.

.. image:: ../content/Calibration-corridor.jpg
       :align: center
       :alt: Corridor Modal Config

Properties
++++++++++++

- Name - Name of Intersection -- often this is the common Major road among intersections and their sensors
- Corridor Longitude - Latitude of the center of Corridor
- Corridor Latitude - Longitude of the center of Corridor

Drawing
-------------
Drawing polygons and polylines in both the image space and satellite map space is a key component of the features in this app. The features for drawing polygons and polylines are essentially the same, the only difference being if the first and last points are connected. We will cover the features for drawing both below.

Image Drawing Tutorial
~~~~~~~~~~~~~~~~~~~~~~~

1) The image drawing container has pan and zoom capabilities. When in a drawing mode, left click on the image to draw and add points.

  .. image:: ../content/Calibration-image_drawing_1.jpg
        :align: center
        :alt: Image Drawing Tutorial 1

  .. image:: ../content/Calibration-image_drawing_2.jpg
        :align: center
        :alt: Image Drawing Tutorial 2

2) A polygon can be completed using either option below. A polyline can be completed using the endpoint option only.

  .. image:: ../content/Calibration-image_drawing_3.jpg
        :align: center
        :alt: Image Drawing Tutorial 3

  .. image:: ../content/Calibration-image_drawing_4.jpg
        :align: center
        :alt: Image Drawing Tutorial 4

3) The shape will be saved upon completion. The user can then click the shape to enter edit mode.

  .. image:: ../content/Calibration-image_drawing_5.jpg
        :align: center
        :alt: Image Drawing Tutorial 5

  .. image:: ../content/Calibration-image_drawing_6.jpg
        :align: center
        :alt: Image Drawing Tutorial 6


4) Polygons and polylines have the same capabilities of adding, moving, and deleting points.


  .. image:: ../content/Calibration-image_drawing_7.jpg
        :align: center
        :alt: Image Drawing Tutorial 7

  .. image:: ../content/Calibration-image_drawing_8.jpg
        :align: center
        :alt: Image Drawing Tutorial 8


Satellite Map Drawing Tutorial
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1) The map drawing container has pan and zoom capabilities. When in drawing mode, left click on the map to draw and add points.

  .. image:: ../content/Calibration-map_drawing_1.jpg
        :align: center
        :alt: Map Drawing Tutorial 1

  .. image:: ../content/Calibration-map_drawing_2.jpg
        :align: center
        :alt: Map Drawing Tutorial 2


2) A polygon can be completed using either option below.A polyline can be completed using the endpoint option only.

  .. image:: ../content/Calibration-map_drawing_3.jpg
        :align: center
        :alt: Map Drawing Tutorial 3

  .. image:: ../content/Calibration-map_drawing_4.jpg
        :align: center
        :alt: Map Drawing Tutorial 4

3) The shape will be saved upon completion. The user can edit at anytime and does not need to click to enter edit mode.

  .. image:: ../content/Calibration-map_drawing_5.jpg
        :align: center
        :alt: Map Drawing Tutorial 5


4) Polygons and polylines have the same capabilities of adding, moving and deleting points.



  .. image:: ../content/Calibration-map_drawing_6.jpg
        :align: center
        :alt: Map Drawing Tutorial 6

  .. image:: ../content/Calibration-map_drawing_7.jpg
        :align: center
        :alt: Map Drawing Tutorial 7

GIS Calibration
-------------------------
Camera calibration in this application refers to obtaining a homography matrix for projecting points from sensor image coordinates, (i.e. pixel coordinates), to the global satellite space (i.e. latitude/longitude co-ordinates). Calibration is required because objects detected and located in the image do not provide sufficient information for data analysis.


Project Configuration
~~~~~~~~~~~~~~~~~~~~~~

A place consists of a name, a location, and an `OpenStreetMap` (.osm) link.

Fill out the form to setup GIS project.

.. image:: ../content/Calibration-city_config.png
        :align: center
        :alt: GIS Project Setup Modal Config




- Map File-  Open Street Map Link, a link to the (.bz2) file that contains the open street map links
- Camera Latitude - global latitude of the place
- Camera Longitude - global longitude of the place

GIS Calibration
~~~~~~~~~~~~~~~~~~~~
Consider the case of a car driving along the road. A car can be detected and tracked through a series of video frames, but without calibration, its speed could only be tracked at a pixel scale. Instead, we want to transform those detected pixel locations to satellite coordinates, to be able to track the speed of the car in kilometers or miles per hour.

To calibrate, we will draw one polygon in the image view marking static landmark features and another polygon in the satellite view that matches the same landmark features as in the image view. The idea is that these landmarks are an example of how the same object is located in image coordinates and global satellite coordinates. The more accurate landmark pairs, the better the homography matrix will be able to be calculated.

How To Calibrate
^^^^^^^^^^^^^^^^^

1) Load the Camera Calibration Tool

  .. image:: ../content/Calibration-Calibration_Step_1.jpg
        :align: center
        :alt: Calibration Step 1

2) Draw a polygon of at least 8 landmark features that can be seen in both the image and the satellite view.

  .. image:: ../content/Calibration-Calibration_Step_2.jpg
        :align: center
        :alt: Calibration Step 2

3) Draw a polygon on the satellite map with landmark features that match the polygon in the image.

  .. image:: ../content/Calibration-Calibration_Step_3.jpg
        :align: center
        :alt: Calibration Step 3

4) Use the toggle markers button for better visibility on the exact location of a point.

  .. image:: ../content/Calibration-Calibration_Step_4.jpg
        :align: center
        :alt: Calibration Step 4

5) Draw the Region of Interest (ROI), which represents the area for which the calibration is intended to be valid. This does not necessarily have to be the sensor's entire Field of View. Typically the calibration holds up to 200m away from the sensor.

  .. image:: ../content/Calibration-Calibration_Step_5.jpg
        :align: center
        :alt: Calibration Step 5

  Further notes on the Region of Interest (ROI): The homography matrix from the calibration relies on the assumption that the road is flat, and so the calibration is expected to produce erroneous results in the sky, on grassy hills, in trees, etc. Therefore, the ROI should cover the parts of the road that the sensor can see, so the end application knows to ignore points outside this region. This helps prevent errors and improve performance. The ROI can be further adjusted at the Camera Validation step as the user will learn more about how well the calibration works in certain areas of the image.

6) Toggle the visibility of either the calibration or ROI polygons for better visibility if required.

  .. image:: ../content/Calibration-Calibration_Step_6.jpg
        :align: center
        :alt: Calibration Step 6

7) Click the Calibrate button to calculate the homography matrix.

  .. image:: ../content/Calibration-Calibration_Step_7.jpg
        :align: center
        :alt: Calibration Step 7

8) Review the reprojection errors of the points. Click edit to go back and adjust points, or Accept Calibration to accept the homography matrix.

  .. image:: ../content/Calibration-Calibration_Step_8.jpg
        :align: center
        :alt: Calibration Step 8

 Further notes on reprojection error: The reprojection error is calculated by projecting a point in the image polygon (point 1 on the image polygon), to satellite coordinates. The distance (meters) between that point, and the corresponding point drawn on the satellite image (that is, point 1 on the satellite image in the case that point 1 on the image polygon was projected to satellite coordinates), is the reprojection error for that point correspondence.


GIS Sensor Validation
~~~~~~~~~~~~~~~~~~~~~~~

Sensor Validation is used as a tool to evaluate the homography matrix generated at the calibration step. Validation is necessary, as simply having accurate reprojection errors does not guarantee the homography matrix will work for all locations in the image. The aim is to validate that the homography matrix is valid for possible car trajectories in the area of our ROI.

To validate, we will draw test trajectories that a car could feasibly drive in the image and the app will automatically project the trajectory to the satellite map. The trajectory does not need to be a legal driving path, but should be feasible in the sense that a car could drive in the locations along the trajectory. The main idea is that the if we draw a trajectory in the eastbound lanes on the image, it should project to the eastbound lanes on the map. Or, if we draw a trajectory in the westbound lanes on the image, it should project to the westbound lanes on the map.

A good validation will test all parts of the Region of Interest as well as all parts of the Camera's Field of View to see that the calibration holds up.

How To Validate
^^^^^^^^^^^^^^^^^

1) Load the Validation Tool

  .. image:: ../content/Calibration-Validation_Step_1.jpg
        :align: center
        :alt: Validation Step 1

2) Draw a trajectory on the image, and validate that the projected polyline makes sense in the satellite map. Feel free to clear the image and try drawing as many trajectories as desired. Validate the Calibration when satisfied.

  .. image:: ../content/Calibration-Validation_Step_1.jpg
        :align: center
        :alt: Validation Step 1

Road Link Generation for GIS Calibration
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Overview
^^^^^^^^^^^

This App allows us to generate the Road Links that will show up in the UI. In essence, we need to draw lines on the satellite map that represent the intersections we are interested in. We will then be able to query the corresponding links from the Open Street Maps. Find the open street maps from https://download.geofabrik.de/ . We are looking for the `.bz2` file.

- Once we have calibrated all the sensors, and added them to the correct intersection, we are ready to generate the Road Links. In this view, we will be drawing line segments, to generate the links that show up in the UI. It may be helpful to revisit the sensors in the intersection to visualize the roads that the intersection covers.
- Edit the Intersection and choose to `Edit Road Links`

  .. image:: ../content/Calibration-Road_Link_Gen_1.png
      :align: center
      :alt: Road Link Generation Step 1

- When we are drawing, we want to draw line segments that correlate to the roads being seen by the sensors in that intersection. For each road in the sensor, there needs to be a line drawn, that starts where a car would be coming from to where it is going. For example, a road going north:

* When drawing the line segments, we want to draw them in terms of the whole intersection. For instance, if you have a northbound and southbound sensor, then you can draw 2 line segments one SB and one NB

  .. image:: ../content/Calibration-Road_Link_Gen_2.png
    :align: center
    :alt: Road Link Generation Step 2

Road Link Validation
~~~~~~~~~~~~~~~~~~~~~~~

After generating the links, this will enable us to load the links that we will see in the MDX UI, and validate that we have all the links that we need. Notice that the line segment markers go in increasing order in the direction that cars will be moving. For one way roads, make sure there is only one link. It is possible that the links do not match up cleanly. This is an issue with the granularity of the Open Street maps.

  .. image:: ../content/Calibration-Road_Link_Val_1.png
      :align: center
      :alt: Road Link Validation Step 1

If they are valid, click “Validate Links”

Corridor Data Generation
~~~~~~~~~~~~~~~~~~~~~~~~~~
A corridor is a way of grouping sensors along the same road. Cameras assigned to a specific corridor should automatically appear in the corridor map view.

One main attribute of a corridor is the corridor length. To generate the length, a polyline is drawn along the road that covers all sensors along the corridor (the polyline does not need to touch the sensor, but simply pass by it). The app will automatically calculate and display the length after drawing.

How to Edit a Corridor
^^^^^^^^^^^^^^^^^^^^^^^^


1) Load the corridor view. In this view you can see all sensors that belong to the chosen corridor.

  .. image:: ../content/Calibration-Corridor_Gen_1.jpg
      :align: center
      :alt: Corridor Generation Step 1

2) Click a sensor to get more information. This can be used to confirm the correct sensors are in the corridor.

  .. image:: ../content/Calibration-Corridor_Gen_2.jpg
    :align: center
    :alt: Corridor Generation Step 2

3) Draw a line along the length of the corridor. When the line is drawn, the length (in meters) of the corridor will automatically be calculated.

  .. image:: ../content/Calibration-Corridor_Gen_3.jpg
      :align: center
      :alt: Corridor Generation Step 3

Approximate Calibration
--------------------------

Approximate Calibration allows us to define a 1:1 mapping between a sensor and a plane without a pre-defined coordinate system. In this type of calibration, we will use the 4-points in the pixel space and a user defined coordinate system to calibrate sensors.

Approximate Camera Calibration
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Generating Homography Matrix
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

1) Draw 4 Point Rectangle

 To Generate the sensor calibration, the first step is to draw a 4 point rectangle on a flat space in the image. The best practice is to start drawing the rectangle from the bottom left of the image, and to add points in a counter-clockwise fashion.

  .. image:: ../content/Calibration-Approx_Calib_Gen_1.png
      :align: center
      :alt: Approximate Calibration Generation Step 1

2) Define the Cartesian Coordinate System

 On the left side, for each point drawn, you'll see two boxes with the x and y coordinate which defines that point in the coordinate system. Approximate the distance between the points in centimeters, and define your coordinate system based on the (x,y) distance from your origin.

  .. image:: ../content/Calibration-Approx_Calib_Gen_2.png
      :align: center
      :alt: Approximate Calibration Generation Step 2

 Make sure the origin (0,0) of the coordinate system is at the left bottom of the image.

3) Draw the Region of Interest (ROI), which represents the area for which the calibration is intended to be valid. This does not necessarily have to be the sensor's entire Field of View.

  .. image:: ../content/Calibration-Approx_Calib_Gen_3.png
      :align: center
      :alt: Approximate Calibration Generation Step 3

 Further notes on the Region of Interest (ROI): The homography matrix from the calibration relies on the assumption that the floor is flat, and so the calibration is expected to produce erroneous results on stairs, sloped terrain, etc.  Therefore, the ROI should cover the parts of the floor that the sensor can see, so the end application knows to ignore points outside this region. This helps prevent errors and improve performance. The ROI can be further adjusted at the Sensor Validation step as the user will learn more about how well the calibration works in certain areas of the image.

4) Use the toggle markers button for better visibility on the exact location of a point, or to hide the specified polygon or polyline.

5) Click the Calibrate button to calculate the homography matrix.

6) Review the reprojection errors of the points. Click edit to go back and adjust points, or Accept Calibration to accept the homography matrix.

  .. image:: ../content/Calibration-Approx_Calib_Gen_4.png
      :align: center
      :alt: Approximate Calibration Generation Step 6

  Further notes on reprojection error: The reprojection error is calculated by finding the Euclidean distance between the point and it's corresponding projected point. The distance (meters) between that point, and the corresponding point drawn on the satellite image (point 1 on the real world image in the case that point 1 on the image polygon was projected to satellite coordinates), is the reprojection error for that point correspondence.

Adding Tripwires/Direction
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

In cartesian and multi-sensor tracking calibration, we are able to add tripwires to enable counting.

1) Draw the tripwire

  Draw a line segment that defines the region where we want to detect if objects have crossed. Make sure that the polyline has only 2 points.

  .. image:: ../content/Calibration-Tripwire_Gen_1.png
      :align: center
      :alt: Tripwire Creation

2) Draw the Tripwire Direction Wire.

  When we are drawing, we want to draw line segments that correlate to the direction that objects will be entering the tripwire. This line must intersect the tripwire line. The 0 Point is be outside the zone, and the 1 point will be across the trip wire. Make sure that the polyline has only 2 points.

  .. image:: ../content/Calibration-Tripwire_Direction_Gen_1.png
    :align: center
    :alt: Tripwire Direction Creation


Approximate Validation
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Validating the Images
^^^^^^^^^^^^^^^^^^^^^^^^

1) Load the Validation Tool

2) Draw a trajectory on the image, and validate that the projected polyline makes sense in the transformed image. Feel free to clear the image and try drawing as many trajectories as desired. Validate the Calibration when satisfied.

  The ROI, Tripwire, and Direction lines will be projected onto the real world image. To adjust these lines, go back to the `Approximate Camera Calibration`_ section.


Generating Warped Image
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

We can adjust the warped image by padding pixels which moves the center of the crop taken, and adjusting the size of the crop.

  .. image:: ../content/Calibration-Warped_Image_Gen_1.png
      :align: center
      :alt: Warped Image Generation Step 1


Multi-Camera Tracking Calibration
-----------------------------------

Multi-Camera Tracking Calibration allows us to define a 1:1 mapping between a sensor and a floor plan or building map. In this type of calibration, we will use the at least 8 points in the pixel space and a matching 8 points in the floor plan domain to calibrate sensors. We will calibrate a sensor to a crop of the floor plan that corresponds to that sensor.

Multi-Camera Tracking Floorplan Setup
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Upload a floor plan of the place being calibrated. This will be used to make sure the correlation between sensor and floor plan is consistent. After uploading a floor plan and importing the sensors, the sensors need to be placed on the floor plan. 

1) For each sensor, place a sensor on it's location on the floor plan

  .. image:: ../content/FloorPlan-Setup_Step_1.png
        :align: center
        :alt: Multi-Camera Tracking Floorplan Setup Step 1

2) Toggle the Change Scale Factor button, to add a Scale Factor between pixels to meters.

  .. image:: ../content/FloorPlan-Setup_Step_2.png
        :align: center
        :alt: Multi-Camera Tracking Floorplan Setup Step 2

3) Use the toggle markers button for better visibility on the exact location of a point.


Multi-Camera Tracking Camera Calibration
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Generating Homography Matrix
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

1) Draw a polygon of at least 8 landmark features that can be seen in both the image and the floor plan view.

  .. image:: ../content/FloorPlan-Calibration_Step_1.jpg
        :align: center
        :alt: Multi-Camera Tracking Calibration Step 1

2) Draw a polygon on the Multi-Camera Tracking map with landmark features that match the polygon in the image.

  .. image:: ../content/FloorPlan-Calibration_Step_2.jpg
        :align: center
        :alt: Multi-Camera Tracking Calibration Step 2

3) Use the toggle markers button for better visibility on the exact location of a point.

4) Draw the Region of Interest (ROI), which represents the area for which the calibration is intended to be valid. This does not necessarily have to be the sensor's entire Field of View.

  .. image:: ../content/FloorPlan-Calibration_Step_3.jpg
        :align: center
        :alt: Multi-Camera Tracking Calibration Step 4

  Further notes on the Region of Interest (ROI): The homography matrix from the calibration relies on the assumption that the area is flat, and so the calibration is expected to produce erroneous results in non-planar areas. Therefore, the ROI should cover the parts of the room that the sensor can see, so the end application knows to ignore points outside this region. This helps prevent errors and improve performance. The ROI can be further adjusted at the Camera Validation step as the user will learn more about how well the calibration works in certain areas of the image.

5) Use the toggle markers button for better visibility on the exact location of a point, or to hide the specified polygon or polyline.

6) Click the Calibrate button to calculate the homography matrix.

7) Review the reprojection errors of the points. Click edit to go back and adjust points, or Accept Calibration to accept the homography matrix.

  Further notes on reprojection error: The reprojection error is calculated by finding the Euclidean distance between the point and it's corresponding projected point. The distance (meters) between that point, and the corresponding point drawn on the satellite image (point 1 on the real world image in the case that point 1 on the image polygon was projected to the floor plan coordinates), is the reprojection error for that point correspondence.

Adding Tripwires/Direction
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

In cartesian calibration, we are able to add tripwires to enable counting.

1) Draw the tripwire

  Draw a line segment that defines the region where we want to detect if objects have crossed. Make sure that the polyline has only 2 points. When we are drawing, we want to draw line segments that correlate to the direction that objects will be entering the tripwire. This line must intersect the tripwire line. The 0 Point is be outside the zone, and the 1 point will be across the trip wire. Make sure that the polyline has only 2 points.

  .. image:: ../content/FloorPlan-Calibration-Tripwire_Gen_1.jpg
      :align: center
      :alt: Tripwire Creation

Multi-Camera Tracking Validation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Validating the Images
^^^^^^^^^^^^^^^^^^^^^^^^

1) Load the Validation Tool

2) Draw a trajectory on the image, and validate that the projected polyline makes sense in the transformed image. Feel free to clear the image and try drawing as many trajectories as desired. Validate the Calibration when satisfied.

  The ROI, Tripwire, and Direction lines will be projected onto the real world image. To adjust these lines, go back to the `Multi-Camera Tracking Camera Calibration`_ section.

Image Calibration
--------------------------

Image Calibration allows us to define ROIs, and tripwires for a sensor without any homography calibration.

Image Camera Calibration
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Generating the Artifacts
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

1) Draw the Region of Interest (ROI), which represents the area for which the calibration is intended to be valid. This does not necessarily have to be the sensor's entire Field of View.

  .. image:: ../content/ImageCalibration.png
        :align: center
        :alt: Multi-Camera Tracking Calibration Step 4

2) Use the toggle markers button for better visibility on the exact location of a point, or to hide the specified polygon or polyline.

Adding Tripwires/Direction
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

In calibration, we are able to add tripwires to enable counting.

1) Draw the tripwire

  Draw a line segment that defines the region where we want to detect if objects have crossed. Make sure that the polyline has only 2 points. When we are drawing, we want to draw line segments that correlate to the direction that objects will be entering the tripwire. This line must intersect the tripwire line. The 0 Point is be outside the zone, and the 1 point will be across the trip wire. Make sure that the polyline has only 2 points as shown in the above image.


Export
------------

  .. image:: ../content/Calibration-Export_GIS.png
      :align: center
      :alt: Export Step 1

Export Camera Calibration
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Click `Export Camera Calibration` to generate the ``calibration.json``. It will include all validated sensors in your project. The file will download to the `Downloads` folder on the user's computer.

Export RoadLink Network
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For GIS Calibration only, click `Generate Intersection Road Links` to generate the ``network.json``. It will include all validated intersections and their respective road links, which will be populated in the UI. The file will download to the `Downloads` folder on the user's computer.

Export Image Metadata
~~~~~~~~~~~~~~~~~~~~~~~~

Click `Export Image Metadata` to generate the imageMetadata.json and the images, which includes all the sensors and the associated details needed for the MDX Web API. The file will download to the `Downloads` folder on the user's computer.

Export Images
~~~~~~~~~~~~~~~~~~~~~~~~

Click `Export Image Metadata` to generate the images, which includes all the sensors and the associated details needed for the MDX Web API. The file will download to the `Downloads` folder on the user's computer.


Consuming Calibration Toolkit Artifacts
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Add the `calibration.json`, `network.json`, etc using the web-api `/config/upload-files` endpoint. It inserts the config file into web-api. For `MDX Analytics Stream`, these artifacts are passed via command-line to transforming image coordinates to geo/cartesian coordinates.

For more information on consuming the sensor calibration see :doc:`MDX_Web_API` and :doc:`MDX_Analytics_Stream`.
