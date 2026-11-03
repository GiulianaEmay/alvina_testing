"""Link the native PO -> SO result, without guessing native hooks or flags.

The request service creates only the buyer PO when native intercompany is
installed. This adapter observes standard ORM creation in that private call,
validates the returned commercial identity and links exactly one supplier SO.
No native generator is suppressed and no company setting is changed.
"""
from odoo import api, Command, models, _
from odoo.exceptions import ValidationError

from .supply_context import NATIVE_SUPPLY


class NativeSupplySale(models.Model):
    _inherit = 'sale.order'

    @api.model_create_multi
    def create(self, values_list):
        scope = NATIVE_SUPPLY.get()
        if not scope:
            return super().create(values_list)
        scope['attempts'] += len(values_list)
        if scope['phase'] != 'purchase' or scope['attempts'] != 1:
            scope['invalid'] = True
            raise ValidationError(_('Odoo intentó generar más de una venta para este abastecimiento. Se revierte el envío.'))
        request = self.env['alvina.branch.request'].sudo().browse(scope['request_id'])
        values = dict(values_list[0])
        if (values.get('company_id', self.env.company.id) != request.supply_company_id.id
                or values.get('partner_id') != request.company_id.partner_id.id):
            scope['invalid'] = True
            raise ValidationError(_('El automatismo generó una venta de otra compañía o cliente. Se revierte el envío.'))
        commands = []
        for command in values.get('order_line', []):
            if command[0] != Command.CREATE:
                scope['invalid'] = True
                raise ValidationError(_('La venta nativa debe generar sus propias líneas de producto.'))
            commands.append(Command.create(dict(command[2])))
        values.update(scope['sale_header'], order_line=commands,
                      alvina_branch_request_id=request.id)
        supplier = request.supply_company_id
        service = self.sudo().with_context(
            allowed_company_ids=supplier.ids, tracking_disable=True,
            mail_create_nosubscribe=True).with_company(supplier)
        # Native versions can create nested lines or create the header first.
        # Both paths pass through the same standard sale.order.line contract.
        scope['creating_sale'] = True
        try:
            sale = super(NativeSupplySale, service).create([values])
        finally:
            scope['creating_sale'] = False
        scope['sale_id'] = sale.id
        return sale


class NativeSupplySaleLine(models.Model):
    _inherit = 'sale.order.line'

    @api.model_create_multi
    def create(self, values_list):
        scope = NATIVE_SUPPLY.get()
        if not scope:
            return super().create(values_list)
        request = self.env['alvina.branch.request'].sudo().browse(scope['request_id'])
        lines = {line.product_id.id: line for line in request.line_ids}
        prepared = []
        try:
            for values in values_list:
                row = dict(values)
                order = self.env['sale.order'].sudo().browse(
                    row.get('order_id') or self.env.context.get('default_order_id')).exists()
                if (scope['phase'] != 'purchase' or not order
                        or order.alvina_branch_request_id != request
                        or order.company_id != request.supply_company_id
                        or order.partner_id != request.company_id.partner_id
                        or (scope['sale_id'] and order.id != scope['sale_id'])
                        or (not scope['sale_id'] and not scope.get('creating_sale'))):
                    raise ValidationError(_('El automatismo intentó añadir líneas fuera de la venta de este requerimiento.'))
                line = lines.get(row.get('product_id'))
                if not line or line.id in scope['seen_lines'] or row.get('display_type'):
                    raise ValidationError(_('La venta nativa contiene productos ajenos o repetidos.'))
                uom = self.env['uom.uom'].sudo().browse(
                    row.get('product_uom_id') or line.product_uom_id.id).exists()
                if not uom:
                    raise ValidationError(_('La venta nativa no conserva la unidad solicitada.'))
                qty = uom._compute_quantity(row.get('product_uom_qty', 0), line.product_uom_id, round=False)
                if line.product_uom_id.compare(qty, line.product_qty):
                    raise ValidationError(_('La venta nativa no conserva las cantidades del requerimiento.'))
                scope['seen_lines'].add(line.id)
                # Preserve native recursion/link fields, replacing only the
                # validated commercial terms and the request's own line link.
                row.update(scope['sale_lines'][line.id])
                prepared.append(row)
            service = self.sudo().with_context(
                allowed_company_ids=request.supply_company_id.ids,
                tracking_disable=True).with_company(request.supply_company_id)
            return super(NativeSupplySaleLine, service).create(prepared)
        except Exception:
            scope['invalid'] = True
            raise


class NativeSupplyPurchase(models.Model):
    _inherit = 'purchase.order'

    @api.model_create_multi
    def create(self, values_list):
        scope = NATIVE_SUPPLY.get()
        if scope:
            scope['invalid'] = True
            raise ValidationError(_(
                'El automatismo intentó crear otra compra. Configure el sentido '
                'compra de sucursal → venta de Industria; el envío se ha revertido.'))
        return super().create(values_list)


class NativeSupplyInvoice(models.Model):
    _inherit = 'account.move'

    @api.model_create_multi
    def create(self, values_list):
        if NATIVE_SUPPLY.get():
            NATIVE_SUPPLY.get()['invalid'] = True
            raise ValidationError(_('El abastecimiento no permite facturas automáticas. Se revierte el envío.'))
        return super().create(values_list)
