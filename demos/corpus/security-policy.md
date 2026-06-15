# Security Policy

All production access requires SSO with hardware-key MFA. Secrets are stored in
the managed KMS; no secret may be committed to a repository. TLS 1.3 terminates
at the gateway, and certificates are rotated automatically every 60 days via the
ACME integration. Access to customer data is least-privilege and reviewed
quarterly. Penetration tests run twice a year.
