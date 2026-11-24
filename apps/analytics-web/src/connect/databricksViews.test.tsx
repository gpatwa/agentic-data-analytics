import assert from 'node:assert/strict';
import { test } from 'node:test';

import { renderToStaticMarkup } from 'react-dom/server';

import type { DbxScriptView } from './databricksApi';
import { DatabricksStage, DbxConnectedSummary, DbxConnections, DbxCredentialsForm, DbxDetailsForm, DbxEnrolPanel, DbxRemovedNote, DbxResultPanel, DbxRotatePanel, DbxScriptPanel, DbxSignIn, DbxStepper, DbxTrackChooser } from './databricksViews';

const noop = () => undefined;
const view: DbxScriptView = {
  setup_id: 's', connection_id: 'dbx-1', host: 'h', catalog: 'sales', tables: ['data.orders'], summary: ['Creates one service principal for Compass.'], digest: 'ab12cd34ef567890', principal_name: 'compass-conn-0a1b2c3d', warehouse_name: 'Demo Warehouse',
  steps: [{ kind: 'admin', text: 'create the service principal' }, { kind: 'sql', text: 'GRANT SELECT ON TABLE `a`.`b`.`c` TO `<APPLICATION_ID>`' }], removal: [],
};

test('the stage is honest and claims nothing it has not earned', () => {
  const html = renderToStaticMarkup(<DatabricksStage />);
  assert.match(html, /internal testing/);
  assert.match(html, /only run against fakes/);
  assert.match(html, /no security review|none of it has had a security review/);
  assert.match(html, /holds no admin credential/);
  assert.doesNotMatch(html, /supported|certified|SOC ?2|compliant|enterprise-grade|secure by|production-ready/i);
});

test('the details form lists problems and turns off for a viewer who cannot manage', () => {
  const html = renderToStaticMarkup(<DbxDetailsForm host="" catalog="" warehouse="" tablesText="" problems={['Name the catalog']} busy={false} canManage={false} onChange={noop} onSubmit={noop} />);
  assert.match(html, /data-testid="problems"/);
  assert.match(html, /Name the catalog/);
  assert.match(html, /<button[^>]*disabled/);
});

test('the script panel keeps downloads off until the application id fills the grants', () => {
  const waiting = renderToStaticMarkup(<DbxScriptPanel view={view} applicationId="" busy={false} onApplicationId={noop} onApply={noop} onDownload={noop} onCancel={noop} />);
  assert.match(waiting, /placeholder until you do/);
  assert.match(waiting, /Download script<\/button>/);
  assert.match(waiting.match(/<button[^>]*>Download script/)?.[0] ?? '', /disabled/);
  const ready = renderToStaticMarkup(<DbxScriptPanel view={{ ...view, steps: [{ kind: 'sql', text: 'GRANT x TO `abc`' }] }} applicationId="abc" busy={false} onApplicationId={noop} onApply={noop} onDownload={noop} onCancel={noop} />);
  assert.doesNotMatch(ready.match(/<button[^>]*>Download script/)?.[0] ?? '', /disabled/);
});

test('the secret field is a password field with no autocomplete and no value echoed back into other fields', () => {
  const html = renderToStaticMarkup(<DbxCredentialsForm applicationId="app" secret="" httpPath="" problems={[]} busy={false} onChange={noop} onSubmit={noop} />);
  assert.match(html, /type="password"[^>]*autoComplete="off"|autoComplete="off"[^>]*type="password"/);
  assert.match(html, /never shown again/);
});

test('a failed check names each table and shows the plain-words error; a pass says what was and was not checked', () => {
  const bad = renderToStaticMarkup(<DbxResultPanel errorCode="" result={{ ok: false, code: 'connection_test_failed', checks: [{ table: 'sales.data.orders', passed: false }], connection_status: 'removed' }} />);
  assert.match(bad, /Not connected/);
  assert.match(bad, /Cannot read sales\.data\.orders/);
  assert.match(bad, /secret was deleted/);
  const good = renderToStaticMarkup(<DbxResultPanel errorCode="" result={{ ok: true, code: null, checks: [{ table: 'sales.data.orders', passed: true }], connection_status: 'active' }} />);
  assert.match(good, /Connected and checked/);
  assert.match(good, /not tested against anything your admin should/);
  assert.match(renderToStaticMarkup(<DbxResultPanel errorCode="invalid_secret" result={null} />), /Nothing was stored/);
});

