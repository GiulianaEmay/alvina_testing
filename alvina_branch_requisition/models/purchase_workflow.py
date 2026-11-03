"""Buyer-driven confirmation and receipt, scoped to new branch requests."""
from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError

from .dispatch import _RECEIVING
from .supply_context import PURCHASE_CONFIRMATION


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    alvina_manual_purchase = fields.Boolean(
        related='alvina_branch_request_id.manual_purchase_flow')

    def _alvina_check_buyer(self):
        self.ensure_one()
        self.check_access('write')
        request = self.sudo().alvina_branch_request_id
        if (not request.manual_purchase_flow or self.company_id not in self.env.companies
                or not self.env.user.has_group('purchase.group_purchase_user')
                or self.env.uid not in request.branch_id.user_ids.ids):
            raise AccessError(_('Esta operación corresponde al comprador autorizado de la sucursal.'))
        return request

    def button_confirm(self):
        return self._alvina_confirm_purchase('button_confirm')

    def button_approve(self, force=False):
        return self._alvina_confirm_purchase('button_approve', force=force)

    def _alvina_confirm_purchase(self, method, **kwargs):
        # Core confirmation calls button_approve in the same stack. A private
        # capability prevents recursion; an RPC context cannot enable it.
        managed = self.filtered(lambda order:
            order.alvina_manual_purchase and order.alvina_branch_request_id.supply_flow
            and order.state in ('draft', 'sent', 'to approve')
            and order.id not in PURCHASE_CONFIRMATION.get())
        ordinary = self - managed
        result = getattr(super(PurchaseOrder, ordinary), method)(**kwargs) if ordinary else True
        for order in managed:
            with self.env.cr.savepoint():
                request = order._alvina_check_buyer()
                self.env.cr.execute('SELECT id FROM purchase_order WHERE id = %s FOR UPDATE', [order.id])
                order.invalidate_recordset(['state', 'picking_ids'])
                if order.state == 'purchase':
                    continue
                configuration = request.supply_config_id
                configuration._validate_for_branch(request.branch_id)
                native = configuration._native_intercompany_installed()
                if bool(request.native_sale_snapshot) != native:
                    raise ValidationError(_(
                        'El automatismo interempresa cambió desde el envío. Revise esta cotización antes de confirmarla.'))
                token = PURCHASE_CONFIRMATION.set(PURCHASE_CONFIRMATION.get() | {order.id})
                try:
                    def confirm():
                        # Re-enter the complete public inheritance chain while
                        # the capability is active. Native overrides may wrap
                        # this class and generate their sale after super().
                        return getattr(order, method)(**kwargs)

                    if native:
                        supplier = request.supply_company_id
                        sales = self.env['sale.order'].sudo().with_context(
                            allowed_company_ids=supplier.ids).with_company(supplier)
                        values = request.native_sale_snapshot
                        request._confirm_native_supply(order, sales.new(values), values,
                                                       confirm_purchase=confirm)
                    else:
                        result = confirm()
                        if order.state == 'purchase':
                            sale = request.sale_order_id.with_context(
                                allowed_company_ids=request.supply_company_id.ids).with_company(
                                    request.supply_company_id)
                            if not sale:
                                raise ValidationError(_('Falta la venta de Industria vinculada a esta compra.'))
                            if sale.state in ('draft', 'sent'):
                                sale.action_confirm()
                            request._check_confirmed_supply_pair(sale, order)
                finally:
                    PURCHASE_CONFIRMATION.reset(token)
        return result


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    alvina_purchase_receipt = fields.Boolean(compute='_compute_alvina_purchase_receipt')

    @api.model
    def _get_view(self, view_id=None, view_type='form', **options):
        arch, view = super()._get_view(view_id, view_type, **options)
        if view_type == 'form' and arch.xpath("//field[@name='alvina_purchase_receipt']"):
            # Apply to the final inherited header, including installed Odoo
            # extensions. Visibility remains record-based and cache-safe.
            actions = {'action_alvina_validate_receipt', 'action_alvina_print_receipt',
                       'action_alvina_autofill', 'action_alvina_receipt_guide'}
            for node in arch.xpath('//header/button | //header/widget'):
                if node.get('name') not in actions:
                    node.set('invisible', 'alvina_purchase_receipt or (%s)' % node.get('invisible', 'False'))
        return arch, view

    @api.depends('purchase_id.alvina_branch_request_id.manual_purchase_flow', 'picking_type_code', 'return_id')
    def _compute_alvina_purchase_receipt(self):
        for picking in self:
            picking.alvina_purchase_receipt = bool(
                picking.picking_type_code == 'incoming' and not picking.return_id
                and picking.sudo().purchase_id.alvina_branch_request_id.manual_purchase_flow)

    def _alvina_check_receipt_buyer(self):
        self.ensure_one()
        self.check_access('read')
        if not self.alvina_purchase_receipt:
            raise ValidationError(_('Seleccione una recepción del formulario de sucursales.'))
        request = self.purchase_id._alvina_check_buyer()
        if (self.company_id != request.company_id
                or self.purchase_id.state != 'purchase'
                or self.picking_type_id != request.branch_id.purchase_picking_type_id):
            raise ValidationError(_('La recepción debe corresponder a la OC confirmada de esta sucursal.'))
        for move in self.sudo().move_ids.filtered(lambda row: row.state != 'cancel'):
            line = move.purchase_line_id.alvina_request_line_id
            if (move.purchase_line_id.order_id != self.purchase_id
                    or line.request_id != request or move.product_id != line.product_id
                    or move.company_id != self.company_id
                    or move.location_dest_id.usage != 'internal'
                    or move.location_dest_id.company_id != self.company_id):
                raise ValidationError(_('La recepción contiene productos u operaciones ajenas a la sucursal.'))
        return request

    def action_alvina_autofill(self):
        self._alvina_check_receipt_buyer()
        with self.env.cr.savepoint():
            self.env.cr.execute('SELECT id FROM stock_picking WHERE id = %s FOR UPDATE', [self.id])
            self.invalidate_recordset(['state', 'move_ids'])
            if self.state in ('done', 'cancel', 'draft'):
                raise ValidationError(_('Solo puede completar cantidades de una recepción pendiente.'))
            for move in self.sudo().move_ids.filtered(lambda row: row.state not in ('done', 'cancel')):
                move.write({'quantity': move.product_uom_qty, 'picked': True})
        return True

    def action_alvina_validate_receipt(self):
        self._alvina_check_receipt_buyer()
        with self.env.cr.savepoint():
            self.env.cr.execute('SELECT id FROM stock_picking WHERE id = %s FOR UPDATE', [self.id])
            self.invalidate_recordset(['state', 'move_ids'])
            if self.state in ('done', 'cancel'):
                raise ValidationError(_('Esta recepción ya fue procesada.'))
            if self.picking_type_id.create_backorder == 'never' and any(
                    move.product_uom.compare(move.quantity, move.product_uom_qty) < 0
                    for move in self.sudo().move_ids if move.state != 'cancel'):
                raise ValidationError(_('Configure la recepción para conservar los saldos pendientes.'))
            token = _RECEIVING.set(_RECEIVING.get() | {self.id})
            try:
                # Authorize as the buyer, elevate only the verified receipt.
                # Standard lot/serial/quantity checks still run in Inventory.
                return self.sudo().with_company(self.company_id).with_context(
                    allowed_company_ids=self.company_id.ids, skip_backorder=True,
                    picking_ids_not_to_backorder=[]).button_validate()
            finally:
                _RECEIVING.reset(token)

    def action_alvina_print_receipt(self):
        self._alvina_check_receipt_buyer()
        report = 'stock.action_report_delivery' if self.state == 'done' else 'stock.action_report_picking'
        return self.env.ref(report).report_action(self)

    def action_alvina_receipt_guide(self):
        self._alvina_check_receipt_buyer()
        if self.state != 'done':
            raise ValidationError(_('Valide la recepción antes de generar su guía interna valorizada.'))
        guide = self._alvina_emit_dispatch()
        return guide.action_print()
