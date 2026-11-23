import { useEffect, useState } from 'react';

import { ConnectError } from './api';
import { beginDatabricks, cancelDatabricks, checkDbxConnection, completeDatabricks, enrolStatus, issueEnrolFile, issueRotateFile, listDbxConnections, removeDbxConnection, rotateDbxSecret, rotateStatus, scriptWithId, setDbxWarmUp } from './databricksApi';
import type { DbxConnection, DbxEnrolFile, DbxEnrolStatus, DbxRemoval, DbxResult, DbxScriptView } from './databricksApi';
import { dbxRemovalText, dbxScriptFilename, dbxScriptText, validateCredentials, validateDbxDetails, validateSecret } from './databricksFlow';
import type { Track } from './databricksFlow';
import { DatabricksStage, DbxConnections, DbxCredentialsForm, DbxDetailsForm, DbxEnrolPanel, DbxRemovedNote, DbxResultPanel, DbxRotatePanel, DbxScriptPanel, DbxTrackChooser } from './databricksViews';
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
  const [connections, setConnections] = useState<DbxConnection[]>([]);
  const [rotating, setRotating] = useState<string | null>(null);
  const [rotateSecret, setRotateSecret] = useState('');
  const [rotateProblems, setRotateProblems] = useState<string[]>([]);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [track, setTrack] = useState<Track>('enrol');
  const [file, setFile] = useState<DbxEnrolFile | null>(null);
  const [status, setStatus] = useState<DbxEnrolStatus | null>(null);
  const [rotation, setRotation] = useState<{ id: string; file: DbxEnrolFile | null; status: DbxEnrolStatus | null } | null>(null);
  const [removed, setRemoved] = useState<{ removal: DbxRemoval; applicationId: string } | null>(null);

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

  const refresh = async () => setConnections(await listDbxConnections(cfg));

  useEffect(() => {
    if (ready) void guard(refresh);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready]);

  // While the admin runs the file, ask Compass every few seconds what happened. It stops when the file is spent, expired or the screen is closed.
  useEffect(() => {
    if (!view || !file || (status && status.state !== 'waiting')) return undefined;
    const timer = setInterval(() => {
      void enrolStatus(cfg, view.setup_id)
        .then(async (next) => {
          setStatus(next);
          if (next.state === 'connected') await refresh();
        })
        .catch(() => undefined); // a failed poll is retried at the next tick
    }, 3000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view?.setup_id, file, status?.state]);

  // The same polling for a rotation by file.
  useEffect(() => {
    if (!rotation || !rotation.file || (rotation.status && rotation.status.state !== 'waiting')) return undefined;
    const id = rotation.id;
    const timer = setInterval(() => {
      void rotateStatus(cfg, id)
        .then(async (next) => {
          setRotation((current) => (current && current.id === id ? { ...current, status: next } : current));
          if (next.state === 'connected') await refresh();
        })
        .catch(() => undefined);
    }, 3000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rotation?.id, rotation?.file, rotation?.status?.state]);

  const rotateByFile = (id: string) =>
    guard(async () => {
      const issued = await issueRotateFile(cfg, id);
      setRotation({ id, file: issued, status: null });
    });

  const show = () => {
    const found = validateDbxDetails({ host, catalog, warehouse, tablesText });
    setProblems(found);
    if (found.length) return;
    void guard(async () => {
      setResult(null);
      setFile(null);
      setStatus(null);
      setRemoved(null);
      setView(await beginDatabricks(cfg, { host: host.trim().toLowerCase(), catalog: catalog.trim(), warehouse_name: warehouse.trim(), tables: parseTables(tablesText).tables }));
    });
  };

  const issue = () =>
    view &&
    guard(async () => {
      setStatus(null);
      setFile(await issueEnrolFile(cfg, view.setup_id));
    });

  const done = () => {
    setView(null);
    setFile(null);
    setStatus(null);
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
      if (outcome.ok) {
        setView(null);
        await refresh();
      }
    });
  };

  const cancel = () =>
    view &&
    guard(async () => {
      await cancelDatabricks(cfg, view.setup_id);
      done();
      setSecret('');
    });

  const submitRotation = (id: string) => {
    const found = validateSecret(rotateSecret);
    setRotateProblems(found);
    if (found.length) return;
    const value = rotateSecret;
    setRotateSecret(''); // the secret leaves page state before it is sent
    void guard(async () => {
      setResult(await rotateDbxSecret(cfg, id, value));
      setRotating(null);
      await refresh();
    });
  };

  const check = (id: string) =>
    guard(async () => {
      setResult(await checkDbxConnection(cfg, id));
      await refresh();
    });

  const warmUp = (id: string, enabled: boolean) =>
    guard(async () => {
      await setDbxWarmUp(cfg, id, enabled);
      await refresh();
    });

  const remove = (id: string) =>
    guard(async () => {
      const applicationId = connections.find((c) => c.connection_id === id)?.application_id ?? '';
      setRemoved({ removal: await removeDbxConnection(cfg, id), applicationId });
      setConfirming(null);
      setResult(null);
      await refresh();
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
          {view && <DbxTrackChooser track={track} onChoose={setTrack} />}
          {view && track === 'enrol' && (
            <DbxEnrolPanel view={view} file={file} status={status} busy={busy} onIssue={() => void issue()} onDownload={() => file && save(file.filename, file.content)}
              onDone={done} onCancel={() => void cancel()} />
          )}
          {view && track === 'manual' && (
            <>
              <DbxScriptPanel view={view} applicationId={applicationId} busy={busy} onApplicationId={setApplicationId} onApply={() => void apply()} onDownload={download}
                onCancel={() => void cancel()} />
              <DbxCredentialsForm applicationId={applicationId} secret={secret} httpPath={httpPath} problems={credProblems} busy={busy}
                onChange={(field, value) => ({ applicationId: setApplicationId, secret: setSecret, httpPath: setHttpPath })[field](value)} onSubmit={connect} />
            </>
          )}
          {rotation && (
            <DbxRotatePanel host={connections.find((c) => c.connection_id === rotation.id)?.host ?? ''} file={rotation.file} status={rotation.status} busy={busy}
              onIssue={() => void rotateByFile(rotation.id)} onDownload={() => rotation.file && save(rotation.file.filename, rotation.file.content)} onClose={() => setRotation(null)} />
          )}
          {removed && <DbxRemovedNote removal={removed.removal} applicationId={removed.applicationId} />}
          <DbxConnections connections={connections} rotating={rotating} secret={rotateSecret} problems={rotateProblems} confirming={confirming} busy={busy}
            onRotate={(id) => { setRotating(id); setRotateSecret(''); setRotateProblems([]); }} onRotateFile={(id) => void rotateByFile(id)} onSecret={setRotateSecret} onSubmitSecret={submitRotation}
            onCheck={(id) => void check(id)} onAskRemove={setConfirming} onRemove={(id) => void remove(id)} onWarmUp={(id, enabled) => void warmUp(id, enabled)} canManage={canManage} />
          <DbxResultPanel result={result} errorCode={errorCode} />
        </>
      )}
    </>
  );
}
