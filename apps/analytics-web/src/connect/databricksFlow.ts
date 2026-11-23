import { parseTables } from './flow';
import type { DbxEnrolStatus, DbxScriptView } from './databricksApi';

export const DBX_SUFFIXES = ['.cloud.databricks.com', '.azuredatabricks.net', '.gcp.databricks.com'];
const APP_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const HTTP_PATH = /^\/sql\/1\.0\/(warehouses|endpoints)\/[0-9a-f]{8,32}$/;

export interface DbxDetails {
  host: string;
  catalog: string;
  warehouse: string;
  tablesText: string;
}

/** Checks that need no round trip; the server checks all of this again. */
export function validateDbxDetails(input: DbxDetails): string[] {
  const problems: string[] = [];
  const host = input.host.trim().toLowerCase();
  if (/[\s/@:]/.test(host)) problems.push('Use only the hostname: no https://, port, path or spaces');
  else if (!DBX_SUFFIXES.some((suffix) => host.endsWith(suffix) && host.length > suffix.length)) {
    problems.push(`The hostname must end in ${DBX_SUFFIXES.join(', ')}`);
  }
  if (!input.catalog.trim()) problems.push('Name the catalog');
  if (/[`*%]/.test(input.catalog)) problems.push('The catalog name cannot contain ` * or %');
  if (!input.warehouse.trim()) problems.push('Name the SQL warehouse Compass may use');
  problems.push(...parseTables(input.tablesText).problems);
  return problems;
}

export const looksLikeApplicationId = (value: string) => APP_ID.test(value.trim().toLowerCase());
export const looksLikeHttpPath = (value: string) => HTTP_PATH.test(value.trim());

export interface CredentialInput {
  applicationId: string;
  secret: string;
  httpPath: string;
}

export function validateCredentials(input: CredentialInput): string[] {
  const problems: string[] = [];
  if (!looksLikeApplicationId(input.applicationId)) problems.push('The application id is a UUID, for example 0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0');
  if (!looksLikeHttpPath(input.httpPath)) problems.push('The HTTP path looks like /sql/1.0/warehouses/abc123');
  if (input.secret.length < 16 || /\s/.test(input.secret)) problems.push('Paste the whole OAuth secret, with no spaces');
  return problems;
}

/** The text an admin runs: what to do in Databricks, then the SQL. The digest lets the screen, the file and the record be compared. */
export function dbxScriptText(view: DbxScriptView): string {
  return [
    `-- Compass Connect registration for ${view.host}`,
    `-- Digest ${view.digest}`,
    '-- Run as a workspace admin who can create service principals and grant on the catalog.',
    '',
    ...view.steps.map((step, i) => (step.kind === 'sql' ? `${step.text};` : `-- Step ${i + 1}: ${step.text}`)),
    '',
  ].join('\n');
}

export function dbxRemovalText(view: DbxScriptView): string {
  return [
    `-- Compass Connect removal for ${view.host}`,
    `-- Digest ${view.digest}`,
    '',
    ...view.removal.map((step) => (step.kind === 'sql' ? `${step.text};` : `-- ${step.text}`)),
    '',
  ].join('\n');
}

export const dbxScriptFilename = (view: DbxScriptView) => `compass-databricks-${view.principal_name.slice(-8)}-${view.digest.slice(0, 8)}.sql`;

/** Whether the grants still carry the placeholder, that is the application id has not been given yet. */
export const needsApplicationId = (view: DbxScriptView) => view.steps.some((step) => step.text.includes('<APPLICATION_ID>'));

/** The server warns this many days before a secret ends (it lasts 90 days). Kept equal to ROTATE_WITHIN_DAYS in app/connect/drift.py. */
export const ROTATE_WITHIN_DAYS = 30;

/** Plain words for when a connection's secret stops working. */
export function expiryText(status: string, days: number | null): string {
  if (status === 'suspended') return 'Paused: Compass could not read the tables, or the secret ended. Rotate the secret to resume.';
  if (days === null) return 'Secret expiry unknown.';
  if (days < 0) return 'The secret has expired. Rotate it.';
  if (days <= ROTATE_WITHIN_DAYS) return `The secret ends in ${days} day${days === 1 ? '' : 's'}. Rotate it before then.`;
  return `The secret is good for ${days} more days.`;
}

export const validateSecret = (secret: string): string[] => (secret.length < 16 || /\s/.test(secret) ? ['Paste the whole OAuth secret, with no spaces'] : []);

export type Track = 'enrol' | 'manual';

/** Plain words for each state the enrol file's setup can be in. */
export function enrolStatusText(status: DbxEnrolStatus | null): string {
  if (!status) return 'Waiting for your admin to run the file.';
  if (status.state === 'waiting') return `Waiting for your admin to run the file. It stops working in ${status.expires_in_minutes} minute${status.expires_in_minutes === 1 ? '' : 's'}.`;
  if (status.state === 'expired') return 'The file expired before it was run. Get a new one.';
  if (status.state === 'connected') return 'Connected. Compass read each table you named.';
  return 'Not connected. The file removed what it had created, so nothing is left behind.';
}

/** The command that removes the principal and its grants later. Empty until the application id looks right, so a wrong value is never offered. */
export const removeCommand = (applicationId: string) => (looksLikeApplicationId(applicationId) ? `python3 compass-enrol.py --remove ${applicationId.trim().toLowerCase()}` : '');

/** Plain words for each state of a rotation by file. */
export function rotateStatusText(status: DbxEnrolStatus | null): string {
  if (!status) return 'Waiting for your admin to run the file.';
  if (status.state === 'waiting') return `Waiting for your admin to run the file. It stops working in ${status.expires_in_minutes} minute${status.expires_in_minutes === 1 ? '' : 's'}.`;
  if (status.state === 'expired') return 'The file expired before it was run. Get a new one.';
  if (status.state === 'connected') return 'Rotated. Compass read each table with the new secret, and the older secrets were revoked.';
  return 'Not rotated. The new secret was deleted and the connection is as it was.';
}
