"""Preserve the supplying company of bindings created before split-company support."""

import logging


_logger = logging.getLogger(__name__)


def migrate(cr, version):
    # Odoo executes this before _auto_init. Backfill before the new required field
    # can acquire the administrator's current company as its default.
    cr.execute("""
        ALTER TABLE alvina_branch_binding
        ADD COLUMN IF NOT EXISTS supply_company_id integer
    """)
    cr.execute("""
        UPDATE alvina_branch_binding AS binding
           SET supply_company_id = warehouse.company_id
          FROM stock_warehouse AS warehouse
         WHERE warehouse.id = binding.warehouse_id
           AND binding.supply_company_id IS NULL
    """)
    updated = cr.rowcount
    cr.execute("""
        SELECT COUNT(*)
          FROM alvina_branch_binding
         WHERE supply_company_id IS NULL
    """)
    if cr.fetchone()[0]:
        raise RuntimeError(
            "Cannot determine a binding's supplying company from its warehouse. "
            "Correct the existing warehouse assignment before upgrading."
        )
    # Do not change requesting companies, authorized users or request history.
    # The ORM creates the foreign key. The post-migration recomputes new stored
    # related fields on headers and lines in dependency order.
    _logger.info("Preserved supplying companies for %s existing branch bindings", updated)
