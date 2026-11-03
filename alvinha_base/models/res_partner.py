from odoo import models, fields, api, _
from odoo.exceptions import UserError


import requests
import logging


_logger = logging.getLogger(__name__)



class ResPartner(models.Model):
    _inherit = "res.partner"
    
    alias = fields.Char(string="Alias")
    order_frequency = fields.Selection(
        selection=[
            ('daily', 'Diario'),
            ('weekly', 'Semanal'),
            ('fortnightly', 'Quincenal'),
            ('in_turn', 'Por turno'),
            ('monthly', 'Mensual')
        ],
        string="Frecuencia de orden"
    )
    distribution_priority = fields.Integer(string="Prioridad de distribución", help="1 es la prioridad más alta")
    is_supplier = fields.Boolean(string="Proveedor")
    is_customer = fields.Boolean(string="Cliente")
    type_client = fields.Selection(
        selection=[
            ('store', 'Bodega'),
            ('branch', 'Sucursal'),
            ('extra', 'Extra')
        ],
        string="Tipo de cliente",
        tracking=True,
    )
 

