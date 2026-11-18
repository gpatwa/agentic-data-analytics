import { ErrorNotice } from './views';
import { STEP_LABELS, describeRun, tableKey } from './productRunFlow';
import type { ProductSetupView, RegistrationView, RunView, ScriptView, TableRef } from './types';

export function ProductRunStage() {
  return (
    <section className="gv-card cn-stage" aria-label="Where this stands" data-view="pr-stage">
      <h2>Register once, then no script for each connection</h2>
      <p className="cn-stage-note" role="note">
        This is in internal testing. Its pieces have been checked on one test Snowflake account; the whole path has not run end to end for a customer,
        and it has had no security review.
      </p>
      <p className="cn-small">
        Your admin runs one registration script. After that, each new connection is planned here, you read and approve the exact statements, you sign in on
        Snowflake&apos;s own page, and Compass sets it up. Compass never sees your password.
      </p>
    </section>
  );
}

export interface DraftFormProps {
  database: string;
  tablesText: string;
  usersText: string;
  credit: string;
  problems: string[];
  busy: boolean;
  canManage: boolean;
  onChange: (field: 'database' | 'tablesText' | 'usersText' | 'credit', value: string) => void;
  onSubmit: () => void;
}

export function DraftForm(p: DraftFormProps) {
  return (
    <section className="gv-card" data-view="pr-draft" aria-label="What Compass may ever read">
      <h2>1. What may Compass ever read?</h2>
      <p>This is the ceiling. Later connections can only use tables from this list, and Compass can never grant more.</p>
      <div className="cn-form">
        <label>Database
          <input value={p.database} placeholder="ANALYTICS" onChange={(e) => p.onChange('database', e.target.value)} />
        </label>
        <label>Tables, one per line as SCHEMA.TABLE
          <textarea rows={4} value={p.tablesText} placeholder={'PUBLIC.ORDERS\nPUBLIC.CUSTOMERS'} onChange={(e) => p.onChange('tablesText', e.target.value)} />
        </label>
        <label>Snowflake users who may use the setup role, one per line
          <textarea rows={2} value={p.usersText} placeholder="YOUR_ADMIN_USER" onChange={(e) => p.onChange('usersText', e.target.value)} />
        </label>
        <label>Credit limit for the small warehouse Compass will use
          <input value={p.credit} inputMode="numeric" onChange={(e) => p.onChange('credit', e.target.value)} />
        </label>
      </div>
      {p.problems.length > 0 && (
        <ul className="gv-problems" role="alert" data-testid="problems">{p.problems.map((x) => <li key={x}>{x}</li>)}</ul>
      )}
      <div className="gv-actions"><button onClick={p.onSubmit} disabled={p.busy || !p.canManage}>Show the registration script</button></div>
    </section>
  );
}

export interface ScriptReviewProps {
  script: ScriptView;
  onDownload: () => void;
  onCopy: () => void;
  onDownloadRemoval: () => void;
}

export function ScriptReview({ script, onDownload, onCopy, onDownloadRemoval }: ScriptReviewProps) {
  return (
    <section className="gv-card" data-view="pr-script" aria-label="Review the registration script">
      <h2>2. Review the registration script</h2>
      <ul data-testid="pr-summary">{script.summary.map((line) => <li key={line}>{line}</li>)}</ul>
      <details className="cn-script">
        <summary>The exact statements ({script.statements.length})</summary>
        <pre data-testid="pr-statements">{script.statements.map((statement) => `${statement};`).join('\n')}</pre>
        <p className="cn-small">Digest <code>{script.digest.slice(0, 16)}</code>. The downloaded file carries the same digest.</p>
      </details>
      <ol className="cn-steps">
        <li>Give the script to a Snowflake admin (ACCOUNTADMIN). They run it once.</li>
        <li>After the last statement, copy the <code>OAUTH_CLIENT_ID</code> value it shows (it is not a secret), and run <code>SELECT CURRENT_ACCOUNT();</code> to get your account locator.</li>
        <li>Enter both below.</li>
      </ol>
      <div className="gv-actions">
        <button onClick={onDownload}>Download script</button>
        <button onClick={onCopy}>Copy script</button>
        <button onClick={onDownloadRemoval}>Download removal script</button>
      </div>
    </section>
  );
}

export interface RegisterFormProps {
  registrationId: string;
  host: string;
  clientId: string;
  locator: string;
  busy: boolean;
  onChange: (field: 'registrationId' | 'host' | 'clientId' | 'locator', value: string) => void;
  onSubmit: () => void;
}

export function RegisterForm(p: RegisterFormProps) {
  return (
    <section className="gv-card" data-view="pr-register" aria-label="Register Compass">
      <h2>3. Register</h2>
      <div className="cn-form">
        <label>A name for this registration
          <input value={p.registrationId} onChange={(e) => p.onChange('registrationId', e.target.value)} />
        </label>
        <label>Snowflake hostname
          <input value={p.host} placeholder="acme.snowflakecomputing.com" onChange={(e) => p.onChange('host', e.target.value)} />
        </label>
        <label>Client id (from the script&apos;s last result)
          <input value={p.clientId} onChange={(e) => p.onChange('clientId', e.target.value)} />
        </label>
        <label>Account locator (from SELECT CURRENT_ACCOUNT())
          <input value={p.locator} placeholder="AB12345" onChange={(e) => p.onChange('locator', e.target.value)} />
        </label>
      </div>
      <div className="gv-actions"><button onClick={p.onSubmit} disabled={p.busy}>Register</button></div>
    </section>
  );
}

