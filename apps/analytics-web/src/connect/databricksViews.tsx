import type { DbxConnection, DbxEnrolFile, DbxEnrolStatus, DbxRemoval, DbxResult, DbxScriptView } from './databricksApi';
import { JOURNEY, dbxRemovalText, dbxScriptText, enrolCommand, enrolStatusText, expiryText, needsApplicationId, profileLooksWrong, removeCommand, rotateStatusText } from './databricksFlow';
import type { Track } from './databricksFlow';
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
        Your admin runs one file that creates a read-only service principal for exactly the tables you name and hands its secret straight to Compass, so
        nothing is copied or pasted. Compass holds no admin credential, and never sees anyone&apos;s password.
      </p>
    </section>
  );
}

export function DbxStepper({ current }: { current: number }) {
  return (
    <ol className="cn-stepper" aria-label="Where you are in connecting Databricks" data-testid="stepper">
      {JOURNEY.map((label, i) => (
        <li key={label} data-state={i < current ? 'done' : i === current ? 'current' : 'todo'} aria-current={i === current ? 'step' : undefined}>
          <span className="cn-step-n">{i < current ? '\u2713' : i + 1}</span> {label}
        </li>
      ))}
    </ol>
  );
}

export interface DbxSignInProps {
  token: string;
  busy: boolean;
  onToken: (value: string) => void;
  onOpen: () => void;
}

export function DbxSignIn(p: DbxSignInProps) {
  return (
    <section className="gv-card" data-view="dbx-signin" aria-label="Sign in">
      <h2>Step 1 of 4: Sign in</h2>
      <p>Paste the access token of a person who is allowed to manage connections, then choose Open. Only they can set up or remove a connection.</p>
      <p className="cn-small">Trying this on your own computer? The token is in <code>.local-stack/token.txt</code> in the repository.</p>
      <div className="gv-form">
        <label>Access token<input type="password" autoComplete="off" spellCheck={false} value={p.token} onChange={(e) => p.onToken(e.target.value)} /></label>
        <button onClick={p.onOpen} disabled={p.busy || !p.token}>Open</button>
      </div>
    </section>
  );
}

export interface DbxConnectedSummaryProps {
  tables: string[];
  checks: { table: string; passed: boolean }[];
  onDone: () => void;
}

