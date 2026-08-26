/*
 * SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: LicenseRef-NvidiaProprietary
 *
 * NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
 * property and proprietary rights in and to this material, related
 * documentation and any modifications thereto. Any use, reproduction,
 * disclosure or distribution of this material and related documentation
 * without an express license agreement from NVIDIA CORPORATION or
 * its affiliates is strictly prohibited.
 */


import React, { Fragment } from "react";
import { Route, Switch, BrowserRouter as Router, Redirect } from "react-router-dom";
import CalibrationLoader from "./components/calibration_tool/CalibrationLoader";
import CartesianLoader from "./components/cartesian_calibration/CartesianLoader";
import CartesianValidationLoader from "./components/cartesian_validation/CartesianValidationLoader"
import ImageCalibrationLoader from "./components/image_calibration/ImageCalibrationLoader";
import FloorPlanCalibrationLoader from "./components/floorplan_calibration/FloorPlanCalibrationLoader";
import FloorPlanValidationLoader from "./components/floorplan_validation/FloorPlanValidationLoader"
import LinksDrawingLoader from "./components/road_links_tool/LinksDrawingLoader";
import LinksValidationLoader from "./components/links_validation_tool/LinksValidationLoader";
import ValidationLoader from "./components/validation_tool/ValidationLoader";
import ProjectPage from "./components/projects/ProjectPage";
import MTMCProjectPage from "./components/pages/mtmc/MTMCProjectPage";
import UploadFloorPlanProjectImagePage from "./components/pages/mtmc/UploadImage/UploadFloorPlanProjectImagePage";
import DiscoverSensorsPage from "./components/pages/mtmc/DiscoverSensors/DiscoverSensorsPage";
import FloorplanProjectPage from "./components/projects/ProjectPage";
import CityPage from "./components/projects/CityPage";
import Homepage from "./components/common/Homepage";
import Help from "./components/help/Help";
import JSONSchema from "./components/help/JSONSchema";
import UsageOverview from "./components/help/UsageOverview";
import CorridorsLoader from "./components/corridors/CorridorsLoader";
import SetupFloorPlanLoader from "./components/mtmc/SetupFloorPlanLoader";
import SetupSensorsPage from "./components/pages/mtmc/SetupSensors/SetupSensorsPage";
import CalibrateSensorsPage from "./components/pages/mtmc/CalibrateSensors/CalibrateSensorsPage";
import ExportSensorsPage from "./components/pages/mtmc/ExportSensors/ExportSensorsPage";
import GISProjectPage from "./components/pages/gis/GISProjectPage";
import CartesianProjectPage from "./components/pages/cartesian/CartesianProjectPage";
import GISDiscoverSensorsPage from "./components/pages/gis/GISDiscoverSensors/GISDiscoverSensorsPage";
import GISExportSensorsPage from "./components/pages/gis/GISExportSensors/GISExportSensorsPage";
import GISSetupSensorsPage from "./components/pages/gis/GISSetupSensors/GISSetupSensorsPage";
import GISCalibrateSensorsPage from "./components/pages/gis/GISCalibrateSensors/GISCalibrateSensorsPage";
import GISSetupProjectPage from "./components/pages/gis/GISSetupProject/GISSetupProjectPage";
import GISGenerateRoadLinksPage from "./components/pages/gis/GISGenerateRoadLinks/GISGenerateRoadLinksPage";
import GISSetupCorridorsPage from "./components/pages/gis/GISSetupCorridors/GISSetupCorridorsPage";


import CartesianDiscoverSensorsPage from "./components/pages/cartesian/CartesianDiscoverSensors/CartesianDiscoverSensorsPage";
import CartesianSetupOriginPage from "./components/pages/cartesian/CartesianSetupOrigin/CartesianSetupOriginPage";
import CartesianCalibrateSensorsPage from "./components/pages/cartesian/CartesianCalibrateSensors/CartesianCalibrateSensorsPage";
import CartesianExportSensorsPage from "./components/pages/cartesian/CartesianExportSensors/CartesianExportSensorsPage";
import CartesianSetupSensorsPage from "./components/pages/cartesian/CartesianSetupSensors/CartesianSetupSensorsPage";
import ImageProjectPage from "./components/pages/image/ImageProjectPage";
import ImageDiscoverSensorsPage from "./components/pages/image/ImageDiscoverSensors/ImageDiscoverSensorsPage";
import ImageCalibrateSensorsPage from "./components/pages/image/ImageCalibrateSensors/ImageCalibrateSensorsPage";
import ImageExportSensorsPage from "./components/pages/image/ImageExportSensors/ImageExportSensorsPage";
import ImageSetupSensorsPage from "./components/pages/image/ImageSetupSensors/ImageSetupSensorsPage";
import { prod } from "mathjs";


var prodBaseUrlPath = window.location.pathname;
console.log ("test", window.location.pathname, window)
// if (this.history.location.path === "/" && this.history.location.path !== prodBaseUrlPath) {
//   console.log("mismatch in prodbaseurl")
//   prodBaseUrlPath = '';
// }
// if path was empty, don't prepend anything to the new paths
if (prodBaseUrlPath === '/' || window.location.port === "3000" || window.location.port === "8003") {
  prodBaseUrlPath = '';
}
else if (prodBaseUrlPath.includes("/calibration")) { // why did we only do this?
  prodBaseUrlPath = window.location.pathname.split("/calibration")[0] +  "/calibration"
}

