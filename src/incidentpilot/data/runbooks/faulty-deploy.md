# Faulty deployment

Use this runbook when an error spike follows a known deployment and logs identify a change-related failure.

1. Compare deployment time with the error-rate change and inspect logs for the changed component.
2. A simulated rollback may be proposed only when the version and causal evidence are recorded and an operator explicitly approves it.
3. Verify that the simulated error rate falls and the previous version is marked active.
