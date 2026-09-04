# Viva Bank Connector (`account_online_viva`)

Odoo 19 module that imports transactions from a Viva.com (Viva Payments) business
account into Odoo as bank statement lines, ready for reconciliation. It is
**self-hosted**: Odoo talks to the Viva REST API directly with your own
credentials — no third-party aggregator and no Odoo online-sync proxy.

## What It Does

- Imports Viva business-account **IBAN ledger transactions** as
  `account.bank.statement.line` records on a bank journal
- **Scheduled polling** every 12 hours via an `ir.cron` job (incremental, with a
  7-day overlap window so nothing is missed at the boundary)
- **Manual fetch** — *Fetch Transactions* on the bank journal (form and
  dashboard card) or on the Viva Account form
- **Date-range fetch** wizard for one-off historical imports (does not move the
  incremental cursor)
- **Wallet discovery** wizard that tests your credentials, lists your wallets,
  and auto-creates a bank journal + Viva Account for each one
- **Idempotent** — transactions are de-duplicated by Viva transaction id, so
  re-runs and overlapping windows never create duplicates
- Imports only real balance movements (Viva `typeId` 20/21); available-balance
  holds such as card-authorisation reserves (`typeId` 32) are ignored
- Readable labels from Viva's transaction sub-types, enriched from the other
  Data Services feeds: clearance and commission lines carry the shop order
  reference and customer of the settled sale ("Card payments clearance:
  Shop order 1042, CUSTOMER NAME"), card purchases carry merchant, city,
  country, masked card and MCC ("ACME SUPPLIES, ATHENS GRC (Card
  purchase ••1234)")
- Books an **opening balance** from Viva's MT940 statement on the first sync
  of an empty journal, so the journal balance matches Viva
- Skips transactions whose currency differs from the journal currency (a run
  where *everything* is skipped is flagged as an error and does not advance
  the incremental cursor), and transactions dated on or before an accounting
  lock date
- **Multi-company** with per-company API credentials
- **Edition-agnostic** — works on Odoo Community and Enterprise (built on the
  base `account` module, not on Enterprise online synchronization)

## How It Works

- **`VivaClient`** (`models/viva_client.py`) — a plain Python client for the Viva
  REST API: OAuth2 client-credentials token, wallet listing, and paginated
  transaction search. It has no ORM dependencies, so it is unit-testable in
  isolation.
- **`viva.account`** (`models/viva_account.py`) — links one Viva **wallet** to
  one Odoo **bank journal**. It holds the sync window state
  (`sync_start_date`, `last_successful_to`, `last_error`) and orchestrates the
  fetch → map → de-duplicate → create pipeline.
- **Statement-line dedup** — a `viva_transaction_id` field on
  `account.bank.statement.line` backed by a partial unique index on
  `(journal_id, viva_transaction_id)`, plus a Python pre-filter and an
  `IntegrityError` fallback for concurrent races.
- **Bank-feed source** — registers `viva` as a statement source on bank
  journals, alongside Odoo's built-in sources.

## Requirements

- **Odoo 19**
- A **Viva.com business account** with API access
- **Viva API credentials** — the **Account Transactions credentials**
  (*Διαπιστευτήρια συναλλαγών λογαριασμού*): an OAuth2 Client ID and Client Secret
- **Data Services API enabled on those credentials.** The connector reads
  transaction data through Viva's Data Services API, which requires the OAuth
  scope `urn:viva:payments:biservices:datafileapi`. This scope is **not granted
  by default** — Viva must enable Data Services on the credential's User Role.
  See [Viva access requirements](#viva-access-requirements) below.

No external Python packages required (uses `requests` shipped with Odoo).

**Dependencies:** `account`, `mail` (both hard).

## Installation

Copy this module into your Odoo addons path and install it:

```bash
odoo-bin -d <dbname> -i account_online_viva --stop-after-init
```

## Configuration

1. **Credentials** — *Settings > Accounting > Bank Feeds > Viva.com*: pick the Environment (Demo / Production) and enter the Client ID
   and Client Secret. Credentials are stored per-company, so multi-company
   setups can use different ones.
2. **Connect wallets** — either click **Discover Wallets** in that settings
   block (accounting managers only; tests the credentials and creates a bank
   journal + Viva Account for each wallet that does not already have one), or
   open a bank journal, choose **Viva.com** under *Bank Feeds* and type the
   wallet id — the Viva Account is created for you.
3. **Review** — the journal form shows the earliest import date (defaults to
   90 days back), the *Fetched Until* date and any failure; the full list is
   under *Accounting > Configuration > Viva Accounts*.

## Usage

- **Automatic:** the cron *"Viva: fetch bank transactions"* runs every 12 hours
  and imports new transactions for every configured account. Each account syncs
  inside its own savepoint, so one account failing does not block the others;
  failures are recorded on the account (`last_error`) and posted to its chatter.
- **Manual (current window):** click **Fetch Transactions** on the bank
  journal's dashboard card or form, or on the Viva Account.
- **Manual (historical range):** **Fetch Date Range** imports a specific
  period without disturbing the incremental watermark.
- **Reset Sync** (managers, on the Viva Account) clears the watermark so the
  next fetch restarts from the earliest import date; dedup prevents duplicates.

Imported lines land on the journal's bank statement lines for normal
reconciliation.

## Limitations

- **Opening balance:** booked once, on the first sync of a journal without
  statement lines, from the MT940 closing balance of the day before the
  import window (up to 14 days back). Set the *earliest import date* before
  the first sync: backfilling a period *before* that date with *Fetch Date
  Range* double counts, since the opening line already includes it (delete
  the opening line and *Reset Sync* after a deep backfill). Like Odoo's own
  online sync, the opening line must be reconciled once against your
  opening-balance / equity account.
- **Enrichment is best effort:** the sales and card-expenses calls fail
  soft (plain labels, a warning in the log). Two sales with the same amount on
  the same day (typical for the flat refund fee) are matched in feed order, so
  the customer named on a *commission* line can be swapped between them.
  Foreign-currency card purchases do not appear in the card-expenses feed and
  keep the plain "MERCHANT (Card purchase)" label. The sales endpoint works
  with the Data Services token today, although Viva's spec lists another
  scope for it.
- **Card data:** Viva exposes no original currency/amount for foreign card
  purchases (only the separate foreign-currency fee line mentions them), and
  no receipt reference.

## Security

- **Viva Accounts:** read-only for *Invoicing & Banks* users
  (`account.group_account_basic`); create and update for *Accounting Managers*
  (`account.group_account_manager`).
- **Client Secret:** restricted to *Settings* users (`base.group_system`) and
  excluded from copy, on both the company field and its settings mirror.
- **Wallet discovery** and **Reset Sync** are manager-only. The manual fetch
  actions also enforce the `account.group_account_basic` check in Python, so
  they cannot be triggered over RPC by an unprivileged user.
- A multi-company record rule isolates each company's Viva Accounts. Bookkeeping
  writes performed during a sync run with elevated rights, while the originating
  user remains the creator of the statement lines.

## Viva API

| Purpose            | Method & Path                                                | OAuth scope |
|--------------------|--------------------------------------------------------------|-------------|
| OAuth2 token       | `POST {accounts-host}/connect/token`                         | — (client-credentials) |
| Wallet discovery   | `GET {api-host}/merchants/v1/wallets`                        | `urn:viva:payments:core:api:merchants:wallets` |
| Transaction search | `POST {api-host}/dataservices/v2/accounttransactions/Search` | `urn:viva:payments:biservices:datafileapi` |
| Sale transactions (enrichment) | `POST {api-host}/dataservices/v2/transactions/Search` | same token (works live; spec lists another scope) |
| Card expenses (enrichment) | `POST {api-host}/dataservices/v1/issuing/merchantexpenses` | `urn:viva:payments:biservices:datafileapi` |
| MT940 (opening balance) | `GET {api-host}/dataservices/v2/merchants/mt940?ReportDate=` | `urn:viva:payments:biservices:datafileapi` |

Hosts by environment:

- **Demo:** `demo-accounts.vivapayments.com` / `demo-api.vivapayments.com`
- **Production:** `accounts.vivapayments.com` / `api.vivapayments.com`

The HTTP timeout defaults to 60 seconds and can be changed via the system
parameter `account_online_viva.timeout`.

### Viva access requirements

Transaction data is served by Viva's **Data Services API**. Every
`/dataservices/*` endpoint (account transactions, exports, MT940, webhook
subscriptions) requires a single OAuth scope:
**`urn:viva:payments:biservices:datafileapi`**.

This scope is **gated**: a freshly created Account Transactions credential is
*not* authorized for it by default. Requesting a token for it returns
`invalid_scope`, and the data endpoints reject any other token with
`401 — "The audience is invalid"` (Data Services needs a `biservices`-audience
token, whereas ordinary credentials only mint `core_api`-audience tokens). To
enable it, ask Viva (account manager / support) to grant the **Data Services
API** to the credential's **User Role**.

Wallet discovery additionally needs working **Wallet API** access
(`urn:viva:payments:core:api:merchants:wallets`). This is optional — IBANs can be
configured manually if the Wallet API is not enabled.

You can verify access without Odoo using the bundled smoke test (it reads
credentials from environment variables — never pass secrets on the command line):

```bash
export VIVA_CLIENT_ID=... VIVA_CLIENT_SECRET=... VIVA_ENV=production
python3 account_online_viva/scripts/test_viva_dataservices.py \
    --token-only --scope urn:viva:payments:biservices:datafileapi
# Expect HTTP 200 once Data Services is enabled; `invalid_scope` means it is not.
```

## Status

This module is **Alpha**. The transaction-import path is **verified end-to-end
against the live production Viva API** (2026-07-07): OAuth token with the
`datafileapi` scope, `POST /dataservices/v2/accounttransactions/Search`, and
its pagination contract (full pages until `HTTP 204 No Content`; the deprecated
`totalPages` field is ignored).

Remaining TODO (blocked on credentials, not code):

- **Wallet discovery** needs a **core_api-audience credential** with Wallet API
  access (`urn:viva:payments:core:api:merchants:wallets`). Data Services
  credentials are biservices-only: they cannot mint that scope
  (`invalid_scope`), and the wallet endpoints reject biservices tokens with
  401 *audience invalid*. Until Viva provides such a credential, the
  *Discover Wallets* wizard fails with a clear message — create the bank
  journal and Viva Account manually instead (the `walletId` appears in the
  transaction data).

Wallet-response field names are still coded defensively (multiple key
spellings tolerated) and should be confirmed once Wallet API access is
granted.

## License

LGPL-3
