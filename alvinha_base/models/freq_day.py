from odoo import fields, models, api 

class FreqDay(models.Model):
    _name = "alvinha.freq.day"
    _description = "Frequencia de dias"

    _order = 'sequence'

    name = fields.Char(string="Nombre", required=True)
    code = fields.Char()
    sequence = fields.Integer(default=10)

    