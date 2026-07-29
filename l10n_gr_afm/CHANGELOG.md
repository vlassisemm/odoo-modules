# Changelog — l10n_gr_afm

All notable changes to this module are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/); versions are the module
portion of the Odoo manifest version (`19.0.{major}.{minor}.{patch}`).
Importance: **patch** = fixes without data impact, **minor** = backward-compatible
features/fields, **major** = breaking changes requiring migration scripts.

## [1.0.0] - 2026-03-23

### Added
- Initial release: AADE AFM (Greek VAT registry) lookup for partners.
- SOAP integration with the RgWsPublic2 service (`_l10n_gr_afm_build_envelope`,
  `_l10n_gr_afm_call_aade`, `_l10n_gr_afm_parse_response`,
  `_l10n_gr_afm_extract_vat`), lxml-built envelopes, configurable timeout.
- "Fetch from AADE" partner button gated by computed `l10n_gr_afm_can_fetch`;
  preview wizard with apply-to-partner flow; audit trail via chatter note.
- Per-company AADE credentials (password `base.group_system` + `copy=False`,
  mirrored on settings); Python group check before consuming AADE quota.
- Greek `el.po` translation; standalone debug script
  `scripts/test_aade_call.py`.

### Fixed
- (2026-03-19, released within 1.0.0) "/" placeholder for name, explicit
  field labels, and primary-button styling on the partner form.
