import type { DbxConnection, DbxResult, DbxScriptView } from './databricksApi';
import { dbxRemovalText, dbxScriptText, expiryText, needsApplicationId } from './databricksFlow';
import { ErrorNotice } from './views';

export function DatabricksStage() {
  return (
    <section className="gv-card cn-stage" aria-label="Where this stands" data-view="dbx-stage">
      <h2>Connect Databricks: your admin creates a read-only identity</h2>
      <p className="cn-stage-note" role="note">
        This is in internal testing. The registration script ran on one test Databricks workspace; this screen and the way Compass stores the secret
        have only run against fakes, and none of it has had a security review.
      </p>
      <p className="cn-small">
        Your admin runs one script that creates a read-only service principal for exactly the tables you name. They give Compass its application id and
        secret once. Compass holds no admin credential, and never sees anyone&apos;s password.
      </p>
    </section>
  );
}

export interface DbxDetailsFormProps {
  host: string;
  catalog: string;
  warehouse: string;
  tablesText: string;
  problems: string[];
  busy: boolean;
  canManage: boolean;
  onChange: (field: 'host' | 'catalog' | 'warehouse' | 'tablesText', value: string) => void;
  onSubmit: () => void;
}

export function DbxDetailsForm(p: DbxDetailsFormProps) {
  return (
    <section className="gv-card" data-view="dbx-details" aria-label="What Compass may read">
      <h2>1. What may Compass read?</h2>
      <div className="cn-form">
        <label>Workspace hostname
          <input value={p.host} placeholder="dbc-1234abcd-5678.cloud.databricks.com" onChange={(e) => p.onChange('host', e.target.value)} />
        </label>
        <label>Catalog
          <input value={p.catalog} placeholder="sales" onChange={(e) => p.onChange('catalog', e.target.value)} />
        </label>
        <label>Tables, one per line as SCHEMA.TABLE
          <textarea rows={4} value={p.tablesText} placeholder={'data.orders\ndata.customers'} onChange={(e) => p.onChange('tablesText', e.target.value)} />
        </label>
        <label>SQL warehouse Compass may use (by name)
          <input value={p.warehouse} placeholder="Serverless Starter Warehouse" onChange={(e) => p.onChange('warehouse', e.target.value)} />
        </label>
      </div>
      {p.problems.length > 0 && (
        <ul className="cn-problems" data-testid="problems">{p.problems.map((problem) => <li key={problem}>{problem}</li>)}</ul>
      )}
      <button onClick={p.onSubmit} disabled={p.busy || !p.canManage}>Show the script</button>
    </section>
  );
}

export interface DbxScriptPanelProps {
  view: DbxScriptView;
  applicationId: string;
  busy: boolean;
  onApplicationId: (value: string) => void;
  onApply: () => void;
  onDownload: (kind: 'script' | 'removal') => void;
  onCancel: () => void;
}

export function DbxScriptPanel(p: DbxScriptPanelProps) {
  const waiting = needsApplicationId(p.view);
  return (
    <section className="gv-card" data-view="dbx-script" aria-label="The script your admin runs">
      <h2>2. Your admin runs this script</h2>
      <ul>{p.view.summary.map((line) => <li key={line}>{line}</li>)}</ul>
      <ol className="cn-steps">
        {p.view.steps.map((step, i) => (
          <li key={`${i}-${step.text}`}>{step.kind === 'sql' ? <code>{step.text}</code> : step.text}</li>
        ))}
      </ol>
      <p className="cn-small">
        After step 1 Databricks shows the principal&apos;s <strong>application id</strong>. Paste it here to fill in the grants.
        {waiting && ' The SQL below still has a placeholder until you do.'}
      </p>
      <div className="cn-form">
        <label>Application id
          <input value={p.applicationId} placeholder="0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0" autoComplete="off" onChange={(e) => p.onApplicationId(e.target.value)} />
        </label>
        <button onClick={p.onApply} disabled={p.busy || !p.applicationId.trim()}>Fill in the grants</button>
      </div>
      <p className="cn-small">Digest {p.view.digest}</p>
      <div className="cn-actions">
        <button onClick={() => p.onDownload('script')} disabled={waiting}>Download script</button>
        <button onClick={() => p.onDownload('removal')} disabled={waiting}>Download removal script</button>
        <button onClick={p.onCancel} disabled={p.busy}>Cancel</button>
      </div>
    </section>
  );
}

export interface DbxCredentialsProps {
  applicationId: string;
  secret: string;
  httpPath: string;
  problems: string[];
  busy: boolean;
  onChange: (field: 'applicationId' | 'secret' | 'httpPath', value: string) => void;
  onSubmit: () => void;
}

