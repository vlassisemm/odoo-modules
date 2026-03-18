# Copyright 2026 Vlassis Emmanouil
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
{
    'name': 'Greece - AFM Lookup',
    'version': '19.0.1.0.0',
    'category': 'Accounting/Localizations',
    'development_status': 'Beta',
    'countries': ['gr'],
    'author': 'Vlassis Emmanouil',
    'website': 'https://github.com/vlassisemm/odoo-modules',
    'maintainers': ['vlassisemm'],
    'license': 'LGPL-3',
    'summary': 'Fetch business registry data from AADE using Greek VAT numbers',
    'depends': ['base_vat', 'l10n_gr', 'sales_team'],
    'data': [
        'security/ir.model.access.csv',
        'views/res_config_settings_views.xml',
        'views/res_partner_views.xml',
        'views/l10n_gr_afm_lookup_wizard_views.xml',
    ],
    'auto_install': False,
}
