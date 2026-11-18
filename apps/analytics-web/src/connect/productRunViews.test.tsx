import assert from 'node:assert/strict';
import { test } from 'node:test';

import { renderToStaticMarkup } from 'react-dom/server';

import { DraftForm, PlanPanel, ProductRunStage, RegisterForm, RunPanel, ScriptReview, SetupPanel, Stepper } from './productRunViews';
import type { ProductSetupView, RegistrationView, RunView, ScriptView } from './types';

const noop = () => undefined;
const script: ScriptView = {
  reg_suffix: 'a1b2c3d4', summary: ['Creates a setup role.', 'While signed in as the setup role, Compass can read those tables as well.'],
  statements: ['CREATE ROLE R', 'GRANT SELECT ON TABLE "A"."B"."C" TO ROLE R WITH GRANT OPTION'], teardown: ['DROP ROLE R'], digest: 'ab12cd34ef567890'.repeat(4),
};
const registration: RegistrationView = {
  registration_id: 'reg-00000001', host: 'acme.snowflakecomputing.com', setup_role: 'COMPASS_REG_SETUP_A1B2C3D4', database: 'ANALYTICS',
  ceiling_tables: ['analytics.public.orders', 'analytics.public.customers'], ceiling: [{ schema: 'PUBLIC', table: 'ORDERS' }, { schema: 'PUBLIC', table: 'CUSTOMERS' }], setup_user_count: 1,
};
const setup = (status: ProductSetupView['status']): ProductSetupView => ({
  setup_id: 's-0000000001', connection_id: 'c1', registration_id: 'reg-00000001', status, approved: status !== 'awaiting_approval', signed_in: status === 'ready_to_run',
  summary: ['Create one read-only role (R) and one service user.'], statements: ['CREATE ROLE R', 'GRANT SELECT ON TABLE "A"."B"."C" TO ROLE R'], teardown: ['DROP ROLE R'], digest: 'ab12cd34ef567890'.repeat(4),
});
const run = (over: Partial<RunView>): RunView => ({ ok: true, code: null, created: ['role', 'user'], leftovers: [], connection_id: 'c1', connection_status: 'active', failed_checks: [], ...over });

test('the stage is honest: internal testing, not run end to end, no security review, and never an unearned claim', () => {
  const html = renderToStaticMarkup(<ProductRunStage />);
  assert.match(html, /internal testing/);
  assert.match(html, /not run end to end for a customer/);
  assert.match(html, /no security review/);
  assert.match(html, /never sees your password/);
  assert.doesNotMatch(html, /supported|certified|SOC ?2|compliant|enterprise-grade|secure by|production-ready/i);
});

test('the draft form lists problems and turns off for a viewer who cannot manage', () => {
  const html = renderToStaticMarkup(<DraftForm database="" tablesText="" usersText="" credit="1" problems={['Name the database']} busy={false} canManage={false} onChange={noop} onSubmit={noop} />);
  assert.match(html, /data-testid="problems"/);
  assert.match(html, /Name the database/);
  assert.match(html, /<button[^>]*disabled/);
  assert.match(html, /This is the ceiling/);
});

test('the registration script review shows what the admin is told, the exact statements and the next steps', () => {
  const html = renderToStaticMarkup(<ScriptReview script={script} onDownload={noop} onCopy={noop} onDownloadRemoval={noop} />);
  for (const line of script.summary) assert.ok(html.includes(line));
  assert.match(html, /can read those tables as well/);
  assert.match(html, /The exact statements \(2\)/);
  assert.match(html, /WITH GRANT OPTION/);
  assert.match(html, /ab12cd34ef567890/);
  assert.match(html, /OAUTH_CLIENT_ID/);
  assert.match(html, /CURRENT_ACCOUNT/);
  assert.match(html, /not a secret/);
  for (const label of ['Download script', 'Copy script', 'Download removal script']) assert.ok(html.includes(label));
});

test('registering asks for the client id and the account locator, nothing secret', () => {
  const html = renderToStaticMarkup(<RegisterForm registrationId="reg-a1b2c3d4" host="" clientId="" locator="" busy={false} onChange={noop} onSubmit={noop} />);
  assert.match(html, /Client id/);
  assert.match(html, /Account locator/);
  assert.doesNotMatch(html, /type="password"|secret key|private key/i);
});