export interface PlanPanelProps {
  registrations: RegistrationView[];
  selectedId: string;
  selectedKeys: string[];
  busy: boolean;
  canManage: boolean;
  onSelect: (id: string) => void;
  onToggle: (key: string) => void;
  onPlan: () => void;
}

export function PlanPanel(p: PlanPanelProps) {
  const chosen = p.registrations.find((r) => r.registration_id === p.selectedId);
  return (
    <section className="gv-card" data-view="pr-plan" aria-label="Connect tables">
      <h2>Connect tables</h2>
      {p.registrations.length === 0 ? (
        <p data-testid="no-registrations">Nothing is registered yet. Register Compass first (steps 1 to 3 above).</p>
      ) : (
        <>
          <label className="cn-form">Registration
            <select value={p.selectedId} onChange={(e) => p.onSelect(e.target.value)}>
              <option value="">Choose one</option>
              {p.registrations.map((r) => <option key={r.registration_id} value={r.registration_id}>{r.registration_id} ({r.host})</option>)}
            </select>
          </label>
          {chosen && (
            <fieldset className="cn-tables">
              <legend>Tables Compass may read in this connection (from what your admin allowed)</legend>
              {chosen.ceiling.map((t: TableRef) => (
                <label key={tableKey(t)}>
                  <input type="checkbox" checked={p.selectedKeys.includes(tableKey(t))} onChange={() => p.onToggle(tableKey(t))} /> <code>{tableKey(t)}</code>
                </label>
              ))}
            </fieldset>
          )}
          <div className="gv-actions">
            <button onClick={p.onPlan} disabled={p.busy || !p.canManage || !chosen || p.selectedKeys.length === 0}>Review what will be created</button>
          </div>
        </>
      )}
    </section>
  );
}

export function Stepper({ current }: { current: number }) {
  return (
    <ol className="cn-stepper" aria-label="Progress" data-current={current}>
      {STEP_LABELS.map((label, index) => {
        const state = index < current ? 'done' : index === current ? 'current' : 'todo';
        return <li key={label} data-state={state}>{label}</li>;
      })}
    </ol>
  );
}

export interface SetupPanelProps {
  setup: ProductSetupView;
  step: number;
  busy: boolean;
  onApprove: () => void;
  onSignIn: () => void;
  onRun: () => void;
  onCancel: () => void;
}

export function SetupPanel({ setup, step, busy, onApprove, onSignIn, onRun, onCancel }: SetupPanelProps) {
  return (
    <section className="gv-card" data-view="pr-setup" aria-label="Review and approve">
      <h2>Review what will be created</h2>
      <Stepper current={step} />
      <ul data-testid="pr-setup-summary">{setup.summary.map((line) => <li key={line}>{line}</li>)}</ul>
      <details className="cn-script" open={setup.status === 'awaiting_approval'}>
        <summary>The exact statements ({setup.statements.length})</summary>
        <pre data-testid="pr-setup-statements">{setup.statements.map((statement) => `${statement};`).join('\n')}</pre>
        <p className="cn-small">Digest <code>{setup.digest.slice(0, 16)}</code>. You approve exactly these statements; if they change you must read them again.</p>
      </details>
      {setup.status === 'awaiting_sign_in' && (
        <p className="cn-small" data-testid="sign-in-note">
          Snowflake&apos;s own sign-in page will open. Sign in as a user who has the Compass setup role and choose Allow. Compass never sees your password, and the
          sign-in lasts only a few minutes.
        </p>
      )}
      <div className="gv-actions">
        {setup.status === 'awaiting_approval' && <button onClick={onApprove} disabled={busy}>Approve these statements</button>}
        {setup.status === 'awaiting_sign_in' && <button onClick={onSignIn} disabled={busy}>Sign in on Snowflake</button>}
        {setup.status === 'ready_to_run' && <button onClick={onRun} disabled={busy}>Run the setup</button>}
        <button onClick={onCancel} disabled={busy}>Cancel</button>
      </div>
    </section>
  );
}

export function RunPanel({ run }: { run: RunView }) {
  const outcome = describeRun(run);
  return (
    <section className={outcome.tone === 'ok' ? 'gv-card' : 'gv-card gv-failed'} data-view="pr-run" data-tone={outcome.tone} aria-live="polite">
      <h2>{outcome.title}</h2>
      <p>{outcome.detail}</p>
      {run.ok && <p className="cn-small">Nothing in the customer&apos;s warehouse was changed except the read-only role and user shown in the statements.</p>}
    </section>
  );
}

export function ReturnNotice({ code }: { code: string }) {
  return <ErrorNotice code={code} />;
}
