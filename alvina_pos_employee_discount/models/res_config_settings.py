import math
from decimal import Decimal

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    pos_alvina_discount_enabled = fields.Boolean(
        related='pos_config_id.alvina_discount_enabled', readonly=False)
    alvina_discount_rate = fields.Float(
        string='Descuento (%)', default=15, config_parameter='alvina.discount_rate')
    alvina_weekly_limit = fields.Float(
        string='Cupo semanal PEN', default=100, config_parameter='alvina.weekly_limit')
    alvina_timezone = fields.Selection(
        [('America/Lima', 'America/Lima')], default='America/Lima',
        string='Zona horaria', config_parameter='alvina.timezone')

    @api.constrains('alvina_discount_rate', 'alvina_weekly_limit')
    def _check_alvina_settings(self):
        for record in self:
            if (not math.isfinite(record.alvina_discount_rate) or not math.isfinite(record.alvina_weekly_limit)
                    or not 0 < record.alvina_discount_rate <= 15 or not 0 < record.alvina_weekly_limit <= 100
                    or Decimal(str(record.alvina_weekly_limit)) != Decimal(str(record.alvina_weekly_limit)).quantize(Decimal('0.01'))):
                raise ValidationError(_('El beneficio permite hasta 15 % y S/ 100 semanales.'))

    def set_values(self):
        if not self.env.user.has_group('alvina_pos_employee_discount.group_discount_manager'):
            # Other settings remain available to their standard administrators.
            for record in self:
                params = self.env['ir.config_parameter'].sudo()
                if (record.alvina_discount_rate != float(params.get_param('alvina.discount_rate', 15))
                        or record.alvina_weekly_limit != float(params.get_param('alvina.weekly_limit', 100))):
                    raise AccessError(_('Solo el administrador del beneficio puede cambiar el cupo.'))
        return super().set_values()