export function DbxCredentialsForm(p: DbxCredentialsProps) {
  return (
    <section className="gv-card" data-view="dbx-credentials" aria-label="Give Compass the identity">
      <h2>3. Give Compass the identity, once</h2>
      <p className="cn-small">
        The secret is sent once, stored in Compass&apos;s secret store and never shown again. If the check below fails, it is deleted.
        You can also give it a new secret later by running the script&apos;s secret step again.
      </p>
      <div className="cn-form">
        <label>Application id
          <input value={p.applicationId} autoComplete="off" onChange={(e) => p.onChange('applicationId', e.target.value)} />
        </label>
        <label>OAuth secret
          <input type="password" value={p.secret} autoComplete="off" spellCheck={false} onChange={(e) => p.onChange('secret', e.target.value)} />
        </label>
        <label>SQL warehouse HTTP path
          <input value={p.httpPath} placeholder="/sql/1.0/warehouses/abc123" autoComplete="off" onChange={(e) => p.onChange('httpPath', e.target.value)} />
        </label>
      </div>
      {p.problems.length > 0 && (
        <ul className="cn-problems" data-testid="problems">{p.problems.map((problem) => <li key={problem}>{problem}</li>)}</ul>
      )}
      <button onClick={p.onSubmit} disabled={p.busy}>Connect and check</button>
    </section>
  );
}

export function DbxResultPanel({ result, errorCode }: { result: DbxResult | null; errorCode: string }) {
  if (errorCode) return <ErrorNotice code={errorCode} />;
  if (!result) return null;
  return (
    <section className="gv-card" data-view="dbx-result" aria-label="Connection check">
      <h2>{result.ok ? 'Connected and checked' : 'Not connected'}</h2>
      {result.ok ? (
        <p>Compass read each table you named. It was not tested against anything your admin should <em>not</em> have granted; the script&apos;s own read-back does that.</p>
      ) : (
        <>
          <ErrorNotice code={result.code ?? 'connection_test_failed'} />
          <p>The secret was deleted. Check the script ran in full, then try again with a new secret.</p>
        </>
      )}
      <ul>
        {result.checks.map((check) => (
          <li key={check.table} data-passed={check.passed}>{check.passed ? 'Can read' : 'Cannot read'} {check.table}</li>
        ))}
      </ul>
    </section>
  );
}

export { dbxRemovalText, dbxScriptText };

export interface DbxConnectionsProps {
  connections: DbxConnection[];
  rotating: string | null;
  secret: string;
  problems: string[];
  confirming: string | null;
  busy: boolean;
  onRotate: (id: string | null) => void;
  onSecret: (value: string) => void;
  onSubmitSecret: (id: string) => void;
  onCheck: (id: string) => void;
  onAskRemove: (id: string | null) => void;
  onRemove: (id: string) => void;
  onWarmUp?: (id: string, enabled: boolean) => void;
  canManage?: boolean;
}

export function DbxConnections(p: DbxConnectionsProps) {
  if (!p.connections.length) return null;
  return (
    <section className="gv-card" data-view="dbx-connections" aria-label="Connected Databricks workspaces">
      <h2>Connected workspaces</h2>
      {p.connections.map((c) => (
        <article key={c.connection_id} className="cn-connection" data-status={c.status} data-needs-rotation={c.needs_rotation}>
          <h3>{c.host}</h3>
          <p className="cn-small">{c.tables.join(', ')}</p>
          <p role={c.needs_rotation ? 'alert' : undefined}>{expiryText(c.status, c.expires_in_days)}</p>
          {c.status === 'active' && p.onWarmUp && (
            <div className="cn-warm" data-warm-up={c.warm_up}>
              <label>
                <input type="checkbox" checked={c.warm_up} disabled={p.busy || p.canManage === false} onChange={(e) => p.onWarmUp?.(c.connection_id, e.target.checked)} />
                {' '}Warm this warehouse when someone opens Compass
              </label>
              <p className="cn-small">
                When this is on, opening Compass after a quiet period runs a few short read-only queries on your warehouse, and starts it if it was stopped. That uses warehouse time
                on your account. It is off unless you turn it on, and it adds no access: the queries use this connection's own read-only access.
              </p>
            </div>
          )}
          <div className="cn-actions">
            <button onClick={() => p.onCheck(c.connection_id)} disabled={p.busy}>Check now</button>
            <button onClick={() => p.onRotate(p.rotating === c.connection_id ? null : c.connection_id)} disabled={p.busy}>Give a new secret</button>
            <button onClick={() => p.onAskRemove(c.connection_id)} disabled={p.busy}>Remove</button>
          </div>
          {p.rotating === c.connection_id && (
            <div className="cn-form">
              <label>New OAuth secret
                <input type="password" value={p.secret} autoComplete="off" spellCheck={false} onChange={(e) => p.onSecret(e.target.value)} />
              </label>
              {p.problems.length > 0 && <ul className="cn-problems" data-testid="problems">{p.problems.map((x) => <li key={x}>{x}</li>)}</ul>}
              <p className="cn-small">The old secret is kept until the new one is checked. If the check fails, the old one stays.</p>
              <button onClick={() => p.onSubmitSecret(c.connection_id)} disabled={p.busy}>Replace and check</button>
            </div>
          )}
          {p.confirming === c.connection_id && (
            <div role="alertdialog" aria-label="Confirm removal">
              <p>Compass will stop using this connection and delete its secret now. Your admin removes the principal and grants with the removal script.</p>
              <button onClick={() => p.onRemove(c.connection_id)} disabled={p.busy}>Yes, remove it</button>
              <button onClick={() => p.onAskRemove(null)}>Keep it</button>
            </div>
          )}
        </article>
      ))}
    </section>
  );
}
