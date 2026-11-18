import assert from 'node:assert/strict';
import { test } from 'node:test';

import { describeError, ERROR_COPY } from './copy';
import {
  CALLBACK_CODES,
  currentStep,
  describeRun,
  isConnectReturn,
  parseReturn,
  parseUsers,
  registrationScriptFilename,
  registrationScriptText,
  tableKey,
  tablesFromKeys,
  toggleKey,
  validateDraft,
} from './productRunFlow';
import type { ProductSetupView, RunView, ScriptView } from './types';

const script: ScriptView = {
  reg_suffix: 'a1b2c3d4', summary: ['Register Compass once.'], statements: ['CREATE ROLE R', 'GRANT SELECT ON TABLE "A"."B"."C" TO ROLE R WITH GRANT OPTION'],
  teardown: ['DROP ROLE R'], digest: 'ab12cd34'.repeat(8),
};
const setup = (status: ProductSetupView['status']): ProductSetupView => ({
  setup_id: 's-0000000001', connection_id: 'c1', registration_id: 'reg-00000001', status, approved: status !== 'awaiting_approval', signed_in: status === 'ready_to_run',
  summary: [], statements: [], teardown: [], digest: 'x'.repeat(64),
});
const run = (over: Partial<RunView>): RunView => ({ ok: true, code: null, created: ['role', 'user'], leftovers: [], connection_id: 'c1', connection_status: 'active', failed_checks: [], ...over });

test('the return from Snowflake is read from a fixed path and an allow-listed code only', () => {
  assert.deepEqual(parseReturn('/connect/review/setup-0000000001', ''), { kind: 'review', setupId: 'setup-0000000001' });
  assert.deepEqual(parseReturn('/connect/review/setup-0000000001/', ''), { kind: 'review', setupId: 'setup-0000000001' });
  for (const bad of ['/connect/review/short', '/connect/review/has space-1', '/connect/review/a/b-0000000001', '/connect/review/' + 'x'.repeat(65), '/connect/reviews/setup-0000000001', '/']) {
    assert.equal(parseReturn(bad, ''), null, bad);
  }
  assert.deepEqual(parseReturn('/connect/oauth-error', '?code=state_used'), { kind: 'error', code: 'state_used' });
  assert.deepEqual(parseReturn('/connect/oauth-error', '?code=<script>alert(1)</script>'), { kind: 'error', code: 'callback_malformed' });
  assert.deepEqual(parseReturn('/connect/oauth-error', ''), { kind: 'error', code: 'callback_malformed' });
  assert.deepEqual(parseReturn('/connect/oauth-error', '?code=access_denied'), { kind: 'error', code: 'callback_malformed' });
});

test('the app opens the Connect view only for the addresses Snowflake sends the browser back to', () => {
  assert.equal(isConnectReturn('/connect/review/setup-0000000001', ''), true);
  assert.equal(isConnectReturn('/connect/oauth-error', '?code=state_used'), true);
  for (const path of ['/', '/connect', '/connect/review', '/governed', '/connect/review/short']) assert.equal(isConnectReturn(path, ''), false, path);
});

test('every code the callback can show has plain words', () => {
  for (const code of CALLBACK_CODES) assert.ok(ERROR_COPY[code], code);
});

test('the step shows where a setup is, and done once it has run', () => {
  assert.equal(currentStep(null, null), -1);
  assert.equal(currentStep(setup('awaiting_approval'), null), 1);
  assert.equal(currentStep(setup('awaiting_sign_in'), null), 2);
  assert.equal(currentStep(setup('ready_to_run'), null), 3);
  assert.equal(currentStep(null, run({})), 4);
});

test('setup users are taken as typed, deduplicated and bounded', () => {
  assert.deepEqual(parseUsers('A, B\nA\n  C ').users, ['A', 'B', 'C']);
  assert.match(parseUsers('').problems[0], /at least one/);
  assert.match(parseUsers(Array.from({ length: 11 }, (_, i) => `U${i}`).join('\n')).problems.join(), /At most 10/);
  assert.match(parseUsers('bad\u0007name').problems.join(), /characters we cannot use/);
});