export function DbxConnectedSummary(p: DbxConnectedSummaryProps) {
  return (
    <section className="gv-card" data-view="dbx-connected" aria-label="Connected">
      <h2>Step 4 of 4: Connected</h2>
      <p>Compass can read the tables you named, and nothing else:</p>
      <ul>{(p.checks.length ? p.checks.map((c) => c.table) : p.tables).map((t) => <li key={t}>Can read {t}</li>)}</ul>
      <h3>What to do next</h3>
      <ol className="cn-steps">
        <li><strong>Ask a question.</strong> Open <em>Workspace</em> in the left menu. Answers use only the definitions your team has certified.</li>
        <li><strong>Optional: warm the warehouse.</strong> Below, in Connected workspaces, turn on warming so the first question after a quiet period is fast. It uses a little warehouse time, so it is off until you choose.</li>
        <li><strong>Later:</strong> rotate the secret before it ends (you will see a warning 30 days ahead), or remove the connection. Both are buttons below.</li>
      </ol>
      <div className="cn-actions"><button onClick={p.onDone}>Connect another workspace</button></div>
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
      <h2>Step 2 of 4: What may Compass read?</h2>
      <p className="cn-small">Name one workspace, one catalog and the exact tables Compass may read, and the SQL warehouse it may use. Nothing is created yet.</p>
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
      <button onClick={p.onSubmit} disabled={p.busy || !p.canManage}>Continue</button>
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

export function DbxTrackChooser({ track, onChoose }: { track: Track; onChoose: (track: Track) => void }) {
  return (
    <section className="gv-card" data-view="dbx-track" aria-label="How your admin sets this up">
      <h2>Prefer another way?</h2>
      <div className="cn-actions" role="radiogroup" aria-label="Setup method">
        <button role="radio" aria-checked={track === 'enrol'} data-selected={track === 'enrol'} onClick={() => onChoose('enrol')}>One file (recommended)</button>
        <button role="radio" aria-checked={track === 'manual'} data-selected={track === 'manual'} onClick={() => onChoose('manual')}>Run the script yourself</button>
      </div>
      <p className="cn-small">
        {track === 'enrol'
          ? 'Your admin runs one file under their own Databricks sign-in. It creates the identity and sends its secret straight to Compass: nobody copies or sees the secret.'
          : 'Your admin runs the script below, then you paste the identity\'s application id and secret here once.'}
      </p>
    </section>
  );
}

export interface DbxEnrolPanelProps {
  view: DbxScriptView;
  file: DbxEnrolFile | null;
  status: DbxEnrolStatus | null;
  profile: string;
  busy: boolean;
  onProfile: (value: string) => void;
  onIssue: () => void;
  onDownload: () => void;
  onCopy: (command: string) => void;
  onDone: () => void;
  onCancel: () => void;
}

export function DbxEnrolPanel(p: DbxEnrolPanelProps) {
  const connected = p.status?.state === 'connected';
  const command = p.file ? enrolCommand(p.file.filename, p.profile) : '';
  return (
    <section className="gv-card" data-view="dbx-enrol" aria-label="The file your admin runs">
      <h2>Step 3 of 4: Run one file</h2>
      <p>Your admin runs one file in a terminal, signed in to Databricks as themselves. It sets everything up and tells Compass; nobody copies a secret.</p>
      <ul className="cn-small">
        <li>It creates one read-only service principal. It can read only the {p.view.tables.length} table(s) you named in catalog {p.view.catalog} (USE CATALOG, USE SCHEMA and SELECT: no write, no grant option) and may use the SQL warehouse &apos;{p.view.warehouse_name}&apos;, which Compass never edits.</li>
        <li>The file is plain Python you can read first. It prints only short codes and writes nothing to disk. If any step fails it removes everything it created.</li>
        <li>It works once, for 30 minutes. Compass holds no admin credential. To remove it later, run the same file with <code>--remove</code>, or use Remove after it is connected.</li>
      </ul>
      <ol className="cn-steps cn-journey-steps">
        <li>
          <strong>Get the file.</strong>{' '}
          {!p.file ? <button onClick={p.onIssue} disabled={p.busy}>Get the file</button> : (
            <>
              Done. Digest <code data-testid="file-digest">{p.file.digest}</code>: your admin can check it with <code>python3 {p.file.filename} --digest</code>; the two must match.
              <div className="cn-actions">
                <button onClick={p.onDownload}>Download {p.file.filename}</button>
                <button onClick={p.onIssue} disabled={p.busy || connected}>Get a new file</button>
              </div>
            </>
          )}
        </li>
        <li>
          <strong>Run it in your terminal.</strong>{' '}
          {!p.file ? <span className="cn-small">The exact command appears once you have the file.</span> : (
            <>
              <label className="cn-small">Databricks CLI profile (optional, the sign-in your admin already uses)
                <input value={p.profile} placeholder="my-profile" autoComplete="off" spellCheck={false} onChange={(e) => p.onProfile(e.target.value)} />
              </label>
              {profileLooksWrong(p.profile) && <p className="cn-small" role="alert">A profile name uses only letters, numbers, dots, dashes and underscores; it was left out of the command.</p>}
              <p><code data-testid="run-command">{command}</code> <button onClick={() => p.onCopy(command)}>Copy command</button></p>
            </>
          )}
        </li>
        <li>
          <strong>Watch it here.</strong>
          <div role="status" aria-live="polite" data-state={p.status?.state ?? 'waiting'} data-testid="enrol-status">
            <p>{p.file ? enrolStatusText(p.status) : 'Nothing has run yet.'}</p>
            {p.status?.code && <ErrorNotice code={p.status.code} />}
            {p.status && p.status.checks.length > 0 && (
              <ul>{p.status.checks.map((c) => <li key={c.table} data-passed={c.passed}>{c.passed ? 'Can read' : 'Cannot read'} {c.table}</li>)}</ul>
            )}
          </div>
        </li>
      </ol>
      <div className="cn-actions">
        {connected && <button onClick={p.onDone}>Done</button>}
        {!connected && <button onClick={p.onCancel} disabled={p.busy}>Back</button>}
      </div>
    </section>
  );
}

export function DbxRemovedNote({ removal, applicationId }: { removal: DbxRemoval; applicationId: string }) {
  const command = removeCommand(applicationId);
  return (
    <section className="gv-card" data-view="dbx-removed" aria-label="Access removed from Compass">
      <h2>Compass can no longer connect</h2>
      <p>{removal.note}</p>
      {command ? <p>To remove the identity and its grants from Databricks, your admin runs <code data-testid="remove-command">{command}</code> with the enrol file, or the removal script.</p>
        : <p>Your admin removes the identity and its grants with the removal script.</p>}
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

export interface DbxRotatePanelProps {
  host: string;
  file: DbxEnrolFile | null;
  status: DbxEnrolStatus | null;
  busy: boolean;
  onIssue: () => void;
  onDownload: () => void;
  onClose: () => void;
}

export function DbxRotatePanel(p: DbxRotatePanelProps) {
  const connected = p.status?.state === 'connected';
  return (
    <section className="gv-card" data-view="dbx-rotate" aria-label="Rotate the secret with one file">
      <h2>Rotate the secret for {p.host}</h2>
      <ul className="cn-small">
        <li>Your admin runs one file under their own Databricks sign-in. It makes a new secret, sends it straight to Compass, and no one copies it.</li>
        <li>Compass checks it can read every table with the new secret. Only then does the file delete the older secrets. If the check fails, the new secret is deleted and the connection is as it was.</li>
        <li>It works once, for 30 minutes.</li>
      </ul>
      {!p.file && <button onClick={p.onIssue} disabled={p.busy}>Get the file</button>}
      {p.file && (
        <>
          <p>Digest <code data-testid="rotate-digest">{p.file.digest}</code>. Your admin can check it with <code>python3 {p.file.filename} --digest</code>; the two must match.</p>
          <p>To run it: <code>{p.file.run}</code></p>
          <div className="cn-actions">
            <button onClick={p.onDownload}>Download {p.file.filename}</button>
            <button onClick={p.onIssue} disabled={p.busy || connected}>Get a new file</button>
          </div>
          <div role="status" aria-live="polite" data-state={p.status?.state ?? 'waiting'} data-testid="rotate-status">
            <p>{rotateStatusText(p.status)}</p>
            {p.status?.code && <ErrorNotice code={p.status.code} />}
            {p.status && p.status.checks.length > 0 && (
              <ul>{p.status.checks.map((c) => <li key={c.table} data-passed={c.passed}>{c.passed ? 'Can read' : 'Cannot read'} {c.table}</li>)}</ul>
            )}
          </div>
        </>
      )}
      <div className="cn-actions"><button onClick={p.onClose}>{connected ? 'Done' : 'Close'}</button></div>
    </section>
  );
}

export interface DbxConnectionsProps {
  connections: DbxConnection[];
  rotating: string | null;
  secret: string;
  problems: string[];
  confirming: string | null;
  busy: boolean;
  onRotate: (id: string | null) => void;
  onRotateFile?: (id: string) => void;
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
            {p.onRotateFile && <button onClick={() => p.onRotateFile?.(c.connection_id)} disabled={p.busy || p.canManage === false}>Rotate with a file</button>}
            <button onClick={() => p.onRotate(p.rotating === c.connection_id ? null : c.connection_id)} disabled={p.busy}>Paste a new secret</button>
            <button onClick={() => p.onAskRemove(c.connection_id)} disabled={p.busy}>Remove</button>
          </div>
          {p.rotating === c.connection_id && (
            <div className="cn-form">
              <label>New OAuth secret
                <input type="password" value={p.secret} autoComplete="off" spellCheck={false} onChange={(e) => p.onSecret(e.target.value)} />
              </label>
              {p.problems.length > 0 && <ul className="cn-problems" data-testid="problems">{p.problems.map((x) => <li key={x}>{x}</li>)}</ul>}
              <p className="cn-small">The old secret is kept until the new one is checked. If the check fails, the old one stays. Prefer "Rotate with a file": nothing to paste.</p>
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