test('with nothing registered the plan panel says so; with a registration it offers only the exact ceiling tables', () => {
  assert.match(renderToStaticMarkup(<PlanPanel registrations={[]} selectedId="" selectedKeys={[]} busy={false} canManage onSelect={noop} onToggle={noop} onPlan={noop} />), /Nothing is registered yet/);
  const html = renderToStaticMarkup(<PlanPanel registrations={[registration]} selectedId="reg-00000001" selectedKeys={[]} busy={false} canManage onSelect={noop} onToggle={noop} onPlan={noop} />);
  assert.match(html, /PUBLIC\.ORDERS/);
  assert.match(html, /PUBLIC\.CUSTOMERS/);
  assert.equal((html.match(/type="checkbox"/g) ?? []).length, 2);
  assert.match(html, /<button[^>]*disabled[^>]*>Review what will be created/); // nothing picked yet
  const picked = renderToStaticMarkup(<PlanPanel registrations={[registration]} selectedId="reg-00000001" selectedKeys={['PUBLIC.ORDERS']} busy={false} canManage onSelect={noop} onToggle={noop} onPlan={noop} />);
  assert.doesNotMatch(picked, /<button[^>]*disabled[^>]*>Review what will be created/);
});

test('the stepper marks done, current and to-do steps', () => {
  const html = renderToStaticMarkup(<Stepper current={2} />);
  assert.match(html, /data-state="done">Review/);
  assert.match(html, /data-state="done">Approve/);
  assert.match(html, /data-state="current">Sign in/);
  assert.match(html, /data-state="todo">Run/);
});

test('each setup state offers exactly one next action, and cancel always', () => {
  const approve = renderToStaticMarkup(<SetupPanel setup={setup('awaiting_approval')} step={1} busy={false} onApprove={noop} onSignIn={noop} onRun={noop} onCancel={noop} />);
  assert.match(approve, /Approve these statements/);
  assert.doesNotMatch(approve, /Sign in on Snowflake|Run the setup/);
  assert.match(approve, /GRANT SELECT ON TABLE/);
  assert.match(approve, /if they change you must read them again/);
  const signIn = renderToStaticMarkup(<SetupPanel setup={setup('awaiting_sign_in')} step={2} busy={false} onApprove={noop} onSignIn={noop} onRun={noop} onCancel={noop} />);
  assert.match(signIn, /Sign in on Snowflake/);
  assert.doesNotMatch(signIn, /Approve these statements|Run the setup/);
  assert.match(signIn, /data-testid="sign-in-note"/);
  assert.match(signIn, /never sees your password/);
  const ready = renderToStaticMarkup(<SetupPanel setup={setup('ready_to_run')} step={3} busy={false} onApprove={noop} onSignIn={noop} onRun={noop} onCancel={noop} />);
  assert.match(ready, /Run the setup/);
  assert.doesNotMatch(ready, /Approve these statements|Sign in on Snowflake/);
  for (const html of [approve, signIn, ready]) assert.match(html, />Cancel</);
});

test('buttons are off while busy', () => {
  const html = renderToStaticMarkup(<SetupPanel setup={setup('ready_to_run')} step={3} busy onApprove={noop} onSignIn={noop} onRun={noop} onCancel={noop} />);
  assert.equal((html.match(/<button[^>]*disabled/g) ?? []).length, 2);
});

test('a result says connected only when the connection test passed', () => {
  const ok = renderToStaticMarkup(<RunPanel run={run({})} />);
  assert.match(ok, /Connected and tested/);
  assert.match(ok, /Nothing in the customer(&#x27;|')s warehouse was changed except the read-only role and user/);
  const untested = renderToStaticMarkup(<RunPanel run={run({ connection_status: 'pending' })} />);
  assert.match(untested, /Set up, not tested yet/);
  assert.doesNotMatch(untested, /Connected and tested/);
  const wrong = renderToStaticMarkup(<RunPanel run={run({ ok: false, code: 'wrong_account', connection_status: 'removed', created: [] })} />);
  assert.match(wrong, /data-tone="bad"/);
  assert.match(wrong, /Nothing was created/);
  assert.doesNotMatch(wrong, /Nothing in the customer/);
});
