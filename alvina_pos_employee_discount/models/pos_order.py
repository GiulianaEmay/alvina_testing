import hashlib
import json
import re
from datetime import timedelta
from decimal import Decimal

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError


class PosOrder(models.Model):
    _inherit = 'pos.order'

    # No employee relation or DNI is ever loaded into the POS dataset.
    alvina_operation = fields.Char(copy=False)
    alvina_confirmed = fields.Boolean(copy=False, readonly=True)
    alvina_amount = fields.Monetary(copy=False)
    alvina_original_total = fields.Monetary(copy=False)

    def _alvina_check_operator(self, allow_paid=False):
        self.ensure_one()
        if not self.env.user.has_group('point_of_sale.group_pos_user'):
            raise AccessError(_('Se requiere acceso al punto de venta.'))
        self.check_access('read')
        self.check_access('write')
        if (self.company_id not in self.env.companies
                or self.session_id.state != 'opened'
                or self.session_id.user_id != self.env.user):
            raise AccessError(_('Utilice un pedido de su sesión POS abierta.'))
        if not self.config_id.alvina_discount_enabled or self.currency_id.name != 'PEN':
            raise UserError(_('El beneficio requiere un POS habilitado en moneda PEN.'))
        if self.state != 'draft' and not (allow_paid and self.state == 'paid'):
            raise UserError(_('El pedido ya no está en borrador.'))

    @api.model
    def _alvina_find_order(self, uuid):
        if not isinstance(uuid, str) or len(uuid) > 100:
            raise UserError(_('Pedido inválido.'))
        order = self.search([('uuid', '=', uuid)], limit=1)
        if not order:
            raise UserError(_('Primero sincronice el pedido con el servidor.'))
        order._alvina_check_operator()
        return order

    def _alvina_employee(self, dni):
        if not isinstance(dni, str) or not re.fullmatch(r'[0-9]{8}', dni):
            raise UserError(_('Ingrese un DNI de 8 dígitos.'))
        # Deliberate, scoped elevation: exact identifier lookup; no HR read ACL
        # for cashiers and no employee recordset returned over RPC.
        employee = self.env['hr.employee'].sudo().with_context(active_test=False).search([
            ('alvina_authorized_dni', '=', dni),
            ('alvina_discount_authorized', '=', True), ('active', '=', True),
        ], limit=1)
        if not employee:
            raise UserError(_('DNI no disponible para el beneficio. Consulte a RR. HH.'))
        return employee

    def _alvina_fingerprint(self):
        self.ensure_one()
        values = {
            'lines': sorted([
                (line.uuid, line.product_id.id, line.qty, line.price_unit,
                 sorted(line.tax_ids.ids), line.product_uom_id.id,
                 line.refunded_orderline_id.id, line.combo_parent_id.id,
                 line.extra_tax_data)
                for line in self.lines
            ], key=lambda value: value[0]),
            'fiscal': self.fiscal_position_id.id, 'pricelist': self.pricelist_id.id,
            'partner': self.partner_id.id, 'currency': self.currency_id.id,
            'company': self.company_id.id,
        }
        return hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()

    def _alvina_total(self, rate):
        tax = self.env['account.tax']
        bases = self.lines._prepare_tax_base_line_values()
        for base in bases:
            base['discount'] = rate
        tax._add_tax_details_in_base_lines(bases, self.company_id)
        tax._round_base_lines_tax_details(bases, self.company_id)
        return self.currency_id.round(tax._get_tax_totals_summary(
            base_lines=bases, currency=self.currency_id, company=self.company_id,
        )['total_amount_currency'])

    def _alvina_plan(self, available, previous=None):
        if not self.lines or any(
            line.qty <= 0 or line.price_unit < 0 or line.refunded_orderline_id
            or line.product_id.type == 'combo' or line.combo_parent_id
            or (line.price_type == 'manual')
            or ('is_reward_line' in line._fields and line.is_reward_line)
            for line in self.lines
        ):
            raise UserError(_('Use una venta positiva sin promociones, combos ni precios manuales.'))
        # Nonstandard pricelists may embed promotions without a discount field.
        if self.pricelist_id and self.pricelist_id.item_ids:
            raise UserError(_('El beneficio no se acumula con reglas de listas de precios.'))
        expected = (previous.effective_rate if previous
                    and previous.state in ('reserved', 'expired')
                    and self.alvina_operation == self.uuid else 0)
        if not (all(abs(line.discount) < 1e-8 for line in self.lines)
                or all(abs(line.discount - expected) < 1e-8 for line in self.lines)):
            raise UserError(_('Retire los descuentos adicionales antes de aplicar el beneficio.'))
        rate, policy_limit = self.env['alvina.employee.discount']._policy()
        original = self._alvina_total(0)
        target = self.currency_id.round(float(min(
            Decimal(str(original)) * rate / 100, max(Decimal(0), available))))
        if target <= 0:
            raise UserError(_('El beneficio semanal se agotó o la compra no genera descuento.'))
        # Find the greatest tax-inclusive reduction which does not exceed the cap.
        # Odoo's own tax engine handles included/excluded/fixed taxes and rounding.
        nominal = float(rate)
        nominal_amount = self.currency_id.round(original - self._alvina_total(nominal))
        if self.currency_id.compare_amounts(nominal_amount, target) == 0:
            return original, nominal_amount, nominal
        low, high = 0.0, 100.0
        for iteration in range(42):
            mid = (low + high) / 2
            reduction = self.currency_id.round(original - self._alvina_total(mid))
            if self.currency_id.compare_amounts(reduction, target) <= 0:
                low = mid
            else:
                high = mid
        effective = round(low, 10)
        amount = self.currency_id.round(original - self._alvina_total(effective))
        if amount <= 0 or self.currency_id.compare_amounts(amount, target) > 0:
            # Rounding the rate up at a tax boundary must never overspend the cap.
            effective = max(0, effective - 0.00000001)
            amount = self.currency_id.round(original - self._alvina_total(effective))
        if amount <= 0:
            raise UserError(_('No es posible aplicar un descuento con la precisión de esta compra.'))
        return original, amount, effective

    def _alvina_lock_attempt(self, employee):
        ledger = self.env['alvina.employee.discount'].sudo()
        entry = ledger.search([('operation', '=', self.uuid)], limit=1)
        for candidate in (employee | entry.employee_id).sorted('id'):
            ledger._lock_employee(candidate)
        entry.invalidate_recordset()
        if entry and entry.state == 'reserved' and entry.expires_at <= fields.Datetime.now():
            entry.write({'state': 'expired'})
        if entry and entry.employee_id != employee and entry.state not in ('cancelled', 'expired'):
            raise UserError(_('Retire el beneficio antes de cambiar de colaborador.'))
        return entry

    @api.model
    def alvina_lookup(self, uuid, dni):
        order = self._alvina_find_order(uuid)
        employee = order._alvina_employee(dni)
        ledger = self.env['alvina.employee.discount'].sudo()
        existing = order._alvina_lock_attempt(employee)
        week = ledger._week()
        used = ledger._usage(employee, week, existing)
        rate, limit = ledger._policy()
        available = max(Decimal(0), limit - used)
        original, amount, effective = order._alvina_plan(available, existing)
        return {'name': employee.name, 'used': float(used), 'available': float(available),
                'amount': amount, 'original': original, 'effective_rate': effective,
                'rate': float(rate), 'limit': float(limit), 'week': str(week)}

    @api.model
    def alvina_reserve(self, uuid, dni, verified=False):
        if verified is not True:
            raise UserError(_('Verifique presencialmente la identidad del titular.'))
        order = self._alvina_find_order(uuid)
        employee = order._alvina_employee(dni)
        ledger = self.env['alvina.employee.discount'].sudo()
        entry = order._alvina_lock_attempt(employee)
        employee.invalidate_recordset()
        if not employee.active or not employee.alvina_discount_authorized:
            raise UserError(_('El colaborador ya no está autorizado.'))
        if entry and entry.state == 'consumed':
            raise UserError(_('Esta operación ya pertenece a otro beneficio o ya fue confirmada.'))
        if entry and entry.employee_id != employee:
            if order.alvina_operation or any(line.discount for line in order.lines):
                raise UserError(_('Retire el beneficio anterior antes de cambiar de colaborador.'))
            # Keep the cancelled attempt auditable instead of rewriting its employee.
            entry.write({'operation': '%s:%s' % (uuid, entry.id)})
            entry.flush_recordset(['operation'])
            entry = ledger.browse()
        week = ledger._week()
        used = ledger._usage(employee, week, entry)
        policy_rate, limit = ledger._policy()
        original, amount, effective = order._alvina_plan(max(Decimal(0), limit - used), entry)
        values = {
            'employee_id': employee.id, 'masked_dni': '****' + dni[-4:],
            'order_id': order.id, 'cashier_id': self.env.uid, 'week_start': week,
            'expires_at': fields.Datetime.now() + timedelta(minutes=10),
            'original_total': original, 'amount': amount, 'effective_rate': effective,
            'fingerprint': order._alvina_fingerprint(), 'state': 'reserved',
            'currency_id': order.currency_id.id, 'operation': uuid,
        }
        if entry:
            entry.write(values)
        else:
            entry = ledger.create(values)
        return {'operation': uuid, 'amount': amount, 'original': original,
                'effective_rate': effective, 'expires_at': fields.Datetime.to_string(entry.expires_at)}

    @api.model
    def alvina_release(self, uuid):
        order = self._alvina_find_order(uuid)
        entry = self.env['alvina.employee.discount'].sudo().search([('operation', '=', uuid)])
        entry._release()
        return True

    @api.model
    def alvina_status(self, uuid):
        if not self.env.user.has_group('point_of_sale.group_pos_user'):
            raise AccessError(_('Se requiere acceso POS.'))
        order = self.search([('uuid', '=', uuid)], limit=1)
        if not order or order.session_id.user_id != self.env.user:
            raise AccessError(_('Pedido no disponible en su sesión.'))
        entry = self.env['alvina.employee.discount'].sudo().search([
            ('order_id', '=', order.id), ('state', '=', 'consumed')], limit=1)
        return {'confirmed': bool(entry and order.state in ('paid', 'done', 'invoiced')),
                'id': order.id, 'state': order.state}

    @api.model
    def alvina_validate(self, uuid):
        order = self._alvina_find_order(uuid)
        order._alvina_validate_reservation()
        return True

    def _alvina_validate_reservation(self, allow_paid=False):
        self.ensure_one()
        ledger = self.env['alvina.employee.discount'].sudo()
        entry = ledger.search([('operation', '=', self.uuid)], limit=1)
        if not entry:
            if self.alvina_operation:
                raise UserError(_('No existe una reserva autorizada para este pedido.'))
            return entry
        ledger._lock_employee(entry.employee_id)
        entry.invalidate_recordset()
        if entry.state in ('cancelled', 'expired') and not self.alvina_operation:
            if any(line.discount for line in self.lines):
                raise UserError(_('Retire los descuentos del beneficio cancelado antes de cobrar.'))
            return ledger.browse()
        self._alvina_check_operator(allow_paid=allow_paid)
        employee = entry.employee_id
        if (entry.state != 'reserved' or entry.expires_at <= fields.Datetime.now()
                or entry.week_start != ledger._week() or entry.cashier_id != self.env.user
                or not employee.active or not employee.alvina_discount_authorized
                or self.alvina_operation != self.uuid or entry.order_id != self
                or self._alvina_fingerprint() != entry.fingerprint
                or any(abs(line.discount - entry.effective_rate) > 1e-8 for line in self.lines)):
            raise UserError(_('El beneficio cambió o expiró. Retírelo y vuelva a aplicarlo en línea.'))
        policy_rate, limit = ledger._policy()
        used = ledger._usage(employee, entry.week_start, entry)
        original = self._alvina_total(0)
        amount = self.currency_id.round(original - self._alvina_total(entry.effective_rate))
        rate, policy_limit = ledger._policy()
        cap = min(limit - used, Decimal(str(original)) * rate / 100)
        if (self.currency_id.compare_amounts(original, entry.original_total)
                or self.currency_id.compare_amounts(amount, entry.amount)
                or Decimal(str(amount)) > Decimal(str(self.currency_id.round(float(cap))))):
            raise UserError(_('El saldo semanal o el importe fiscal cambió. Reaplique el beneficio.'))
        return entry

    @api.model
    def _get_invoice_lines_values(self, line_values, pos_line, move_type):
        values = super()._get_invoice_lines_values(line_values, pos_line, move_type)
        benefit = (pos_line.order_id.alvina_confirmed
                   or pos_line.refunded_orderline_id.order_id.alvina_confirmed)
        if benefit and 'discount' in values and 'price_unit' in values:
            rate = Decimal(str(values['discount']))
            digits = self.env['decimal.precision'].precision_get('Discount')
            if rate != rate.quantize(Decimal(1).scaleb(-digits)):
                # POS discounts retain full precision; account.move.line.discount
                # rounds to the configured Discount precision (normally 2). Fold
                # an unrepresentable rate into the net unit price, whose standard
                # Odoo 19 field keeps precision. Taxes and payment totals stay equal.
                values['price_unit'] = float(Decimal(str(values['price_unit'])) * (1 - rate / 100))
                values['discount'] = 0.0
                values['name'] += '\n' + _('Descuento Colaborador incluido en el precio neto')
        return values

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [dict(vals, alvina_confirmed=False) for vals in vals_list]
        for vals in vals_list:
            if vals.get('alvina_operation') and vals.get('state', 'draft') != 'draft':
                raise UserError(_('El beneficio requiere un pedido borrador y una reserva previa.'))
        return super().create(vals_list)

    def write(self, vals):
        vals = {key: value for key, value in vals.items() if key != 'alvina_confirmed'}
        financial_fields = {'lines', 'uuid', 'session_id', 'company_id', 'currency_id',
                            'fiscal_position_id', 'pricelist_id',
                            'alvina_operation', 'alvina_amount', 'alvina_original_total',
                            'amount_total', 'amount_tax'}
        for order in self._alvina_frozen_orders():
            if any(key in financial_fields and vals[key] != order[key] for key in vals):
                raise UserError(_('Una venta con beneficio confirmado solo se corrige con una devolución.'))
        if vals.get('state') in ('done', 'invoiced'):
            for order in self.filtered(lambda record: record.state == 'draft'):
                if order.alvina_operation or self.env['alvina.employee.discount'].sudo().search_count([('order_id', '=', order.id)]):
                    raise UserError(_('Confirme el pago del beneficio antes de contabilizar o facturar.'))
        confirming = self.filtered(lambda order: order.state == 'draft') if vals.get('state') == 'paid' else self.browse()
        result = super().write(vals)
        for order in confirming.sorted('id'):
            entry = order._alvina_validate_reservation(allow_paid=True)
            if entry:
                super(PosOrder, order).write({
                    'alvina_amount': entry.amount, 'alvina_original_total': entry.original_total,
                    'alvina_confirmed': True,
                })
                entry.write({'state': 'consumed', 'confirmed_at': fields.Datetime.now()})
            order._alvina_reverse_refunds()
        if vals.get('state') == 'cancel':
            self.env['alvina.employee.discount'].sudo().search([
                ('order_id', 'in', self.ids), ('state', '=', 'reserved')])._release()
        return result

    def _alvina_frozen_orders(self):
        settled = self.filtered(lambda order: order.state in ('paid', 'done', 'invoiced'))
        reversed_orders = self.env['alvina.employee.discount.reversal'].sudo().search([
            ('refund_order_id', 'in', settled.ids)]).refund_order_id
        return settled.filtered(lambda order: order.alvina_operation or order.id in reversed_orders.ids)

    def action_pos_order_paid(self):
        result = super().action_pos_order_paid()
        # New standard refunds arrive already in state=paid through sync_from_ui.
        # Do not rely solely on a draft-to-paid write for their idempotent reversal.
        self._alvina_reverse_refunds()
        return result

    def _alvina_reverse_refunds(self):
        """Recompute cumulative refund usage from posted original-line quantities.

        Reversals always belong to the original ledger week. A unique refund
        record and the employee tuple lock make retries/concurrent refunds safe.
        """
        ledger = self.env['alvina.employee.discount'].sudo()
        originals = self.lines.refunded_orderline_id.order_id
        for original in originals.sorted('id'):
            entry = ledger.search([('order_id', '=', original.id), ('state', '=', 'consumed')], limit=1)
            if not entry:
                continue
            ledger._lock_employee(entry.employee_id)
            entry.invalidate_recordset()
            reversal_model = self.env['alvina.employee.discount.reversal'].sudo()
            if reversal_model.search_count([('usage_id', '=', entry.id), ('refund_order_id', '=', self.id)]):
                continue
            proportion_total = Decimal(0)
            fully_refunded = True
            for line in original.lines:
                refunds = self.env['pos.order.line'].sudo().search([
                    ('refunded_orderline_id', '=', line.id),
                    ('order_id.state', 'in', ['paid', 'done', 'invoiced']),
                ])
                # Only standard refunds at the original price/taxes/discount qualify.
                valid = refunds.filtered(lambda r: (
                    r.qty < 0 and r.product_id == line.product_id
                    and abs(r.price_unit - line.price_unit) < 1e-8
                    and abs(r.discount - line.discount) < 1e-8
                    and r.product_uom_id == line.product_uom_id
                    and r.order_id.currency_id == original.currency_id
                    and r.order_id.fiscal_position_id == original.fiscal_position_id
                    and r.tax_ids_after_fiscal_position == line.tax_ids_after_fiscal_position))
                qty = min(line.qty, -sum(valid.mapped('qty')))
                fully_refunded = fully_refunded and qty >= line.qty
                if line.qty > 0:
                    no_discount = line.tax_ids_after_fiscal_position.compute_all(
                        line.price_unit, original.currency_id, line.qty,
                        product=line.product_id, partner=original.partner_id)['total_included']
                    discounted = line.tax_ids_after_fiscal_position.compute_all(
                        line.price_unit * (1 - line.discount / 100), original.currency_id, line.qty,
                        product=line.product_id, partner=original.partner_id)['total_included']
                    proportion_total += Decimal(str(no_discount - discounted)) * Decimal(str(qty / line.qty))
            restored = entry.amount if fully_refunded else min(entry.amount, original.currency_id.round(float(proportion_total)))
            delta = max(0, original.currency_id.round(restored - entry.reversed_amount))
            reversal_model.create({'usage_id': entry.id, 'refund_order_id': self.id, 'amount': delta})
            entry.write({'reversed_amount': entry.reversed_amount + delta})



