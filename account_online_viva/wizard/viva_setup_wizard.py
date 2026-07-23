# Copyright 2026 Vlassis Emmanouil
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo import _, fields, models
from odoo.exceptions import UserError

from ..models.viva_client import VivaApiError


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
        client = self.company_id._viva_get_client()
        try:
            wallets = client.list_wallets()
        except VivaApiError as exc:
            # TODO (credentials): with a Data-Services-only credential the
            # Wallet API always rejects the token (wrong audience/scope) —
            # discovery needs a core_api credential with Wallet access.
            raise UserError(_(
                'Could not list Viva wallets: %(error)s\n\n'
                'Note: wallet discovery needs a credential with Wallet API '
                'access (scope core:api:merchants:wallets). Data Services '
                'credentials only cover transaction import; with those, '
                'create the bank journal and Viva account manually.',
                error=exc)) from exc

        VivaAccount = self.env['viva.account']
        known_wallet_ids = set(VivaAccount.with_context(active_test=False).search(
            [('company_id', '=', self.company_id.id)]).mapped('wallet_id'))
        created = VivaAccount
        for w in wallets:
            if not w.get('wallet_id') or w['wallet_id'] in known_wallet_ids:
                continue
            currency = VivaAccount._viva_currency_from_code(w.get('currency_code'))
            journal = self._create_bank_journal(w, currency)
            created |= VivaAccount.create({
                'name': w.get('name') or _('Viva %s', w['wallet_id']),
                'company_id': self.company_id.id,
                'journal_id': journal.id,
                'wallet_id': w['wallet_id'],
            })
        if created:
            domain = [('id', 'in', created.ids)]
        else:
            domain = [('company_id', '=', self.company_id.id)]
        return {
            'type': 'ir.actions.act_window',
            'name': _('Viva Accounts'),
            'res_model': 'viva.account',
            'view_mode': 'list,form',
            'domain': domain,
        }

    def _unique_journal_code(self, base_code, company_id):
        """Return a journal code derived from base_code that is unique for
        company_id, or None to let core auto-generate one."""
        existing = set(
            self.env['account.journal'].with_context(active_test=False).search_fetch(
                [('company_id', '=', company_id)], ['code']).mapped('code'))
        code = base_code[:5].upper()
        if code not in existing:
            return code
        # Append numeric suffix until unique; keep within Odoo's 5-char limit.
        # Trim base to make room for each suffix so candidates are genuinely distinct.
        for suffix in range(1, 100):
            suffix_str = str(suffix)
            candidate = (base_code[:5 - len(suffix_str)] + suffix_str).upper()
            if candidate not in existing:
                return candidate
        # Give up: omitting the code lets account.journal's _compute_code
        # assign the next free one (it also checks archived journals).
        return None

    def _create_bank_journal(self, wallet, currency):
        company = self.company_id.sudo()
        wallet_id = str(wallet['wallet_id'])
        base_code = 'V%s' % wallet_id[-4:]
        code = self._unique_journal_code(base_code, company.id)
        vals = {
            'name': _('Viva %s', wallet.get('name') or wallet_id),
            'type': 'bank',
            'company_id': company.id,
            'bank_statements_source': 'viva',
        }
        if code:
            vals['code'] = code
        if currency:
            vals['currency_id'] = currency.id
        if wallet.get('iban'):
            # Core's create() routes bank_acc_number through
            # res.partner.bank._find_or_create_bank_account: no duplicate
            # res.partner.bank rows for an already-registered IBAN. sudo():
            # creating res.partner.bank needs base.group_partner_manager,
            # which accounting managers do not necessarily have.
            vals['bank_acc_number'] = wallet['iban']
        return self.env['account.journal'].sudo().create(vals)
