// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { Component } from 'react';
import { Dropdown, Icon } from 'semantic-ui-react';

const trigger = (
  <span>
    <Icon name="user" /> user
  </span>
);

const options = [
  {
    key: 'user',
    text: (
      <span>
        Signed in as <strong>user</strong>
      </span>
    ),
    disabled: true
  },
  { key: 'profile', text: 'Your Profile' },
  { key: 'stars', text: 'Your Stars' },
  { key: 'explore', text: 'Explore' },
  { key: 'integrations', text: 'Integrations' },
  { key: 'help', text: 'Help' },
  { key: 'settings', text: 'Settings' },
  { key: 'sign-out', text: 'Sign Out' }
];

class MyPage extends Component {
  render() {
    return <Dropdown trigger={trigger} options={options} />;
  }
}

export default MyPage;
