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
