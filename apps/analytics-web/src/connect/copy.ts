// Plain-words copy for every error code the Connect API can return (docs/ONBOARDING_DESIGN.md, error catalogue).
// Each says what happened and what to do. Unknown codes fall back to a generic line that still shows the code.

export interface ErrorCopy {
  title: string;
  action: string;
}

export const ERROR_COPY: Record<string, ErrorCopy> = {
  connect_not_configured: { title: 'Connecting a warehouse is not switched on here.', action: 'Ask the person who runs this deployment to enable it.' },
  missing_bearer_token: { title: 'You are not signed in.', action: 'Paste your access token and try again.' },
  invalid_identity_token: { title: 'That access token was not accepted.', action: 'Check it has not expired and try again.' },
  connection_manager_required: { title: 'Your account cannot connect or remove a warehouse.', action: 'Ask an owner to add you to the connection managers.' },
  ip_address_not_allowed: { title: 'Use the warehouse hostname, not an IP address.', action: 'Copy the hostname from your Snowflake account page, for example acme.snowflakecomputing.com.' },
  not_a_vendor_domain: { title: 'That is not a Snowflake hostname.', action: 'It must end in .snowflakecomputing.com.' },
  invalid_host: { title: 'That hostname has characters we do not accept.', action: 'Use only the hostname: no https://, port, path or spaces.' },
  account_mismatch: { title: 'The account does not match the hostname.', action: 'The account is the part of the hostname before .snowflakecomputing.com.' },
  non_public_address: { title: 'That hostname does not point to a public Snowflake address.', action: 'Check the hostname, or ask your network team.' },
  address_changed: { title: 'That hostname now points somewhere different.', action: 'Nothing was connected. Check the hostname with your network team, then start again.' },
  dns_failed: { title: 'We could not look that hostname up.', action: 'Check the spelling and try again.' },
  unsupported_engine: { title: 'That warehouse is not available yet.', action: 'Only Snowflake can be connected today.' },
  invalid_scope: { title: 'One of the table names cannot be used.', action: 'Use exact names, one per line as SCHEMA.TABLE, with no * or % and no repeats.' },
  target_claimed: { title: 'This warehouse is already connected to another team.', action: 'Ask your admin, or contact support if that is a mistake.' },
  setup_not_found: { title: 'That setup is no longer open.', action: 'Setups lapse after 24 hours. Start again; nothing was changed in your warehouse.' },
  connection_not_found: { title: 'That connection no longer exists.', action: 'Refresh the page.' },
  connection_test_not_configured: { title: 'The connection test is not switched on here.', action: 'Ask the person who runs this deployment.' },
  connection_test_failed: { title: 'The connection test did not pass.', action: 'The checks below say what is missing. Ask your admin to re-run the script, then test again.' },
};

export function describeError(code: string): ErrorCopy {
  return ERROR_COPY[code] ?? { title: 'Something went wrong.', action: `Try again, and quote this code if it continues: ${code}.` };
}
