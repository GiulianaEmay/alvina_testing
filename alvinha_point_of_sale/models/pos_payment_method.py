from odoo import fields, models, api

class PosPaymentMethod(models.Model):
    _inherit = 'pos.payment.method'

    auto_invoice = fields.Boolean(
        string="Facturar automáticamente",
        help="Habilita la facturación automática desde el pago automatico",
        default=False,
    )

    @api.model
    def _load_pos_data_fields(self, config):
        response = super()._load_pos_data_fields(config)
        response.append('auto_invoice')
        return response