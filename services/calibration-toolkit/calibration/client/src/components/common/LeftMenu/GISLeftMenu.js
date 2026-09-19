// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { Component } from 'react';
import { Link } from 'react-router-dom';
import { Icon } from 'semantic-ui-react';
import Header from '../Header/Header';
import './LeftMenu.css';
import PropTypes from "prop-types";


// class SubMenu extends Component {
//   render() {
//     let subMenu = this.props.submenu;

//     if (subMenu !== null) {
//       return (
//         <div>
//           {subMenu.map(submenu => {
//             return (
//               <div key={submenu.name} className="sub-menu">
//                 <Link to={submenu.name}>
//                   <Icon name="plus" size="small"/>
//                   <span>{submenu.name}</span>
//                 </Link>
//               </div>
//             );
//           })}
//         </div>
//       );
//     } else {
//       return <div></div>
//     }
//   }
// }

class GISLeftMenu extends Component {
  constructor(props) {
    super(props);
    this.state = {
      activeMenu: 'dashboard'
    };
  }

  render() {
    const { projectId } = this.props;


    const menus = [
        {
          name: 'Setup Project',
          link: `/projects/geo/setupProject/${projectId}`,
          // icon: 'checkmark box',
          // submenus: [
          //   { name: 'input' },
          //   { name: 'range-picker' }
          // ],
          key: "setupProject"
        },
        // {
        //   name: 'Setup Places',
        //   link: `/projects/geo/setupPlaces/${projectId}`,
        //   // icon: 'calendar check',
        //   key: "setupFloorplan"
        // },
        {
          name: 'Discover Sensors',
          link: `/projects/geo/discoverSensors/${projectId}`,
          // icon: 'checkmark box',
          // submenus: [
          //   { name: 'input' },
          //   { name: 'range-picker' }
          // ],
          key: "discover"
        },
        // {
        //   name: 'Setup Sensors',
        //   link: `/projects/geo/setupSensors/${projectId}`,
        //   // icon: 'sitemap',
        //   key: "setupSensors"

        // },

        {
          name: 'Calibrate Sensors',
          link: `/projects/geo/calibrateSensors/${projectId}`,
          // icon: 'grid layout',
          key: "calibration"
        },

        {
          name: 'Generate Road Links',
          link: `/projects/geo/generateRoadLinks/${projectId}`,
          // icon: 'grid layout',
          key: "calibration"
        },
        // {
        //   name: 'Setup Corridors',
        //   link: `/projects/geo/setupCorridors/${projectId}`,
        //   // icon: 'grid layout',
        //   key: "calibration"
        // },
        {
          name: 'Export',
          link: `/projects/geo/exportSensors/${projectId}`,
          // icon: 'bar chart',
          key: "exportSensors"
        }
      ]

    return (
      <div>
        {/* <Header menu={this.state.activeMenu} /> */}
        <div className="left-menus">
          {menus.map((item,idx) => {
            // console.log ("gis", idx+1)
            const index = `${idx+1}.) `
            // if (item.submenus) {
            //   return (
            //     <div key={item.name}
            //       className={this.state.activeMenu === item.name ? 'menu active' : 'menu' }
            //       onClick={() => this.setState({ activeMenu: item.name })}>
            //         <Icon name={item.icon} size="large"/>
            //         <span>{item.name}</span>
            //         <Icon name={this.state.activeMenu === item.name ? "angle up" : "angle down" }/>
            //       <div className="">
            //         <div className={ 'sub-menu-container ' +
            //             (this.state.activeMenu === item.name ? 'active' : '') } >
            //           <SubMenu submenu={item.submenus} menu={item} />
            //         </div>
            //       </div>
            //     </div>
            //   )
            // } else {
              return (
                <Link to={item.link} name={item.name} key={item.name}
                  className={this.state.activeMenu === item.name ? 'menu active' : 'menu' }
                  onClick={() => this.setState({ activeMenu: item.name }) && this.props.openModule(item.key)}
                  >
                  {/* <Icon name={item.icon} size="large"/> */}
                  <span>{index}{item.name}</span>
                </Link>
              )
            // }
          })}
        </div>
      </div>
    );
  }
}

export default GISLeftMenu;

GISLeftMenu.propTypes = {
//   /** list of dicts to display*/
//   menus: PropTypes.array.isRequired,
  /** ID of the project to add the project to */
  projectId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]).isRequired,
//   navigate: PropTypes.func.isRequired

};