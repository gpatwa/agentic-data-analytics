import type { Capabilities, ConnectConfig, ConnectionView, RemovalView, SetupInput, SetupView, VerifyView } from './types';

export class ConnectError extends Error {
  constructor(readonly code: string, readonly status: number) {
    super(code);
  }
}

async function call<T>(cfg: ConnectConfig, method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${cfg.baseUrl}/api/v2/connect${path}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${cfg.token}`,
      ...(cfg.apiKey ? { 'X-API-Key': cfg.apiKey } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    let code = `http_${response.status}`;
    try {
      const detail = ((await response.json()) as { detail?: unknown }).detail;
      if (typeof detail === 'string') code = detail; // the API returns short stable codes, never messages
    } catch {
      /* keep the generic code */
    }
    throw new ConnectError(code, response.status);
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

export const getCapabilities = (cfg: ConnectConfig) => call<Capabilities>(cfg, 'GET', '/capabilities');
export const listConnections = (cfg: ConnectConfig) => call<ConnectionView[]>(cfg, 'GET', '/connections');
export const startSetup = (cfg: ConnectConfig, input: SetupInput) => call<SetupView>(cfg, 'POST', '/setups', input);
export const verifySetup = (cfg: ConnectConfig, id: string) => call<VerifyView>(cfg, 'POST', `/setups/${encodeURIComponent(id)}/verify`);
export const cancelSetup = (cfg: ConnectConfig, id: string) => call<void>(cfg, 'POST', `/setups/${encodeURIComponent(id)}/cancel`);
export const removeAccess = (cfg: ConnectConfig, id: string) => call<RemovalView>(cfg, 'DELETE', `/connections/${encodeURIComponent(id)}`);
