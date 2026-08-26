// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import React from 'react';
import { Icon, Dropdown } from 'semantic-ui-react';
import './Notification.css';

export default function Notification ({ icon, numOfNew }) {
  const menuDropdownStyle = {
    marginRight: '-65px'
  };
  const iconStyle = {
    margin: '0 10px 0 0'
  };
  let newNotification = (
    <div className="new-notification">
      <span>{numOfNew}</span>
    </div>
  );
  const trigger = (
    <div>
      <Icon name={icon} size="large" style={iconStyle} />
      {numOfNew > 0 ? newNotification : null}
    </div>
  );

  return (
    <Dropdown trigger={trigger} icon={false} pointing>
      <Dropdown.Menu style={menuDropdownStyle}>
        <Dropdown.Header icon="tags" content="Filter by tag" />
        <Dropdown.Divider />
        <Dropdown.Item>
          <Icon name="attention" className="right floated" />
          Important
        </Dropdown.Item>
        <Dropdown.Item>
          <Icon name="comment" className="right floated" />
          Announcement
        </Dropdown.Item>
        <Dropdown.Item>
          <Icon name="conversation" className="right floated" />
          Discussion
        </Dropdown.Item>
      </Dropdown.Menu>
    </Dropdown>
  );
}

