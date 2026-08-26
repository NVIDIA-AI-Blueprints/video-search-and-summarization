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

class MTMCLeftMenu extends Component {
  constructor(props) {
    super(props);
    this.state = {
      activeMenu: 'dashboard'
    };
  }

  render() {
    const { projectId } = this.props;
    // const menus = [
    //   {
    //     name: '1.) Upload Floor Plan',
    //   },
    //   {
    //     name: '2.) Setup Sensors',
    //     // icon: 'checkmark box',
    //     submenus: [
    //       { name: 'input' },
    //       { name: 'range-picker' }
    //     ]
    //   },
    //   {
    //     name: '3.) Setup Place Hierachy',
    //     // icon: 'sitemap',
    //   },
    //   {
    //     name: '4.) Setup Floor Plan',
    //     // icon: 'calendar check',
    //   },
    //   {
    //     name: '5.) Calibrate Sensors',
    //     // icon: 'grid layout',
    //   },
    //   {
    //     name: '6.) Export',
    //     // icon: 'bar chart',
    //   }
    // ];

    const menus = [
        {
          name: '1.) Upload Floor Plan',
          link: `/projects/mtmc/uploadFloorPlan/${projectId}`,
          // onClick: this.toggleProjectModal,
          key: "upload"
        },
        {
          name: '2.) Discover Sensors',
          link: `/projects/mtmc/discoverSensors/${projectId}`,
          // icon: 'checkmark box',
          // submenus: [
          //   { name: 'input' },
          //   { name: 'range-picker' }
          // ],
          key: "discover"
        },
        {
          name: '3.) Setup Sensors',
          link: `/projects/mtmc/setupSensors/${projectId}`,
          // icon: 'sitemap',
          key: "setupSensors"

        },
        {
          name: '4.) Setup Floor Plan',
          link: `/projects/mtmc/setupFloorplan/${projectId}`,
          // icon: 'calendar check',
          key: "setupFloorplan"

        },
        {
          name: '5.) Calibrate Sensors',
          link: `/projects/mtmc/calibrateSensors/${projectId}`,
          // icon: 'grid layout',
          key: "calibration"
        },
        {
          name: '6.) Export',
          link: `/projects/mtmc/exportSensors/${projectId}`,
          // icon: 'bar chart',
          key: "exportSensors"
        }
      ]

    return (
      <div>
        {/* <Header menu={this.state.activeMenu} /> */}
        <div className="left-menus">
          {menus.map(item => {
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
                  <span>{item.name}</span>
                </Link>
              )
            // }
          })}
        </div>
      </div>
    );
  }
}

export default MTMCLeftMenu;

MTMCLeftMenu.propTypes = {
//   /** list of dicts to display*/
//   menus: PropTypes.array.isRequired,
  /** ID of the project to add the project to */
  projectId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]).isRequired,
//   navigate: PropTypes.func.isRequired

};