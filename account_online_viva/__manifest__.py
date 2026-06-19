# Copyright 2026 Vlassis Emmanouil
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
{
    'name': 'Viva Bank Connector',
    'version': '19.0.1.0.0',
    'category': 'Accounting/Accounting',
    'summary': 'Import Viva (viva.com) business-account transactions as bank statement lines',
    'author': 'Vlassis Emmanouil',
    'website': 'https://github.com/vlassisemm/odoo-modules',
    'license': 'LGPL-3',
    'development_status': 'Alpha',
    'maintainers': ['vlassisemm'],
    'depends': ['account', 'mail'],
    'data': [
        'security/ir.model.access.csv',
        'security/viva_security.xml',
        'data/ir_cron.xml',
        'views/res_config_settings_views.xml',
        'views/viva_fetch_wizard_views.xml',
        'views/viva_account_views.xml',
        'views/viva_setup_wizard_views.xml',
    ],
    'assets': {},
}
