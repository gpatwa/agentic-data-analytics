import { describeError } from './copy';
import { parseTables } from './flow';
import type { ProductSetupView, RunView, ScriptView, TableRef } from './types';

/** The short codes the sign-in callback can put on the error page. A test keeps this equal to the server's list. */
export const CALLBACK_CODES = [
  'state_invalid', 'state_expired', 'state_used', 'binding_mismatch', 'authorization_refused', 'missing_code', 'token_exchange_failed', 'callback_malformed',
] as const;

export type ReturnTarget = { kind: 'review'; setupId: string } | { kind: 'error'; code: string } | null;

/** Where Snowflake's sign-in sent the browser back to: a setup to continue, or a fixed error page with one short code. */
export function parseReturn(pathname: string, search: string): ReturnTarget {
  const review = /^\/connect\/review\/([A-Za-z0-9_-]{8,64})\/?$/.exec(pathname);
  if (review) return { kind: 'review', setupId: review[1] };
  if (pathname === '/connect/oauth-error') {
    const code = new URLSearchParams(search).get('code') ?? '';
    return { kind: 'error', code: (CALLBACK_CODES as readonly string[]).includes(code) ? code : 'callback_malformed' };
  }
  return null;
}

/** True when the address is one Snowflake's sign-in sends the browser back to. */
export const isConnectReturn = (pathname: string, search: string): boolean => parseReturn(pathname, search) !== null;

export const STEP_LABELS = ['Review', 'Approve', 'Sign in', 'Run'] as const;

/** 0 to 3 is the step in progress; 4 means all done; -1 means nothing planned yet. */
export function currentStep(setup: ProductSetupView | null, run: RunView | null): number {
  if (run) return 4;
  if (!setup) return -1;
  return setup.status === 'awaiting_approval' ? 1 : setup.status === 'awaiting_sign_in' ? 2 : 3;
}

export interface UsersParsed {
  users: string[];
  problems: string[];
}

/** One Snowflake user name per line or comma, exactly as typed. Between 1 and 10 of them. */
export function parseUsers(text: string): UsersParsed {
  const users: string[] = [];
  for (const raw of text.split(/[\n,]/)) {
    const name = raw.trim();
    if (name && !users.includes(name)) users.push(name);
  }
  const problems: string[] = [];
  if (!users.length) problems.push('Name at least one Snowflake user who may use the setup role');
  if (users.length > 10) problems.push('At most 10 users');
  if (users.some((u) => /[\u0000-\u001f]/.test(u) || u.length > 255)) problems.push('A user name has characters we cannot use');
  return { users, problems };
}

export function validateDraft(input: { database: string; tablesText: string; usersText: string; credit: string }): string[] {
  const problems: string[] = [];
  if (!input.database.trim()) problems.push('Name the database');
  problems.push(...parseTables(input.tablesText).problems);
  problems.push(...parseUsers(input.usersText).problems);
  const credit = Number(input.credit);
  if (!Number.isInteger(credit) || credit < 1 || credit > 100) problems.push('The credit limit must be a whole number from 1 to 100');
  return problems;
}

export function tableKey(table: TableRef): string {
  return `${table.schema}.${table.table}`;
}

export function toggleKey(selected: string[], key: string): string[] {
  return selected.includes(key) ? selected.filter((k) => k !== key) : [...selected, key];
}

export function tablesFromKeys(all: TableRef[], keys: string[]): TableRef[] {
  return all.filter((t) => keys.includes(tableKey(t)));
}

/** The text the admin runs once. The header carries the digest and says plainly what the setup role can read. */
export function registrationScriptText(script: ScriptView): string {
  return [
    '-- Compass registration (run once, as ACCOUNTADMIN)',
    `-- Digest ${script.digest}`,
    '-- This lets Compass set up read-only access later, only when one of your named users signs in and approves.',
    '-- The setup role can read the tables you named while signed in as it. Run the removal script to undo all of this.',
    '',
    ...script.statements.map((statement) => `${statement};`),
    '',
  ].join('\n');
}

export const registrationScriptFilename = (script: ScriptView) => `compass-registration-${script.digest.slice(0, 8)}.sql`;

const CHECK_LABELS: Record<string, string> = {
  read_only_grants: 'Your access is read-only',
  metadata_read: 'We can read your table layout',
};

export interface RunOutcome {
  tone: 'ok' | 'warn' | 'bad';
  title: string;
  detail: string;
}

/** What a finished run means, in plain words. A pass without a connection test is not reported as connected. */
export function describeRun(run: RunView): RunOutcome {
  if (run.ok && run.connection_status === 'active') {
    return { tone: 'ok', title: 'Connected and tested', detail: `Read-only access was set up (${run.created.join(' and ')}) and the connection test passed.` };
  }
  if (run.ok) {
    return { tone: 'warn', title: 'Set up, not tested yet', detail: 'The connection test is not switched on here, so the connection is not active yet.' };
  }
  if (run.code === 'connection_test_failed') {
    const failed = run.failed_checks.map((name) => CHECK_LABELS[name] ?? name).join('; ');
    return { tone: 'warn', title: 'Set up, but the connection test did not pass', detail: `Not passed: ${failed || 'a check'}. The connection is switched off until it passes.` };
  }
  const copy = describeError(run.code ?? 'unknown');
  const left = run.leftovers.length ? ` Compass could not remove: ${run.leftovers.join(', ')}. Your admin should drop these.` : '';
  return { tone: 'bad', title: copy.title, detail: `${copy.action}${left}` };
}
