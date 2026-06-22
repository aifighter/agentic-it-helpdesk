# VPN Disconnects Every 10-15 Minutes

Use this runbook when remote employees lose VPN every few minutes or cannot access internal tools.

Diagnostic steps:

1. Confirm the user's location, device, and VPN client version.
2. Check VPN service status for the user's region.
3. Check recent VPN changes.
4. Search incident history for similar reconnect loops.
5. Verify device compliance before recommending network access changes.

Resolution path:

- If US-East VPN is degraded, advise switching to `us-west-vpn.company.test`.
- If the client is older than 6.4, ask the employee to update the VPN client and restart.
- If device posture is non-compliant, do not recommend bypasses; escalate to Endpoint Security.
- If no outage exists, ask what network the employee is using and which gateway is selected.

Escalate:

- Disconnects continue after client upgrade and alternate gateway.
- Device is non-compliant.
- Employee needs privileged network access they do not already have.
