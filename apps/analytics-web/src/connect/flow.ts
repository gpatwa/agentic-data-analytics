import type { SetupView, TableRef } from './types';

export const SNOWFLAKE_SUFFIX = '.snowflakecomputing.com';
export const MAX_TABLES = 50;

export function accountFromHost(host: string): string {
  const name = host.trim().toLowerCase();
  return name.endsWith(SNOWFLAKE_SUFFIX) ? name.slice(0, -SNOWFLAKE_SUFFIX.length) : '';
}

export interface ParsedTables {
  tables: TableRef[];
  problems: string[];
}

/** One SCHEMA.TABLE per line (commas also split). Names are sent exactly as typed: Snowflake shows them in the case to use. */
export function parseTables(text: string): ParsedTables {
  const tables: TableRef[] = [];
  const problems: string[] = [];
  const seen = new Set<string>();
  for (const raw of text.split(/[\n,]/)) {
    const entry = raw.trim();
    if (!entry) continue;
    const parts = entry.split('.');
    if (parts.length !== 2 || parts.some((part) => !part.trim())) {
      problems.push(`"${entry}" must be SCHEMA.TABLE`);
      continue;
    }
    if (/[*%]/.test(entry)) {
      problems.push(`"${entry}" has a wildcard; name each table`);
      continue;
    }
    const [schema, table] = parts.map((part) => part.trim());
    const key = `${schema}.${table}`;
    if (seen.has(key)) {
      problems.push(`"${entry}" is listed twice`);
      continue;
    }
    seen.add(key);
    tables.push({ schema, table });
  }
  if (tables.length > MAX_TABLES) problems.push(`At most ${MAX_TABLES} tables at a time`);
  if (!tables.length && !problems.length) problems.push('Name at least one table');
  return { tables, problems };
}

export interface DetailsInput {
  host: string;
  database: string;
  tablesText: string;
}

/** Checks that need no server round trip. The server checks all of this again; this only saves a wait. */
export function validateDetails(input: DetailsInput): string[] {
  const problems: string[] = [];
  const host = input.host.trim().toLowerCase();
  if (!accountFromHost(host) || accountFromHost(host).startsWith('.')) problems.push(`The hostname must end in ${SNOWFLAKE_SUFFIX}`);
  else if (/[\s/@:]/.test(host)) problems.push('Use only the hostname: no https://, port, path or spaces');
  if (!input.database.trim()) problems.push('Name the database');
  problems.push(...parseTables(input.tablesText).problems);
  return problems;
}

/** The text an admin runs. The header carries the digest so the screen, the file and the record can be compared. */
export function scriptText(setup: SetupView): string {
  return [
    `-- Compass Connect setup for ${setup.host}`,
    `-- Digest ${setup.digest}`,
    '-- Run once, as ACCOUNTADMIN or a role that can create roles, users, warehouses and resource monitors.',
    '-- It creates one read-only role and one service user. It changes nothing else.',
    '',
    ...setup.statements.map((statement) => `${statement};`),
    '',
  ].join('\n');
}

export function teardownText(statements: string[], digest: string, host: string): string {
  return [`-- Compass Connect removal for ${host}`, `-- Digest ${digest}`, '', ...statements.map((statement) => `${statement};`), ''].join('\n');
}

export const scriptFilename = (setup: SetupView) => `compass-connect-${setup.account}-${setup.digest.slice(0, 8)}.sql`;

export function failedChecks(checks: { name: string; passed: boolean }[]): string[] {
  return checks.filter((check) => !check.passed).map((check) => check.name);
}
