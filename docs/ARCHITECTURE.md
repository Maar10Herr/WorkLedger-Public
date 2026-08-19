# WorkLedger architecture, schema, and UX plan

## Architecture

A cohesive Django 5.2 monolith runs as `web` (Gunicorn) and `worker` (Celery), backed by PostgreSQL 18 and Redis. Django templates provide all pages. Alpine.js owns local progressive-disclosure state and local drafts; HTMX is reserved for server-derived fragments (lookup, history, saved-location creation). Originals, previews, exports, and backups live below a configurable host bind mount; PostgreSQL stores metadata only.

Domain code is split into explicit Django apps: `accounts` (owner PIN/session), `ledger` (events/revisions/audit), `travel` (locations/journeys/routes/trains/passes), `evidence` (streamed attachments/previews), `expenses` (expenses and reimbursement), `taxes` (versioned facts/rules/derivations), and `exports` (range packages and portable backup). Services own transactions and calculations; views remain thin.

## Core schema

- `Owner`: singleton identity, Argon2id PIN hash, failed-attempt and lockout state.
- `Event`: stable UUID, event type, current revision FK, created timestamp.
- `EventRevision`: immutable UUID, event FK, parent FK, monotonic revision number, effective timestamp, recorded timestamp, complete JSON snapshot, completeness, deletion marker, comment, previous/current global audit hash.
- `AuditEntry`: append-only sequence for revisions, attachments, exports, backups, and verification results.
- `Employer`, `Location`, `FavouriteJourney`, `RouteDefinition`, `RailPass`: reusable master data with versioned or auditable changes where evidentiary significance exists.
- `Attachment`: UUID metadata, content hash, detected format, original path, preview status/error, linkage through an evidence-group relation.
- Current domain tables (`Journey`, `ExternalActivity`, `Expense`, `Reimbursement`, `TaxTreatment`) use event UUIDs and are rebuilt/updated transactionally from the current complete snapshot; immutable revisions remain the source of historical truth.
- `TaxRuleSet` and rate rows are effective-dated data. Derived tax results record rule version, inputs, warnings, and resulting Decimal amounts.

PostgreSQL constraints enforce revision-number uniqueness and valid parent relationships. Deferred constraint triggers validate the event current pointer. `BEFORE UPDATE OR DELETE` triggers reject changes to revisions and audit entries for the runtime DB role. A narrowly privileged migration/restore role owns the tables; application roles cannot disable triggers. The global hash is calculated under a transaction-level advisory lock over canonical JSON plus the prior hash.

## UX

After PIN login the home page has exactly two dominant controls: **enter new** and **view past entries**. A compact overflow menu contains settings, exports, reimbursement queue, unresolved entries, and status.

`enter new` opens three large branches. Work-from-home posts immediately with one tap. Travel and expense forms are single-page decision trees: destination then transport, with only mode-specific fields visible. Every form accepts an incomplete save and then shows the smallest useful missing-facts list. Recent values and unsent drafts stay in browser storage. Mobile controls are thumb-sized, labels are explicit, and the normal receipt file input omits `capture`.

The timeline is dense, reverse chronological, filterable, and links to current facts, derivations, attachments, reimbursement state, and revision diffs. Exports are date-range actions, never scheduled annual jobs.

## Security and reliability

PIN setup/change never logs plaintext. Six or more digits are accepted and stored only with Django Argon2PasswordHasher (Argon2id); verification uses the password library's constant-time path. Failed attempts incur escalating server delay and persistent temporary lockout. CSRF, secure HttpOnly SameSite cookies, explicit trusted hosts/origins, sensitive-data log filters, upload streaming, file signature validation, path isolation, and per-file/disk-space checks are mandatory. Local HTTP cookies are configurable for localhost development; Tailscale HTTPS is documented.

All exports are deterministic (stable ordering, canonical JSON, fixed schemas). Backup uses `pg_dump` plus content files, manifest, and SHA-256 checksums; restore targets a clean stack and runs database integrity, hash, current-pointer, and audit-chain verification.

## Verification strategy

Use vertical TDD slices. Unit/property tests cover tax boundaries, decimal arithmetic, canonical audit hashing, filters, and revision diffs. PostgreSQL integration tests exercise triggers and concurrency. Django client tests cover workflows. Playwright uses an iPhone viewport. Compose smoke tests verify health/readiness, worker execution, export generation, and a backup restored into a second clean Compose project.
