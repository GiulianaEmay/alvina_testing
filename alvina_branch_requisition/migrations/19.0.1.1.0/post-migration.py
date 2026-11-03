"""Populate new stored related fields after both models have been initialized."""

import logging

from odoo import SUPERUSER_ID, api


_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    names = ['supply_company_id', 'warehouse_id']
    # On Odoo 19 the line model can initialize before the header's new related
    # values are populated. Recompute in dependency order after loading the addon,
    # including archived branches and every historical request.
    for model_name in ('alvina.branch.request', 'alvina.branch.request.line'):
        model = env[model_name].with_context(active_test=False)
        last_id = 0
        count = 0
        while records := model.search([('id', '>', last_id)], order='id', limit=1000):
            for name in names:
                env.add_to_compute(model._fields[name], records)
            records._recompute_recordset(names)
            records.flush_recordset(names)
            last_id = records[-1].id
            count += len(records)
        _logger.info('Populated supply fields on %s existing %s records', count, model_name)
