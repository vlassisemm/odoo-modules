# Changelog — l10n_gr_edi (patched official addon)

This module is a **patched copy of Odoo's official `l10n_gr_edi`** (Greece
myDATA), installed by placing this repository's addons path before the
official one. It is versioned as *upstream base + local lineage*:

- The upstream base within the 19.0 series is **`1.0`** (Odoo rarely bumps
  official-addon versions; changes ship silently).
- Local releases extend it: **minor** bumps for features (`1.1`), an added
  **patch** segment for fixes (`1.1.1`). If upstream ever bumps its own
  version, the next `[re-port]` entry records the new base and the local
  counter restarts on top of it.
- Entries are tagged **[local]** (our changes) or **[re-port]** (rebase onto a
  newer upstream snapshot, listing the re-applied patches). This file — not
  git alone — is the authoritative record of divergence from upstream.

## [1.1] - 2026-07-29 — [local]

### Changed
- **Vendor-bill fetch rewritten end-to-end** (`_cron_l10n_gr_edi_fetch_invoices`
  moved to new `models/res_company_fetch.py`; official `res_company.py` now
  fields-only):
  - Mark watermark (`res.company.l10n_gr_edi_fetch_mark`, system-only; clear
    it to refetch the 90-day window) instead of refetching 90 days each run;
    `continuationToken` pagination with an empty-token guard.
  - Partner resolution: EL-prefix-aware, company-scoped VAT matching
    (active preferred over archived); auto-created vendors tagged
    "myDATA Auto-created" and enriched from the AADE registry via
    `l10n_gr_afm` when installed (soft dependency).
  - Lines: descriptions from `itemDescr`/`lineComments`/`itemCode`; full VAT
    category map incl. codes 9 (3%) and 10 (4%); purchase-scoped tax
    matching; withheld/fees/stamp-duty/other-taxes/deductions mapped to real
    taxes or explicit fallback lines (totals always reconcile); guard against
    charge percentages colliding with VAT rates.
  - Header: credit invoices (5.1/5.2) as `in_refund` with `reversed_entry_id`
    via correlated MARKs; `ref` from series/aa; foreign currency;
    `account.move.l10n_gr_edi_is_fetched` + "Fetched from myDATA" filter.
  - Chatter note per fetched bill: source (MARK/UID/type/QR link — http(s)
    only), partner resolution, tax-mapping report, payment methods, warnings
    incl. a totals checksum against `invoiceSummary`.
  - Cancellations (`cancelledInvoicesDoc/cancelledInvoice`): auto-cancel only
    never-posted untouched drafts (`not posted_before`); chatter note +
    activity on posted bills; fail-closed on missing marks; idempotent.
  - Robustness: per-invoice savepoints, per-company isolation (fetch and
    cancellation processing), guarded `l10n_gr_edi.fetch_timeout`
    config parameter (default 30s).
- New data: `res.partner.category` "myDATA Auto-created"
  (`data/l10n_gr_edi_fetch_data.xml`).
- Tests: `tests/test_mydata_bill_fetch.py` (7 classes, ~60 tests).

## [1.0] - 2026-03-19 — [local]

### Fixed
- **UTF-8 encoding:** XML generation switched from ISO-8859-7 to UTF-8,
  manual `.encode('ISO-8859-7')` calls removed,
  `Content-Type: application/xml; charset=UTF-8` header added.
- **Floating-point rounding:** `net_value`, `vat_amount`, and classification
  `amount` wrapped with `round(..., 2)`.

(Released without a version bump on top of the vendored base.)

## [1.0] - 2026-03-19 — [re-port]

- Vendored the official Odoo 19.0 `l10n_gr_edi` addon as the upstream base.
