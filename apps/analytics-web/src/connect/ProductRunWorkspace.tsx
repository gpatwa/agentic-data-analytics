import { useEffect, useState } from 'react';

import { ConnectError } from './api';
import { save } from './download';
import { parseTables, teardownText } from './flow';
import {
  approveSetup,
  cancelProductSetup,
  draftRegistrationScript,
  getProductSetup,
  listRegistrations,
  planSetup,
  registerCompass,
  runProductSetup,
  startSignIn,
} from './productRunApi';
import {
  type ReturnTarget,
  currentStep,
  parseUsers,
  registrationScriptFilename,
  registrationScriptText,
  tablesFromKeys,
  toggleKey,
  validateDraft,
} from './productRunFlow';
import { DraftForm, PlanPanel, ProductRunStage, RegisterForm, ReturnNotice, RunPanel, ScriptReview, SetupPanel } from './productRunViews';
import type { ConnectConfig, ProductSetupView, RegistrationView, RunView, ScriptView } from './types';
import { ErrorNotice } from './views';

export interface ProductRunWorkspaceProps {
  cfg: ConnectConfig;
  ready: boolean; // the access token was entered and accepted
  canManage: boolean;
  returned: ReturnTarget; // where the browser came back to after Snowflake's sign-in
}

export function ProductRunWorkspace({ cfg, ready, canManage, returned }: ProductRunWorkspaceProps) {
  const [database, setDatabase] = useState('');
  const [tablesText, setTablesText] = useState('');
  const [usersText, setUsersText] = useState('');
  const [credit, setCredit] = useState('1');
  const [problems, setProblems] = useState<string[]>([]);
  const [script, setScript] = useState<ScriptView | null>(null);
  const [registrationId, setRegistrationId] = useState('');
  const [host, setHost] = useState('');
  const [clientId, setClientId] = useState('');
  const [locator, setLocator] = useState('');
  const [registrations, setRegistrations] = useState<RegistrationView[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const [setup, setSetup] = useState<ProductSetupView | null>(null);
  const [run, setRun] = useState<RunView | null>(null);
  const [errorCode, setErrorCode] = useState(returned?.kind === 'error' ? returned.code : '');
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

  useEffect(() => {
    if (!ready) return;
    void guard(async () => {
      setRegistrations(await listRegistrations(cfg));
      if (returned?.kind === 'review') {
        const back = await getProductSetup(cfg, returned.setupId);
        setSetup(back);
        setSelectedId(back.registration_id);
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready]);

  const ceilingOfDraft = () => parseTables(tablesText).tables;

  const showScript = () => {
    const found = validateDraft({ database, tablesText, usersText, credit });
    setProblems(found);
    if (found.length) return;
    void guard(async () => {
      const drafted = await draftRegistrationScript(cfg, {
        database: database.trim(), tables: ceilingOfDraft(), setup_users: parseUsers(usersText).users, credit_quota: Number(credit),
      });
      setScript(drafted);
      setRegistrationId(`reg-${drafted.reg_suffix}`);
    });
  };

  const register = () =>
    script &&
    guard(async () => {
      await registerCompass(cfg, {
        registration_id: registrationId.trim(), reg_suffix: script.reg_suffix, host: host.trim().toLowerCase(), client_id: clientId.trim(),
        account_locator: locator.trim(), database: database.trim(), tables: ceilingOfDraft(), setup_users: parseUsers(usersText).users,
      });
      setRegistrations(await listRegistrations(cfg));
      setScript(null);
    });

  const plan = () =>
    guard(async () => {
      const chosen = registrations.find((r) => r.registration_id === selectedId);
      if (!chosen) return;
      setRun(null);
      setSetup(await planSetup(cfg, selectedId, tablesFromKeys(chosen.ceiling, selectedKeys)));
    });

  const approve = () => setup && guard(async () => setSetup(await approveSetup(cfg, setup.setup_id, setup.digest)));

  const signIn = () =>
    setup &&
    guard(async () => {
      const { authorize_url } = await startSignIn(cfg, setup.registration_id, setup.setup_id);
      window.location.assign(authorize_url); // Snowflake's own page; it sends the browser back to /connect/review/<setup>
    });

  const runIt = () =>
    setup &&
    guard(async () => {
      setRun(await runProductSetup(cfg, setup.setup_id));
      setSetup(null);
    });

  const cancel = () =>
    setup &&
    guard(async () => {
      await cancelProductSetup(cfg, setup.setup_id);
      setSetup(null);
    });

  return (
    <div className="gv cn">
      <ProductRunStage />
      {errorCode && (returned?.kind === 'error' && errorCode === returned.code ? <ReturnNotice code={errorCode} /> : <ErrorNotice code={errorCode} />)}
      {!ready && <p className="cn-small">Paste your access token above and choose Open to continue.</p>}
      {ready && (
        <>
          <DraftForm
            database={database} tablesText={tablesText} usersText={usersText} credit={credit} problems={problems} busy={busy} canManage={canManage}
            onChange={(field, value) => (field === 'database' ? setDatabase(value) : field === 'tablesText' ? setTablesText(value) : field === 'usersText' ? setUsersText(value) : setCredit(value))}
            onSubmit={showScript}
          />
          {script && (
            <>
              <ScriptReview
                script={script}
                onDownload={() => save(registrationScriptFilename(script), registrationScriptText(script))}
                onCopy={() => void navigator.clipboard?.writeText(registrationScriptText(script))}
                onDownloadRemoval={() => save(`compass-registration-removal-${script.digest.slice(0, 8)}.sql`, teardownText(script.teardown, script.digest, 'your account'))}
              />
              <RegisterForm
                registrationId={registrationId} host={host} clientId={clientId} locator={locator} busy={busy}
                onChange={(field, value) => (field === 'registrationId' ? setRegistrationId(value) : field === 'host' ? setHost(value) : field === 'clientId' ? setClientId(value) : setLocator(value))}
                onSubmit={register}
              />
            </>
          )}
          {!setup && !run && (
            <PlanPanel
              registrations={registrations} selectedId={selectedId} selectedKeys={selectedKeys} busy={busy} canManage={canManage}
              onSelect={(id) => { setSelectedId(id); setSelectedKeys([]); }}
              onToggle={(key) => setSelectedKeys((keys) => toggleKey(keys, key))}
              onPlan={plan}
            />
          )}
          {setup && <SetupPanel setup={setup} step={currentStep(setup, run)} busy={busy} onApprove={approve} onSignIn={signIn} onRun={runIt} onCancel={cancel} />}
          {run && <RunPanel run={run} />}
        </>
      )}
    </div>
  );
}
