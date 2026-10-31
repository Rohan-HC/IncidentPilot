# Database connection pool exhaustion

Use this runbook when active connections approach the configured pool limit and logs show acquisition timeouts.

1. Compare active connections with the pool limit and inspect timeout logs.
2. Check for client leaks, long transactions, and an unusual increase in request volume. These are hypotheses until separate evidence confirms them.
3. A simulated pool-size increase may be proposed only after an operator reviews the evidence and approves it. Verify that active connections fall below the new limit and latency improves.