const conn = (over: Partial<import('./databricksApi').DbxConnection> = {}) => ({
  connection_id: 'dbx-1', host: 'dbc-1.cloud.databricks.com', catalog: 'sales', tables: ['sales.data.orders'], status: 'active', expires_at: null, expires_in_days: 80, needs_rotation: false, warm_up: false, application_id: '0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0', ...over,
});
const props = { rotating: null, secret: '', problems: [], confirming: null, busy: false, onRotate: noop, onSecret: noop, onSubmitSecret: noop, onCheck: noop, onAskRemove: noop, onRemove: noop };

test('connections show status and expiry, raise an alert only when rotation is due, and never show a secret', () => {
  const calm = renderToStaticMarkup(<DbxConnections {...props} connections={[conn()]} />);
  assert.match(calm, /good for 80 more days/);
  assert.doesNotMatch(calm, /role="alert"/);
  const due = renderToStaticMarkup(<DbxConnections {...props} connections={[conn({ expires_in_days: 3, needs_rotation: true })]} />);
  assert.match(due, /role="alert"/);
  assert.match(due, /ends in 3 days/);
  const paused = renderToStaticMarkup(<DbxConnections {...props} connections={[conn({ status: 'suspended', needs_rotation: true })]} />);
  assert.match(paused, /Paused/);
  assert.equal(renderToStaticMarkup(<DbxConnections {...props} connections={[]} />), '');
});

test('the rotation form is a password field that keeps the old secret until the new one is checked; removal asks first', () => {
  const html = renderToStaticMarkup(<DbxConnections {...props} connections={[conn()]} rotating="dbx-1" confirming="dbx-1" />);
  assert.match(html, /type="password"/);
  assert.match(html, /old secret is kept until the new one is checked/);
  assert.match(html, /Confirm removal/);
  assert.match(html, /delete its secret now/);
});

test('the warm-up switch is off by default, says what it costs, and is off for a viewer who cannot manage or a paused connection', () => {
  const off = renderToStaticMarkup(<DbxConnections {...props} connections={[conn()]} onWarmUp={noop} />);
  assert.match(off, /type="checkbox"/);
  assert.doesNotMatch(off, /checked=""/);
  assert.match(off, /uses warehouse time/);
  assert.match(off, /starts it if it was stopped/);
  assert.match(off, /adds no access/);
  const on = renderToStaticMarkup(<DbxConnections {...props} connections={[conn({ warm_up: true })]} onWarmUp={noop} />);
  assert.match(on, /checked=""/);
  const viewer = renderToStaticMarkup(<DbxConnections {...props} connections={[conn()]} onWarmUp={noop} canManage={false} />);
  assert.match(viewer, /type="checkbox"[^>]*disabled/);
  assert.doesNotMatch(renderToStaticMarkup(<DbxConnections {...props} connections={[conn({ status: 'suspended' })]} onWarmUp={noop} />), /checkbox/);
});

const file = { filename: 'compass-enrol.py', content: 'print("x")', digest: '1533e320a7cfd48a', expires_in_minutes: 30, run: 'python3 compass-enrol.py' };
const enrolProps = { view, file: null, status: null, profile: '', busy: false, onProfile: noop, onIssue: noop, onDownload: noop, onCopy: noop, onDone: noop, onCancel: noop };

test('the chooser recommends the one file and says nobody copies the secret; the other choice stays available', () => {
  const html = renderToStaticMarkup(<DbxTrackChooser track="enrol" onChoose={noop} />);
  assert.match(html, /One file \(recommended\)/);
  assert.match(html, /nobody copies or sees the secret/);
  assert.match(html, /Run the script yourself/);
  assert.match(html, /aria-checked="true"[^>]*data-selected="true"[^>]*>One file/);
  assert.match(renderToStaticMarkup(<DbxTrackChooser track="manual" onChoose={noop} />), /paste the identity/);
});

test('before the file is issued the panel says what it does and offers only to get it', () => {
  const html = renderToStaticMarkup(<DbxEnrolPanel {...enrolProps} />);
  assert.match(html, /plain Python you can read first/);
  assert.match(html, /nobody copies a secret/);
  assert.match(html, /Demo Warehouse/);
  assert.match(html, /<code>--remove<\/code>/);
  assert.doesNotMatch(html, /secret you give to Compass once|run the removal script/, 'the manual track\'s wording must not appear in the one-file flow');
  assert.match(html, /removes everything it created/);
  assert.match(html, /works once, for 30 minutes/);
  assert.match(html, />Get the file</);
  assert.doesNotMatch(html, /file-digest/);
});

