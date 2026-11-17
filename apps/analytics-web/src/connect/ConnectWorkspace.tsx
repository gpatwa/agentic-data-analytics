import { useState } from 'react';

import { ConnectError, cancelSetup, getCapabilities, listConnections, removeAccess, startSetup, verifySetup } from './api';
import { accountFromHost, parseTables, scriptFilename, scriptText, teardownText, validateDetails } from './flow';
import type { Capabilities, ConnectionView, RemovalView, SetupView, VerifyView } from './types';
import { ChecksPanel, ConnectedPage, DetailsForm, ErrorNotice, ReviewPage, StagePanel } from './views';

const BASE_URL = import.meta.env.VITE_ANALYTICS_API_URL ?? '';
const API_KEY = import.meta.env.VITE_ANALYTICS_API_KEY ?? '';

function save(filename: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/plain' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

export function ConnectWorkspace() {
  const [token, setToken] = useState('');
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [connections, setConnections] = useState<ConnectionView[]>([]);
  const [host, setHost] = useState('');
  const [database, setDatabase] = useState('');
  const [tablesText, setTablesText] = useState('');
  const [problems, setProblems] = useState<string[]>([]);
  const [setup, setSetup] = useState<SetupView | null>(null);
  const [result, setResult] = useState<VerifyView | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [removal, setRemoval] = useState<{ id: string; view: RemovalView } | null>(null);
  const [errorCode, setErrorCode] = useState('');
  const [busy, setBusy] = useState(false);
  const cfg = { baseUrl: BASE_URL, token, apiKey: API_KEY || undefined };

  async function guard(work: () => Promise<void>) {
    setBusy(true);
    setErrorCode('');
    try {
      await work();
    } catch (failure) {
      setErrorCode(failure instanceof ConnectError ? failure.code : 'request_failed');
    } finally {
      setBusy(false);
    }
  }

  const load = () =>
    guard(async () => {
      setCapabilities(await getCapabilities(cfg));
      setConnections(await listConnections(cfg));
    });

  const review = () => {
    const found = validateDetails({ host, database, tablesText });
    setProblems(found);
    if (found.length) return;
    void guard(async () => {
      setResult(null);
      setSetup(await startSetup(cfg, { host: host.trim().toLowerCase(), account: accountFromHost(host), database: database.trim(), tables: parseTables(tablesText).tables, credit_quota: 1 }));
    });
  };

  const verify = () =>
    setup &&
    guard(async () => {
      const outcome = await verifySetup(cfg, setup.setup_id);
      setResult(outcome);
      if (outcome.ok) {
        setSetup(null);
        setConnections(await listConnections(cfg));
      }
    });

  const cancel = () =>
    setup &&
    guard(async () => {
      await cancelSetup(cfg, setup.setup_id);
      setSetup(null);
      setResult(null);
    });

  const remove = (connection: ConnectionView) =>
    guard(async () => {
      const view = await removeAccess(cfg, connection.connection_id);
      setRemoval({ id: connection.connection_id, view });
      setConfirming(null);
      setConnections(await listConnections(cfg));
    });

  const removedConnection = removal ? ({ connection_id: removal.id, engine: 'snowflake', host: host || 'your warehouse', account: '', status: 'removed', tables: [], auth_method: 'key_pair' } as ConnectionView) : null;

  return (
    <div className="gv cn">
      <StagePanel capabilities={capabilities} />
      <section className="gv-card" aria-label="Sign in">
        <div className="gv-form">
          <label>Access token<input type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} /></label>
          <button onClick={() => void load()} disabled={busy || !token}>Open</button>
        </div>
      </section>
      {errorCode && <ErrorNotice code={errorCode} />}
      {connections.map((connection) => (
        <ConnectedPage
          key={connection.connection_id}
          connection={connection}
          confirming={confirming === connection.connection_id}
          removal={null}
          busy={busy}
          onAskRemove={() => setConfirming(connection.connection_id)}
          onConfirmRemove={() => void remove(connection)}
          onKeep={() => setConfirming(null)}
          onDownloadTeardown={() => undefined}
        />
      ))}
      {removal && removedConnection && (
        <ConnectedPage
          connection={removedConnection}
          confirming={false}
          removal={removal.view}
          busy={busy}
          onAskRemove={() => undefined}
          onConfirmRemove={() => undefined}
          onKeep={() => undefined}
          onDownloadTeardown={() => save(`compass-connect-removal-${removal.view.teardown_digest.slice(0, 8)}.sql`, teardownText(removal.view.teardown, removal.view.teardown_digest, removedConnection.host))}
        />
      )}
      {capabilities && !setup && (
        <DetailsForm
          host={host}
          database={database}
          tablesText={tablesText}
          problems={problems}
          busy={busy}
          canManage={capabilities.can_manage}
          onChange={(field, value) => (field === 'host' ? setHost(value) : field === 'database' ? setDatabase(value) : setTablesText(value))}
          onSubmit={review}
        />
      )}
      {setup && (
        <ReviewPage
          setup={setup}
          busy={busy}
          onDownload={() => save(scriptFilename(setup), scriptText(setup))}
          onCopy={() => void navigator.clipboard?.writeText(scriptText(setup))}
          onVerify={() => void verify()}
          onCancel={() => void cancel()}
        />
      )}
      {result && <ChecksPanel result={result} />}
    </div>
  );
}
