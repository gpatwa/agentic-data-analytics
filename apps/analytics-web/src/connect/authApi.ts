// How a person signs in. Today the only provider is the local stack's own sign-in (a Sign in button for the test administrator on this computer); a deployed instance has no such
// routes, so the screen falls back to pasting an access token. Single sign-in with the company login (Entra) is not built into this web app yet.
export interface AuthOptions {
  dev_login: boolean;
  label: string;
}

const NONE: AuthOptions = { dev_login: false, label: '' };

/** Asks the API what sign-in it offers. Never throws: any failure means "paste a token". */
export async function getAuthOptions(baseUrl: string): Promise<AuthOptions> {
  try {
    const response = await fetch(`${baseUrl}/api/v2/auth/options`);
    if (!response.ok) return NONE;
    const body = (await response.json()) as Partial<AuthOptions>;
    return body.dev_login === true ? { dev_login: true, label: typeof body.label === 'string' ? body.label : 'the test administrator' } : NONE;
  } catch {
    return NONE;
  }
}

/** The local stack's sign-in: answers a token for its own throwaway key. */
export async function devLogin(baseUrl: string): Promise<string> {
  const response = await fetch(`${baseUrl}/api/v2/auth/dev-login`, { method: 'POST' });
  if (!response.ok) throw new Error('sign_in_failed');
  const body = (await response.json()) as { token?: unknown };
  if (typeof body.token !== 'string' || !body.token) throw new Error('sign_in_failed');
  return body.token;
}
