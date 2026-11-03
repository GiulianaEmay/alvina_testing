from odoo import models, api, fields 

class SaleOrder(models.Model):
    _inherit = "sale.order"

    is_created_mrp = fields.Boolean(default=False, string="¿Esta creando ordenes de producción?")
    is_from_mrp = fields.Boolean(default=False, string="¿se creo desde el MRP?")


    def action_view_mrp_productions(self):
        self.ensure_one()

        action = self.env.ref('mrp.mrp_production_action').read()[0]
        mos = self.env['mrp.production'].search([
            ('sale_order_id', '=', self.id)
        ])

        action['domain'] = [('id', 'in', mos.ids)]

        return action