# Incident Postmortem — 2026-03-14 Checkout Outage

**Status:** Resolved · **Severity:** SEV-1 · **Author:** Priya Nadkarni (SRE)

## Summary

On 2026-03-14, Helix Freight's checkout API was unavailable or degraded for
**73 minutes**, from 14:02 to 15:15 UTC. Approximately 31,000 checkout
attempts failed. No data was lost.

## Root cause

The root cause was **database connection-pool exhaustion**. The checkout
service's pool was capped at `max_connections = 200`. A marketing email sent at
14:00 UTC drove a 6x traffic spike; the pool saturated within 90 seconds and new
requests blocked on connection acquisition until they timed out.

This was **not** a code deploy, a TLS/certificate problem, or an upstream
provider outage — all three were ruled out during triage (see timeline).

## Resolution

1. Raised `max_connections` from 200 to **800** on the checkout pool.
2. Added a circuit breaker that sheds load at 90% pool utilization.
3. Capped marketing-email send rate to 2,000 messages/minute.

## Follow-up actions

- HELIX-4471: load-test the pool ceiling before the next campaign (owner: Priya).
- HELIX-4472: alert on pool utilization > 75% (owner: Marcus).
