# Copyright 2026 Vlassis Emmanouil
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo import _, fields, models
from odoo.exceptions import UserError

from ..models.viva_client import VivaClient, VivaApiError


class VivaSetupWizard(models.TransientModel):
    _name = 'viva.setup.wizard'
    _description = 'Viva Setup / Wallet Discovery'

    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company)

    def action_discover(self):
        self.ensure_one()
        if not self.env.user.has_group('account.group_account_manager'):
            raise UserError(_('Only accounting managers can configure Viva.'))
        company = self.company_id.sudo()
        if not (company.viva_client_id and company.viva_client_secret):
            raise UserError(_('Set the Viva Client ID and Secret first (Settings).'))
        client = VivaClient(
            company.viva_client_id, company.viva_client_secret, company.viva_environment)
        try:
            wallets = client.list_wallets()
        except VivaApiError as exc:
            raise UserError(_('Could not connect to Viva: %s', exc)) from exc

        VivaAccount = self.env['viva.account']
        created = VivaAccount
        for w in wallets:
            if not w.get('wallet_id'):
                continue
            if VivaAccount.search_count([
                    ('wallet_id', '=', w['wallet_id']),
                    ('company_id', '=', self.company_id.id)]):
                continue
            currency = VivaAccount._viva_currency_from_code(w.get('currency_code'))
            journal = self._create_bank_journal(w, currency)
            created |= VivaAccount.create({
                'name': w.get('name') or _('Viva %s', w['wallet_id']),
                'company_id': self.company_id.id,
                'journal_id': journal.id,
                'wallet_id': w['wallet_id'],
                'iban': w.get('iban'),
                'currency_id': currency.id or False,
            })
        return {
            'type': 'ir.actions.act_window',
            'name': _('Viva Accounts'),
            'res_model': 'viva.account',
            'view_mode': 'list,form',
            'domain': [('id', 'in', created.ids or VivaAccount.search([]).ids)],
        }

    def _create_bank_journal(self, wallet, currency):
        company = self.company_id
        vals = {
            'name': _('Viva %s', wallet.get('name') or wallet['wallet_id']),
            'type': 'bank',
            'code': ('V%s' % wallet['wallet_id'][-4:])[:5].upper(),
            'company_id': company.id,
            'bank_statements_source': 'viva',
        }
        if currency:
            vals['currency_id'] = currency.id
        if wallet.get('iban'):
            bank_acc = self.env['res.partner.bank'].create({
                'acc_number': wallet['iban'],
                'partner_id': company.partner_id.id,
                'company_id': company.id,
            })
            vals['bank_account_id'] = bank_acc.id
        return self.env['account.journal'].create(vals)
