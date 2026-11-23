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
