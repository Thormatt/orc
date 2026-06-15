# Glossary

- **Booking**: a single freight reservation created via the Checkout API.
- **Tracking Mesh**: the telemetry pipeline that ingests shipment status events.
- **Pool utilization**: the fraction of a service's database connection pool in
  use; the primary leading indicator for checkout latency.
- **Circuit breaker**: a load-shedding mechanism that rejects excess requests to
  protect the database during a spike.
- **SEV-1**: highest-severity incident; customer-facing outage.
