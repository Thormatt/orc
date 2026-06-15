# Platform Architecture

The Helix platform is a set of Python services behind an API gateway. The
Checkout API and Tracking Mesh each own a PostgreSQL 15 database; cross-service
reads go through a read-replica. Async work runs on a Redis-backed queue.
Services are deployed as containers on a managed Kubernetes cluster in eu-north-1
with a warm standby in eu-central-1. Connection pooling to PostgreSQL is handled
per-service with a configurable max-connections cap.
