"""A reusable sales role bounded to real branch supplies, including analysis."""
from odoo import api, fields, models, _
from odoo.exceptions import AccessError
from .supply_context import native_sale_expected


class BranchBindingLogistics(models.Model):
    _inherit = 'alvina.branch.binding'

    def _alvina_user_can_attend_supply(self):
        """Private authorization shared by dispatch exports and balance actions.

        The logistics role attends all branches supplied by a company already
        authorized and selected for that user. It never adds company memberships
        or rewrites the branch's explicit requester assignments.
        """
        self.ensure_one()
        branch = self.sudo()
        return bool(
            self.env.su
            or self.env.uid in branch.user_ids.ids
            or (self.env.user.has_group(
                'alvina_branch_requisition.group_industry_logistics')
                and branch.supply_company_id in self.env.companies)
        )


class SaleOrderLogistics(models.Model):
    _inherit = 'sale.order'

    @api.model_create_multi
    def create(self, vals_list):
        # Rules also protect create, but fail before defaulting/computations:
        # this role prepares branch orders created by the validated service.
        if not self.env.su and not native_sale_expected() and self.env.user.has_group(
                'alvina_branch_requisition.group_industry_logistics'):
            raise AccessError(_(
                'Logística atiende las ventas generadas por los requerimientos de '
                'sucursales; no crea ni duplica ventas manuales.'))
        return super().create(vals_list)


class SaleReportLogistics(models.Model):
    _inherit = 'sale.report'

    alvina_sale_order_id = fields.Many2one(
        'sale.order', string='Venta de origen', readonly=True)

    def _select_additional_fields(self):
        values = super()._select_additional_fields()
        # The official query groups by s.id. A real relational field lets the
        # SQL analysis enforce the same scope as orders, including aggregation.
        values['alvina_sale_order_id'] = 's.id'
        return values