test('the draft is checked before a round trip', () => {
  const ok = { database: 'ANALYTICS', tablesText: 'PUBLIC.ORDERS', usersText: 'ADMIN_USER', credit: '1' };
  assert.deepEqual(validateDraft(ok), []);
  assert.match(validateDraft({ ...ok, database: ' ' }).join(), /database/);
  assert.match(validateDraft({ ...ok, tablesText: 'PUBLIC.*' }).join(), /wildcard/);
  assert.match(validateDraft({ ...ok, usersText: '' }).join(), /at least one/);
  for (const credit of ['0', '101', '1.5', 'abc', '']) assert.match(validateDraft({ ...ok, credit }).join(), /credit limit/, credit);
});

test('picking tables uses the exact names the server returned and never invents one', () => {
  const all = [{ schema: 'PUBLIC', table: 'ORDERS' }, { schema: 'MART', table: 'daily' }];
  assert.deepEqual(toggleKey(toggleKey([], 'PUBLIC.ORDERS'), 'PUBLIC.ORDERS'), []);
  assert.deepEqual(tablesFromKeys(all, ['MART.daily', 'PUBLIC.PAYROLL']), [{ schema: 'MART', table: 'daily' }]); // an unlisted key is dropped
  assert.equal(tableKey(all[1]), 'MART.daily');
});

test('the registration script carries the digest, the privilege note, every statement and a removal hint', () => {
  const text = registrationScriptText(script);
  assert.match(text, /Digest ab12cd34/);
  assert.match(text, /ACCOUNTADMIN/);
  assert.match(text, /setup role can read the tables you named/);
  assert.match(text, /removal script/);
  for (const statement of script.statements) assert.ok(text.includes(`${statement};`));
  assert.equal(registrationScriptFilename(script), 'compass-registration-ab12cd34.sql');
});

test('a finished run is described honestly', () => {
  assert.equal(describeRun(run({})).tone, 'ok');
  assert.match(describeRun(run({})).title, /Connected and tested/);
  const untested = describeRun(run({ connection_status: 'pending' }));
  assert.equal(untested.tone, 'warn');
  assert.match(untested.title, /not tested yet/);
  assert.doesNotMatch(untested.title, /Connected/);
  const failedTest = describeRun(run({ ok: false, code: 'connection_test_failed', connection_status: 'suspended', failed_checks: ['metadata_read'] }));
  assert.equal(failedTest.tone, 'warn');
  assert.match(failedTest.detail, /We can read your table layout/);
  const wrong = describeRun(run({ ok: false, code: 'wrong_account', connection_status: 'removed', created: [] }));
  assert.equal(wrong.tone, 'bad');
  assert.match(wrong.title, /different Snowflake account/);
  assert.match(describeRun(run({ ok: false, code: 'setup_step_3:RuntimeError', leftovers: ['role R'] })).detail, /Compass could not remove: role R/);
});

test('error lookup ignores a detail after a colon and a step number, and falls back to quoting the code', () => {
  assert.equal(describeError('admin_connect_failed:RuntimeError').title, ERROR_COPY.admin_connect_failed.title);
  assert.equal(describeError('setup_step_7:RuntimeError').title, ERROR_COPY.setup_step.title);
  assert.match(describeError('weird_code').action, /weird_code/);
});

test('the new product-run codes all have a title and a next step in plain words', () => {
  const codes = [
    'registration_not_found', 'registration_id_taken', 'invalid_registration', 'redirect_not_configured', 'registration_not_configured', 'outside_ceiling',
    'sign_in_required', 'sign_in_expired', 'not_approved', 'digest_mismatch', 'wrong_admin', 'approval_expired', 'unknown_request', 'setup_not_pending',
    'wrong_account', 'wrong_role', 'registration_changed', 'name_taken', 'deadline_exceeded', 'setup_step', 'admin_connect_failed', 'grants_missing',
    'grants_unexpected', 'grants_dangerous', 'too_many_sign_ins',
  ];
  for (const code of codes) {
    const copy = ERROR_COPY[code];
    assert.ok(copy && copy.title.length > 10 && copy.action.length > 10, code);
    assert.doesNotMatch(`${copy.title} ${copy.action}`, /exception|traceback|stack|null|undefined/i, code);
  }
});
