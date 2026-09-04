# Changelog — account_online_viva

All notable changes to this module are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/); versions are the module
portion of the Odoo manifest version (`19.0.{major}.{minor}.{patch}`).
Importance: **patch** = fixes without data impact, **minor** = backward-compatible
features/fields, **major** = breaking changes requiring migration scripts.

## [1.3.0] - 2026-09-04

Enrichment from the other Data Services feeds (same `datafileapi` scope;
all verified live 2026-09-04). Every extra call is best effort: a failing
feed is logged and the sync continues with plain labels.

### Added
- **Sale references on clearance and commission lines.** Each sale in
  `POST /dataservices/v2/transactions/Search` produces one "Card payments
  clearance" line (gross, same day and amount) and one "Card commission"
  line (= `totalCommission`). Matching lines are labelled
  "Card payments clearance: Shop order 1042, CUSTOMER NAME", the
  customer becomes the partner name, and a `viva_sale` subset is stored in
  the line's transaction details (order code, reference, amounts, status —
  never the customer's e-mail or phone). Refunds read "Card refund
  clearance: …".
- **Card purchase details** from `POST /dataservices/v1/issuing/merchantexpenses`
  (its `walletTransactionId` equals the account transaction id): merchant
  with city and country, masked card number and MCC —
  "ACME SUPPLIES, ATHENS GRC (Card purchase ••1234)"; a `viva_card`
  subset is stored in the transaction details.
- **Opening balance.** On the first sync of a journal without statement
  lines, the closing balance of the day before the import window is read
  from `GET /dataservices/v2/merchants/mt940` (looking back up to 14 days
  for a statement day) and booked as "Opening statement: first
  synchronization", as Odoo's online sync does. Skipped when the MT940
  currency differs from the journal currency.
- `VivaClient.search_sales()`, `merchant_expenses()`, `mt940()`.

## [1.2.0] - 2026-09-04

Findings from the first live import against production data.

### Fixed
- **Available-balance holds were imported as transactions.** Viva `typeId`
  32 entries (card-purchase reserve/unreserve, sale-transaction reserve,
  obligation holds) only move the *available* balance and always net to
  zero; they appeared as paired +/- phantom lines in Bank Matching. Only
  balance movements (`typeId` 20/21) are imported now; ignored holds are
  counted in the chatter note. `typeId` 21 (overdraft) is kept on the
  docs' word only — none seen in data. If a journal was already synced on
  an earlier version, delete the phantom lines (statement lines whose
  `transaction_details->>'typeId' = '32'`, all unreconciled ±pairs) and
  run *Reset Sync*; real lines are deduplicated by transaction id.
- **Watermark advanced after an all-skipped run.** A fetch whose
  transactions were all skipped for currency mismatch (journal currency not
  set, or wrong) still advanced *Fetched Until*, so the next incremental run
  silently lost everything before the 7-day overlap. Such a run now keeps
  the watermark and flags the account with an explicit error naming both
  currencies.

### Changed
- Readable statement labels: Viva `subTypeId` is mapped to a name
  ("Card commission", "Card payments clearance", "Pricing cashback",
  "Transfer to IBAN", …) and appended to the counterparty when present
  ("CLOUDFLARE (Card purchase)"); `userDescription` is used as label text
  too. The bare "Viva 20/13" form only remains for unknown subtypes.

## [1.1.0] - 2026-09-04

### Added
- Journal-level Viva configuration, mirroring Odoo's own bank feeds: choosing
  *Viva.com* under **Bank Feeds** on a bank journal reveals the wallet id
  (entering one creates the Viva account), the earliest import date, the
  *Fetched Until* date, a failure indicator and link-style actions
  (*Fetch Transactions*, *Fetch Date Range*, *Viva Account*).
- Dashboard bank card: native-style *Fetch Transactions* link with a
  *Fetched until …* status line (or a red *Last fetch failed* link).
- *Reset Sync* action (manager-only) to clear the incremental watermark.
- Viva Accounts list: search view (Sync Error / Archived filters, group by),
  red rows on error, inline fetch button, optional columns; form: title,
  archived ribbon, journal smart button, error alert shown only when set;
  empty-state help text.
- Settings: own *Bank Feeds › Viva.com* block (Odoo's *Bank & Cash* block is
  Enterprise-only) with labelled credential rows plus *Discover Wallets* and
  *Viva Accounts* links.
- Dashboard: a connected Viva journal no longer shows the "Connect your bank"
  helper text.

### Changed
- `viva.account.last_successful_to` is now a `Date` (*Fetched Until*); it
  always held a window end date and displayed a spurious time.
- Menus: *Viva Accounts* moved into Configuration › Accounting (with
  Journals, where Odoo's own *Online Synchronization* sits); the
  *Discover Wallets* menu entry is gone — the wizard is launched from Settings.
- Native wording throughout: *Fetch Transactions*, *Fetch Date Range*,
  *Discover Wallets*, *Discard*.
- After a fetch, open the journal's native view: the bank reconciliation
  widget on Enterprise (`action_open_reconcile`), the journal's statement
  lines on Community.
- Cron: a company without credentials is logged once and its accounts are
  flagged with `last_error` instead of failing (and posting a note) per account.
- Setup wizard group check raises `AccessError` like the other actions.

### Fixed
- Fetch actions, the date-range wizard and read access on Viva accounts now
  require `account.group_account_basic` (as Odoo's own online sync does)
  instead of `account.group_account_user`, which Community never grants —
  on Community even an Accounting Administrator could not fetch.

## [1.0.1] - 2026-07-23

### Fixed
- Drop the legacy per-column unique index on `viva_transaction_id` on upgrade;
  the declarative composite `(journal_id, viva_transaction_id)` unique index is
  the only dedup constraint.

### Changed
- Sync hardening: per-scope OAuth2 token expiry handling, pagination stops on
  HTTP 204/short page (spec-deprecated `totalPages` ignored), page-cap
  overflow raises instead of truncating, SKIP LOCKED row lock with quiet
  cron skip on contention (`VivaLockedError`), inverted fetch windows raise,
  only `UniqueViolation` swallowed on line creation; expanded test coverage.

## [1.0.0] - 2026-06-22

### Added
- Initial release: self-hosted Viva.com bank connector on base `account`
  (edition-agnostic, no Enterprise `account_online_synchronization`).
- `VivaClient` service layer: OAuth2 client-credentials per scope, paginated
  Data Services transaction search.
- `viva.account` model linking one Viva wallet to one bank journal; owns sync
  state (`sync_start_date`, `last_successful_to`, `last_error`) and the
  fetch → map → dedup → create pipeline.
- Statement-line dedup via `viva_transaction_id` + partial unique index.
- 12h cron with per-account savepoint isolation and `_commit_progress`;
  incremental windows with 7-day overlap; currency/lock-date guards with
  skip counts reported in chatter.
- "Viva: Discover Wallets" setup wizard (manager-only) with journal
  auto-creation; date-range fetch wizard (does not advance the watermark);
  journal dashboard fetch button.
- Security: read-only for account users, full for managers, Python group
  check on manual fetch, multi-company `ir.rule`, secret restricted to
  `base.group_system` on company and settings mirror.
