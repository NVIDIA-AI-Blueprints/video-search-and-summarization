
tasks

~~- create edit project~~
~~- switch project~~
- create import project
- scale factor
  ~~\* create but~~
  - drawing function
  - backend
- image calibration
- image validation
  ~~\* fix sensor map placement~~
  - fix color
- make sure lng for GIS
  ~~\* change port for django~~
- figure out how to make React dynamic port
- fix all city calibration type to project calibrationtype
- tool tips
- ui change
- remove console.log
- remove unused-vars
- fix drawing trip lines
- fix validation for approximate
- refactor loader/apps
- remove homography stats for floorplan/ cartesian/mtmc
- create advanced config for sensors
- add arrows for tripwire trip lines https://stackoverflow.com/questions/53307322/leaflet-polyline-arrows
- add sensor count calibrated/validated
- add searchable table
- render tabs for corridors/intersections only for GIS
- remove city
- will get a blank calibration json with empty strings/ array
- change corridor to new schema
- MTMC Calibration
- get rid of localhost
- remove doctype in calibration.json
- remove city in calibration.json
- redo corridors export
- be able to edit roi/tripwire/direction/ids
- change trip wire lines to arrows
- import project

NvStreamer/Vst Integration and cleaning up old code
Ability to create multiple projects
Ability to save, import and edit a project
Any other inputs from Milind and
image calibration -- image utility
once you upload floorplan, display floorplan or show that floor plan has been uploaded.

1.  upload FP
2.  setup sensors - discover sensors OR manually add sensors
    1. show grid of sensors that have been added
    2. and then + sensor to add new sensor
3.  in floorplan setup
    1. change Scale Factor to "Pixel to Map scale"
4.  Instead of close goes to next.
5.  highlight active tab
6.  Separate page for calibraitng sensors. filterable table of calibrated/validated sensors (P1)
7.  "upload sensor image" button not calibration
8.  Pin calibration/tripwire/sensors to panel

    1.  Draw sensor coordinate on calibration page.
    2.  be able to edit coordinate. (p1)
    3.  FP -> Floor Plan Calibration
    4.  Calibration -> Image Calibration
    5.  Draw Corresponding Polygons on Floor Plan and Camera
    6.  when you click icon, give text header saying what to do
    7.  Animations
        1. show FP calibration
        2. show calibration
    8.  buttons correspond to panel
    9.        4)  get rid of calibration error

    10. Best Practices
    11. add details
    12. more points better,
    13. Validation is the key to best way to fix Calibrating
    14.
    15. video
    16. Rules

9.  best practices for validation

    1. Draw points on your sensor image, and see if it corresponds to floor plan view.

10.

what to do with edit project and remove all calibrations.
use switch routing instead of having all paths in app.js
~~check that image paths are consistent with multiple projects~~
~~fix upload floor plan images~~
~~fix image csv~~
homography for mtmc
~~image download and json~~
nginx

GIS

- intersection/city/corridor
- map api
- osm
- roadlinks
- corridor sensors
- calibration refresh?

Cartesian
~~\* fix validation for cartesian~~

~~1) Image~~

1. create place hierarchy -2
2. scale factor -line - 1
3. upload images web api - 2
4. import calib jsonp - 2
5. upsert/insert web api - 2
~~6. build city osm modal .5~~
~~7. check roadnetwork 1hr~~
~~8. check gis output~~
~~9. check corridors - .5hr~~
10. check cartesian output
~~11. check image output - .5~~
12. show only sensors from that project??
13. remove major/minor roads?x
14. change osm download/ roadlinks download to within project
15. rename ROI/Tripwire

allow same sensor name diferent project??

~~1) GIS app~~
2) import calibration


import calibration
check scale factor
upload calibration to webapi
upload imagedata to webpai