import type { Capabilities, ConnectionView, EngineCapability, RemovalView, SetupView, VerifyView } from './types';
import { describeError } from './copy';
import { teardownText } from './flow';

export const NEVER = [
  'write to, change or delete anything in your data. The role it uses can only read.',
  'keep copies of your tables. Questions run where your data is; only the answer comes back.',
  'ask for or store an admin login. Your admin runs the script themselves.',
  'run SQL written by a model. Certified definitions build the SQL.',
];

export function ErrorNotice({ code }: { code: string }) {
  const copy = describeError(code);
  return (
    <div className="error-banner cn-error" role="alert" data-code={code}>
      <strong>{copy.title}</strong> {copy.action}
    </div>
  );
}

export function StagePanel({ capabilities }: { capabilities: Capabilities | null }) {
  const engine: EngineCapability | undefined = capabilities?.engines[0];
  return (
    <section className="gv-card cn-stage" aria-label="Where this stands" data-view="stage">
      <h2>Connect your warehouse</h2>
      <p className="cn-stage-note" role="note">
        {engine ? engine.stage : 'Connect mode is in internal testing. It has not had a security review and has not run against a customer account.'}
      </p>
      <ul className="cn-engines">
        <li>
          <strong>Snowflake</strong> <span className="gv-badge">in internal testing</span>
          <small> Databricks is not available for your own data yet.</small>
        </li>
      </ul>
      {engine && !engine.product_run_setup.available && (
        <p className="cn-small">Setup is by a script your admin runs once. Signing in to do it for them is not available yet.</p>
      )}
      {capabilities && !capabilities.can_manage && <p className="cn-small">Your account can look but not change. Ask an owner to add you to the connection managers.</p>}
    </section>
  );
}

export interface DetailsFormProps {
  host: string;
  database: string;
  tablesText: string;
  problems: string[];
  busy: boolean;
  canManage: boolean;
  onChange: (field: 'host' | 'database' | 'tablesText', value: string) => void;
  onSubmit: () => void;
}

export function DetailsForm(props: DetailsFormProps) {
  return (
    <section className="gv-card" data-view="details" aria-label="Your Snowflake account">
      <h2>Which tables may Compass read?</h2>
      <p>Compass starts with access to nothing. You name the tables; the script gives read-only access to exactly those.</p>
      <div className="cn-form">
        <label>Snowflake hostname
          <input value={props.host} placeholder="acme.snowflakecomputing.com" onChange={(e) => props.onChange('host', e.target.value)} />
        </label>
        <label>Database
          <input value={props.database} placeholder="ANALYTICS" onChange={(e) => props.onChange('database', e.target.value)} />
        </label>
        <label>Tables, one per line as SCHEMA.TABLE
          <textarea rows={5} value={props.tablesText} placeholder={'PUBLIC.ORDERS\nPUBLIC.CUSTOMERS'} onChange={(e) => props.onChange('tablesText', e.target.value)} />
          <small>Type the names as Snowflake shows them, usually in capitals. No * or % wildcards.</small>
        </label>
      </div>
      {props.problems.length > 0 && (
        <ul className="gv-problems" role="alert" data-testid="problems">
          {props.problems.map((problem) => <li key={problem}>{problem}</li>)}
        </ul>
      )}
      <div className="gv-actions">
        <button onClick={props.onSubmit} disabled={props.busy || !props.canManage}>Review what will be created</button>
      </div>
    </section>
  );
}

export interface ReviewProps {
  setup: SetupView;
  busy: boolean;
  onDownload: () => void;
  onCopy: () => void;
  onVerify: () => void;
  onCancel: () => void;
}

