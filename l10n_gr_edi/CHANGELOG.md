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

## [1.3.2] - 2026-08-04 — [local]

### Fixed
- A fiscal position expressing no VAT preference no longer blocks
  resolution: the Aegean "Domestic" position ships with an empty tax list
  (and x2many reads skip archived taxes), which produced an empty
  `map_tax` result and a fallback line on every bill of Aegean-registered
  suppliers charging the mainland rate. The hierarchy is now: the position
  wins when it maps to exactly one same-rate replacement; a deliberate
  different-rate mapping still falls back with a warning; an empty or
  ambiguous mapping expresses no preference and the payload rate stands.

## [1.3.1] - 2026-08-04 — [local]

### Fixed
- VAT resolution was refused on every domestic bill: the Greek chart's
  auto-applied "Domestic" fiscal positions list the Import/EU taxes as
  selectable destinations with `original_tax_ids` pointing at the domestic
  taxes, so `map_tax(24% G)` returns multiple candidates. A tax that itself
  lists the fiscal position (`tax.fiscal_position_ids`) is now kept unmapped —
  only taxes foreign to the position go through `map_tax`. Refusal warning
  reworded ("provides no single X% replacement").

## [1.3] - 2026-08-04 — [local]

Vendor-bill fetch: VAT amounts now land on real purchase taxes instead of
fallback lines. The previous unique-percent match could never fire on the
Greek CoA (seven active 24% purchase taxes), so every fetched bill needed
manual tax work.

### Added
- `_l10n_gr_edi_resolve_vat_tax()`: resolves the payload VAT rate to the
  Greek chart-template purchase tax by xml_id
  (`account.{company}_l10n_gr_tax_p{rate}_{G|S}`). The G/S suffix comes from
  the myDATA document type (1.x and retail-goods 11.1/11.3 → `G`; 2.x and
  retail-services 11.2/11.4 → `S`). Types without a goods/services signal
  (3.x, 5.x, 8.x), rates without a template tax (3%), and archived taxes keep
  the explicit fallback-line behavior — never guess.
- Fiscal-position precedence: the created bill carries the partner's fiscal
  position (`_get_fiscal_position`) and the resolved tax is mapped through it,
  so per-vendor tax preferences win over the G/S heuristic. A mapping that
  changes the rate is refused with a chatter warning (the payload totals are
  the legal record and must reconcile).
- `NON_FISCAL_INVOICE_TYPES`: delivery notes (type 9.3) are logistics
  documents and no longer create €0 vendor bills; they are skipped (debug
  log) while still advancing the watermark. Future: could feed inventory
  receipts.

### Changed
- VAT exemption categories are now surfaced in the chatter Warnings section
  (previously collected but never rendered).
- Fallback warning reworded: "VAT x% could not be mapped to a purchase tax".

## [1.2] - 2026-08-03 — [re-port]

Upstream 19.0 snapshot advanced to `8e07e45a2393` (2026-05-27) and selected
upstream changes backported from `saas-19.4`. All prior local patches
(UTF-8/rounding, vendor-bill fetch) remain applied; no conflicts beyond
context shifts.

### Added
- **Greek CIUS (Peppol BIS 3.0) for B2G e-invoicing** — backport of upstream
  `699e25230382` ([ADD] l10n_gr_edi: implement CIUS for Greece, saas-19.4),
  taken verbatim at its ADD state (which targets the 19.0-era
  `account_edi_ubl_cii` dict-node builder API — all hooks verified present
  in 19.0):
  - New `account.edi.xml.ubl_gr` builder (`models/account_edi_xml_ubl_gr.py`):
    GR-R-001 composite invoice number
    (`VAT|date|branch|inv_type|series|serial`), MARK as
    `AdditionalDocumentReference` (`##M.AR.K##`), project/contract references,
    billing reference on credit notes, contracting-authority party
    identification, CPV item classification, and the Greek CIUS business-rule
    constraints (GR-R-003/004/006/007, GR-BT-10/25/46/158).
  - New fields: `account.move.l10n_gr_edi_budget_type` /
    `l10n_gr_edi_project_reference` / `l10n_gr_edi_contract_reference`;
    `account.move.line.l10n_gr_edi_cpv_code` (computed from product);
    `product.template.l10n_gr_edi_cpv_code`;
    `res.partner.l10n_gr_edi_contracting_authority_name` / `_code`
    (format-validated).
  - `ubl_gr` registered as partner `invoice_edi_format` (suggested when
    contracting-authority data is set); UBL XML generation and Peppol sending
    are held back until the myDATA MARK is received
    (`_need_ubl_cii_xml` / `_is_applicable_to_move` overrides + send alert).
  - New dependency: `account_edi_ubl_cii` (auto-installed with `account`).
  - Tests: `tests/test_xml_ubl_gr.py` + `grcius_out_invoice.xml` /
    `grcius_out_refund.xml` fixtures. Fixtures taken at the `91b56353` state
    (item `Name`-only, no `Description`) — that is what the 19.0 UBL export
    actually emits; the ADD-state fixtures fail against 19.0 core.

### Fixed
- **Invoice report/product form UI** — upstream 19.0 `8e07e45a2393`: myDATA
  QR code moves to a new page when space runs out; myDATA classification
  group no longer shrinks on the product form.
- Tests: write `False` instead of `''` to selection fields (upstream
  `feed371f5e27`, test-only portion).

### Not ported (require saas-19.4 core, revisit on Odoo 20)
- `ir.access.csv` conversion (`e7cc76b2`) — 19.0 uses `ir.model.access.csv`.
- `peppol_eas/peppol_endpoint` → `routing_scheme/routing_endpoint` rename
  (`6f8c2526`) — fields don't exist in 19.0 (the `91b56353` fixture update
  *was* taken, see above).
- `account.move.is_refund()` cleanup (`686b2eb6`) — method absent in 19.0.
- Product-form `position="inside"` layout fix (`b64d2ef1`) — depends on a
  saas `account` view change.
- Manifest harmonization and saas i18n exports.

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
