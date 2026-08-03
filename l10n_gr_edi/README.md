# l10n_gr_edi — Greece myDATA (patched)

This is the official Odoo `l10n_gr_edi` module with bug fixes and feature
work applied on top. See `CHANGELOG.md` for the authoritative record of
divergence from upstream.

## Installation

This module must **override** the built-in `l10n_gr_edi` that ships with Odoo. To achieve this, place this repository in an addons path that has **higher priority** (listed earlier) than the official Odoo addons directory.

In your Odoo configuration:

```ini
[options]
addons_path = /path/to/odoo-modules,/path/to/odoo/addons
```

When Odoo scans addons paths left-to-right, it will find this patched version first and use it instead of the built-in one.

If you are using `git-aggregator`, configure it to merge this repo's addons path before the official one.

## Fixes

### UTF-8 Encoding

The official module encodes XML in ISO-8859-7 (Greek character set). This causes errors when partner names or city names contain non-Greek characters (e.g., Latin, Cyrillic). This patch switches all XML generation to UTF-8 and adds the proper `Content-Type: application/xml; charset=UTF-8` header to myDATA API requests.

### Floating-Point Rounding

`net_value`, `vat_amount`, and classification `amount` fields could contain floating-point artifacts (e.g., `100.00000000000001`) due to intermediate arithmetic. This patch wraps these values with `round(..., 2)` to ensure clean 2-decimal-place amounts in the XML sent to myDATA.

## Features on top of upstream

### Vendor-bill fetch (1.1)

The `RequestDocs` vendor-bill fetch cron is rewritten end-to-end (`models/res_company_fetch.py`): MARK watermark instead of refetching 90 days, pagination, partner auto-create with AADE enrichment, full tax mapping with reconciling fallback lines, credit-note correlation, cancellation handling, and per-invoice/per-company error isolation. Details in `CHANGELOG.md` [1.1].

### Greek CIUS / Peppol B2G (1.2)

Backport of upstream's `saas-19.4` Greek CIUS implementation (UBL BIS 3.0) for B2G e-invoicing via Peppol: `account.edi.xml.ubl_gr` builder, contracting-authority fields on partners, budget/project/contract references on invoices, CPV codes on products/lines, and Greek CIUS business-rule validation. UBL generation and Peppol sending are held back until the myDATA MARK is received. Details in `CHANGELOG.md` [1.2].

## License

LGPL-3 (same as the official module).
