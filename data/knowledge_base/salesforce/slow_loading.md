# Salesforce Slow Loading

Use this runbook when Salesforce loads slowly, especially if multiple employees in one office report the same issue.

Diagnostic steps:

1. Check Salesforce vendor/internal service status.
2. Check office network status for the employee location.
3. Search incident history for location-specific SaaS slowness.
4. Ask whether slowness affects all pages or only reports/dashboards if the scope is unclear.

Resolution path:

- If Salesforce is healthy but the office network is degraded, explain the network correlation and suggest VPN alternate routing or another network.
- If Salesforce has a vendor incident, communicate the active incident and next update time.
- If only one browser is affected and no outage exists, suggest private browsing, cache clear, and extension isolation.

Escalate:

- Whole office is affected with no known incident.
- Revenue-impacting work is blocked and no workaround exists.
- The agent cannot correlate symptoms with status or history.
