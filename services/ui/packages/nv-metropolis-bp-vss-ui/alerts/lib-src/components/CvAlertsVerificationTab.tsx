// SPDX-License-Identifier: MIT
import React, { useCallback, useMemo, useState } from 'react';
import { Button, TextInput } from '@nvidia/foundations-react-core';
import {
  IconAlertCircle,
  IconCheck,
  IconChevronDown,
  IconChevronUp,
  IconCopy,
  IconDeviceFloppy,
  IconEdit,
  IconLoader2,
  IconPlus,
  IconRefresh,
  IconTrash,
  IconX,
} from '@tabler/icons-react';
import {
  CreateCvAlertsVerificationConfigInput,
  useCvAlertsVerificationConfigs,
} from '../hooks/useCvAlertsVerificationConfigs';
import {
  VerificationAlertConfig,
  VerificationAlertConfigDraft,
  VerificationAlertConfigUpdate,
} from '../types';

interface CvAlertsVerificationTabProps {
  isDark: boolean;
  alertsApiUrl?: string;
  visible: boolean;
}

type EditableFields = Pick<
  VerificationAlertConfigDraft,
  'alert_type' | 'output_category' | 'prompt' | 'enrichment_prompt'
> & {
  saving?: boolean;
  error?: string;
};

let draftSequence = 0;

const nextDraftId = (): string => {
  draftSequence += 1;
  return `verification-draft-${Date.now()}-${draftSequence}`;
};

const emptyDraft = (): VerificationAlertConfigDraft => ({
  draftId: nextDraftId(),
  alert_type: '',
  output_category: '',
  prompt: '',
  enrichment_prompt: '',
});

const editableFromConfig = (config: VerificationAlertConfig): EditableFields => ({
  alert_type: config.alert_type,
  output_category: config.output_category ?? '',
  prompt: config.prompt,
  enrichment_prompt: config.enrichment_prompt ?? '',
});

const validate = (fields: EditableFields): string | null => {
  const alertType = fields.alert_type.trim();
  const prompt = fields.prompt.trim();
  if (!alertType || !prompt) return 'Alert type and user prompt are required.';
  if (alertType.length > 100) return 'Alert type must be 100 characters or fewer.';
  if (fields.output_category.trim().length > 200) {
    return 'Output category must be 200 characters or fewer.';
  }
  for (const [label, value] of [
    ['User prompt', fields.prompt],
    ['Enrichment prompt', fields.enrichment_prompt],
  ]) {
    if (value.length > 5000) return `${label} must be 5000 characters or fewer.`;
  }
  return null;
};

const optional = (value: string): string | null => value.trim() || null;

const COLUMN_COUNT = 5;

const verificationAddDraftRef: { current: (() => void) | null } = { current: null };

export const triggerVerificationAddDraft = (): boolean => {
  const addDraft = verificationAddDraftRef.current;
  if (!addDraft) return false;
  addDraft();
  return true;
};

