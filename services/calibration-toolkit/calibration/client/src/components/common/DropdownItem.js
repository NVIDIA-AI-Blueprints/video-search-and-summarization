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

import React from "react";
import PropTypes from "prop-types";
import { Dropdown } from "semantic-ui-react";
import { useHistory } from "react-router-dom";

/**
 * Dropdown item used in the menubar for listing and selecting different
 * projects
 */
function DropdownItem(props) {
  const { project, index, active } = props;
  const history = useHistory();

  function handleClick(path) {
    history.push(path);
  }

  return (
    <Dropdown.Item
      icon="road"
      color="green"
      key={index}
      text={
        project.value.length > 20
          ? project.value.substring(0, 16).concat("...")
          : project.value
      }
      active={Number(active) === Number(project.key)}
      onClick={() => handleClick(`/projects/${project.key}`)}
      data-testid={`${project.value}`}
    />
  );
}

export default DropdownItem;

DropdownItem.propTypes = {
  /** Project information to dipslay in the dropdown item */
  project: PropTypes.object.isRequired,
  /** Number to use as key of dropdown item*/
  index: PropTypes.number.isRequired,
  /** Number respresenting the active project ID */
  active: PropTypes.oneOfType([PropTypes.string, PropTypes.number]).isRequired
};