test('once issued it shows the digest to compare, the run command and the live status', () => {
  const waiting = renderToStaticMarkup(<DbxEnrolPanel {...enrolProps} file={file} status={{ state: 'waiting', code: null, checks: [], expires_in_minutes: 12 }} />);
  assert.match(waiting, /data-testid="file-digest">1533e320a7cfd48a/);
  assert.match(waiting, /--digest/);
  assert.match(waiting, /stops working in 12 minutes/);
  assert.match(waiting, /Download compass-enrol\.py/);
  assert.doesNotMatch(waiting, />Done</);
});

test('a refusal shows its plain-words error and a connection shows each table with a Done button', () => {
  const refused = renderToStaticMarkup(<DbxEnrolPanel {...enrolProps} file={file} status={{ state: 'refused', code: 'registration_dangerous', checks: [], expires_in_minutes: 0 }} />);
  assert.match(refused, /data-code="registration_dangerous"/);
  assert.match(refused, /more access than it should/);
  assert.match(refused, /nothing is left behind/);
  const connected = renderToStaticMarkup(<DbxEnrolPanel {...enrolProps} file={file} status={{ state: 'connected', code: null, checks: [{ table: 'sales.data.orders', passed: true }], expires_in_minutes: 0 }} />);
  assert.match(connected, /Can read sales\.data\.orders/);
  assert.match(connected, />Done</);
  assert.doesNotMatch(connected, />Back</);
});

test('removal tells the admin the exact command, only when the application id is real', () => {
  const removal = { connection_id: 'dbx-1', status: 'removed', credential_deleted: true, teardown: [], note: 'Compass can no longer connect.' };
  const ok = renderToStaticMarkup(<DbxRemovedNote removal={removal} applicationId="0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0" />);
  assert.match(ok, /data-testid="remove-command">python3 compass-enrol\.py --remove 0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0/);
  const bad = renderToStaticMarkup(<DbxRemovedNote removal={removal} applicationId="rm -rf /" />);
  assert.doesNotMatch(bad, /remove-command|rm -rf/);
  assert.match(bad, /removal script/);
});

test('each connection offers rotation with a file first and pasting a secret second', () => {
  const html = renderToStaticMarkup(<DbxConnections {...props} onRotateFile={noop} connections={[conn()]} rotating="dbx-1" />);
  assert.ok(html.indexOf('Rotate with a file') > 0 && html.indexOf('Rotate with a file') < html.indexOf('Paste a new secret'));
  assert.match(html, /Prefer &quot;Rotate with a file&quot;: nothing to paste/);
  assert.doesNotMatch(renderToStaticMarkup(<DbxConnections {...props} connections={[conn()]} />), /Rotate with a file/);  // not offered where the screen cannot do it
});

const rotateProps = { host: 'dbc-1.cloud.databricks.com', file: null, status: null, busy: false, onIssue: noop, onDownload: noop, onClose: noop };

test('the rotate panel says what the file does, then shows the digest and the live state', () => {
  const before = renderToStaticMarkup(<DbxRotatePanel {...rotateProps} />);
  assert.match(before, /Rotate the secret for dbc-1\.cloud\.databricks\.com/);
  assert.match(before, /Only then does the file delete the older secrets/);
  assert.match(before, />Get the file</);
  const waiting = renderToStaticMarkup(<DbxRotatePanel {...rotateProps} file={{ ...file, filename: 'compass-rotate.py' }} status={{ state: 'waiting', code: null, checks: [], expires_in_minutes: 9 }} />);
  assert.match(waiting, /data-testid="rotate-digest">1533e320a7cfd48a/);
  assert.match(waiting, /in 9 minutes/);
  assert.match(waiting, />Close</);
  const failed = renderToStaticMarkup(<DbxRotatePanel {...rotateProps} file={file} status={{ state: 'refused', code: 'connection_test_failed', checks: [{ table: 'sales.data.orders', passed: false }], expires_in_minutes: 0 }} />);
  assert.match(failed, /Cannot read sales\.data\.orders/);
  assert.match(failed, /connection is as it was/);
  const done = renderToStaticMarkup(<DbxRotatePanel {...rotateProps} file={file} status={{ state: 'connected', code: null, checks: [{ table: 'sales.data.orders', passed: true }], expires_in_minutes: 0 }} />);
  assert.match(done, />Done</);
});

