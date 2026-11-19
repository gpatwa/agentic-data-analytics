import { useState } from 'react';

import { ConnectError } from './api';
import { beginDatabricks, cancelDatabricks, completeDatabricks, scriptWithId } from './databricksApi';
import type { DbxResult, DbxScriptView } from './databricksApi';
import { dbxScriptFilename, dbxRemovalText, dbxScriptText, validateCredentials, validateDbxDetails } from './databricksFlow';
import { DatabricksStage, DbxCredentialsForm, DbxDetailsForm, DbxResultPanel, DbxScriptPanel } from './databricksViews';
import { save } from './download';
import { parseTables } from './flow';
import type { ConnectConfig } from './types';

export function DatabricksWorkspace({ cfg, ready, canManage }: { cfg: ConnectConfig; ready: boolean; canManage: boolean }) {
  const [host, setHost] = useState('');
  const [catalog, setCatalog] = useState('');
  const [warehouse, setWarehouse] = useState('');
  const [tablesText, setTablesText] = useState('');
  const [problems, setProblems] = useState<string[]>([]);
  const [view, setView] = useState<DbxScriptView | null>(null);
  const [applicationId, setApplicationId] = useState('');
  const [secret, setSecret] = useState('');
  const [httpPath, setHttpPath] = useState('');
  const [credProblems, setCredProblems] = useState<string[]>([]);
  const [result, setResult] = useState<DbxResult | null>(null);
  const [errorCode, setErrorCode] = useState('');
  const [busy, setBusy] = useState(false);

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

  const show = () => {
    const found = validateDbxDetails({ host, catalog, warehouse, tablesText });
    setProblems(found);
    if (found.length) return;
    void guard(async () => {
      setResult(null);
      setView(await beginDatabricks(cfg, { host: host.trim().toLowerCase(), catalog: catalog.trim(), warehouse_name: warehouse.trim(), tables: parseTables(tablesText).tables }));
    });
  };

  const apply = () =>
    view &&
    guard(async () => {
      setView(await scriptWithId(cfg, view.setup_id, applicationId.trim().toLowerCase()));
    });

  const connect = () => {
    if (!view) return;
    const found = validateCredentials({ applicationId, secret, httpPath });
    setCredProblems(found);
    if (found.length) return;
    const body = { application_id: applicationId.trim().toLowerCase(), client_secret: secret, http_path: httpPath.trim() };
    setSecret(''); // the secret leaves this page's memory as soon as it is sent
    void guard(async () => {
      const outcome = await completeDatabricks(cfg, view.setup_id, body);
      setResult(outcome);
      if (outcome.ok) setView(null);
    });
  };

  const cancel = () =>
    view &&
    guard(async () => {
      await cancelDatabricks(cfg, view.setup_id);
      setView(null);
      setSecret('');
    });

  const download = (kind: 'script' | 'removal') => {
    if (!view) return;
    save(kind === 'script' ? dbxScriptFilename(view) : dbxScriptFilename(view).replace('.sql', '-removal.sql'),
      kind === 'script' ? dbxScriptText(view) : dbxRemovalText(view));
  };

  return (
    <>
      <DatabricksStage />
      {!ready && <p className="cn-small">Paste your access token above and choose Open to continue.</p>}
      {ready && (
        <>
          <DbxDetailsForm host={host} catalog={catalog} warehouse={warehouse} tablesText={tablesText} problems={problems} busy={busy} canManage={canManage}
            onChange={(field, value) => ({ host: setHost, catalog: setCatalog, warehouse: setWarehouse, tablesText: setTablesText })[field](value)} onSubmit={show} />
          {view && (
            <>
              <DbxScriptPanel view={view} applicationId={applicationId} busy={busy} onApplicationId={setApplicationId} onApply={() => void apply()} onDownload={download}
                onCancel={() => void cancel()} />
              <DbxCredentialsForm applicationId={applicationId} secret={secret} httpPath={httpPath} problems={credProblems} busy={busy}
                onChange={(field, value) => ({ applicationId: setApplicationId, secret: setSecret, httpPath: setHttpPath })[field](value)} onSubmit={connect} />
            </>
          )}
          <DbxResultPanel result={result} errorCode={errorCode} />
        </>
      )}
    </>
  );
}
