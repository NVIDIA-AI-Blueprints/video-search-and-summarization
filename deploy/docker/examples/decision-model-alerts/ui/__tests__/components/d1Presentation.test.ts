// SPDX-FileCopyrightText: Copyright (c) 2026 D1 project contributors
// SPDX-License-Identifier: Apache-2.0
import { describeEventWindow, describeTrigger, eventTriggerQuestion, matchIncident } from '../../components/d1Presentation';

const savedIncident = () => ({
  id: 'saved-event', stream_id: 'traffic-recorded', rule_revision: 2,
  rule: { stream_id: 'traffic-recorded', config_revision: 1, field: 'noul', operator: 'gte', value: 0.8, consecutive_samples: 3, pre_seconds: 15, post_seconds: 5 },
  trigger: { config_revision: 1, question: { instructions: 'Are vehicles stopped or crawling?' }, answer: { noul: 0.97 } },
  decision_ids: ['first', 'second', 'third'],
});

describe('D1 decision model event presentation provenance', () => {
  it('matches both event identity and source, even when another source uses the same event ID', () => {
    const incident = savedIncident();
    const otherSource = { ...incident, stream_id: 'warehouse' };
    expect(matchIncident({ id: incident.id, stream_id: incident.stream_id }, [otherSource, incident])).toBe(incident);
    expect(matchIncident({ id: incident.id, stream_id: incident.stream_id }, [otherSource])).toBeNull();
    expect(matchIncident({ id: 'missing', stream_id: incident.stream_id }, [incident])).toBeNull();
    expect(matchIncident({}, [{}])).toBeNull();
    expect(matchIncident(null, [])).toBeNull();
    expect(matchIncident(incident, null as any)).toBeNull();
  });

  it('explains the saved threshold and question after current settings have changed', () => {
    const incident = savedIncident();
    const currentConfig = { ...incident.rule, value: 0.95, consecutive_samples: 5 };
    const currentQuestion = 'Is the road empty?';
    expect(describeTrigger(incident)).toBe('The D1 decision model’s yes score was at least 80% for 3 consecutive checks.');
    expect(eventTriggerQuestion(incident)).toBe('Are vehicles stopped or crawling?');
    expect(describeTrigger(incident)).not.toContain('95%');
    expect(eventTriggerQuestion(incident)).not.toBe(currentQuestion);
    expect(currentConfig.consecutive_samples).toBe(5);
  });

  it('preserves fractional probability thresholds without treating ordinary scores as percentages', () => {
    const incident = savedIncident();
    expect(describeTrigger({ ...incident, rule: { ...incident.rule, value: 0.825, operator: 'lte' } })).toBe('The D1 decision model’s yes score was at most 82.5% for 3 consecutive checks.');
    expect(describeTrigger({ ...incident, rule: { ...incident.rule, value: 0.803 } })).toBe('The D1 decision model’s yes score was at least 80.3% for 3 consecutive checks.');
    expect(describeTrigger({ ...incident, rule: { ...incident.rule, field: 'score', value: 0.825, operator: 'lte' } })).toBe('The D1 decision model’s score was at most 0.825 for 3 consecutive checks.');
    expect(describeTrigger({ ...incident, rule: { ...incident.rule, field: 'confidence', value: 0.9, operator: 'eq', consecutive_samples: 1 } })).toBe('The D1 decision model’s confidence was exactly 90% for 1 check.');
    expect(describeTrigger({ ...incident, rule: { ...incident.rule, field: 'probability', probability_key: 'stopped', value: 0.7 } })).toBe('The D1 decision model’s probability for “stopped” was at least 70% for 3 consecutive checks.');
    expect(describeTrigger({ ...incident, rule: { ...incident.rule, field: 'choice', operator: 'eq', value: 'stopped' } })).toBe('The D1 decision model chose “stopped” for 3 consecutive checks.');
  });

  it('returns no explanation for absent, invalid, or mismatched trigger metadata', () => {
    const incident = savedIncident();
    for (const rule of [null, {}, [], { ...incident.rule, field: 'unknown' }, { ...incident.rule, operator: 'contains' }, { ...incident.rule, operator: 'toString' },
      { ...incident.rule, value: '0.8' }, { ...incident.rule, value: Infinity }, { ...incident.rule, value: 1.01 },
      { ...incident.rule, consecutive_samples: 0 }, { ...incident.rule, consecutive_samples: 2.5 },
      { ...incident.rule, field: 'probability' }, { ...incident.rule, field: 'choice', value: 'busy' },
      { ...incident.rule, stream_id: 'warehouse' }, { ...incident.rule, config_revision: 2 }]) {
      expect(describeTrigger({ ...incident, rule })).toBeNull();
    }
    expect(describeTrigger(undefined)).toBeNull();
    expect(eventTriggerQuestion({ question: { instructions: 'Current question must not be substituted' } })).toBeNull();
    expect(eventTriggerQuestion({ trigger: { question: { instructions: '  ' } } })).toBeNull();
    expect(eventTriggerQuestion({ trigger: { question: { instructions: 'Original\nquestion?' } } })).toBe('Original\nquestion?');
  });

  it('describes the requested window from saved pre/post values, not actual media duration', () => {
    const incident = savedIncident();
    expect(describeEventWindow({ ...incident, clip_start: 0, clip_end: 20.233 })).toBe('15 seconds before + 5 seconds after the event');
    expect(describeEventWindow({ rule: { pre_seconds: 1, post_seconds: 2.5 } })).toBe('1 second before + 2.5 seconds after the event');
    expect(describeEventWindow({ rule: { pre_seconds: 0, post_seconds: 5 } })).toBe('0 seconds before + 5 seconds after the event');
    for (const rule of [{}, { pre_seconds: -1, post_seconds: 5 }, { pre_seconds: 0, post_seconds: 0 },
      { pre_seconds: NaN, post_seconds: 5 }, { pre_seconds: '15', post_seconds: 5 }, { pre_seconds: 121, post_seconds: 5 }]) {
      expect(describeEventWindow({ rule })).toBeNull();
    }
    expect(describeEventWindow(null)).toBeNull();
  });
});