test('the stepper shows where the admin is: done, current and still to do, with the current one marked for assistive technology', () => {
  const html = renderToStaticMarkup(<DbxStepper current={2} />);
  for (const label of ['Sign in', 'Choose your data', 'Run one file', 'Connected']) assert.match(html, new RegExp(label));
  assert.equal((html.match(/data-state="done"/g) ?? []).length, 2);
  assert.equal((html.match(/data-state="current"/g) ?? []).length, 1);
  assert.equal((html.match(/data-state="todo"/g) ?? []).length, 1);
  assert.match(html, /aria-current="step"[^>]*>[\s\S]*Run one file/);
});

test('the sign-in step says what to paste, who may, and where the local token is', () => {
  const html = renderToStaticMarkup(<DbxSignIn token="" busy={false} options={null} onToken={noop} onOpen={noop} onSignIn={noop} />);
  assert.match(html, /Step 1 of 4: Sign in/);
  assert.match(html, /allowed to manage connections/);
  assert.match(html, /company login is not set up in this build/);
  assert.match(html, /local stack/);
  assert.match(html, /type="password"/);
  assert.match(html, /<button[^>]*disabled[^>]*>Open/, 'Open stays off until there is a token');
});

test('the details step is step 2 and its button says Continue, not a jargon word', () => {
  const html = renderToStaticMarkup(<DbxDetailsForm host="" catalog="" warehouse="" tablesText="" problems={[]} busy={false} canManage onChange={noop} onSubmit={noop} />);
  assert.match(html, /Step 2 of 4: What may Compass read\?/);
  assert.match(html, />Continue</);
  assert.match(html, /Nothing is created yet/);
});

test('the run-the-file step is three numbered actions: get it, run it with a command to copy, watch it', () => {
  const before = renderToStaticMarkup(<DbxEnrolPanel {...enrolProps} />);
  assert.match(before, /Step 3 of 4: Run one file/);
  assert.match(before, /Get the file\./);
  assert.match(before, /Run it in your terminal\./);
  assert.match(before, /Watch it here\./);
  assert.match(before, /Nothing has run yet/);
  assert.doesNotMatch(before, /run-command/);
  const after = renderToStaticMarkup(<DbxEnrolPanel {...enrolProps} file={file} profile="my-profile" status={{ state: 'waiting', code: null, checks: [], expires_in_minutes: 20 }} />);
  assert.match(after, /data-testid="run-command">python3 compass-enrol\.py --profile my-profile/);
  assert.match(after, />Copy command</);
  assert.match(after, /in 20 minutes/);
  const odd = renderToStaticMarkup(<DbxEnrolPanel {...enrolProps} file={file} profile="x; rm -rf /" />);
  assert.match(odd, /data-testid="run-command">python3 compass-enrol\.py</, 'an odd profile never reaches the copied command');
  assert.match(odd, /left out of the command/);
});

test('the connected step lists what Compass can read and tells the admin what to do next', () => {
  const html = renderToStaticMarkup(<DbxConnectedSummary tables={['sales.data.orders']} checks={[{ table: 'sales.data.orders', passed: true }]} onDone={noop} />);
  assert.match(html, /Step 4 of 4: Connected/);
  assert.match(html, /Can read sales\.data\.orders/);
  assert.match(html, /What to do next/);
  assert.match(html, /Ask a question/);
  assert.match(html, /warm the warehouse/);
  assert.match(html, /30 days ahead/);
  assert.match(html, />Connect another workspace</);
});

test('when the API offers a sign-in the step has a Sign in button, and pasting a token is tucked away as the other way', () => {
  const html = renderToStaticMarkup(<DbxSignIn token="" busy={false} options={{ dev_login: true, label: 'the local test administrator (this computer only)' }} onToken={noop} onOpen={noop} onSignIn={noop} />);
  assert.match(html, /data-testid="sign-in"[^>]*>Sign in</);
  assert.match(html, /Sign in as the local test administrator/);
  assert.match(html, /<details[^>]*><summary>Use an access token instead<\/summary>/);
  assert.doesNotMatch(html, /company login is not set up/);
  assert.match(renderToStaticMarkup(<DbxSignIn token="" busy options={{ dev_login: true, label: 'x' }} onToken={noop} onOpen={noop} onSignIn={noop} />), /<button[^>]*disabled[^>]*data-testid="sign-in"|data-testid="sign-in"[^>]*disabled/);
});
