from odoo import fields, models, api 

class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"
    
 
    mrp_contact_id = fields.Many2one(
        comodel_name='res.partner',
        related='company_id.mrp_contact_id',
        string="Contacto especifico", 
        readonly=False,
        help="Este contacto es el contacto al que se le agregar o quitara unidades según la accion que se tome",
    )