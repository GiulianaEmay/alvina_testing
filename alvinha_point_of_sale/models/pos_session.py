from datetime import datetime, time

import pytz

from odoo import api, fields, models


class PosSession(models.Model):
    _inherit = 'pos.session'

    horario = fields.Selection(
        [
            ('am', 'AM'),
            ('pm', 'PM'),
        ],
        string='Horario',
        compute='_compute_horario',
        store=True,
        readonly=True,
    )

    @api.depends('start_at', 'config_id.pm_start_hour')
    def _compute_horario(self):
        for session in self:
            session.horario = 'am'

            if not session.start_at:
                continue

            # TZ del usuario o UTC por defecto
            tz_name = self.env.user.tz or 'UTC'
            tz = pytz.timezone(tz_name)

            # Convertir datetime UTC -> timezone usuario
            start_at_utc = fields.Datetime.from_string(session.start_at)
            start_at_utc = pytz.UTC.localize(start_at_utc)

            local_dt = start_at_utc.astimezone(tz)

            # Hora configurada inicio PM
            pm_start = session.config_id.pm_start_hour or 12.0

            hours = int(pm_start)
            minutes = int(round((pm_start - hours) * 60))

            pm_time = time(hours, minutes)

            session.horario = (
                'pm'
                if local_dt.time() >= pm_time
                else 'am'
            )


