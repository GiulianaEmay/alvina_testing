from odoo import api, fields, models


class PosConfig(models.Model):
    _inherit = 'pos.config'

    alvina_discount_enabled = fields.Boolean(string='Descuento colaborador ALVIÑA', default=True)

    @api.model
    def _load_pos_data_fields(self, config):
        result = super()._load_pos_data_fields(config)
        return result + ['alvina_discount_enabled'] if result else result
