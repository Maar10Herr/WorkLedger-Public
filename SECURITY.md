# Security policy

WorkLedger is experimental self-hosted software intended for deployment on a
trusted machine or trusted private network. It has not received an independent
security audit and must not be exposed directly to the public internet.

## Reporting a vulnerability

Please use GitHub's private vulnerability-reporting or Security Advisory
interface for this repository. Do not include credentials, personal records,
database dumps, or unredacted evidence in an issue.

Include the affected release, deployment mode, reproduction steps, expected
impact, and the smallest safe proof of concept. Security reports are assessed
before public disclosure.

## Supported versions

Only the latest tagged release receives security fixes. Pin images and Python
dependencies exactly as documented, retain loopback-only port binding, and
apply patch releases promptly.

## Deployment boundary

WorkLedger stores sensitive evidence. Operators are responsible for host
hardening, encrypted backups, access control, TLS at any reverse proxy, and
restricting database, Redis, and attachment storage to trusted principals.
