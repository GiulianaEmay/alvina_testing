import re

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    alvina_discount_authorized = fields.Boolean(
        string='Beneficio ALVIÑA autorizado', groups='hr.group_hr_user', tracking=True,
    )
    alvina_authorized_dni = fields.Char(
        compute='_compute_alvina_dni', store=True, copy=False,
        groups='hr.group_hr_user', index=True,
    )
    _alvina_dni_unique = models.Constraint(
        'UNIQUE(alvina_authorized_dni)',
        'Ya existe un colaborador autorizado con este DNI.',
    )

    @api.depends('alvina_discount_authorized', 'identification_id', 'current_version_id.identification_id')
    def _compute_alvina_dni(self):
        for employee in self:
            employee.alvina_authorized_dni = (
                (employee.current_version_id.identification_id if employee.current_version_id else employee.identification_id)
                if employee.alvina_discount_authorized else False
            )

    @api.constrains('alvina_discount_authorized', 'identification_id', 'alvina_authorized_dni')
    def _check_alvina_dni(self):
        for employee in self:
            if employee.alvina_discount_authorized and not re.fullmatch(
                r'[0-9]{8}', (employee.current_version_id.identification_id
                               if employee.current_version_id else employee.identification_id) or ''
            ):
                raise ValidationError(_('El DNI autorizado debe contener exactamente 8 dígitos.'))
