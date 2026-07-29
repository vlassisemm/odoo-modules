# Changelog — account_online_viva

All notable changes to this module are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/); versions are the module
portion of the Odoo manifest version (`19.0.{major}.{minor}.{patch}`).
Importance: **patch** = fixes without data impact, **minor** = backward-compatible
features/fields, **major** = breaking changes requiring migration scripts.

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
