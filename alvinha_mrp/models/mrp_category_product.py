from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

DAY_CODES = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']


class MrpCategoryProduct(models.Model):
    _name = "mrp.category.product"

    name = fields.Char(string="Nombre")
    sequence = fields.Integer(string="Sequence", default=1)
    active = fields.Boolean(default=True)

    product_ids = fields.Many2many('product.product', string="Productos")

    partner_ids = fields.Many2many(
        comodel_name='res.partner',
        string="Enviar correo a"
    )

    hours_1 = fields.Float(string="Primera hora de envio")
    hours_2 = fields.Float(string="Segunda hora de envio")
    hours_3 = fields.Float(string="Tercera hora de envio")
    available_day_ids = fields.Many2many(
        'alvinha.freq.day',
        string='Días disponibles'
    )
    # Horario de visibilidad en el formulario web (hora local).
    # Si ambos valores son 0, la categoría se muestra todo el día.
    # Si "desde" es mayor que "hasta" (ej. 22:00 a 02:00), el rango cruza la medianoche.
    available_hour_from = fields.Float(
        string="Disponible desde",
        default=0.0,
        help="Hora desde la cual la categoría aparece en el formulario web.",
    )
    available_hour_to = fields.Float(
        string="Disponible hasta",
        default=0.0,
        help="Hora hasta la cual la categoría aparece en el formulario web. "
             "Deje ambas horas en 00:00 para mostrarla todo el día.",
    )
    production_manager = fields.Many2one(
        comodel_name='hr.employee',
    )

    @api.constrains('available_hour_from', 'available_hour_to')
    def _check_available_hours(self):
        for rec in self:
            for value in (rec.available_hour_from, rec.available_hour_to):
                if value < 0 or value >= 24:
                    raise ValidationError(_("Las horas de disponibilidad deben estar entre 00:00 y 23:59."))

    def _is_available_at(self, local_dt):
        """Indica si la categoría está disponible en la fecha/hora local dada
        (datetime ya convertido a la zona horaria del negocio)."""
        self.ensure_one()

        # 1. Día de la semana
        if self.available_day_ids:
            day_code = DAY_CODES[local_dt.weekday()]
            if day_code not in self.available_day_ids.mapped('code'):
                return False

        # 2. Horario
        start, end = self.available_hour_from, self.available_hour_to
        if not start and not end:
            return True
        now_hour = local_dt.hour + local_dt.minute / 60.0
        if start <= end:
            return start <= now_hour < end
        # Rango que cruza la medianoche
        return now_hour >= start or now_hour < end

    @api.model
    def _get_available_categories(self, local_dt):
        categories = self.search([])
        return categories.filtered(lambda c: c._is_available_at(local_dt))

    def action_open_report_wizard(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Reportes ',
            'res_model': 'mrp.category.product.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_mrp_category_id': self.id,
            }
        }
