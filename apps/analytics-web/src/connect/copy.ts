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
  // Databricks (admin pre-creates the principal)
  invalid_application_id: { title: 'That is not a service principal application id.', action: 'Copy the application id (a UUID) that the script step printed.' },
  invalid_http_path: { title: 'That is not a SQL warehouse HTTP path.', action: 'Copy it from the warehouse\'s Connection details; it looks like /sql/1.0/warehouses/abc123.' },
  invalid_secret: { title: 'That secret cannot be used.', action: 'Paste the OAuth secret exactly as Databricks showed it, with no spaces. Nothing was stored.' },
  invalid_request: { title: 'Some details were missing.', action: 'Fill in all three fields. Nothing was stored.' },
  // Registration, sign-in and product-run setup
  registration_not_found: { title: 'That registration does not exist.', action: 'Register Compass first, or pick one from the list.' },
  registration_id_taken: { title: 'That registration name is already used.', action: 'Choose a different registration name.' },
  invalid_registration: { title: 'The registration details cannot be used.', action: 'Check the tables, the users and the credit limit, then try again.' },
  redirect_not_configured: { title: 'The sign-in address is not set up on this deployment.', action: 'Ask the person who runs this deployment.' },
  registration_not_configured: { title: 'Drafting the registration script is not switched on here.', action: 'Ask the person who runs this deployment to set the sign-in address and Compass network addresses.' },
  outside_ceiling: { title: 'Those tables are outside what your admin allowed.', action: 'Pick tables from the allowed list, or register again with a wider list.' },
  sign_in_required: { title: 'The admin has not signed in yet.', action: 'Sign in on Snowflake first, then run the setup.' },
  sign_in_expired: { title: 'The sign-in has expired.', action: 'Sign in again. Sign-ins last only a few minutes, on purpose.' },
  not_approved: { title: 'This setup has not been approved yet.', action: 'Read the statements and approve them first.' },
  digest_mismatch: { title: 'The statements changed since you read them.', action: 'Reload the setup and read the statements again before approving.' },
  wrong_admin: { title: 'Only the person who planned this setup can approve it.', action: 'Ask them to approve it, or plan it again yourself.' },
  approval_expired: { title: 'The approval window has passed.', action: 'Plan the setup again. Nothing was changed in your warehouse.' },
  unknown_request: { title: 'That approval request is no longer open.', action: 'Plan the setup again. Nothing was changed in your warehouse.' },
  setup_not_pending: { title: 'That setup has already run or was cancelled.', action: 'Plan it again if you still want it.' },
  wrong_account: { title: 'You signed in to a different Snowflake account.', action: 'Nothing was created. Sign in to the account you registered and try again.' },
  wrong_role: { title: 'The sign-in did not use the Compass setup role.', action: 'Nothing was created. Sign in as a user who was given the setup role, and approve the role Compass asks for.' },
  registration_changed: { title: 'The setup role no longer matches the registration.', action: 'Nothing was created. Someone changed what the setup role can do; ask your admin, or register again.' },
  name_taken: { title: 'A role or user with that name already exists.', action: 'Nothing of yours was changed. Plan the setup again to get fresh names.' },
  deadline_exceeded: { title: 'The setup took too long and was stopped.', action: 'What it had created was removed. Try again.' },
  setup_step: { title: 'One of the setup steps was refused by Snowflake.', action: 'What it had created was removed. Check the setup role still has its grants, then try again.' },
  admin_connect_failed: { title: 'Compass could not open a session with that sign-in.', action: 'Nothing was created. Sign in again.' },
  grants_missing: { title: 'The new access is missing something it should have.', action: 'What it created was removed. Try again.' },
  grants_unexpected: { title: 'The new access has more than it should.', action: 'What it created was removed. Check the setup role, then try again.' },
  grants_dangerous: { title: 'The new access has a permission Compass never grants.', action: 'What it created was removed. Check the setup role, then try again.' },
  admin_required: { title: 'A setup needs a named person to approve it.', action: 'Sign in with your own account and plan the setup again.' },
  grants_unverified: { title: 'Compass could not confirm the new access.', action: 'What it created was removed. Try again.' },
  invalid_setup_id: { title: 'That setup name cannot be used.', action: 'Start again from the setup page.' },
  script_digest_mismatch: { title: 'The statements do not match their fingerprint.', action: 'Nothing was run. Plan the setup again; if it repeats, tell support.' },
  too_many_sign_ins: { title: 'Too many sign-ins are open at once.', action: 'Finish or wait for the ones you started, then try again.' },
  state_invalid: { title: 'That sign-in did not start from Compass.', action: 'Start the sign-in again from the setup page.' },
  state_expired: { title: 'The sign-in took too long.', action: 'Start the sign-in again.' },
  state_used: { title: 'That sign-in was already used.', action: 'Start the sign-in again.' },
  binding_mismatch: { title: 'That sign-in belongs to a different browser.', action: 'Start the sign-in again in this browser.' },
  authorization_refused: { title: 'Snowflake did not let you sign in.', action: 'Check you are allowed to use the Compass setup role, then try again.' },
  missing_code: { title: 'Snowflake did not complete the sign-in.', action: 'Start the sign-in again.' },
  token_exchange_failed: { title: 'Compass could not finish the sign-in with Snowflake.', action: 'Start the sign-in again. If it repeats, ask your admin to check the Compass registration.' },
  callback_malformed: { title: 'The sign-in answer could not be read.', action: 'Start the sign-in again.' },
  enrol_not_configured: { title: 'The enrol file is not available on this installation yet.', action: 'Use "Run the script yourself" instead. Nothing was created.' },
  registration_dangerous: { title: 'The new identity had more access than it should.', action: 'The enrol file removed what it created. Nothing was stored. Ask your Compass contact before trying again.' },
  registration_unexpected: { title: 'The new identity had access to something you did not name.', action: 'The enrol file removed what it created. Nothing was stored. Check which grants your workspace adds by default, then get a new file.' },
  registration_missing: { title: 'The new identity is missing some of the access you named.', action: 'The enrol file removed what it created. Check the admin account can grant on these tables, then get a new file.' },
  registration_warehouse: { title: 'The new identity does not have exactly the right access to the warehouse.', action: 'The enrol file removed what it created. Check the warehouse name, then get a new file.' },
  setup_changed: { title: 'This setup changed after the file was made.', action: 'Get a new file and run that one.' },
  token_used: { title: 'That file was already used.', action: 'Each file works once. Get a new file.' },
  token_expired: { title: 'That file expired.', action: 'A file works for 30 minutes. Get a new one.' },
  token_invalid: { title: 'Compass did not recognise that file.', action: 'Get a new file from this screen and run that one.' },
  too_many_attempts: { title: 'Too many failed attempts from this computer.', action: 'Wait a few minutes, then get a new file.' },
  connection_test_failed: { title: 'The connection test did not pass.', action: 'The checks below say what is missing. Ask your admin to re-run the script, then test again.' },
};

/** Codes can carry a detail after a colon (`admin_connect_failed:RuntimeError`) or a step number (`setup_step_3:...`); look up the stable part. */
export function describeError(code: string): ErrorCopy {
  const base = code.split(':')[0].replace(/^setup_step_\d+$/, 'setup_step');
  return ERROR_COPY[base] ?? { title: 'Something went wrong.', action: `Try again, and quote this code if it continues: ${code}.` };
}
