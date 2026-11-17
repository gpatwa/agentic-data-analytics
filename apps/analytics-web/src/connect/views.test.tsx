import assert from 'node:assert/strict';
import { test } from 'node:test';

import { renderToStaticMarkup } from 'react-dom/server';

import { describeError, ERROR_COPY } from './copy';
import type { Capabilities, ConnectionView, RemovalView, SetupView, VerifyView } from './types';
import { ChecksPanel, ConnectedPage, DetailsForm, ErrorNotice, NEVER, ReviewPage, StagePanel } from './views';

const capabilities = (can_manage: boolean): Capabilities => ({
  can_manage,
  engines: [{
    engine: 'snowflake',
    stage: 'Connect mode is in internal testing. It has not had a security review and has not run against a customer account.',
    setup_methods: ['script'], product_run_setup: { available: false, reason: 'admin_sign_in_not_built' }, credential: { kind: 'key_pair', note: '' },
  }],
});
const setup: SetupView = {
  setup_id: 's1', connection_id: 'c1', status: 'awaiting_admin', method: 'script', host: 'acme.snowflakecomputing.com', account: 'acme',
  database: 'ANALYTICS', tables: ['analytics.public.orders', 'analytics.public.customers'],
  summary: ['Create one read-only role (R) and one service user with a key you can revoke.', 'It cannot write, change or delete anything, and cannot read any other table.'],
  statements: ['CREATE ROLE R', 'GRANT SELECT ON TABLE "ANALYTICS"."PUBLIC"."ORDERS" TO ROLE R'], teardown: ['DROP ROLE R'], digest: 'ab12cd34ef567890'.repeat(4),
};
const noop = () => undefined;
const connection: ConnectionView = { connection_id: 'c1', engine: 'snowflake', host: 'acme.snowflakecomputing.com', account: 'acme', status: 'active', tables: ['analytics.public.orders'], auth_method: 'key_pair' };

test('the stage panel is honest and never claims more than the evidence', () => {
  const html = renderToStaticMarkup(<StagePanel capabilities={capabilities(true)} />);
  assert.match(html, /internal testing/);
  assert.match(html, /not had a security review/);
  assert.match(html, /Databricks is not available/);
  assert.match(html, /script your admin runs once/);
  assert.doesNotMatch(html, /supported|certified|SOC ?2|compliant|enterprise-grade|secure by/i);
});

test('a viewer who cannot manage sees why, and the form button is off', () => {
  assert.match(renderToStaticMarkup(<StagePanel capabilities={capabilities(false)} />), /connection managers/);
  const html = renderToStaticMarkup(<DetailsForm host="" database="" tablesText="" problems={[]} busy={false} canManage={false} onChange={noop} onSubmit={noop} />);
  assert.match(html, /<button[^>]*disabled/);
});

test('problems are listed where the person is looking', () => {
  const html = renderToStaticMarkup(<DetailsForm host="x" database="" tablesText="" problems={['Name the database']} busy={false} canManage onChange={noop} onSubmit={noop} />);
  assert.match(html, /data-testid="problems"/);
  assert.match(html, /Name the database/);
  assert.match(html, /exact names|as Snowflake shows them/);
});

test('the review page lists what is created, what it can read, what Compass never does, and the exact statements', () => {
  const html = renderToStaticMarkup(<ReviewPage setup={setup} busy={false} onDownload={noop} onCopy={noop} onVerify={noop} onCancel={noop} />);
  for (const line of setup.summary) assert.ok(html.includes(line.replace(/&/g, '&amp;')));
  for (const table of setup.tables) assert.ok(html.includes(table));
  assert.equal((html.match(/<li>(write to|keep copies|ask for|run SQL)/g) ?? []).length, NEVER.length);
  assert.match(html, /The exact statements \(2\)/);
  assert.match(html, /GRANT SELECT ON TABLE/);
  assert.match(html, /ab12cd34ef567890/);
  assert.match(html, /lapses in 24 hours/);
  assert.match(html, /Nothing is created in your warehouse until your admin runs the script/);
  for (const label of ['Download script', 'Copy script', 'test the connection', 'Cancel setup']) assert.ok(html.includes(label));
});

test('a passing test shows only the two checks that ran on their data, and says the rest did not', () => {
  const result: VerifyView = {
    ok: true, code: null, connection_status: 'active',
    checks: [{ name: 'read_only_grants', label: 'Your access is read-only', passed: true }, { name: 'metadata_read', label: 'We can read your table layout', passed: true }],
    not_run_on_customer_data: ['A governed query ran', 'Nothing sensitive leaks'],
  };
  const html = renderToStaticMarkup(<ChecksPanel result={result} />);
  assert.match(html, /<h2>Connected<\/h2>/);
  assert.equal((html.match(/data-passed="true"/g) ?? []).length, 2);
  assert.match(html, /These two checks run on your tables\. The others run on sample data, not on yours/);
  assert.doesNotMatch(html, /8 checks|eight checks|all checks/i);
});

test('a failing test names the check that failed and what to do', () => {
  const result: VerifyView = {
    ok: false, code: 'connection_test_failed', connection_status: 'pending',
    checks: [{ name: 'read_only_grants', label: 'Your access is read-only', passed: false }, { name: 'metadata_read', label: 'We can read your table layout', passed: true }],
    not_run_on_customer_data: [],
  };
  const html = renderToStaticMarkup(<ChecksPanel result={result} />);
  assert.match(html, /did not pass/);
  assert.match(html, /data-check="read_only_grants" data-passed="false"/);
  assert.match(html, /data-code="connection_test_failed"/);
  assert.match(html, /re-run the script/);
});

test('removing access asks first, in plain words, then hands back the removal script', () => {
  const asking = renderToStaticMarkup(<ConnectedPage connection={connection} confirming removal={null} busy={false} onAskRemove={noop} onConfirmRemove={noop} onKeep={noop} onDownloadTeardown={noop} />);
  assert.match(asking, /data-testid="confirm-remove"/);
  assert.match(asking, /deletes its key now and can no longer connect/);
  assert.match(asking, /until your admin runs the removal script/);
  const removal: RemovalView = { connection_id: 'c1', status: 'removed', credential_deleted: true, teardown: ['DROP USER U', 'DROP ROLE R'], teardown_digest: 'ee'.repeat(32), note: 'Compass can no longer connect.' };
  const done = renderToStaticMarkup(<ConnectedPage connection={connection} confirming={false} removal={removal} busy={false} onAskRemove={noop} onConfirmRemove={noop} onKeep={noop} onDownloadTeardown={noop} />);
  assert.match(done, /Access removed/);
  assert.match(done, /DROP USER U;/);
  assert.match(done, /Digest eeeeeeee/);
});

test('every error has a plain title and a next step, and unknown codes still show the code', () => {
  for (const [code, copy] of Object.entries(ERROR_COPY)) {
    assert.ok(copy.title.length > 10 && copy.action.length > 10, code);
    assert.doesNotMatch(`${copy.title} ${copy.action}`, /exception|traceback|stack|null|undefined/i, code);
  }
  assert.match(renderToStaticMarkup(<ErrorNotice code="weird_new_code" />), /weird_new_code/);
  assert.equal(describeError('ip_address_not_allowed').title, 'Use the warehouse hostname, not an IP address.');
});
