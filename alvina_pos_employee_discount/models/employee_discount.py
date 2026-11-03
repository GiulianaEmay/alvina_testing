"""Private benefit ledger. Cashiers access only the narrow pos.order RPC surface."""
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class EmployeeDiscount(models.Model):
    _name = 'alvina.employee.discount'
    _description = 'Historial de beneficio ALVIÑA'
    _order = 'create_date desc, id desc'
    _rec_name = 'operation'

    employee_id = fields.Many2one('hr.employee', required=True, ondelete='restrict', index=True)
    masked_dni = fields.Char(required=True)
    order_id = fields.Many2one('pos.order', required=True, ondelete='restrict', index=True)
    company_id = fields.Many2one(related='order_id.company_id', store=True, index=True)
    config_id = fields.Many2one(related='order_id.config_id', store=True, index=True)
    session_id = fields.Many2one(related='order_id.session_id', store=True)
    cashier_id = fields.Many2one('res.users', required=True, ondelete='restrict')
    week_start = fields.Date(required=True, index=True)
    confirmed_at = fields.Datetime()
    expires_at = fields.Datetime(required=True)
    original_total = fields.Monetary(required=True)
    amount = fields.Monetary(required=True)
    reversed_amount = fields.Monetary(default=0)
    currency_id = fields.Many2one('res.currency', required=True)
    state = fields.Selection([
        ('reserved', 'Reservado'), ('consumed', 'Consumido'),
        ('cancelled', 'Cancelado'), ('expired', 'Expirado'),
    ], required=True, default='reserved', index=True)
    operation = fields.Char(required=True, index=True)
    fingerprint = fields.Char(required=True)
    effective_rate = fields.Float(digits=(16, 10))
    balance = fields.Monetary(compute='_compute_balance', string='Disponible esta semana')
    _operation_unique = models.Constraint('UNIQUE(operation)', 'La operación ya está registrada.')
    _positive = models.Constraint(
        'CHECK(amount >= 0 AND reversed_amount >= 0 AND reversed_amount <= amount)',
        'El consumo y su reversión deben ser positivos y estar dentro del importe original.')

    @api.model
    def _week(self, now=None):
        now = now or fields.Datetime.now()
        local = now.replace(tzinfo=ZoneInfo('UTC')).astimezone(ZoneInfo('America/Lima'))
        return local.date() - timedelta(days=local.weekday())

    @api.model
    def _policy(self):
        params = self.env['ir.config_parameter'].sudo()
        try:
            rate = Decimal(params.get_param('alvina.discount_rate', '15'))
            limit = Decimal(params.get_param('alvina.weekly_limit', '100'))
            valid = (rate.is_finite() and limit.is_finite()
                     and Decimal(0) < rate <= 15 and Decimal(0) < limit <= 100
                     and limit == limit.quantize(Decimal('0.01')))
        except (InvalidOperation, ValueError, TypeError):
            valid = False
        if not valid:
            raise UserError(_('Configuración inválida: máximo 15 % y S/ 100, con cupo de hasta dos decimales.'))
        return rate, limit

    @api.model
    def _lock_employee(self, employee):
        # UPDATE acquires a row lock AND changes the tuple version. Under Odoo's
        # REPEATABLE READ a concurrent old snapshot must retry, not read stale usage.
        self.env.cr.execute('UPDATE hr_employee SET id = id WHERE id = %s RETURNING id', [employee.id])
        if not self.env.cr.fetchone():
            raise UserError(_('El colaborador ya no está disponible.'))
        employee.invalidate_recordset()

    @api.model
    def _usage(self, employee, week, exclude=None):
        now = fields.Datetime.now()
        ledger = self.sudo()
        ledger.search([
            ('employee_id', '=', employee.id), ('state', '=', 'reserved'),
            ('expires_at', '<=', now),
        ]).write({'state': 'expired'})
        entries = ledger.search([
            ('employee_id', '=', employee.id), ('week_start', '=', week),
            ('state', 'in', ['reserved', 'consumed']),
            ('id', '!=', exclude.id if exclude else 0),
        ])
        return sum((Decimal(str(row.amount)) - Decimal(str(row.reversed_amount))
                    for row in entries), Decimal(0))

    def _compute_balance(self):
        policy_rate, limit = self._policy()
        # Reporting must not lock employees or mutate expired reservations.
        # Batch a single snapshot of active usage for all visible employees.
        entries = self.sudo().search([
            ('employee_id', 'in', self.employee_id.ids), ('week_start', '=', self._week()),
            '|', ('state', '=', 'consumed'),
            '&', ('state', '=', 'reserved'), ('expires_at', '>', fields.Datetime.now()),
        ])
        usage = {}
        for entry in entries:
            employee_id = entry.employee_id.id
            usage[employee_id] = usage.get(employee_id, Decimal(0)) + Decimal(str(entry.amount)) - Decimal(str(entry.reversed_amount))
        for record in self:
            record.balance = float(max(Decimal(0), limit - usage.get(record.employee_id.id, Decimal(0))))

    def _release(self):
        for entry in self.sorted('employee_id'):
            self._lock_employee(entry.employee_id)
            entry.invalidate_recordset()
            if entry.state == 'reserved':
                entry.write({'state': 'cancelled'})


class EmployeeDiscountReversal(models.Model):
    _name = 'alvina.employee.discount.reversal'
    _description = 'Reversiones auditables del beneficio ALVIÑA'
    _order = 'create_date desc'

    usage_id = fields.Many2one('alvina.employee.discount', required=True, ondelete='restrict')
    refund_order_id = fields.Many2one('pos.order', required=True, ondelete='restrict')
    company_id = fields.Many2one(related='refund_order_id.company_id', store=True)
    amount = fields.Monetary(required=True)
    currency_id = fields.Many2one(related='usage_id.currency_id')
    _refund_unique = models.Constraint('UNIQUE(refund_order_id, usage_id)', 'Devolución ya procesada.')
