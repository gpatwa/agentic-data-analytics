import assert from 'node:assert/strict';
import { test } from 'node:test';

import type { DbxScriptView } from './databricksApi';
import { dbxRemovalText, dbxScriptText, expiryText, needsApplicationId, validateCredentials, validateDbxDetails, validateSecret } from './databricksFlow';

const ok = { host: 'dbc-1234abcd-5678.cloud.databricks.com', catalog: 'sales', warehouse: 'Starter', tablesText: 'data.orders\ndata.customers' };
const view = (placeholder: boolean): DbxScriptView => ({
  setup_id: 's', connection_id: 'dbx-1', host: ok.host, catalog: 'sales', tables: ['data.orders'], summary: [], digest: 'ab12cd34ef567890', principal_name: 'compass-conn-0a1b2c3d',
  steps: [{ kind: 'admin', text: "create the service principal 'compass-conn-0a1b2c3d'" },
    { kind: 'sql', text: `GRANT USE CATALOG ON CATALOG \`sales\` TO \`${placeholder ? '<APPLICATION_ID>' : '0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0'}\`` }],
  removal: [{ kind: 'sql', text: 'REVOKE USE CATALOG ON CATALOG `sales` FROM `x`' }, { kind: 'admin', text: 'delete the service principal' }],
});

test('good details pass and each bad one is named', () => {
  assert.deepEqual(validateDbxDetails(ok), []);
  assert.match(validateDbxDetails({ ...ok, host: 'evil.example.com' }).join(' '), /must end in/);
  assert.match(validateDbxDetails({ ...ok, host: 'https://dbc.cloud.databricks.com' }).join(' '), /only the hostname/);
  assert.match(validateDbxDetails({ ...ok, host: '.cloud.databricks.com' }).join(' '), /must end in/);
  assert.match(validateDbxDetails({ ...ok, catalog: 'a`b' }).join(' '), /cannot contain/);
  assert.match(validateDbxDetails({ ...ok, catalog: '' }).join(' '), /Name the catalog/);
  assert.match(validateDbxDetails({ ...ok, warehouse: ' ' }).join(' '), /warehouse/);
  assert.match(validateDbxDetails({ ...ok, tablesText: 'data.*' }).join(' '), /wildcard/);
});

test('credentials are checked before anything is sent', () => {
  const good = { applicationId: '0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0', secret: 'a-long-enough-secret-value', httpPath: '/sql/1.0/warehouses/abcdef0123456789' };
  assert.deepEqual(validateCredentials(good), []);
  assert.equal(validateCredentials({ ...good, applicationId: 'nope' }).length, 1);
  assert.equal(validateCredentials({ ...good, httpPath: '/etc/passwd' }).length, 1);
  assert.equal(validateCredentials({ ...good, secret: 'short' }).length, 1);
  assert.equal(validateCredentials({ ...good, secret: 'has a space in it 0123456789' }).length, 1);
  assert.ok(!validateCredentials({ ...good, secret: 'x' }).join(' ').includes('x '), 'a problem never repeats the secret');
});

test('the script file carries the digest, comments the admin steps and ends statements with semicolons', () => {
  const text = dbxScriptText(view(false));
  assert.match(text, /Digest ab12cd34ef567890/);
  assert.match(text, /-- Step 1: create the service principal/);
  assert.match(text, /GRANT USE CATALOG ON CATALOG `sales` TO `0b1c2d3e[^`]*`;/);
  assert.match(dbxRemovalText(view(false)), /-- delete the service principal/);
});

test('the placeholder is detected until the application id is given', () => {
  assert.equal(needsApplicationId(view(true)), true);
  assert.equal(needsApplicationId(view(false)), false);
});

test('the workspace clears the secret from page state before the request is made, and never stores it', async () => {
  const { readFileSync } = await import('node:fs');
  const source = readFileSync(`${process.cwd()}/src/connect/DatabricksWorkspace.tsx`, 'utf8');
  const cleared = source.indexOf("setSecret('')");
  const sent = source.indexOf('completeDatabricks(cfg');
  assert.ok(cleared > 0 && sent > cleared, 'setSecret("") must come before the request');
  assert.ok(!/localStorage|sessionStorage|console\./.test(source), 'no storage or logging in the screen');
});

test('expiry is said in plain words, and a paused connection says what to do', () => {
  assert.match(expiryText('active', 90), /good for 90 more days/);
  assert.match(expiryText('active', 14), /ends in 14 days/);
  assert.match(expiryText('active', 1), /ends in 1 day\./);
  assert.match(expiryText('active', -2), /expired/);
  assert.match(expiryText('suspended', 80), /Paused.*new secret/);
  assert.match(expiryText('active', null), /unknown/);
});

test('a new secret is checked before it is sent, and the problem never repeats it', () => {
  assert.deepEqual(validateSecret('a-long-enough-secret-value'), []);
  assert.equal(validateSecret('short').length, 1);
  assert.ok(!validateSecret('has a space inside').join(' ').includes('has a space'));
});

test('rotation clears the new secret from page state before the request is made', async () => {
  const { readFileSync } = await import('node:fs');
  const source = readFileSync(`${process.cwd()}/src/connect/DatabricksWorkspace.tsx`, 'utf8');
  const fn = source.slice(source.indexOf('const submitRotation'));
  assert.ok(fn.indexOf("setRotateSecret('')") > 0 && fn.indexOf('rotateDbxSecret(cfg') > fn.indexOf("setRotateSecret('')"));
});
