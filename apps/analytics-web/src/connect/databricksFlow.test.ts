import assert from 'node:assert/strict';
import { test } from 'node:test';

import type { DbxScriptView } from './databricksApi';
import { JOURNEY, ROTATE_WITHIN_DAYS, dbxRemovalText, dbxScriptText, enrolStatusText, expiryText, needsApplicationId, removeCommand, rotateStatusText, validateCredentials, validateDbxDetails, validateSecret } from './databricksFlow';

const ok = { host: 'dbc-1234abcd-5678.cloud.databricks.com', catalog: 'sales', warehouse: 'Starter', tablesText: 'data.orders\ndata.customers' };
const view = (placeholder: boolean): DbxScriptView => ({
  setup_id: 's', connection_id: 'dbx-1', host: ok.host, catalog: 'sales', tables: ['data.orders'], summary: [], digest: 'ab12cd34ef567890', principal_name: 'compass-conn-0a1b2c3d', warehouse_name: 'Demo Warehouse',
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
  assert.match(expiryText('active', ROTATE_WITHIN_DAYS), new RegExp(`ends in ${ROTATE_WITHIN_DAYS} days`));
  assert.match(expiryText('active', ROTATE_WITHIN_DAYS + 1), /good for 31 more days/);
  assert.match(expiryText('active', 1), /ends in 1 day\./);
  assert.match(expiryText('active', -2), /expired/);
  assert.match(expiryText('suspended', 80), /Paused.*Rotate the secret/);
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

test('each state of the enrol file has plain words, and a refusal says nothing is left behind', () => {
  assert.match(enrolStatusText(null), /Waiting for your admin/);
  assert.match(enrolStatusText({ state: 'waiting', code: null, checks: [], expires_in_minutes: 1 }), /in 1 minute\./);
  assert.match(enrolStatusText({ state: 'waiting', code: null, checks: [], expires_in_minutes: 29 }), /29 minutes/);
  assert.match(enrolStatusText({ state: 'expired', code: null, checks: [], expires_in_minutes: 0 }), /expired/);
  assert.match(enrolStatusText({ state: 'connected', code: null, checks: [], expires_in_minutes: 0 }), /Connected/);
  assert.match(enrolStatusText({ state: 'refused', code: 'connection_test_failed', checks: [], expires_in_minutes: 0 }), /nothing is left behind/);
});

test('the remove command is offered only for a well-formed application id', () => {
  assert.equal(removeCommand('0B1C2D3E-4F50-6172-8394-A5B6C7D8E9F0'), 'python3 compass-enrol.py --remove 0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0');
  assert.equal(removeCommand(''), '');
  assert.equal(removeCommand('x; rm -rf /'), '');
});

test('a rotation by file has plain words for each state, and a failed one says the connection is as it was', () => {
  assert.match(rotateStatusText({ state: 'waiting', code: null, checks: [], expires_in_minutes: 12 }), /in 12 minutes/);
  assert.match(rotateStatusText({ state: 'connected', code: null, checks: [], expires_in_minutes: 0 }), /older secrets were revoked/);
  assert.match(rotateStatusText({ state: 'refused', code: 'connection_test_failed', checks: [], expires_in_minutes: 0 }), /connection is as it was/);
  assert.match(rotateStatusText({ state: 'expired', code: null, checks: [], expires_in_minutes: 0 }), /expired/);
});

test('the warning window matches the server (30 days before a 90-day secret ends)', () => {
  assert.equal(ROTATE_WITHIN_DAYS, 30);
});

test('the Databricks tab becomes ready from its own capabilities route, never the generic one that answers 503 without Snowflake Connect', async () => {
  const { getDbxCapabilities } = await import('./databricksApi');
  const seen: { url: string; auth: string | null }[] = [];
  const real = globalThis.fetch;
  globalThis.fetch = (async (url: string, init?: RequestInit) => {
    seen.push({ url: String(url), auth: new Headers(init?.headers).get('Authorization') });
    return new Response(JSON.stringify({ engine: 'databricks', setup_method: 'admin_pre_creates_principal', can_manage: true, stage: 's' }), { status: 200 });
  }) as typeof fetch;
  try {
    const caps = await getDbxCapabilities({ baseUrl: '', token: 'tok' });
    assert.equal(caps.can_manage, true);
    assert.deepEqual(seen, [{ url: '/api/v2/connect/databricks/capabilities', auth: 'Bearer tok' }]);
  } finally {
    globalThis.fetch = real;
  }
  const source = (await import('node:fs')).readFileSync('src/connect/ConnectWorkspace.tsx', 'utf8');
  assert.match(source, /mode === 'databricks'[\s\S]{0,400}getDbxCapabilities\(cfg\)/, 'the Open button must ask the Databricks route in the Databricks tab');
  assert.match(source, /<DatabricksWorkspace cfg=\{cfg\} ready=\{dbxCapabilities !== null\}/, 'the tab is ready from its own capabilities');
});

test('the journey has four steps and the right one is current for each state', async () => {
  const { journeyStep, enrolCommand, profileLooksWrong } = await import('./databricksFlow');
  assert.deepEqual([...JOURNEY], ['Sign in', 'Choose your data', 'Run one file', 'Connected']);
  assert.equal(journeyStep({ ready: false, hasSetup: false, connected: false }), 0);
  assert.equal(journeyStep({ ready: true, hasSetup: false, connected: false }), 1);
  assert.equal(journeyStep({ ready: true, hasSetup: true, connected: false }), 2);
  assert.equal(journeyStep({ ready: true, hasSetup: true, connected: true }), 3);
  assert.equal(journeyStep({ ready: false, hasSetup: true, connected: true }), 0, 'signed out is always step 1');
  assert.equal(enrolCommand('compass-enrol.py', ''), 'python3 compass-enrol.py');
  assert.equal(enrolCommand('compass-enrol.py', ' social-intelligence-free '), 'python3 compass-enrol.py --profile social-intelligence-free');
  for (const bad of ['a b', 'x;y', '$(id)', '`x`', 'a/b', 'x'.repeat(65)]) assert.equal(enrolCommand('compass-enrol.py', bad), 'python3 compass-enrol.py', bad);
  assert.equal(profileLooksWrong('x;y'), true);
  assert.equal(profileLooksWrong(''), false);
});
