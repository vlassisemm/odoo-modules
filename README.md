# Odoo Modules

Custom Odoo modules for Greek localization and business workflows.

## Modules

### [`l10n_gr_afm`](l10n_gr_afm/) — Greece AFM Lookup (v19.0.1.0.0)

Fetch business registry data from AADE (Independent Authority for Public Revenue) using a partner's Greek VAT number (AFM).

- **"Fetch from AADE"** button on partner contacts with a Greek VAT
- Preview wizard to review fetched data before applying changes
- Populates name, address, Tax Office (DOY), and primary activity code (KAD)
- Multi-company support with per-company AADE credentials
- Chatter audit trail (optional, when `mail` is installed)
- Access restricted to Sales and Accounting users

### [`l10n_gr_edi`](l10n_gr_edi/) — Greece myDATA (v1.0, patched)

Patched version of the official Odoo `l10n_gr_edi` module for Greece's myDATA e-invoicing platform. Fixes applied on top of the official code:

- **UTF-8 encoding** — official module uses ISO-8859-7, which breaks on non-Greek characters
- **Floating-point rounding** — wraps `net_value`, `vat_amount`, and classification `amount` with `round(..., 2)`

> **Note:** This module must override the built-in `l10n_gr_edi`. Place this repository's addons path **before** the official Odoo addons path so it takes priority.

## Installation

Clone this repository into your Odoo addons path:

```bash
git clone https://github.com/vlassisemm/odoo-modules.git
```

Add the path to your Odoo configuration. List this path **before** the official Odoo addons directory (required for `l10n_gr_edi` to override the built-in version):

```ini
[options]
addons_path = /path/to/odoo-modules,/path/to/odoo/addons
```

Then install modules from **Settings > Apps** or via command line:

```bash
odoo-bin -d <dbname> -i <module_name> --stop-after-init
```

## Compatibility

All modules target **Odoo 19** (branch `19.0`).

## Contributing

Contributions are welcome. Please open an issue or pull request on [GitHub](https://github.com/vlassisemm/odoo-modules).

## License

All modules are licensed under [LGPL-3.0](LICENSE) unless stated otherwise in their individual manifests.
