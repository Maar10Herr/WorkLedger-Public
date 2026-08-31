# German tax-rule provenance (2026)

WorkLedger stores the implemented facts as versioned `TaxRule` rows. This note
records the legal source and the interpretation used by the 2026 seed migration;
consult applicable tax guidance for filing decisions.

## Primary source

- **EStG § 9:** <https://www.gesetze-im-internet.de/estg/__9.html>
- **Commuting amendment:** Steueränderungsgesetz 2025, BGBl. I 2025 Nr. 363, effective 1 January 2026.

## Implemented rules

### Commuting allowance

EStG §9(1) sentence 3 no. 4 sentence 2 uses **€0.38 for every full kilometre** between residence and first workplace from 1 January 2026. WorkLedger uses €0.38 from kilometre one, only once per destination/day, and applies the annual €4,500 cap unless the recorded transport is an own or employer car.

### Domestic meal per diems

EStG §9(4a) sentence 3:

- 24-hour absence: **€28**
- arrival/departure day associated with an overnight stay: **€14**
- one-day absence longer than eight hours: **€14**

EStG §9(4a) sentence 8 reduces the €28 full-day rate by 20% for breakfast and 40% each for lunch and dinner: €5.60 / €11.20 / €11.20 for the 2026 domestic rate. A recorded personal co-payment offsets the corresponding meal reduction; the remaining per diem is never negative.

### Three-month limit

EStG §9(4a) sentence 6 limits meal per diems to the first three months of a
longer-term activity at the same workplace. Sentence 7 resets the clock after an
interruption of at least four weeks. Uncertain cases remain incomplete until the
owner records yes/no.

## Versioning policy

Rules are append-only. Later legal changes create a new code/effective period;
existing rule rows and derivation records remain unchanged. Exports include the
exact rule codes, values, source citation, revision snapshots, and derivation
hashes used.
