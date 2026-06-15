# Release Notes — Checkout API v4.0

v4.0 introduces idempotency keys on all booking endpoints, a 30% reduction in
p99 latency from query batching, and configurable connection-pool sizing per
environment. Breaking change: the deprecated `/book/legacy` endpoint is removed.
v4.0 shipped 2026-02-10. The circuit-breaker feature flag was added later, in a
2026-03-14 hotfix.
