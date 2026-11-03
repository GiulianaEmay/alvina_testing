"""Logistics confirms the supplier quotation independently of the buyer PO."""
from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError

from .supply_context import NATIVE_SUPPLY, SALE_CONFIRMATION


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    alvina_manual_sale = fields.Boolean(compute='_compute_alvina_manual_sale')

    @api.depends('alvina_branch_request_id.manual_sale_flow', 'alvina_branch_request_id.supply_flow')
    def _compute_alvina_manual_sale(self):
        for order in self:
            request = order.sudo().alvina_branch_request_id
            order.alvina_manual_sale = bool(request.manual_sale_flow and request.supply_flow)

    @api.model
    def _get_view(self, view_id=None, view_type='form', **options):
        arch, view = super()._get_view(view_id, view_type, **options)
        if view_type == 'form' and arch.xpath("//field[@name='alvina_manual_sale']"):
            for node in arch.xpath('//header/button | //header/widget'):
                if not (node.get('name') or '').startswith('action_alvina_sale_'):
                    node.set('invisible', 'alvina_manual_sale or (%s)' % node.get('invisible', 'False'))
        return arch, view

    def _alvina_check_sales_actor(self):
        self.ensure_one()
        self.check_access('write')
        request = self.sudo().alvina_branch_request_id
        if not request.manual_sale_flow or not request.supply_flow:
            raise ValidationError(_('Seleccione una venta del flujo de Logística de sucursales.'))
        if not self.env.su and (
                self.company_id not in self.env.companies
                or not self.env.user.has_group('sales_team.group_sale_salesman')
                or not request.branch_id.with_env(self.env)._alvina_user_can_attend_supply()):
            raise AccessError(_('La confirmación corresponde a Logística autorizada de Industria.'))
        return request

    def action_confirm(self):
        scope = NATIVE_SUPPLY.get()
        # Native PO->SO rules may try to auto-confirm the generated quotation.
        # Keep it draft until the authorized logistics user confirms explicitly.
        deferred = self.filtered(lambda order: order.alvina_manual_sale and scope
            and scope['phase'] == 'purchase'
            and scope['request_id'] == order.alvina_branch_request_id.id)
        active = self - deferred
        managed = active.filtered(lambda order: order.alvina_manual_sale
            and order.state in ('draft', 'sent') and order.id not in SALE_CONFIRMATION.get())
        ordinary = active - managed
        result = super(SaleOrder, ordinary).action_confirm() if ordinary else True
        for order in managed:
            with self.env.cr.savepoint():
                request = order._alvina_check_sales_actor()
                self.env.cr.execute('SELECT id FROM sale_order WHERE id = %s FOR UPDATE', [order.id])
                order.invalidate_recordset(['state', 'picking_ids', 'invoice_ids'])
                if order.state == 'sale':
                    continue
                if request.purchase_order_id.state != 'purchase':
                    raise ValidationError(_('Confirme primero la orden de compra de la sucursal.'))
                invoices = set(order.sudo().invoice_ids.ids)
                done = set(order.sudo().picking_ids.filtered(lambda p: p.state == 'done').ids)
                token = SALE_CONFIRMATION.set(SALE_CONFIRMATION.get() | {order.id})
                native_token = NATIVE_SUPPLY.set({
                    'request_id': request.id, 'purchase_id': request.purchase_order_id.id,
                    'sale_id': order.id, 'phase': 'sale', 'attempts': 0, 'invalid': False,
                })
                try:
                    result = order.action_confirm()
                    if order.state != 'sale' or NATIVE_SUPPLY.get()['invalid']:
                        raise ValidationError(_('No se confirmó la venta de Industria.'))
                    if (set(order.sudo().invoice_ids.ids) != invoices
                            or set(order.sudo().picking_ids.filtered(lambda p: p.state == 'done').ids) != done):
                        raise ValidationError(_('La confirmación no puede facturar ni validar entregas automáticamente.'))
                finally:
                    NATIVE_SUPPLY.reset(native_token)
                    SALE_CONFIRMATION.reset(token)
        return result

    def action_alvina_sale_send(self):
        self._alvina_check_sales_actor()
        return self.action_quotation_send()

    def action_alvina_sale_print(self):
        self._alvina_check_sales_actor()
        return self.env.ref('sale.action_report_saleorder').report_action(self)

    def action_alvina_sale_confirm(self):
        self._alvina_check_sales_actor()
        return self.action_confirm()

    def action_alvina_sale_preview(self):
        self._alvina_check_sales_actor()
        return self.action_preview_sale_order()

    def action_alvina_sale_cancel(self):
        self._alvina_check_sales_actor()
        return self.action_cancel()


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    alvina_sale_delivery = fields.Boolean(compute='_compute_alvina_sale_delivery')

    @api.depends('sale_id.alvina_branch_request_id.manual_sale_flow',
                 'sale_id.alvina_branch_request_id.supply_flow', 'picking_type_code', 'return_id')
    def _compute_alvina_sale_delivery(self):
        for picking in self:
            picking.alvina_sale_delivery = bool(picking.picking_type_code == 'outgoing'
                and not picking.return_id and picking.sudo().sale_id.alvina_manual_sale)

    @api.model
    def _get_view(self, view_id=None, view_type='form', **options):
        arch, view = super()._get_view(view_id, view_type, **options)
        if view_type == 'form' and arch.xpath("//field[@name='alvina_sale_delivery']"):
            for node in arch.xpath('//header/button | //header/widget'):
                if not (node.get('name') or '').startswith('action_alvina_delivery_'):
                    node.set('invisible', 'alvina_sale_delivery or (%s)' % node.get('invisible', 'False'))
        return arch, view

    def _alvina_check_delivery(self, pending=False):
        self.ensure_one()
        self.check_access('read')
        if not self.alvina_sale_delivery:
            raise ValidationError(_('Seleccione una entrega de Logística de sucursales.'))
        request = self._alvina_outgoing_request()
        self._alvina_check_dispatch_actor(request)
        if request.sale_order_id.state != 'sale':
            raise ValidationError(_('Confirme la orden de venta antes de preparar la entrega.'))
        if pending and self.state in ('draft', 'done', 'cancel'):
            raise ValidationError(_('Esta acción requiere una entrega pendiente.'))
        return request

    def action_alvina_delivery_assign(self):
        self._alvina_check_delivery(pending=True)
        return self.action_assign()

    def action_alvina_delivery_validate(self):
        self._alvina_check_delivery(pending=True)
        return self.button_validate()

    def action_alvina_delivery_cancel(self):
        self._alvina_check_delivery(pending=True)
        return self.action_cancel()

    def action_alvina_delivery_autofill(self):
        self._alvina_check_delivery(pending=True)
        with self.env.cr.savepoint():
            self.env.cr.execute('SELECT id FROM stock_picking WHERE id = %s FOR UPDATE', [self.id])
            self.invalidate_recordset(['state', 'move_ids'])
            self._alvina_check_delivery(pending=True)
            for move in self.move_ids.filtered(lambda m: m.state not in ('done', 'cancel')):
                move.write({'quantity': move.product_uom_qty, 'picked': True})
        return True

    def action_alvina_delivery_print(self):
        self._alvina_check_delivery()
        if self.state != 'done':
            raise ValidationError(_('Valide la entrega antes de imprimir su comprobante.'))
        return self.env.ref('stock.action_report_delivery').report_action(self)

    def action_alvina_delivery_guide(self):
        self._alvina_check_delivery()
        if self.state != 'done':
            raise ValidationError(_('Valide la entrega antes de generar su guía interna.'))
        return self._alvina_emit_dispatch().action_print()
