import assert from 'node:assert/strict';
import { test } from 'node:test';

import { renderToStaticMarkup } from 'react-dom/server';

import type { DbxScriptView } from './databricksApi';
import { DatabricksStage, DbxCredentialsForm, DbxDetailsForm, DbxResultPanel, DbxScriptPanel } from './databricksViews';

const noop = () => undefined;
const view: DbxScriptView = {
  setup_id: 's', connection_id: 'dbx-1', host: 'h', catalog: 'sales', tables: ['data.orders'], summary: ['Creates one service principal for Compass.'], digest: 'ab12cd34ef567890', principal_name: 'compass-conn-0a1b2c3d',
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
