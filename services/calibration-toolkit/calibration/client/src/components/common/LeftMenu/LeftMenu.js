// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { Component } from 'react';
import { Link } from 'react-router-dom';
import { Icon } from 'semantic-ui-react';
import Header from '../Header/Header';
import PropTypes from "prop-types";

import './LeftMenu.css';

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

class LeftMenu extends Component {
  constructor(props) {
    super(props);
    this.state = {
      activeMenu: 'dashboard'
    };
  }

  render() {
    const { projectId, menus } = this.props;

    // const menus = [
    //   {
    //     name: 'dashboard',
    //     icon: 'inbox',
    //   },
    //   {
    //     name: 'form',
    //     icon: 'checkmark box',
    //     submenus: [
    //       { name: 'input' },
    //       { name: 'range-picker' }
    //     ]
    //   },
    //   {
    //     name: 'dropdown',
    //     icon: 'sitemap',
    //   },
    //   {
    //     name: 'calendar',
    //     icon: 'calendar check',
    //   },
    //   {
    //     name: 'layout',
    //     icon: 'grid layout',
    //   },
    //   {
    //     name: 'chart',
    //     icon: 'bar chart',
    //   }
    // ];

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
                  onClick={() => this.setState({ activeMenu: item.name })}
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

export default LeftMenu;

LeftMenu.propTypes = {
  /** list of dicts to display*/
  menus: PropTypes.array.isRequired,
  /** ID of the project to add the project to */
  projectId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]).isRequired,

};