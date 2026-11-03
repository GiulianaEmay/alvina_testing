from odoo import fields, models , api 

class MrpProduction(models.Model):
    _inherit = "mrp.production"
    
    sale_order_id = fields.Many2one(
        'sale.order',
        string='Orden de Venta',
        index=True,
    )