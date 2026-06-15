# On-Call Runbook

The primary on-call engineer acknowledges pages within 5 minutes. For checkout
latency alerts: check pool utilization on the Checkout dashboard first, then
upstream PostgreSQL CPU. To shed load, enable the circuit breaker via the
feature flag `checkout.circuit_breaker`. Escalate to the database on-call if
replica lag exceeds 30 seconds. Always open an incident channel for SEV-1 and
SEV-2 events.
