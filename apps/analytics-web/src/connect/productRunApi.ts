import { call } from './api';
import type { ConnectConfig, ProductSetupView, RegisterInput, RegistrationView, RunView, ScriptInput, ScriptView, TableRef } from './types';

const BASE = '/product-run';

export const draftRegistrationScript = (cfg: ConnectConfig, input: ScriptInput) => call<ScriptView>(cfg, 'POST', `${BASE}/registration-script`, input);
export const registerCompass = (cfg: ConnectConfig, input: RegisterInput) => call<RegistrationView>(cfg, 'POST', `${BASE}/registrations`, input);
export const listRegistrations = (cfg: ConnectConfig) => call<RegistrationView[]>(cfg, 'GET', `${BASE}/registrations`);
export const planSetup = (cfg: ConnectConfig, registrationId: string, tables: TableRef[]) =>
  call<ProductSetupView>(cfg, 'POST', `${BASE}/setups`, { registration_id: registrationId, tables });
export const getProductSetup = (cfg: ConnectConfig, id: string) => call<ProductSetupView>(cfg, 'GET', `${BASE}/setups/${encodeURIComponent(id)}`);
export const approveSetup = (cfg: ConnectConfig, id: string, digest: string) =>
  call<ProductSetupView>(cfg, 'POST', `${BASE}/setups/${encodeURIComponent(id)}/approve`, { digest });
export const runProductSetup = (cfg: ConnectConfig, id: string) => call<RunView>(cfg, 'POST', `${BASE}/setups/${encodeURIComponent(id)}/run`);
export const cancelProductSetup = (cfg: ConnectConfig, id: string) => call<void>(cfg, 'POST', `${BASE}/setups/${encodeURIComponent(id)}/cancel`);

/** Starts the Snowflake sign-in. The browser binding cookie is HttpOnly and comes back on this response, so cookies must be accepted. */
export const startSignIn = (cfg: ConnectConfig, registrationId: string, setupId: string) =>
  call<{ authorize_url: string }>(cfg, 'POST', '/oauth/start', { registration_id: registrationId, setup_id: setupId }, 'include');