export function ReviewPage({ setup, busy, onDownload, onCopy, onVerify, onCancel }: ReviewProps) {
  return (
    <section className="gv-card" data-view="review" aria-label="Review what will be created">
      <h2>Review what will be created</h2>
      <div className="cn-columns">
        <div>
          <h3>Compass will ask your admin to create</h3>
          <ul data-testid="summary">{setup.summary.map((line) => <li key={line}>{line}</li>)}</ul>
          <h3>Tables it can read</h3>
          <ul data-testid="tables">{setup.tables.map((table) => <li key={table}><code>{table}</code></li>)}</ul>
        </div>
        <div>
          <h3>Compass will never</h3>
          <ul data-testid="never">{NEVER.map((line) => <li key={line}>{line}</li>)}</ul>
        </div>
      </div>
      <details className="cn-script">
        <summary>The exact statements ({setup.statements.length})</summary>
        <pre data-testid="statements">{setup.statements.map((statement) => `${statement};`).join('\n')}</pre>
        <p className="cn-small">Digest <code>{setup.digest.slice(0, 16)}</code>. The downloaded file and our record carry the same digest.</p>
      </details>
      <ol className="cn-steps">
        <li>Download the script and give it to your Snowflake admin (or run it yourself if you are one).</li>
        <li>When they say it has run, test the connection. Compass checks the access is read-only before anything else.</li>
      </ol>
      <p className="cn-small">This setup lapses in 24 hours if it is not used. Nothing is created in your warehouse until your admin runs the script.</p>
      <div className="gv-actions">
        <button onClick={onDownload}>Download script</button>
        <button onClick={onCopy}>Copy script</button>
        <button onClick={onVerify} disabled={busy}>I have run it: test the connection</button>
        <button onClick={onCancel} disabled={busy}>Cancel setup</button>
      </div>
    </section>
  );
}

export function ChecksPanel({ result }: { result: VerifyView }) {
  const failed = result.checks.filter((check) => !check.passed);
  return (
    <section className={result.ok ? 'gv-card' : 'gv-card gv-failed'} data-view="checks" aria-live="polite">
      <h2>{result.ok ? 'Connected' : 'The connection test did not pass'}</h2>
      <ul className="cn-checks">
        {result.checks.map((check) => (
          <li key={check.name} data-check={check.name} data-passed={check.passed}>
            <span className={check.passed ? 'gv-badge ok' : 'gv-badge bad'}>{check.passed ? 'passed' : 'not passed'}</span> {check.label}
          </li>
        ))}
      </ul>
      {failed.length > 0 && <ErrorNotice code={result.code ?? 'connection_test_failed'} />}
      <p className="cn-small" data-testid="not-run">
        These two checks run on your tables. The others run on sample data, not on yours: {result.not_run_on_customer_data.join('; ').toLowerCase()}.
      </p>
    </section>
  );
}

export interface ConnectedProps {
  connection: ConnectionView;
  confirming: boolean;
  removal: RemovalView | null;
  busy: boolean;
  onAskRemove: () => void;
  onConfirmRemove: () => void;
  onKeep: () => void;
  onDownloadTeardown: () => void;
}

export function ConnectedPage({ connection, confirming, removal, busy, onAskRemove, onConfirmRemove, onKeep, onDownloadTeardown }: ConnectedProps) {
  if (removal) {
    return (
      <section className="gv-card" data-view="removed" aria-live="polite">
        <h2>Access removed</h2>
        <p>{removal.note}</p>
        <pre data-testid="teardown">{teardownText(removal.teardown, removal.teardown_digest, connection.host)}</pre>
        <div className="gv-actions"><button onClick={onDownloadTeardown}>Download removal script</button></div>
      </section>
    );
  }
  return (
    <section className="gv-card" data-view="connected">
      <h2>{connection.host}</h2>
      <p><span className="gv-badge ok">{connection.status}</span> read-only, key pair, {connection.tables.length} table(s)</p>
      <ul>{connection.tables.map((table) => <li key={table}><code>{table}</code></li>)}</ul>
      {confirming ? (
        <div className="cn-confirm" role="alertdialog" data-testid="confirm-remove">
          <p><strong>Remove access?</strong> Compass deletes its key now and can no longer connect. Nothing in your warehouse is changed until your admin runs the removal script, which you will get next.</p>
          <div className="gv-actions">
            <button onClick={onConfirmRemove} disabled={busy}>Remove access</button>
            <button onClick={onKeep} disabled={busy}>Keep it</button>
          </div>
        </div>
      ) : (
        <div className="gv-actions"><button onClick={onAskRemove}>Remove access</button></div>
      )}
    </section>
  );
}
