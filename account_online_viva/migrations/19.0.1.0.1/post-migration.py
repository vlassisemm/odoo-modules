# Copyright 2026 Vlassis Emmanouil
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
import logging

_logger = logging.getLogger(__name__)

# Legacy single-column index created by the old `viva_transaction_id` field
# definition (`index=True`). That flag was dropped in favour of the partial
# UNIQUE index on (journal_id, viva_transaction_id), which already serves the
# dedup lookup. Odoo never auto-drops a de-indexed column's index (see
# registry.check_indexes: "Keep unexpected index"), so upgraded databases keep
# this redundant index while fresh installs never create it. Drop it here so
# both converge. DROP INDEX takes only a brief lock and is a metadata-only op.
_LEGACY_INDEX = 'account_bank_statement_line__viva_transaction_id_index'


def migrate(cr, version):
    cr.execute('DROP INDEX IF EXISTS %s' % _LEGACY_INDEX)
    _logger.info('account_online_viva: dropped legacy index %s if present.',
                 _LEGACY_INDEX)
