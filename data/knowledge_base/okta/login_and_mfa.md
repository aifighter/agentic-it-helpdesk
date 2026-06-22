# Okta Login and MFA Troubleshooting

Use this runbook when an employee cannot sign in to Okta, has repeated password reset failures, sees an account locked message, or cannot complete MFA.

Diagnostic steps:

1. Confirm the employee identity and urgency.
2. Check the employee directory for `account_locked`, `mfa_status`, and risk flags.
3. Check Okta system status before assuming the issue is user-specific.
4. Search resolution history for lockout or MFA-reset patterns.
5. Evaluate policy before claiming the agent can unlock or reset anything.

Agent-authorized resolution:

- If Okta is healthy, the account is locked, MFA is enrolled, and there is no account compromise signal, the agent may unlock the account in the mock environment.
- If MFA push is unreliable but MFA is enrolled, ask the employee to use a six-digit authenticator code.
- Ask the employee for the exact error when the account is active and status is healthy.

Escalate:

- MFA reset or enrollment is required.
- The employee lost their MFA device.
- Account compromise is suspected.
- The employee is unknown or cannot be matched to the directory.
