# Odoo Modules

Custom Odoo modules for Greek localization and business workflows.

## Modules

| Module | Version | Summary |
|--------|---------|---------|
| [`l10n_gr_afm`](l10n_gr_afm/) | 19.0.1.0.0 | Fetch business registry data from AADE using Greek VAT numbers |

## Installation

Clone this repository into your Odoo addons path:

```bash
git clone https://github.com/vlassisemm/odoo-modules.git
```

Add the path to your Odoo configuration:

```ini
[options]
addons_path = /path/to/odoo/addons,/path/to/odoo-modules
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
