"""Uniform, explicitly configured Industria supply business service.

Native automation supplies the supplier sale through standard ORM creation.
No native omission flag, private hook or company setting is guessed or changed.
"""
import math
from contextvars import ContextVar
from datetime import datetime, time

from odoo import api, Command, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError
from .supply_context import NATIVE_SUPPLY


# A client-supplied context value cannot confer technical approval. The scope is
# active only while approving the exact purchase made by this private service.
_AUTOMATED_PURCHASE_APPROVAL = ContextVar('alvina_automated_purchase_approval', default=frozenset())


class BranchRequest(models.Model):
    _inherit = 'alvina.branch.request'

    supply_flow = fields.Boolean(default=False, readonly=True, copy=False, string='Flujo Industria')
    supply_config_id = fields.Many2one('alvina.supply.configuration', readonly=True, ondelete='restrict')
    operation_mode = fields.Selection([
        ('intercompany', 'Compañías distintas'), ('internal', 'Misma compañía')], readonly=True)
    sale_order_ids = fields.One2many('sale.order', 'alvina_branch_request_id', readonly=True)
    sale_order_id = fields.Many2one('sale.order', compute='_compute_sale_order', store=True,
                                    string='Venta de Industria', readonly=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        # Include header, immutable lines, commercial pair and generated stock
        # operations in the same rollback boundary, even if an RPC caller catches
        # a validation failure rather than aborting the entire transaction.
        with self.env.cr.savepoint():
            return super().create(vals_list)

    @api.depends('sale_order_ids')
    def _compute_sale_order(self):
        for rec in self:
            rec.sale_order_id = rec.sale_order_ids[:1]

    def action_send_to_industry(self):
        """Recover an unfulfilled pending request without duplicating its form."""
        self.ensure_one()
        self.check_access('read')
        if (self.env.uid != self.user_id.id
                or self.env.uid not in self.branch_id.sudo().user_ids.ids
                or self.company_id not in self.env.companies
                or not self.env.user.has_group('alvina_branch_requisition.group_requester')):
            raise AccessError(_('El envío corresponde al usuario solicitante autorizado de esta sucursal.'))
        with self.env.cr.savepoint():
            self.env.cr.execute('SELECT id FROM alvina_branch_request WHERE id = %s FOR UPDATE', [self.id])
            self.invalidate_recordset(['supply_flow', 'state', 'sale_order_id', 'purchase_order_id'])
            history = self.sudo()
            if history.supply_flow and history.sale_order_id and history.purchase_order_id:
                return {'type': 'ir.actions.client', 'tag': 'reload'}
            if (self.state != 'pending' or history.sale_order_id or history.purchase_order_id
                    or any(line.qty_supplied or line.qty_received or line.qty_cancelled for line in history.line_ids)):
                raise ValidationError(_(
                    'Solo se puede enviar un requerimiento pendiente sin documentos '
                    'comerciales, entregas, recepciones ni cierres previos.'))
            configuration = self.env['alvina.supply.configuration']._get_configuration()
            if not configuration or not configuration.enabled:
                raise ValidationError(_('El administrador debe habilitar y guardar el abastecimiento de Industria.'))
            configuration._validate_for_branch(self.branch_id)
            self._set_supply_configuration(configuration)
            self._create_supply_documents()
            return {'type': 'ir.actions.client', 'tag': 'reload'}

    def _submission_values(self, branch):
        values = super()._submission_values(branch)
        configuration = self.env['alvina.supply.configuration']._get_configuration()
        if configuration and not configuration.enabled:
            raise ValidationError(_(
                'El abastecimiento de Industria está desactivado. No se ha enviado el '
                'pedido ni generado una compra o venta. Avise al administrador.'))
        if configuration and configuration.enabled:
            configuration._validate_for_branch(branch)
            values.update(supply_flow=True, supply_config_id=configuration.id,
                          operation_mode='internal' if branch.company_id == configuration.company_id else 'intercompany')
        return values

    def _check_supply_actor(self):
        self.ensure_one()
        self.check_access('read')
        if (not self.supply_flow or self.user_id != self.env.user
                or self.env.uid not in self.branch_id.sudo().user_ids.ids
                or self.company_id not in self.env.companies or self.state != 'pending'):
            raise AccessError(_('Requerimiento no autorizado para generar documentos de abastecimiento.'))
        self.supply_config_id.sudo()._validate_for_branch(self.branch_id)

    def _create_supply_documents(self):
        self._check_supply_actor()
        self.env.cr.execute('SELECT id FROM alvina_branch_request WHERE id = %s FOR UPDATE', (self.id,))
        self.invalidate_recordset(['sale_order_id', 'purchase_order_id'])
        existing = self.sudo()
        if existing.sale_order_id:
            if not existing.purchase_order_id:
                raise ValidationError(_(
                    'El abastecimiento tiene una venta sin su compra vinculada. '
                    'Revise su integridad antes de continuar; no se duplicarán documentos.'))
            return existing.sale_order_id
        if existing.purchase_order_id:
            raise ValidationError(_(
                'El abastecimiento tiene una compra sin su venta vinculada. '
                'Revise su integridad antes de continuar; no se duplicarán documentos.'))
        if self.operation_mode == 'internal':
            return self._create_internal_dispatch()
        configuration = self.supply_config_id.sudo()
        supplier = configuration.company_id
        for line in self.line_ids:
            product = line.product_id.sudo()
            if (product.company_id or not product.sale_ok or not product.purchase_ok
                    or product.type != 'consu'):
                raise ValidationError(_(
                    'El flujo entre compañías requiere insumos físicos compartidos, habilitados para compra y venta.'))
        # The supplied private module manufactures at sale-line creation. Until
        # its verified adapter exists, reject affected BOMs rather than falsify
        # is_from_mrp or create manufacturing orders accidentally.
        private_mrp = self.env['ir.module.module'].sudo().search_count([
            ('name', '=', 'alvinha_mrp'), ('state', 'in', ['installed', 'to upgrade'])])
        if private_mrp and 'mrp.bom' in self.env:
            boms = self.env['mrp.bom'].sudo()._bom_find(
                self.line_ids.sudo().product_id, company_id=supplier.id)
            if any(boms.values()):
                raise ValidationError(_(
                    'Estos productos activarían fabricación en alvinha_mrp. Se requiere un adaptador verificado para insumos.'))
        context = {'allowed_company_ids': supplier.ids, 'lang': self.env.lang,
                   'tracking_disable': True, 'mail_create_nosubscribe': True, 'mail_create_nolog': True}
        sales = self.env['sale.order'].sudo().with_context(context).with_company(supplier)
        customer = self.company_id.sudo().partner_id
        sale_values = {
            'alvina_branch_request_id': self.id, 'company_id': supplier.id,
            'partner_id': customer.id, 'partner_invoice_id': customer.id,
            'partner_shipping_id': customer.id, 'warehouse_id': self.warehouse_id.id,
            'pricelist_id': configuration.pricelist_id.id,
            'user_id': configuration._get_sales_responsible().id,
            'origin': self.name, 'date_order': self.request_date,
            'commitment_date': datetime.combine(self.required_date, time(12)), 'state': 'draft',
            'order_line': [Command.create({
                'alvina_request_line_id': line.id, 'product_id': line.product_id.id,
                'product_uom_id': line.product_uom_id.id, 'product_uom_qty': line.product_qty,
            }) for line in self.line_ids],
        }
        native = configuration._native_intercompany_installed()
        # Price with standard virtual sale records; native mode must not create
        # a second supplier sale before Odoo generates the counterpart.
        sale = sales.new(sale_values) if native else sales.create(sale_values)
        # Let sale's standard pricelist, discounts, fiscal position and tax
        # computation produce the commercial terms. Copy its effective unit
        # price to the buyer's currency using the buyer's configured rates.
        buyer = self.company_id.sudo()
        purchases = self.env['purchase.order'].sudo().with_context(dict(context, allowed_company_ids=buyer.ids)).with_company(buyer)
        vendor = supplier.partner_id.with_company(buyer)
        fiscal_position = purchases.env['account.fiscal.position']._get_fiscal_position(vendor)
        commands = []
        for line in sale.order_line.filtered('alvina_request_line_id'):
            base_line = line._prepare_base_line_for_taxes_computation(quantity=1)
            sale.env['account.tax']._add_tax_details_in_base_line(base_line, supplier)
            net_unit = base_line['tax_details']['raw_total_excluded_currency']
            price = sale.currency_id._convert(net_unit, self.currency_id, buyer,
                                             fields.Date.to_date(self.request_date), round=False)
            taxes = line.product_id.with_company(buyer).supplier_taxes_id._filter_taxes_by_company(buyer)
            taxes = fiscal_position.map_tax(taxes)
            price = purchases.env['account.tax']._adapt_price_unit_to_another_taxes(
                price, line.product_id, purchases.env['account.tax'], taxes,
                product_uom=line.product_uom_id)
            if not math.isfinite(price) or price < 0:
                raise ValidationError(_('La lista de precios produjo un precio inválido.'))
            commands.append(Command.create({
                'alvina_request_line_id': line.alvina_request_line_id.id,
                'product_id': line.product_id.id, 'name': line.name,
                'product_qty': line.product_uom_qty, 'product_uom_id': line.product_uom_id.id,
                'price_unit': price, 'technical_price_unit': price, 'discount': 0,
                'tax_ids': [Command.set(taxes.ids)],
                'date_planned': datetime.combine(self.required_date, time(12)),
            }))
        purchase = purchases.create({
            'alvina_branch_request_id': self.id, 'company_id': buyer.id,
            'partner_id': supplier.partner_id.id, 'currency_id': self.currency_id.id,
            'fiscal_position_id': fiscal_position.id,
            'payment_term_id': vendor.property_supplier_payment_term_id.id,
            'picking_type_id': self.branch_id.purchase_picking_type_id.id,
            'state': 'draft', 'user_id': False, 'origin': self.name,
            'date_order': self.request_date, 'order_line': commands,
        })
        if native:
            return self._confirm_native_supply(purchase, sale, sale_values)
        self._confirm_supply_documents(sale, purchase)
        return sale

    def _confirm_native_supply(self, purchase, priced_sale, sale_values):
        self._check_supply_actor()
        header = {key: value for key, value in sale_values.items()
                  if key not in ('order_line', 'state', 'date_order', 'origin')}
        scope = {
            'request_id': self.id, 'purchase_id': purchase.id,
            'sale_id': None, 'attempts': 0, 'invalid': False, 'phase': 'purchase',
            'seen_lines': set(),
            'sale_header': header,
            'sale_lines': {line.alvina_request_line_id.id: {
                'alvina_request_line_id': line.alvina_request_line_id.id,
                'product_id': line.product_id.id, 'product_uom_id': line.product_uom_id.id,
                'product_uom_qty': line.product_uom_qty, 'name': line.name,
                'price_unit': line.price_unit, 'discount': line.discount,
                'tax_ids': [Command.set(line.tax_ids.ids)],
            } for line in priced_sale.order_line},
        }
        token = NATIVE_SUPPLY.set(scope)
        try:
            self._approve_supply_purchase(purchase)
            if not scope['sale_id']:
                raise ValidationError(_(
                    'Odoo no generó la venta de Industria al confirmar la compra. '
                    'Revise en los ajustes interempresa de Industria la generación '
                    'de ventas a partir de compras de otras compañías. '
                    'No se ha registrado el pedido ni documentos incompletos.'))
            sale = self.env['sale.order'].sudo().with_context(
                allowed_company_ids=self.supply_company_id.ids).browse(scope['sale_id'])
            if scope['seen_lines'] != set(self.line_ids.ids):
                raise ValidationError(_('La venta nativa no incluye todos los productos solicitados.'))
            scope['phase'] = 'sale'
            if sale.state in ('draft', 'sent'):
                sale.action_confirm()
            if scope['invalid'] or scope['attempts'] != 1:
                raise ValidationError(_('El automatismo no produjo una única pareja de compra y venta.'))
            # Check terms after all native creation/confirmation overrides.
            if (sale.user_id.id != header['user_id']
                    or sale.pricelist_id.id != header['pricelist_id']):
                raise ValidationError(_('La venta nativa cambió el responsable o la lista de precios de Industria.'))
            for line in sale.order_line.filtered(lambda row: not row.display_type):
                expected = scope['sale_lines'].get(line.alvina_request_line_id.id)
                if (not expected
                        or not math.isclose(line.price_unit, expected['price_unit'], abs_tol=1e-8)
                        or not math.isclose(line.discount, expected['discount'], abs_tol=1e-8)
                        or set(line.tax_ids.ids) != set(expected['tax_ids'][0][2])):
                    raise ValidationError(_('La venta nativa cambió los precios o impuestos acordados. Se revierte el envío.'))
            self._check_confirmed_supply_pair(sale, purchase)
            return sale
        finally:
            NATIVE_SUPPLY.reset(token)

    def _approve_supply_purchase(self, purchase):
        purchase.button_confirm()
        if purchase.state == 'to approve':
            token = _AUTOMATED_PURCHASE_APPROVAL.set(frozenset(purchase.ids))
            try:
                purchase.button_approve()
            finally:
                _AUTOMATED_PURCHASE_APPROVAL.reset(token)

    def _check_confirmed_supply_pair(self, sale, purchase):
        if (sale.state != 'sale' or purchase.state != 'purchase'
                or sale.alvina_branch_request_id.id != self.id
                or purchase.alvina_branch_request_id.id != self.id
                or sale.company_id != self.supply_company_id
                or purchase.company_id != self.company_id):
            raise ValidationError(_('No se confirmó una única compra y venta de las compañías del requerimiento.'))
        if (sale.invoice_ids or purchase.invoice_ids
                or any(p.state == 'done' for p in sale.picking_ids | purchase.picking_ids)):
            raise ValidationError(_('Un automatismo externo facturó o validó existencias. Se revierte el envío.'))
        product_lines = sale.order_line.filtered(lambda line: not line.display_type)
        if (len(product_lines) != len(self.line_ids)
                or set(product_lines.alvina_request_line_id.ids) != set(self.line_ids.ids)):
            raise ValidationError(_('La venta no conserva todas las líneas del requerimiento.'))

    def _confirm_supply_documents(self, sale, purchase):
        """Confirm only this validated pair; physical validation stays manual."""
        self.ensure_one()
        sale.ensure_one()
        purchase.ensure_one()
        self._check_supply_actor()
        if (not sale.env.su or not purchase.env.su
                or sale.alvina_branch_request_id.id != self.id
                or purchase.alvina_branch_request_id.id != self.id
                or sale.company_id.id != self.supply_company_id.id
                or purchase.company_id.id != self.company_id.id
                or sale.state not in ('draft', 'sent')
                or purchase.state not in ('draft', 'sent')):
            raise AccessError(_('La confirmación automática está limitada a los documentos nuevos de este requerimiento.'))
        self.supply_config_id.sudo()._assert_supported()
        try:
            sale.action_confirm()
            self._approve_supply_purchase(purchase)
        except UserError as error:
            raise ValidationError(_(
                'No se pudo confirmar el abastecimiento con su configuración actual: %(reason)s',
                reason=str(error))) from error
        if sale.state != 'sale' or purchase.state != 'purchase':
            raise ValidationError(_(
                'No se pudieron confirmar la venta y la compra de este abastecimiento. '
                'Revise su configuración; el envío se ha revertido.'))
        # This service never creates invoices or validates stock operations.
        if (sale.invoice_ids or purchase.invoice_ids
                or any(picking.state == 'done' for picking in sale.picking_ids | purchase.picking_ids)):
            raise ValidationError(_(
                'Un automatismo externo facturó o validó existencias al confirmar el abastecimiento. '
                'Se requiere revisar esa integración; el envío se ha revertido.'))

    def action_cancel(self):
        self._manager()
        if any(rec.sudo().sale_order_id and rec.sudo().sale_order_id.state != 'cancel' for rec in self):
            raise ValidationError(_('Cancele primero la venta vinculada desde Ventas.'))
        return super().action_cancel()


class RequestLine(models.Model):
    _inherit = 'alvina.branch.request.line'

    def _snapshot_price(self, product, request, qty):
        if not request.supply_flow:
            return super()._snapshot_price(product, request, qty)
        configuration = request.supply_config_id.sudo()
        supplier = configuration.company_id
        pricelist = configuration.pricelist_id.with_context({}).with_company(supplier)
        price = pricelist._get_product_price(
            product.with_context({}).with_company(supplier), qty, uom=product.uom_id,
            date=request.request_date)
        return pricelist.currency_id._convert(price, request.currency_id, request.company_id,
                                              fields.Date.to_date(request.request_date), round=False)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    alvina_branch_request_id = fields.Many2one('alvina.branch.request', readonly=True,
                                               ondelete='restrict', copy=False, index=True,
                                               string='Requerimiento de sucursal')
    alvina_branch_id = fields.Many2one(related='alvina_branch_request_id.branch_id', store=True, string='Sucursal')
    alvina_purchase_order_id = fields.Many2one(related='alvina_branch_request_id.purchase_order_id', string='OC de sucursal')
    alvina_buyer_company_id = fields.Many2one(related='alvina_branch_request_id.company_id', string='Compañía compradora')
    alvina_required_date = fields.Date(related='alvina_branch_request_id.required_date', string='Fecha requerida')
    alvina_request_note = fields.Text(related='alvina_branch_request_id.note', string='Observaciones de la sucursal')
    alvina_delivery_state = fields.Selection(related='alvina_branch_request_id.delivery_state', string='Estado de entrega')
    _alvina_request_unique = models.Constraint('UNIQUE(alvina_branch_request_id)', 'El requerimiento ya tiene una venta de Industria.')

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and any(vals.get('alvina_branch_request_id', self.env.context.get('default_alvina_branch_request_id')) for vals in vals_list):
            raise AccessError(_('La venta vinculada se crea desde el servicio de abastecimiento.'))
        return super().create(vals_list)

    def write(self, vals):
        if 'alvina_branch_request_id' in vals:
            raise AccessError(_('No se permite reasignar el requerimiento de una venta.'))
        return super().write(vals)

    @api.constrains('alvina_branch_request_id', 'company_id', 'partner_id',
                    'partner_shipping_id', 'partner_invoice_id', 'warehouse_id')
    def _check_alvina_identity(self):
        for order in self:
            request = order.sudo().alvina_branch_request_id
            if request and (order.company_id != request.supply_company_id
                            or order.partner_id != request.company_id.partner_id
                            or order.partner_shipping_id != request.company_id.partner_id
                            or order.partner_invoice_id != request.company_id.partner_id
                            or order.warehouse_id != request.warehouse_id):
                raise ValidationError(_('La venta debe conservar Industria, la sucursal y el almacén de su requerimiento.'))

    def action_confirm(self):
        for order in self.filtered('alvina_branch_request_id'):
            configuration = order.sudo().alvina_branch_request_id.supply_config_id
            configuration._assert_supported()
            scope = NATIVE_SUPPLY.get()
            if configuration._native_intercompany_installed() and (
                    not scope or scope['request_id'] != order.alvina_branch_request_id.id):
                raise ValidationError(_('La confirmación interempresa de este pedido la gestiona el servicio de abastecimiento.'))
        return super().action_confirm()

    def unlink(self):
        self.check_access('unlink')
        if any(order.sudo().alvina_branch_request_id for order in self):
            raise ValidationError(_('Cancele la venta vinculada en lugar de eliminar su trazabilidad.'))
        return super().unlink()


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    alvina_sale_order_id = fields.Many2one(related='alvina_branch_request_id.sale_order_id', string='OV de Industria')

    def _approval_allowed(self):
        self.ensure_one()
        if (self.env.su and self.id in _AUTOMATED_PURCHASE_APPROVAL.get()
                and self.alvina_branch_request_id.supply_flow):
            return True
        return super()._approval_allowed()

    def button_confirm(self):
        for order in self.filtered('alvina_branch_request_id'):
            if order.sudo().alvina_branch_request_id.supply_flow:
                order.sudo().alvina_branch_request_id.supply_config_id._assert_supported()
                order._check_native_confirmation_scope()
        return super().button_confirm()

    def button_approve(self, force=False):
        # Approval is also a public confirmation path for orders awaiting a
        # second approver. Do not let it bypass the native automation guard.
        for order in self.filtered('alvina_branch_request_id'):
            if order.sudo().alvina_branch_request_id.supply_flow:
                order.sudo().alvina_branch_request_id.supply_config_id._assert_supported()
                order._check_native_confirmation_scope()
        return super().button_approve(force=force)

    def _check_native_confirmation_scope(self):
        self.ensure_one()
        config = self.sudo().alvina_branch_request_id.supply_config_id
        scope = NATIVE_SUPPLY.get()
        if config._native_intercompany_installed() and (not scope or scope['purchase_id'] != self.id):
            raise ValidationError(_('La confirmación de esta compra interempresa la gestiona el servicio de abastecimiento.'))

    @api.constrains('alvina_branch_request_id', 'picking_type_id')
    def _check_alvina_receipt_identity(self):
        for order in self:
            request = order.sudo().alvina_branch_request_id
            if (request.supply_flow
                    and order.picking_type_id != request.branch_id.purchase_picking_type_id):
                raise ValidationError(_(
                    'La compra debe conservar la recepción configurada para la sucursal solicitante.'))


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    alvina_request_line_id = fields.Many2one('alvina.branch.request.line', readonly=True,
                                             copy=False, ondelete='restrict', index=True)
    _alvina_line_unique = models.Constraint('UNIQUE(alvina_request_line_id)', 'La línea ya tiene una contrapartida de venta.')

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and any(vals.get('alvina_request_line_id', self.env.context.get('default_alvina_request_line_id')) for vals in vals_list):
            raise AccessError(_('La relación con el requerimiento la crea el servicio de abastecimiento.'))
        return super().create(vals_list)

    def write(self, vals):
        if 'alvina_request_line_id' in vals:
            raise AccessError(_('No se permite reasignar una línea de requerimiento.'))
        return super().write(vals)

    @api.constrains('alvina_request_line_id', 'order_id', 'product_id', 'product_uom_id', 'product_uom_qty')
    def _check_request_line(self):
        for line in self:
            request_line = line.sudo().alvina_request_line_id
            if line.order_id.sudo().alvina_branch_request_id and not line.display_type and not request_line:
                raise ValidationError(_('Los productos de esta venta deben proceder del requerimiento vinculado.'))
            if request_line and (line.order_id.alvina_branch_request_id != request_line.request_id
                                 or line.product_id != request_line.product_id
                                 or line.product_uom_id != request_line.product_uom_id
                                 or line.product_uom_qty != request_line.product_qty):
                raise ValidationError(_('Conserve el producto, unidad y cantidad originales; registre los faltantes sin reducir lo solicitado.'))

    def unlink(self):
        self.check_access('unlink')
        if any(line.sudo().alvina_request_line_id for line in self):
            raise ValidationError(_('No se permite eliminar una línea vinculada a un requerimiento.'))
        return super().unlink()


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    alvina_request_line_id = fields.Many2one('alvina.branch.request.line', readonly=True,
                                             copy=False, ondelete='restrict', index=True)
    _alvina_line_unique = models.Constraint('UNIQUE(alvina_request_line_id)', 'La línea ya tiene una contrapartida de compra.')

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and any(vals.get('alvina_request_line_id', self.env.context.get('default_alvina_request_line_id')) for vals in vals_list):
            raise AccessError(_('La relación con el requerimiento la crea el servicio de abastecimiento.'))
        return super().create(vals_list)

    def write(self, vals):
        if 'alvina_request_line_id' in vals:
            raise AccessError(_('No se permite reasignar una línea de requerimiento.'))
        return super().write(vals)

    @api.constrains('alvina_request_line_id', 'order_id', 'product_id', 'product_uom_id', 'product_qty')
    def _check_request_line(self):
        for line in self:
            request_line = line.sudo().alvina_request_line_id
            if line.order_id.sudo().alvina_branch_request_id.supply_flow and not line.display_type and not request_line:
                raise ValidationError(_('Los productos de esta compra deben proceder del requerimiento vinculado.'))
            if request_line and (line.order_id.alvina_branch_request_id != request_line.request_id
                                 or line.product_id != request_line.product_id
                                 or line.product_uom_id != request_line.product_uom_id
                                 or line.product_qty != request_line.product_qty):
                raise ValidationError(_('Conserve el producto, unidad y cantidad originales del requerimiento.'))

    def unlink(self):
        self.check_access('unlink')
        if any(line.sudo().alvina_request_line_id for line in self):
            raise ValidationError(_('No se permite eliminar una línea vinculada a un requerimiento.'))
        return super().unlink()
