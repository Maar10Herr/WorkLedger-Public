# Security policy

WorkLedger is experimental self-hosted software for a trusted machine or
trusted private network. No independent security audit has been performed.
Keep it off the public internet.

## Reporting a vulnerability

Use GitHub's private vulnerability-reporting or Security Advisory interface for
this repository. Keep credentials, personal records, database dumps, and
unredacted evidence out of issues.

Include the affected release, deployment mode, reproduction steps, expected
impact, and the smallest safe proof of concept. Security reports are assessed
before public disclosure.

## Supported versions

Only the latest tagged release receives security fixes. Pin images and Python
dependencies as documented, retain loopback-only port binding, and apply patch
releases promptly.

## Deployment boundary

WorkLedger stores sensitive evidence. Operators are responsible for host
hardening, encrypted backups, access control, TLS at any reverse proxy, and
restricting database, Redis, and attachment storage to trusted principals.
