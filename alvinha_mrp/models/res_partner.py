from odoo import models, fields 

class ResPartner(models.Model):
    _inherti = "res.partner"
    
    mrp_production_line_ids = fields.One2many(
        comodel_name='mrp.production.contact.line',
        inverse_name='partner_id',
        string="Lineas de Producción",  
    )