class DebugRouter extends Router {
  constructor(props){
    super(props);
    console.log('initial history is: ', JSON.stringify(this.history, null,2))
    this.history.listen((location, action)=>{
      console.log(
        `The current URL is ${location.pathname}${location.search}${location.hash}`
      )
      console.log(`The last navigation action was ${action}`, JSON.stringify(this.history, null,2));
    });
  }
}
console.log(prodBaseUrlPath)
/**
 * Main app handling the routes of the URL paths.
 */
 class App extends React.Component {
  render() {
    return (
      <DebugRouter basename={prodBaseUrlPath}>
        <Switch>
          <Route exact path="/help/schema/" component={JSONSchema} />
          <Route exact path="/help/overview/" component={UsageOverview} />
          <Route exact path="/help/" component={Help} />

          {/* <Route exact path="/projects/:projectId" component={ProjectPage} /> */}

          <Route exact path="/projects/mtmc/uploadFloorPlan/:projectId" component={UploadFloorPlanProjectImagePage} />
          <Route exact path="/projects/mtmc/discoverSensors/:projectId" component={DiscoverSensorsPage} />
          <Route exact path="/projects/mtmc/setupSensors/:projectId" component={SetupSensorsPage} />
          <Route exact path="/projects/mtmc/setupFloorplan/:projectId" component={SetupFloorPlanLoader} />
          <Route exact path="/projects/mtmc/calibrateSensors/:projectId" component={CalibrateSensorsPage} />
          <Route exact path="/projects/mtmc/exportSensors/:projectId" component={ExportSensorsPage} />
          <Route exact path="/projects/mtmc/:projectId" component={MTMCProjectPage} />

          <Route exact path="/projects/geo/discoverSensors/:projectId" component={GISDiscoverSensorsPage} />
          <Route exact path="/projects/geo/setupSensors/:projectId" component={GISSetupSensorsPage} />
          <Route exact path="/projects/geo/calibrateSensors/:projectId" component={GISCalibrateSensorsPage} />
          <Route exact path="/projects/geo/exportSensors/:projectId" component={GISExportSensorsPage} />
          <Route exact path="/projects/geo/setupProject/:projectId" component={GISSetupProjectPage} />
          <Route exact path="/projects/geo/generateRoadLinks/:projectId" component={GISGenerateRoadLinksPage} />
          <Route exact path="/projects/geo/setupCorridors/:projectId" component={GISSetupCorridorsPage} />
          <Route exact path="/projects/geo/:projectId" component={GISProjectPage} />


          <Route exact path="/projects/cartesian/discoverSensors/:projectId" component={CartesianDiscoverSensorsPage} />
          <Route exact path="/projects/cartesian/setupOrigin/:projectId" component={CartesianSetupOriginPage} />
          <Route exact path="/projects/cartesian/setupSensors/:projectId" component={CartesianSetupSensorsPage} />
          <Route exact path="/projects/cartesian/calibrateSensors/:projectId" component={CartesianCalibrateSensorsPage} />
          <Route exact path="/projects/cartesian/exportSensors/:projectId" component={CartesianExportSensorsPage} />
          <Route exact path="/projects/cartesian/:projectId" component={CartesianProjectPage} />

          <Route exact path="/projects/image/discoverSensors/:projectId" component={ImageDiscoverSensorsPage} />
          <Route exact path="/projects/image/setupSensors/:projectId" component={ImageSetupSensorsPage} />
          <Route exact path="/projects/image/calibrateSensors/:projectId" component={ImageCalibrateSensorsPage} />
          <Route exact path="/projects/image/exportSensors/:projectId" component={ImageExportSensorsPage} />
          <Route exact path="/projects/image/:projectId" component={ImageProjectPage} />

          <Route exact path="/projects/floorplan/:projectId" component={FloorplanProjectPage} />
          {/* <Route exact path="/projects/:projectId/:cityId" component={CityPage}/> */}
          <Route exact path="/calib/geo/:sensorId" component={CalibrationLoader}/>
          <Route exact path="/calib/cartesian/:sensorId" component={CartesianLoader}/>
          <Route exact path="/calib/floorplan/:sensorId" component={FloorPlanCalibrationLoader}/>
          <Route exact path="/calib/image/:sensorId" component={ImageCalibrationLoader}/>

          <Route exact path="/links/intersection/:intersectionId/" component={LinksDrawingLoader}/>
          <Route exact path="/links/:type/validation/:id" component={LinksValidationLoader}/>
          <Route exact path="/validation/geo/:sensorId" component={ValidationLoader}/>
          <Route exact path="/validation/cartesian/:sensorId" component={CartesianValidationLoader}/>
          <Route exact path="/validation/floorplan/:sensorId" component={FloorPlanValidationLoader}/>
          <Route exact path="/corridor/:projectId/:corridorId" component={CorridorsLoader}/>
          <Route exact path="/" component={Homepage} />
          <Redirect to="/"  component={Homepage}/>

          {/* <Route render={() => <Redirect to="/" />} /> */}
        </Switch>
      </DebugRouter>
    );
  }
}

export default App;
