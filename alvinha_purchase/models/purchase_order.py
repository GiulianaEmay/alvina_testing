from odoo import models, fields, api 

from collections import defaultdict

class PurchaseOrder(models.Model):
    _name = "purchase.order"
    _inherit = ["ab.unlink.mixin", "purchase.order"]

    total_qty = fields.Float(
        string='Cantidad Total',
        compute='_compute_quantities',
        store=True,
    )

    qty_summary = fields.Text(
        string='Resumen de Cantidades',
        compute='_compute_quantities',
        store=True,
    )

    @api.depends_context('lang')
    @api.depends(
        'order_line.product_qty',
        'order_line.product_uom_id',
        'order_line',
    )
    def _compute_quantities(self):
        for order in self:

            total_qty = 0.0
            uom_totals = defaultdict(float)

            for line in order.order_line:
                total_qty += line.product_qty

                uom_name = line.product_uom_id.with_context(
                    lang=self.env.user.lang
                ).display_name or 'Sin UDM'
                uom_totals[uom_name] += line.product_qty

            summary_lines = [
                f"{qty:g} {uom}"
                for uom, qty in uom_totals.items()
            ]

            order.total_qty = total_qty
            order.qty_summary = "\n".join(summary_lines)

    