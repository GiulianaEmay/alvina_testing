from odoo import models, fields 


class SaleOrder(models.Model):
    _name = "sale.order"
    _inherit = ["ab.unlink.mixin", "sale.order"]

    can_edit_lines = fields.Boolean(
        string="Puede editar lineas", 
        compute="_compute_can_edit_lines",
        store=False     
    )

    def _compute_can_edit_lines(self):
        user = self.env.user 
        is_admin = user.has_group('sales_team.group_sale_manager')
        for rec in self:
            if is_admin:
                rec.can_edit_lines = True 
            elif rec.create_uid.id == user.id:
                rec.can_edit_lines = True 
            else:
                rec.can_edit_lines = False 

