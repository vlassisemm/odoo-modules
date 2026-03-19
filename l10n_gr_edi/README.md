# l10n_gr_edi — Greece myDATA (patched)

This is the official Odoo `l10n_gr_edi` module with bug fixes applied on top.

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

## License

LGPL-3 (same as the official module).
