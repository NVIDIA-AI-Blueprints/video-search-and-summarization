// SPDX-License-Identifier: Apache-2.0

// The composition root: the only place that chooses which adapter backs each
// port. Built per call (a client is two strings), so the configuration is read
// at request time and never cached at module load.

import { randomUUID } from 'node:crypto';

import type { ChartInterpreter } from '@/modules/dashboard/domain/ChartInterpreter';
import type { ChartRenderer } from '@/modules/dashboard/domain/ChartRenderer';
import { AgentChartRenderer } from '@/modules/dashboard/infrastructure/agent/chart-renderer';
import { AgentChartInterpreter } from '@/modules/dashboard/infrastructure/agent/chart-interpreter';
import { SupabaseDashboardAdapter } from '@/modules/dashboard/infrastructure/supabase/dashboard';
import { SupabaseHealthAdapter } from '@/modules/health/infrastructure/supabase/health';
import { SupabaseVideoAdapter } from '@/modules/videos/infrastructure/supabase/videos';
import { getServiceConfiguration, type ServiceConfiguration } from '@/lib/env';
import type { HealthRepository } from '@/modules/health/domain/HealthRepository';
import { createPostgrestClient } from '@/lib/postgrest/supabase-client';
import type { DbAdapters } from '@/modules/shared/domain/adapters';
import { SupabaseIncidentAdapter } from '@/modules/reports/infrastructure/supabase/incidents';
import type { AnalysisPorts } from '@/modules/analysis/application/run-contract-analysis';
import { SupabaseAnalysisRunAdapter } from '@/modules/analysis/infrastructure/supabase/analysis-runs';
import { GatewayClient } from '@/modules/analysis/infrastructure/gateway/client';
import { HttpIncidentAgent } from '@/modules/analysis/infrastructure/agent/incident-agent';
import { R2VideoObjectStore } from '@/modules/analysis/infrastructure/r2/video-object-store';
import type { ContractCatalog } from '@/modules/contract/domain/ContractCatalog';
import { defaultContractCatalog } from '@/modules/contract/infrastructure/loader';
import type { UploadAnalysisDeps } from '@/modules/analysis/application/analyse-upload';
import { fetchEvidenceEmbeddings } from '@/modules/reports/infrastructure/agent/evidence-embeddings';
import type { AnalysisJobRepository } from '@/modules/jobs/domain/AnalysisJobRepository';
import { SupabaseAnalysisJobAdapter } from '@/modules/jobs/infrastructure/supabase/analysis-jobs';

/** Outbound contract catalog port: provides shared prompts, schemas and model allowlists. */
export function getContractCatalog(): ContractCatalog {
  return defaultContractCatalog;
}

/** Throws 'Supabase PostgREST is not configured' when the service-role settings are missing. */
export function getDbAdapters(config?: ServiceConfiguration): DbAdapters {
  const db = createPostgrestClient(config);
  return {
    dashboard: new SupabaseDashboardAdapter(db),
    incidents: new SupabaseIncidentAdapter(db),
    videos: new SupabaseVideoAdapter(db),
  };
}

/** Never throws: /api/health must answer when the database is not configured. */
export function getDbHealth(config?: ServiceConfiguration): HealthRepository {
  return new SupabaseHealthAdapter(config);
}

/**
 * The analysis ports. The model port is built only when its URL is configured;
 * the analysis refuses the mode whose port is null. Throws like getDbAdapters
 * when the database is not configured.
 */
export function getAnalysisPorts(config: ServiceConfiguration = getServiceConfiguration()): AnalysisPorts {
  const db = createPostgrestClient(config);
  return {
    runs: new SupabaseAnalysisRunAdapter(db),
    videos: new R2VideoObjectStore(config),
    gateway: config.gatewayUrl ? new GatewayClient(config.gatewayUrl) : null,
    agent: config.agentUrl ? new HttpIncidentAgent(config.agentUrl) : null,
    catalog: defaultContractCatalog,
    randomUUID,
  };
}

/** Everything analyseUpload needs, for POST /api/analysis and the job loop. Pass a checked configuration (requireAnalysisConfiguration). */
export function getUploadAnalysis(config: ServiceConfiguration): UploadAnalysisDeps {
  return {
    ports: getAnalysisPorts(config),
    mode: config.analysisMode,
    db: getDbAdapters(config),
    embed: (revisionId) => fetchEvidenceEmbeddings(config, revisionId),
    defaultModel: config.vlmModel,
    now: () => new Date(),
  };
}

/** The background analysis queue. Throws like getDbAdapters when the database is not configured. */
export function getAnalysisJobs(config?: ServiceConfiguration): AnalysisJobRepository {
  return new SupabaseAnalysisJobAdapter(createPostgrestClient(config));
}

/** The chart request port, in either analysis mode: the agent reads the request with its own LLM; null when INCIDENT_AGENT_BASE_URL is not configured. */
export function getChartInterpreter(config: ServiceConfiguration = getServiceConfiguration()): ChartInterpreter | null {
  return config.agentUrl ? new AgentChartInterpreter(config.agentUrl) : null;
}

/** The chart drawing port: VSS chart_generator on the agent; null when the agent URL is not configured. */
export function getChartRenderer(config: ServiceConfiguration = getServiceConfiguration()): ChartRenderer | null {
  return config.agentUrl ? new AgentChartRenderer(config.agentUrl) : null;
}