export const CvAlertsVerificationTab: React.FC<CvAlertsVerificationTabProps> = ({
  isDark,
  alertsApiUrl,
  visible,
}) => {
  const {
    configs,
    loading,
    error,
    lastRefreshedAt,
    refetch,
    createConfig,
    updateConfig,
    deleteConfig,
  } = useCvAlertsVerificationConfigs({ alertsApiUrl });
  const [drafts, setDrafts] = useState<VerificationAlertConfigDraft[]>([]);
  const [editing, setEditing] = useState<Record<string, EditableFields>>({});
  const [filter, setFilter] = useState('');
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set());

  const toggleExpanded = useCallback((rowKey: string, force?: boolean) => {
    setExpandedRows((current) => {
      const next = new Set(current);
      const shouldExpand = force ?? !next.has(rowKey);
      if (shouldExpand) next.add(rowKey);
      else next.delete(rowKey);
      return next;
    });
  }, []);

  const addDraft = useCallback(() => setDrafts((current) => [...current, emptyDraft()]), []);

  const discardDraft = useCallback((draftId: string) => {
    setDrafts((current) => current.filter((item) => item.draftId !== draftId));
  }, []);

  React.useEffect(() => {
    if (!visible) return;
    verificationAddDraftRef.current = addDraft;
    return () => {
      if (verificationAddDraftRef.current === addDraft) verificationAddDraftRef.current = null;
    };
  }, [addDraft, visible]);

  const thClass = `text-left py-2 px-3 text-xs uppercase tracking-wider font-semibold ${
    isDark ? 'text-neutral-400' : 'text-gray-600'
  }`;
  const optionalPanelClass = isDark
    ? 'bg-neutral-900/60 border-neutral-800'
    : 'bg-gray-50 border-gray-200';
  const optionalLabelClass = `text-xs uppercase tracking-wider font-semibold ${
    isDark ? 'text-neutral-400' : 'text-gray-600'
  }`;
  const optionalHintClass = `text-xs ${isDark ? 'text-neutral-500' : 'text-gray-500'}`;

  const visibleConfigs = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return configs;
    return configs.filter(
      (config) =>
        config.alert_type.toLowerCase().includes(needle) ||
        (config.output_category ?? '').toLowerCase().includes(needle),
    );
  }, [configs, filter]);

  const patchDraft = (
    draftId: string,
    patch: Partial<VerificationAlertConfigDraft>,
  ) => {
    setDrafts((current) =>
      current.map((draft) => (draft.draftId === draftId ? { ...draft, ...patch } : draft)),
    );
  };

  const saveDraft = async (draft: VerificationAlertConfigDraft) => {
    const validationError = validate(draft);
    if (validationError) {
      patchDraft(draft.draftId, { error: validationError });
      return;
    }
    patchDraft(draft.draftId, { saving: true, error: undefined });
    try {
      const payload: CreateCvAlertsVerificationConfigInput = {
        alert_type: draft.alert_type.trim(),
        output_category: optional(draft.output_category),
        prompt: draft.prompt.trim(),
      };
      const enrichment = optional(draft.enrichment_prompt);
      if (enrichment) payload.enrichment_prompt = enrichment;
      await createConfig(payload);
      setDrafts((current) => current.filter((item) => item.draftId !== draft.draftId));
    } catch (err) {
      patchDraft(draft.draftId, {
        saving: false,
        error: err instanceof Error ? err.message : 'Failed to create verification config',
      });
    }
  };

  const saveEdit = async (alertType: string) => {
    const fields = editing[alertType];
    if (!fields) return;
    const validationError = validate(fields);
    if (validationError) {
      setEditing((current) => ({
        ...current,
        [alertType]: { ...current[alertType], error: validationError },
      }));
      return;
    }
    setEditing((current) => ({
      ...current,
      [alertType]: { ...current[alertType], saving: true, error: undefined },
    }));
    const previousEnrichment = optional(
      configs.find((config) => config.alert_type === alertType)?.enrichment_prompt ?? '',
    );
    const nextEnrichment = optional(fields.enrichment_prompt);
    const update: VerificationAlertConfigUpdate = {
      output_category: optional(fields.output_category),
      prompt: fields.prompt.trim(),
    };
    if (nextEnrichment) update.enrichment_prompt = nextEnrichment;
    else if (previousEnrichment) update.enrichment_prompt = null;
    try {
      await updateConfig(alertType, update);
      setEditing((current) => {
        const next = { ...current };
        delete next[alertType];
        return next;
      });
    } catch (err) {
      setEditing((current) => ({
        ...current,
        [alertType]: {
          ...current[alertType],
          saving: false,
          error: err instanceof Error ? err.message : 'Failed to update verification config',
        },
      }));
    }
  };

  const removeConfig = async (alertType: string) => {
    setPendingDelete(null);
    setDeleting(alertType);
    setDeleteError(null);
    try {
      await deleteConfig(alertType);
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : 'Failed to delete verification config');
    } finally {
      setDeleting(null);
    }
  };

  const duplicate = (config: VerificationAlertConfig) => {
    const copy = editableFromConfig(config);
    setDrafts((current) => [
      ...current,
      { ...copy, draftId: emptyDraft().draftId, alert_type: `${copy.alert_type} copy` },
    ]);
  };

  const renderOptionalToggle = (rowKey: string, label: string, hasValues: boolean) => {
    const isExpanded = expandedRows.has(rowKey);
    return (
      <td className="py-2 px-3 align-top">
        <Button
          kind="tertiary"
          type="button"
          aria-label={`${isExpanded ? 'Hide' : 'Show'} optional settings for ${label}`}
          aria-expanded={isExpanded}
          data-testid="verification-optional-toggle"
          onClick={() => toggleExpanded(rowKey)}
        >
          <span className="inline-flex items-center gap-1.5 text-xs">
            {isExpanded ? (
              <IconChevronUp className="w-4 h-4" />
            ) : (
              <IconChevronDown className="w-4 h-4" />
            )}
            Optional
            {hasValues && (
              <span
                aria-hidden="true"
                data-testid="verification-optional-indicator"
                className="w-1.5 h-1.5 rounded-full bg-[#76b900]"
              />
            )}
          </span>
        </Button>
      </td>
    );
  };

  const renderOptionalPanel = (
    fields: EditableFields,
    update: ((patch: Partial<EditableFields>) => void) | null,
  ) => (
    <tr
      data-testid="verification-optional-row"
      className={`border-b ${isDark ? 'border-neutral-800' : 'border-gray-200'}`}
    >
      <td colSpan={COLUMN_COUNT} className="p-0">
        <div className={`m-3 rounded border px-4 py-3 ${optionalPanelClass}`}>
          <div className={`${optionalLabelClass} mb-2`}>Optional settings</div>
          <div className="flex flex-col gap-1 max-w-3xl">
            <span className={`text-sm font-medium ${isDark ? 'text-neutral-200' : 'text-gray-700'}`}>
              Enrichment prompt
            </span>
            {update ? (
              <TextInput
                aria-label="Enrichment prompt"
                data-testid="verification-enrichment-prompt"
                value={fields.enrichment_prompt}
                onValueChange={(value: string) => update({ enrichment_prompt: value })}
                placeholder="Ex: Describe what happened in this clip"
              />
            ) : (
              <div className="text-sm whitespace-pre-wrap">
                {fields.enrichment_prompt || 'Not set'}
              </div>
            )}
            <span className={optionalHintClass}>
              Leave empty to skip the follow-up VLM call after verification.
            </span>
          </div>
        </div>
      </td>
    </tr>
  );

  const renderFields = (
    fields: EditableFields,
    update: (patch: Partial<EditableFields>) => void,
    immutableAlertType: boolean,
  ) => (
    <>
      <td className="py-2 px-3 align-top">
        <TextInput
          aria-label="Alert type"
          data-testid="verification-alert-type"
          value={fields.alert_type}
          readOnly={immutableAlertType}
          onValueChange={(value: string) => update({ alert_type: value })}
          placeholder="Ex: FOV Count Violation"
        />
      </td>
      <td className="py-2 px-3 align-top">
        <TextInput
          aria-label="Output category"
          data-testid="verification-output-category"
          value={fields.output_category}
          onValueChange={(value: string) => update({ output_category: value })}
          placeholder="Ex: Ladder PPE Violation"
        />
      </td>
      <td className="py-2 px-3 align-top">
        <TextInput
          aria-label="User prompt"
          data-testid="verification-user-prompt"
          value={fields.prompt}
          onValueChange={(value: string) => update({ prompt: value })}
          placeholder="Ex: Is anyone violating this safety rule? Answer yes or no."
        />
      </td>
    </>
  );

  return (
    <div className="flex flex-col flex-1 min-h-0" data-testid="verification-alerts-tab">
      <div
        className={`flex-shrink-0 px-6 py-4 border-b ${
          isDark ? 'bg-black border-neutral-700' : 'bg-white border-gray-200'
        }`}
      >
        <div className="flex items-center gap-3">
          <label htmlFor="verification-filter" className="text-sm font-medium">
            Filter by category
          </label>
          <div className="max-w-sm w-full">
            <TextInput
              id="verification-filter"
              data-testid="verification-filter"
              value={filter}
              onValueChange={setFilter}
              placeholder="Ex: Alert type or output category"
            />
          </div>
          <div className="ml-auto flex items-center gap-3 text-xs">
            {lastRefreshedAt && <span>Last refreshed: {lastRefreshedAt.toLocaleTimeString()}</span>}
            <Button
              kind="secondary"
              type="button"
              data-testid="verification-refresh"
              onClick={() => {
                refetch({ minLoadingMs: 500 });
              }}
              disabled={loading}
            >
              <span className="inline-flex items-center gap-1.5">
                <IconRefresh className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
                Refresh
              </span>
            </Button>
          </div>
        </div>
        {(error || deleteError) && (
          <div
            role="alert"
            className={`mt-3 flex items-center gap-2 rounded px-3 py-2 text-sm ${
              isDark ? 'bg-red-500/10 text-red-300' : 'bg-red-50 text-red-700'
            }`}
          >
            <IconAlertCircle className="w-4 h-4" />
            {deleteError ?? error}
          </div>
        )}
      </div>

      <div className="flex-1 overflow-auto pb-4">
        <table className="w-full border-collapse" data-testid="verification-configs-table">
          <thead
            className={`sticky top-0 z-10 border-b ${
              isDark ? 'bg-neutral-900 border-neutral-700' : 'bg-gray-100 border-gray-300'
            }`}
          >
            <tr>
              <th className={`${thClass} w-32`}>Actions</th>
              <th className={thClass}>Alert Type</th>
              <th className={thClass}>Output Category</th>
              <th className={thClass}>User Prompt</th>
              <th className={`${thClass} w-36`}>Optional</th>
            </tr>
          </thead>
          <tbody>
            {loading && configs.length === 0 && drafts.length === 0 && (
              <tr>
                <td colSpan={COLUMN_COUNT} className="py-10 text-center text-sm">
                  <IconLoader2 className="inline w-4 h-4 mr-2 animate-spin" />
                  Loading verification configs…
                </td>
              </tr>
            )}
            {!loading && visibleConfigs.length === 0 && drafts.length === 0 && (
              <tr>
                <td colSpan={COLUMN_COUNT} className="py-10 text-center text-sm">
                  No rules found. Click &ldquo;+ Create alert rule&rdquo; to add one.
                </td>
              </tr>
            )}
            {visibleConfigs.map((config) => {
              const edit = editing[config.alert_type];
              const patchEdit = (patch: Partial<EditableFields>) =>
                setEditing((current) => ({
                  ...current,
                  [config.alert_type]: { ...current[config.alert_type], ...patch },
                }));
              const optionalFields = edit ?? editableFromConfig(config);
              return (
                <React.Fragment key={config.alert_type}>
                  <tr
                    data-testid="verification-config-row"
                    className={`border-b ${isDark ? 'border-neutral-800' : 'border-gray-200'}`}
                  >
                    <td className="py-2 px-3 align-top">
                      <div className="flex items-center gap-1">
                        {edit ? (
                          <>
                            <Button
                              kind="tertiary"
                              type="button"
                              aria-label={`Save ${config.alert_type}`}
                              data-testid="verification-edit-save"
                              disabled={edit.saving}
                              onClick={() => {
                                saveEdit(config.alert_type);
                              }}
                            >
                              {edit.saving ? (
                                <IconLoader2 className="w-4 h-4 animate-spin" />
                              ) : (
                                <IconDeviceFloppy className="w-4 h-4" />
                              )}
                            </Button>
                            <Button
                              kind="tertiary"
                              type="button"
                              aria-label={`Cancel editing ${config.alert_type}`}
                              data-testid="verification-edit-cancel"
                              onClick={() =>
                                setEditing((current) => {
                                  const next = { ...current };
                                  delete next[config.alert_type];
                                  return next;
                                })
                              }
                            >
                              <IconX className="w-4 h-4" />
                            </Button>
                          </>
                        ) : (
                          <>
                            <Button
                              kind="tertiary"
                              type="button"
                              aria-label={`Edit ${config.alert_type}`}
                              data-testid="verification-edit"
                              onClick={() => {
                                setEditing((current) => ({
                                  ...current,
                                  [config.alert_type]: editableFromConfig(config),
                                }));
                                if (config.enrichment_prompt?.trim()) {
                                  toggleExpanded(config.alert_type, true);
                                }
                              }}
                            >
                              <IconEdit className="w-4 h-4" />
                            </Button>
                            <Button
                              kind="tertiary"
                              type="button"
                              aria-label={`Duplicate ${config.alert_type}`}
                              data-testid="verification-duplicate"
                              onClick={() => duplicate(config)}
                            >
                              <IconCopy className="w-4 h-4" />
                            </Button>
                            {pendingDelete === config.alert_type ? (
                              <>
                                <Button
                                  kind="tertiary"
                                  type="button"
                                  aria-label={`Confirm delete ${config.alert_type}`}
                                  data-testid="verification-confirm-delete"
                                  onClick={() => {
                                    removeConfig(config.alert_type);
                                  }}
                                >
                                  <IconCheck className="w-4 h-4 text-red-500" />
                                </Button>
                                <Button
                                  kind="tertiary"
                                  type="button"
                                  aria-label="Cancel delete"
                                  data-testid="verification-cancel-delete"
                                  onClick={() => setPendingDelete(null)}
                                >
                                  <IconX className="w-4 h-4" />
                                </Button>
                              </>
                            ) : (
                              <Button
                                kind="tertiary"
                                type="button"
                                aria-label={`Delete ${config.alert_type}`}
                                data-testid="verification-delete"
                                disabled={deleting === config.alert_type}
                                onClick={() => setPendingDelete(config.alert_type)}
                              >
                                {deleting === config.alert_type ? (
                                  <IconLoader2 className="w-4 h-4 animate-spin" />
                                ) : (
                                  <IconTrash className="w-4 h-4" />
                                )}
                              </Button>
                            )}
                          </>
                        )}
                      </div>
                      {edit?.error && <div className="mt-2 text-xs text-red-500">{edit.error}</div>}
                    </td>
                    {edit ? (
                      renderFields(edit, patchEdit, true)
                    ) : (
                      <>
                        <td className="py-2 px-3 align-top text-sm">{config.alert_type}</td>
                        <td className="py-2 px-3 align-top text-sm">
                          {config.output_category || '—'}
                        </td>
                        <td className="py-2 px-3 align-top text-sm whitespace-pre-wrap">
                          {config.prompt}
                        </td>
                      </>
                    )}
                    {renderOptionalToggle(
                      config.alert_type,
                      config.alert_type,
                      Boolean(optionalFields.enrichment_prompt.trim()),
                    )}
                  </tr>
                  {expandedRows.has(config.alert_type) &&
                    renderOptionalPanel(optionalFields, edit ? patchEdit : null)}
                </React.Fragment>
              );
            })}
            {drafts.map((draft) => (
              <React.Fragment key={draft.draftId}>
                <tr
                  data-testid="verification-draft-row"
                  className={`border-b ${isDark ? 'border-neutral-800' : 'border-gray-200'}`}
                >
                  <td className="py-2 px-3 align-top">
                    <div className="flex items-center gap-1">
                      <Button
                        kind="tertiary"
                        type="button"
                        aria-label="Save verification rule"
                        data-testid="verification-draft-save"
                        disabled={draft.saving}
                        onClick={() => {
                          saveDraft(draft);
                        }}
                      >
                        {draft.saving ? (
                          <IconLoader2 className="w-4 h-4 animate-spin" />
                        ) : (
                          <IconDeviceFloppy className="w-4 h-4" />
                        )}
                      </Button>
                      <Button
                        kind="tertiary"
                        type="button"
                        aria-label="Discard verification draft"
                        data-testid="verification-draft-discard"
                        onClick={() => discardDraft(draft.draftId)}
                      >
                        <IconTrash className="w-4 h-4" />
                      </Button>
                    </div>
                    {draft.error && <div className="mt-2 text-xs text-red-500">{draft.error}</div>}
                  </td>
                  {renderFields(draft, (patch) => patchDraft(draft.draftId, patch), false)}
                  {renderOptionalToggle(
                    draft.draftId,
                    draft.alert_type || 'new rule',
                    Boolean(draft.enrichment_prompt.trim()),
                  )}
                </tr>
                {expandedRows.has(draft.draftId) &&
                  renderOptionalPanel(draft, (patch) => patchDraft(draft.draftId, patch))}
              </React.Fragment>
            ))}
          </tbody>
        </table>
        <div className="flex justify-center pt-6">
          <Button kind="secondary" onClick={addDraft} data-testid="add-verification-rule-inline">
            <span className="flex items-center gap-2">
              <IconPlus
                size={16}
                color={isDark ? '#ffffff' : '#374151'}
                style={{ color: isDark ? '#ffffff' : '#374151' }}
              />
              Create alert rule
            </span>
          </Button>
        </div>
      </div>
    </div>
  );
};
