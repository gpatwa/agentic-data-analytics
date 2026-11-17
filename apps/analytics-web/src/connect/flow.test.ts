import assert from 'node:assert/strict';
import { test } from 'node:test';

import { accountFromHost, failedChecks, MAX_TABLES, parseTables, scriptFilename, scriptText, teardownText, validateDetails } from './flow';
import type { SetupView } from './types';

const setup: SetupView = {
  setup_id: 's1', connection_id: 'c1', status: 'awaiting_admin', method: 'script', host: 'acme.snowflakecomputing.com', account: 'acme',
  database: 'ANALYTICS', tables: ['analytics.public.orders'], summary: [], statements: ['CREATE ROLE R', 'GRANT SELECT ON TABLE "A"."B"."C" TO ROLE R'],
  teardown: ['DROP ROLE R'], digest: 'ab12cd34'.repeat(8),
};

test('tables parse one per line or comma separated, exactly as typed', () => {
  const parsed = parseTables('PUBLIC.ORDERS\n public.Customers , MART.DAILY\n\n');
  assert.deepEqual(parsed.tables, [{ schema: 'PUBLIC', table: 'ORDERS' }, { schema: 'public', table: 'Customers' }, { schema: 'MART', table: 'DAILY' }]);
  assert.deepEqual(parsed.problems, []);
});

test('wildcards, bad shapes, repeats and empty input are problems, not silently fixed', () => {
  assert.match(parseTables('PUBLIC.*').problems[0], /wildcard/);
  assert.match(parseTables('PUBLIC.%').problems[0], /wildcard/);
  assert.match(parseTables('ORDERS').problems[0], /SCHEMA\.TABLE/);
  assert.match(parseTables('A.B.C').problems[0], /SCHEMA\.TABLE/);
  assert.match(parseTables('A.').problems[0], /SCHEMA\.TABLE/);
  assert.match(parseTables('A.B\nA.B').problems[0], /twice/);
  assert.deepEqual(parseTables('  \n ').problems, ['Name at least one table']);
  const many = Array.from({ length: MAX_TABLES + 1 }, (_, i) => `S.T${i}`).join('\n');
  assert.match(parseTables(many).problems.join(), /At most/);
});

test('the account comes from the hostname', () => {
  assert.equal(accountFromHost('Acme-Prod.snowflakecomputing.com'), 'acme-prod');
  assert.equal(accountFromHost('evil.example'), '');
});

test('details validation catches what the server would, before a round trip', () => {
  const ok = { host: 'acme.snowflakecomputing.com', database: 'ANALYTICS', tablesText: 'PUBLIC.ORDERS' };
  assert.deepEqual(validateDetails(ok), []);
  assert.match(validateDetails({ ...ok, host: '10.0.0.1' }).join(), /must end in/);
  assert.match(validateDetails({ ...ok, host: 'evil.example' }).join(), /must end in/);
  assert.match(validateDetails({ ...ok, host: 'acme.snowflakecomputing.com:443' }).join(), /must end in|only the hostname/);
  assert.match(validateDetails({ ...ok, host: 'user@acme.snowflakecomputing.com' }).join(), /only the hostname/);
  assert.match(validateDetails({ ...ok, host: '.snowflakecomputing.com' }).join(), /must end in/);
  assert.match(validateDetails({ ...ok, database: ' ' }).join(), /database/);
  assert.match(validateDetails({ ...ok, tablesText: '' }).join(), /at least one table/);
});

test('the script carries the digest, every statement and the privilege note', () => {
  const text = scriptText(setup);
  assert.match(text, /Digest ab12cd34/);
  assert.match(text, /ACCOUNTADMIN or a role that can create/);
  for (const statement of setup.statements) assert.ok(text.includes(`${statement};`));
  assert.equal(text.split('\n').filter((line) => line.endsWith(';')).length, setup.statements.length);
  assert.match(scriptFilename(setup), /^compass-connect-acme-ab12cd34\.sql$/);
});

test('the removal script carries its own digest and host', () => {
  const text = teardownText(setup.teardown, 'ff'.repeat(32), setup.host);
  assert.match(text, /removal for acme\.snowflakecomputing\.com/);
  assert.match(text, /DROP ROLE R;/);
});

test('failed checks are listed by name', () => {
  assert.deepEqual(failedChecks([{ name: 'a', passed: true }, { name: 'b', passed: false }]), ['b']);
});
