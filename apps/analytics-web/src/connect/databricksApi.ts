import { call } from './api';
import type { ConnectConfig, TableRef } from './types';

// Mirrors services/analytics-api/app/connect/databricks_api.py.
export interface DbxStep {
  kind: 'admin' | 'sql';
  text: string;
}

export interface DbxScriptView {
  setup_id: string;
  connection_id: string;
  host: string;
  catalog: string;
  tables: string[];
  summary: string[];
  steps: DbxStep[];
  removal: DbxStep[];
  digest: string;
  principal_name: string;
  warehouse_name: string;
}

export interface DbxBeginInput {
  host: string;
  catalog: string;
  warehouse_name: string;
  tables: TableRef[];
}

export interface DbxCompleteInput {
  application_id: string;
  client_secret: string;
  http_path: string;
}

export interface DbxResult {
  ok: boolean;
  code: string | null;
  checks: { table: string; passed: boolean }[];
  connection_status: string;
}

const BASE = '/databricks';

export const beginDatabricks = (cfg: ConnectConfig, input: DbxBeginInput) => call<DbxScriptView>(cfg, 'POST', `${BASE}/setups`, input);
export const scriptWithId = (cfg: ConnectConfig, id: string, applicationId: string) =>
  call<DbxScriptView>(cfg, 'POST', `${BASE}/setups/${encodeURIComponent(id)}/script`, { application_id: applicationId });
/** The secret travels in this one request body and is never returned. */
export const completeDatabricks = (cfg: ConnectConfig, id: string, input: DbxCompleteInput) =>
  call<DbxResult>(cfg, 'POST', `${BASE}/setups/${encodeURIComponent(id)}/credentials`, input);
export const cancelDatabricks = (cfg: ConnectConfig, id: string) => call<void>(cfg, 'POST', `${BASE}/setups/${encodeURIComponent(id)}/cancel`);

export interface DbxConnection {
  connection_id: string;
  host: string;
  catalog: string;
  tables: string[];
  status: string;
  expires_at: string | null;
  expires_in_days: number | null;
  needs_rotation: boolean;
  warm_up: boolean;
  application_id: string;
}

export interface DbxRemoval {
  connection_id: string;
  status: string;
  credential_deleted: boolean;
  teardown: string[];
  note: string;
}

export const listDbxConnections = (cfg: ConnectConfig) => call<DbxConnection[]>(cfg, 'GET', `${BASE}/connections`);
/** The new secret travels in this one request body and is never returned. */
export const rotateDbxSecret = (cfg: ConnectConfig, id: string, secret: string) =>
  call<DbxResult>(cfg, 'POST', `${BASE}/connections/${encodeURIComponent(id)}/secret`, { client_secret: secret });
export const checkDbxConnection = (cfg: ConnectConfig, id: string) => call<DbxResult>(cfg, 'POST', `${BASE}/connections/${encodeURIComponent(id)}/check`);
export const removeDbxConnection = (cfg: ConnectConfig, id: string) => call<DbxRemoval>(cfg, 'DELETE', `${BASE}/connections/${encodeURIComponent(id)}`);
/** Turn warming on or off for one connection (an admin action; off by default). Answers the updated connection. */
export const setDbxWarmUp = (cfg: ConnectConfig, id: string, enabled: boolean) =>
  call<DbxConnection>(cfg, 'PUT', `${BASE}/connections/${encodeURIComponent(id)}/warm-up`, { enabled });

/** The one file the admin runs (docs/design/SELF_SERVE_CONNECT_DESIGN.md). It carries a single-use token valid for 30 minutes; asking again revokes the old one. */
export interface DbxEnrolFile {
  filename: string;
  content: string;
  digest: string;
  expires_in_minutes: number;
  run: string;
}

export interface DbxEnrolStatus {
  state: 'waiting' | 'refused' | 'connected' | 'expired';
  code: string | null;
  checks: { table: string; passed: boolean }[];
  expires_in_minutes: number;
}

export const issueEnrolFile = (cfg: ConnectConfig, id: string) => call<DbxEnrolFile>(cfg, 'POST', `${BASE}/setups/${encodeURIComponent(id)}/enrol-file`);
export const enrolStatus = (cfg: ConnectConfig, id: string) => call<DbxEnrolStatus>(cfg, 'GET', `${BASE}/setups/${encodeURIComponent(id)}/status`);

/** The file that gives an existing connection a new secret, with nothing to copy. Same single-use, 30-minute rules as the enrol file. */
export const issueRotateFile = (cfg: ConnectConfig, id: string) => call<DbxEnrolFile>(cfg, 'POST', `${BASE}/connections/${encodeURIComponent(id)}/rotate-file`);
export const rotateStatus = (cfg: ConnectConfig, id: string) => call<DbxEnrolStatus>(cfg, 'GET', `${BASE}/connections/${encodeURIComponent(id)}/rotate-status`);

/** The Databricks tab's own readiness: it must not depend on the generic (Snowflake-era) Connect route, which answers 503 on an installation that only has Databricks Connect. */
export interface DbxCapabilities {
  engine: string;
  setup_method: string;
  can_manage: boolean;
  stage: string;
}

export const getDbxCapabilities = (cfg: ConnectConfig) => call<DbxCapabilities>(cfg, 'GET', `${BASE}/capabilities`);
