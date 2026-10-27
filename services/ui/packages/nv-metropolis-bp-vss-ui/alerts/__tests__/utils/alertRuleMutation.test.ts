// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import { isSuccessfulAlertRuleMutation } from '../../lib-src/utils/alertRuleMutation';

describe('isSuccessfulAlertRuleMutation', () => {
  it.each([
    [
      'NAT start restated in the screenshot',
      'Real-time VLM alert successfully started for sensor sample-warehouse-ladder.mp4.\nAlert Rule ID: f190b51f-e8b7-40c9-9b6e-739f461ba925',
    ],
    [
      'NAT stop restated in the screenshot',
      'The real-time alert for sensor sample-warehouse-ladder.mp4 has been successfully stopped.\nAlert rule ID: f190b51f-e8b7-40c9-9b6e-739f461ba925',
    ],
    ['NAT start tool message', 'Real-time VLM alert started for sensor warehouse_sample.'],
    ['NAT stop tool message', 'Real-time VLM alert stopped for sensor warehouse_sample.'],
    ['already stopped', 'Alert for sensor warehouse_sample was already stopped.'],
    [
      'OpenClaw create',
      'Done. Realtime alert `496aebd1-16d0-4123-81cf-10603e047d02` is live on **warehouse-dock-1** (tag: `ppe_vest_violation`).',
    ],
    [
      'OpenClaw delete',
      'Done. Alert `ppe_vest_violation` on **warehouse-dock-1** has been stopped (rule `496aebd1-16d0-4123-81cf-10603e047d02`).',
    ],
    ['API create copy', 'Realtime alert rule created'],
    ['API delete copy', 'Realtime alert rule deleted'],
    ['modified phrasing', 'The realtime alert rule on warehouse_sample has been updated.'],
  ])('accepts %s', (_label, answer) => {
    expect(isSuccessfulAlertRuleMutation(answer)).toBe(true);
  });

  it.each([
    ['empty', ''],
    ['ordinary chat', 'Hello, how can I help you today?'],
    ['incident listing', 'Found 5 incidents for sensor warehouse_sample.'],
    ['rule inventory', 'The following realtime alert rules are currently running:\n- PPE on dock-1'],
    ['empty inventory', 'No realtime alert rules are currently running.'],
    [
      'stop confirmation',
      'Stop alert `ppe_vest_violation` on **warehouse-dock-1**? (rule ID: `496aebd1-16d0-4123-81cf-10603e047d02`) — yes/no',
    ],
    ['create failure', 'Failed to create alert rule (HTTP 502): rtvi_vlm_unavailable'],
    ['skill create 4xx', 'Could not create the alert rule — the request was rejected.'],
    ['missing sensor', "Sensor 'missing' was not found. Available sensors: warehouse_sample."],
    ['nothing to stop', 'That rule is no longer active — nothing to stop.'],
    ['declined stop', 'OK, the rule stays active.'],
    ['no matching rule', 'No matching rule found for ppe on **warehouse-dock-1**.'],
    ['Slack webhook stop', 'Alert Slack webhook has been stopped.'],
    ['negated start', 'The real-time alert was not started.'],
    ['negated has not been', 'The real-time VLM alert has not been started.'],
  ])('rejects %s', (_label, answer) => {
    expect(isSuccessfulAlertRuleMutation(answer)).toBe(false);
  });

  it('ignores reasoning traces and artifacts when deciding', () => {
    const wrapped = [
      '<agent-think>I will start a real-time alert now.</agent-think>',
      '<vss-ui-artifact>{"version":"1.0","kind":"vss.alert.incidents","payload":{"incidents":[]}}</vss-ui-artifact>',
      'Real-time VLM alert started for sensor warehouse_sample.',
    ].join('\n');
    expect(isSuccessfulAlertRuleMutation(wrapped)).toBe(true);
  });
});
