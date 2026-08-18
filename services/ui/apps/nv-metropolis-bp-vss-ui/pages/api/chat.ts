// SPDX-License-Identifier: MIT
export { chatApiHandler as default } from '@nv-metropolis-bp-vss-ui/chat/api';

export const config = {
  runtime: 'edge',
  api: { bodyParser: { sizeLimit: '5mb' } },
};
