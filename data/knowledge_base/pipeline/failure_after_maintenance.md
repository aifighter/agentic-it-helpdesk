# Pipeline Failures After Maintenance

Use this runbook when Jenkins jobs time out, Tableau reports are stale, Snowflake loads fail, or a data pipeline started failing after a maintenance window.

Diagnostic steps:

1. Check Jenkins, Snowflake, Tableau, and network status.
2. Check recent changes for maintenance that touched firewall rules, service accounts, or routes.
3. Search historical incidents for similar symptoms.
4. Determine whether production data freshness or customer-facing reports are impacted.

Likely causes:

- Firewall allowlists not refreshed for service accounts.
- Service account credentials rotated or expired.
- Network route changed during maintenance.
- Downstream report refresh jobs are stale because upstream Jenkins jobs time out.

Escalate:

- Production pipeline is impacted.
- Fix requires firewall rule changes.
- Fix requires service-account credential rotation.
- Root cause spans multiple systems.

Handoff should include affected systems, maintenance/change references, observed symptoms, business impact, and similar incidents.