class PosOrderLine(models.Model):
    _inherit = 'pos.order.line'

    def _alvina_check_frozen(self):
        if self.order_id._alvina_frozen_orders():
            raise UserError(_('No se pueden modificar productos de una venta con beneficio confirmado.'))

    def write(self, vals):
        if {'qty', 'price_unit', 'discount', 'tax_ids', 'product_id', 'order_id',
            'price_subtotal', 'price_subtotal_incl', 'extra_tax_data',
            'product_uom_id', 'refunded_orderline_id'} & vals.keys():
            self._alvina_check_frozen()
            if vals.get('order_id') and self.env['pos.order'].browse(vals['order_id'])._alvina_frozen_orders():
                raise UserError(_('No se pueden agregar productos a una venta o devolución confirmada con beneficio.'))
        return super().write(vals)

    def unlink(self):
        self._alvina_check_frozen()
        return super().unlink()

    @api.model_create_multi
    def create(self, vals_list):
        default_order = self.env.context.get('default_order_id')
        targets = self.env['pos.order'].browse([vals.get('order_id', default_order) for vals in vals_list if vals.get('order_id', default_order)])
        if targets._alvina_frozen_orders():
            raise UserError(_('No se pueden agregar productos a una venta o devolución confirmada con beneficio.'))
        records = super().create(vals_list)
        # Defaults supplied in RPC context can fill order_id after our pre-check.
        records._alvina_check_frozen()
        return records
