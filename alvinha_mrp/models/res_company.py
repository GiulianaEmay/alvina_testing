from odoo import models, fields, api 

class Company(models.Model):
    _inherit = "res.company"

    mrp_contact_id = fields.Many2one(
        comodel_name='res.partner',
        string="Contacto especifico", 
        readonly=False,
        help="Este contacto es el contacto al que se le agregar o quitara unidades según la accion que se tome. ",
    )