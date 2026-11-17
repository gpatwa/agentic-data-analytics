// Mirrors services/analytics-api/app/connect/api.py (CM4).

export interface EngineCapability {
  engine: string;
  stage: string;
  setup_methods: string[];
  product_run_setup: { available: boolean; reason?: string };
  credential: { kind: string; note: string };
}

export interface Capabilities {
  engines: EngineCapability[];
  can_manage: boolean;
}

export interface TableRef {
  schema: string;
  table: string;
}

export interface SetupInput {
  host: string;
  account: string;
  database: string;
  tables: TableRef[];
  credit_quota: number;
}

export interface SetupView {
  setup_id: string;
  connection_id: string;
  status: string;
  method: string;
  host: string;
  account: string;
  database: string;
  tables: string[];
  summary: string[];
  statements: string[];
  teardown: string[];
  digest: string;
}

export interface CheckView {
  name: string;
  label: string;
  passed: boolean;
}

export interface VerifyView {
  ok: boolean;
  code: string | null;
  checks: CheckView[];
  not_run_on_customer_data: string[];
  connection_status: string;
}

export interface ConnectionView {
  connection_id: string;
  engine: string;
  host: string;
  account: string;
  status: string;
  tables: string[];
  auth_method: string;
}

export interface RemovalView {
  connection_id: string;
  status: string;
  credential_deleted: boolean;
  teardown: string[];
  teardown_digest: string;
  note: string;
}

export interface ConnectConfig {
  baseUrl: string;
  token: string;
  apiKey?: string;
}
