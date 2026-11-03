"""Immutable internal dispatch notes and one-step, restricted buyer receipts.

Commercial confirmations belong to the supply service. Logistics validates a
real outgoing picking; its dispatch note is issued in that same transaction.
The buyer confirms actual received quantities through a narrow receipt service,
which validates the existing purchase picking and records conformity atomically.
No SUNAT operation, invoice or company membership is created here.
"""
from contextvars import ContextVar
from copy import deepcopy

from odoo import api, Command, fields, models, _
from odoo.exceptions import AccessError, ValidationError
from odoo.tools import format_amount

from .validation import quantity as valid_quantity


_ISSUING = ContextVar('alvina_dispatch_issuing', default=False)
_RECEIVING = ContextVar('alvina_dispatch_receiving', default=frozenset())


class BranchDispatch(models.Model):
    _name = 'alvina.branch.dispatch'
    _description = 'Guía interna valorizada de despacho'
    _order = 'issued_date desc, id desc'

    name = fields.Char(required=True, readonly=True, copy=False)
    picking_id = fields.Many2one('stock.picking', required=True, readonly=True, ondelete='restrict')
    request_id = fields.Many2one('alvina.branch.request', required=True, readonly=True, ondelete='restrict')
    branch_id = fields.Many2one(related='request_id.branch_id', store=True)
    company_id = fields.Many2one(related='request_id.supply_company_id', store=True)
    buyer_company_id = fields.Many2one(related='request_id.company_id', store=True)
    currency_id = fields.Many2one('res.currency', required=True, readonly=True)
    issued_by = fields.Many2one('res.users', required=True, readonly=True)
    issued_date = fields.Datetime(required=True, readonly=True)
    snapshot_json = fields.Json(required=True, readonly=True, copy=False)
    line_ids = fields.One2many('alvina.branch.dispatch.line', 'dispatch_id', readonly=True, copy=False)
    receipt_state = fields.Selection([
        ('pending', 'Pendiente de recepción'), ('conform', 'Recepción conforme'),
        ('discrepancy', 'Recepción con diferencias')], default='pending', required=True, readonly=True)
    receipt_confirmation_json = fields.Json(readonly=True, copy=False)
    confirmed_by = fields.Many2one('res.users', readonly=True)
    confirmed_date = fields.Datetime(readonly=True)
    receipt_note = fields.Text(string='Observaciones de recepción', readonly=True)
    can_receive = fields.Boolean(compute='_compute_can_receive')

    _one_picking = models.Constraint('UNIQUE(picking_id)', 'Este despacho ya tiene una guía interna.')

    def _compute_can_receive(self):
        for guide in self:
            guide.can_receive = bool(
                guide.receipt_state == 'pending'
                and self.env.user.has_group('alvina_branch_requisition.group_requester')
                and self.env.user.has_group('purchase.group_purchase_user')
                and guide.buyer_company_id in self.env.companies
                and self.env.uid in guide.sudo().branch_id.user_ids.ids)

    @api.model_create_multi
    def create(self, values_list):
        if not _ISSUING.get():
            raise AccessError(_('La guía se emite exclusivamente desde un despacho validado.'))
        return super().create(values_list)

    def write(self, values):
        raise AccessError(_('La guía y su conformidad son registros inmutables.'))

    def unlink(self):
        raise AccessError(_('La guía forma parte del historial y no se elimina.'))

    def _check_receiving_user(self):
        self.ensure_one()
        self.check_access('read')
        guide = self.sudo()
        if (not self.env.user.has_group('alvina_branch_requisition.group_requester')
                or not self.env.user.has_group('purchase.group_purchase_user')
                or guide.buyer_company_id not in self.env.companies
                or self.env.uid not in guide.branch_id.user_ids.ids):
            raise AccessError(_('La recepción corresponde al usuario autorizado de la sucursal compradora.'))

    def action_open_receipt(self):
        self._check_receiving_user()
        if self.receipt_state != 'pending':
            raise ValidationError(_('Esta guía ya tiene una confirmación de recepción.'))
        wizard = self.env['alvina.branch.dispatch.receipt'].create({
            'dispatch_id': self.id,
            'line_ids': [Command.create({'dispatch_line_id': line.id, 'quantity': 0})
                         for line in self.line_ids],
        })
        return {'type': 'ir.actions.act_window', 'res_model': wizard._name,
                'res_id': wizard.id, 'view_mode': 'form', 'target': 'new',
                'name': _('Confirmar recepción real')}

    def action_print(self):
        self.check_access('read')
        return self.env.ref('alvina_branch_requisition.action_dispatch_note_report').report_action(self)

    def _received_quantities(self):
        """Only real buyer moves attached to this guide, including their returns."""
        self.ensure_one()
        guide = self.sudo()
        quantities = {line.request_line_id.id: 0.0 for line in guide.line_ids}
        receipts = self.env['stock.picking'].sudo().search([('alvina_dispatch_id', '=', self.id)])
        moves = receipts.move_ids
        frontier = moves
        while frontier:
            frontier = frontier.returned_move_ids - moves
            moves |= frontier
        for move in moves:
            line = move.purchase_line_id.alvina_request_line_id
            if (move.state != 'done' or line.id not in quantities
                    or move.company_id != guide.buyer_company_id
                    or move.product_id != line.product_id
                    or move.scrap_id or move.is_inventory):
                continue
            source = (move.location_id.usage == 'internal'
                      and move.location_id.company_id == guide.buyer_company_id)
            destination = (move.location_dest_id.usage == 'internal'
                           and move.location_dest_id.company_id == guide.buyer_company_id)
            quantities[line.id] += (int(destination) - int(source)) * move.product_uom._compute_quantity(
                move.quantity, line.product_uom_id, rounding_method='HALF-UP')
        return {key: max(value, 0.0) for key, value in quantities.items()}

    def _alvina_dispatch_note_payload(self):
        self.ensure_one()
        self.check_access('read')
        dispatch = deepcopy(self.sudo().snapshot_json)
        confirmation = deepcopy(self.sudo().receipt_confirmation_json)
        if not confirmation:
            quantities = self._received_quantities()
            confirmation = {
                'state': 'pending', 'confirmed_by': '', 'confirmed_date': '', 'note': '',
                'lines': [{'request_line_id': line['request_line_id'],
                           'product': line['product'], 'shipped': line['shipped'],
                           'received': quantities[line['request_line_id']],
                           'difference': line['shipped'] - quantities[line['request_line_id']]}
                          for line in dispatch['lines']],
            }
        return {'dispatch': dispatch, 'receipt': confirmation}


class BranchDispatchLine(models.Model):
    _name = 'alvina.branch.dispatch.line'
    _description = 'Detalle inmutable de guía interna'

    dispatch_id = fields.Many2one('alvina.branch.dispatch', required=True, ondelete='cascade', readonly=True)
    company_id = fields.Many2one(related='dispatch_id.company_id', store=True)
    buyer_company_id = fields.Many2one(related='dispatch_id.buyer_company_id', store=True)
    branch_id = fields.Many2one(related='dispatch_id.branch_id', store=True)
    request_line_id = fields.Many2one('alvina.branch.request.line', required=True, ondelete='restrict', readonly=True)
    product_id = fields.Many2one('product.product', required=True, ondelete='restrict', readonly=True)
    description = fields.Char(required=True, readonly=True, string='Producto al emitir')
    product_uom_id = fields.Many2one('uom.uom', required=True, ondelete='restrict', readonly=True)
    quantity = fields.Float(required=True, readonly=True, digits='Product Unit', string='Despachado')
    price_unit = fields.Float(required=True, readonly=True, digits='Product Price', string='Precio al emitir')
    currency_id = fields.Many2one(related='dispatch_id.currency_id')
    amount_total = fields.Monetary(readonly=True, string='Valor del despacho')
    qty_received = fields.Float(compute='_compute_receipt', digits='Product Unit', string='Recibido en Inventario')
    qty_difference = fields.Float(compute='_compute_receipt', digits='Product Unit', string='Diferencia de recepción')

    _one_request_line = models.Constraint('UNIQUE(dispatch_id, request_line_id)', 'No repita el producto en la guía.')

    @api.model_create_multi
    def create(self, values_list):
        if not _ISSUING.get():
            raise AccessError(_('El detalle de guía se registra al emitir el despacho.'))
        return super().create(values_list)

    def write(self, values):
        raise AccessError(_('El detalle de la guía emitida no se modifica.'))

    def unlink(self):
        raise AccessError(_('El detalle de la guía emitida no se elimina.'))

    def _compute_receipt(self):
        for guide in self.dispatch_id:
            quantities = guide._received_quantities()
            for line in self.filtered(lambda item: item.dispatch_id == guide):
                line.qty_received = quantities.get(line.request_line_id.id, 0)
                line.qty_difference = line.quantity - line.qty_received


class StockPickingDispatch(models.Model):
    _inherit = 'stock.picking'

    alvina_dispatch_id = fields.Many2one(
        'alvina.branch.dispatch', string='Guía interna recibida', copy=False, ondelete='restrict')
    alvina_emitted_dispatch_ids = fields.One2many(
        'alvina.branch.dispatch', 'picking_id', string='Guía interna emitida', readonly=True)

    def _alvina_outgoing_request(self):
        self.ensure_one()
        if self.picking_type_code != 'outgoing':
            return self.env['alvina.branch.request']
        requests = self.sudo().move_ids.sale_line_id.alvina_request_line_id.request_id.filtered('supply_flow')
        if not requests:
            return requests
        if len(requests) != 1:
            raise ValidationError(_('Un despacho valorizado debe corresponder a un único requerimiento.'))
        for move in self.sudo().move_ids.filtered(lambda item: item.state != 'cancel'):
            line = move.sale_line_id.alvina_request_line_id
            if (line.request_id != requests or move.company_id != requests.supply_company_id
                    or move.product_id != line.product_id or line._alvina_move_direction(move) != 1):
                raise ValidationError(_('El despacho contiene productos u operaciones ajenos al requerimiento.'))
        return requests

    def _alvina_check_dispatch_actor(self, request):
        self.ensure_one()
        self.check_access('write')
        if self.env.su:
            return
        if (not self.env.user.has_group('alvina_branch_requisition.group_keeper')
                or request.supply_company_id not in self.env.companies
                or not request.branch_id.with_env(self.env)._alvina_user_can_attend_supply()):
            raise AccessError(_('Solo Logística de Industria puede confirmar este despacho.'))

    def _alvina_check_incoming_service(self):
        """Primary supply receipts must use the atomic buyer confirmation.

        A client context cannot grant this capability. Standard physical returns
        keep their usual Inventory workflow and never confirm another guide.
        """
        for picking in self.filtered(lambda item: item.picking_type_code == 'incoming'):
            moves = picking.sudo().move_ids.filtered(lambda item: item.state != 'cancel')
            requests = moves.purchase_line_id.alvina_request_line_id.request_id.filtered('supply_flow')
            real_return = bool(picking.sudo().return_id) and all(
                move.origin_returned_move_id.state == 'done'
                and move.origin_returned_move_id.picking_id == picking.sudo().return_id
                and move.origin_returned_move_id.company_id == move.company_id
                and move.origin_returned_move_id.product_id == move.product_id
                and move.origin_returned_move_id.purchase_line_id == move.purchase_line_id
                and move.origin_returned_move_id.location_id == move.location_dest_id
                and move.origin_returned_move_id.location_dest_id == move.location_id
                for move in moves)
            if requests and not real_return and picking.id not in _RECEIVING.get():
                raise AccessError(_('Confirme la recepción real desde la guía interna de despacho.'))

    def button_validate(self):
        self._alvina_check_incoming_service()
        supplies = self.filtered(lambda picking: bool(picking._alvina_outgoing_request()))
        if supplies and self - supplies:
            raise ValidationError(_('Valide los despachos de sucursales por separado de otras operaciones.'))
        for picking in supplies:
            request = picking._alvina_outgoing_request()
            picking._alvina_check_dispatch_actor(request)
            if picking.picking_type_id.create_backorder == 'never' and any(
                    move.product_uom.compare(move.quantity, move.product_uom_qty) < 0
                    for move in picking.move_ids if move.state != 'cancel'):
                raise ValidationError(_(
                    'Este despacho parcial requiere conservar el saldo. Configure la operación '
                    'con creación de entregas pendientes, sin cancelar el faltante.'))
        # Preserve standard behavior for ordinary Inventory operations.
        ordinary = self - supplies
        result = super(StockPickingDispatch, ordinary).button_validate() if ordinary else True
        if supplies:
            result = super(StockPickingDispatch, supplies.with_context(
                skip_backorder=True, picking_ids_not_to_backorder=[])).button_validate()
        return result

    def _action_done(self):
        with self.env.cr.savepoint():
            return self._alvina_action_done_with_guide()

    def _alvina_action_done_with_guide(self):
        self._alvina_check_incoming_service()
        supplies = self.filtered(lambda picking: bool(picking._alvina_outgoing_request()))
        for picking in supplies:
            picking._alvina_check_dispatch_actor(picking._alvina_outgoing_request())
        result = super()._action_done()
        for picking in supplies.filtered(lambda item: item.state == 'done'):
            picking._alvina_emit_dispatch()
        return result

    def action_emit_alvina_dispatch(self):
        self.ensure_one()
        guide = self._alvina_emit_dispatch()
        return {'type': 'ir.actions.act_window', 'res_model': guide._name,
                'res_id': guide.id, 'view_mode': 'form', 'target': 'current'}

    def _alvina_emit_dispatch(self):
        self.ensure_one()
        request = self._alvina_outgoing_request()
        if not request or self.state != 'done':
            raise ValidationError(_('La guía corresponde a una salida real de Industria ya validada.'))
        self._alvina_check_dispatch_actor(request)
        self.env.cr.execute('SELECT id FROM stock_picking WHERE id = %s FOR UPDATE', [self.id])
        existing = self.env['alvina.branch.dispatch'].sudo().search([('picking_id', '=', self.id)], limit=1)
        if existing:
            return existing.with_env(self.env)
        request = request.sudo()
        sale = request.sale_order_id
        if not sale or sale.company_id != self.company_id:
            raise ValidationError(_('El despacho no tiene una venta de Industria válida.'))
        quantities = {}
        for move in self.sudo().move_ids.filtered(lambda item: item.state == 'done'):
            line = move.sale_line_id.alvina_request_line_id
            quantities[line.id] = quantities.get(line.id, 0) + move.product_uom._compute_quantity(
                move.quantity, line.product_uom_id, rounding_method='HALF-UP')
        bases = []
        rows = []
        for request_line in request.line_ids.filtered(lambda line: quantities.get(line.id, 0) > 0):
            sale_line = sale.order_line.filtered(lambda line: line.alvina_request_line_id == request_line)
            if len(sale_line) != 1:
                raise ValidationError(_('Falta la línea comercial del producto despachado.'))
            shipped = quantities[request_line.id]
            sale_qty = request_line.product_uom_id._compute_quantity(shipped, sale_line.product_uom_id, round=False)
            unit_factor = request_line.product_uom_id._compute_quantity(1, sale_line.product_uom_id, round=False)
            row = {
                'request_line_id': request_line.id, 'product_id': request_line.product_id.id,
                'code': request_line.product_id.default_code or '', 'product': request_line.product_id.name,
                'uom_id': request_line.product_uom_id.id, 'uom': request_line.product_uom_id.name,
                'requested': request_line.product_qty, 'shipped': shipped,
                'price_unit': sale_line.price_unit * unit_factor, 'discount': sale_line.discount,
                'taxes': ', '.join(sale_line.tax_ids.mapped('name')),
            }
            base = sale_line._prepare_base_line_for_taxes_computation(quantity=sale_qty)
            base['_guide_row'] = row
            bases.append(base)
            rows.append(row)
        if not rows:
            raise ValidationError(_('No hay cantidades físicas despachadas para emitir esta guía.'))
        Tax = sale.env['account.tax']
        Tax._add_tax_details_in_base_lines(bases, sale.company_id)
        Tax._round_base_lines_tax_details(bases, sale.company_id)
        summary = Tax._get_tax_totals_summary(bases, sale.currency_id, sale.company_id)
        for base in bases:
            details = base['tax_details']
            base['_guide_row'].update(
                untaxed=sale.currency_id.round(details['total_excluded_currency']),
                tax=sale.currency_id.round(details['total_included_currency'] - details['total_excluded_currency']),
                total=sale.currency_id.round(details['total_included_currency']))
        name = self.env['ir.sequence'].sudo().next_by_code('alvina.branch.dispatch')
        if not name:
            raise ValidationError(_('Falta configurar la secuencia de guías internas Alviña.'))
        issued = fields.Datetime.now()
        company = sale.company_id
        snapshot = {
            'name': name, 'issued_date': fields.Datetime.to_string(issued) + ' UTC',
            'issued_by': self.env.user.name, 'company': company.name,
            'company_vat': company.vat or '',
            'logo': company.logo.decode() if company.logo and not company.uses_default_logo else '',
            'branch': request.branch_id.name, 'branch_code': request.branch_id.branch_code,
            'buyer_company': request.company_id.name, 'request': request.name,
            'sale': sale.name, 'purchase': request.purchase_order_id.name,
            'picking': self.name, 'warehouse': request.warehouse_id.name,
            'currency': sale.currency_id.name, 'currency_id': sale.currency_id.id,
            'notes': self.alvina_dispatch_note or '', 'shortage_note': self.alvina_shortage_note or '',
            'balances': [{
                'product': line.product_id.name, 'code': line.product_id.default_code or '',
                'uom': line.product_uom_id.name, 'requested': line.product_qty,
                'supplied': line.qty_supplied, 'pending': line.qty_pending,
            } for line in request.line_ids],
            'lines': rows, 'totals': {
                'untaxed': sale.currency_id.round(summary['base_amount_currency']),
                'tax': sale.currency_id.round(summary['tax_amount_currency']),
                'total': sale.currency_id.round(summary['total_amount_currency'])},
        }
        token = _ISSUING.set(True)
        try:
            guide = self.env['alvina.branch.dispatch'].sudo().with_context({}).create({
                'name': name, 'picking_id': self.id, 'request_id': request.id,
                'currency_id': sale.currency_id.id, 'issued_by': self.env.uid,
                'issued_date': issued, 'snapshot_json': snapshot,
                'line_ids': [Command.create({
                    'request_line_id': row['request_line_id'], 'product_id': row['product_id'],
                    'description': row['product'], 'product_uom_id': row['uom_id'],
                    'quantity': row['shipped'], 'price_unit': row['price_unit'], 'amount_total': row['total'],
                }) for row in rows],
            })
        finally:
            _ISSUING.reset(token)
        return guide.with_env(self.env)

    @api.model_create_multi
    def create(self, values_list):
        if any(values.get('alvina_dispatch_id', self.env.context.get('default_alvina_dispatch_id'))
               for values in values_list):
            raise AccessError(_('La guía se vincula al confirmar su recepción real.'))
        return super().create(values_list)

    def write(self, values):
        if 'alvina_dispatch_id' in values:
            for picking in self:
                if picking.id not in _RECEIVING.get():
                    raise AccessError(_('Use Confirmar recepción desde la guía interna.'))
                if picking.state in ('done', 'cancel'):
                    raise ValidationError(_('La guía de una recepción validada no se reasigna.'))
        return super().write(values)

    @api.constrains('alvina_dispatch_id', 'company_id', 'picking_type_id', 'move_ids')
    def _check_alvina_guide_receipt(self):
        for picking in self.filtered('alvina_dispatch_id'):
            guide = picking.sudo().alvina_dispatch_id
            if picking.company_id != guide.buyer_company_id or picking.picking_type_code != 'incoming':
                raise ValidationError(_('La guía debe corresponder a una recepción de su compañía compradora.'))
            line_ids = guide.line_ids.request_line_id.ids
            for move in picking.sudo().move_ids.filtered(lambda item: item.state != 'cancel'):
                request_line = move.purchase_line_id.alvina_request_line_id
                if (move.purchase_line_id.order_id != guide.request_id.purchase_order_id
                        or request_line.request_id != guide.request_id
                        or move.product_id != request_line.product_id
                        or (move.quantity and request_line.id not in line_ids)):
                    raise ValidationError(_('La recepción contiene productos o compras ajenas a esta guía interna.'))


class DispatchReceipt(models.TransientModel):
    _name = 'alvina.branch.dispatch.receipt'
    _description = 'Confirmar cantidades realmente recibidas'

    dispatch_id = fields.Many2one('alvina.branch.dispatch', required=True, readonly=True)
    line_ids = fields.One2many('alvina.branch.dispatch.receipt.line', 'wizard_id', required=True)
    note = fields.Text(string='Motivo de diferencias / observaciones')

    @api.model_create_multi
    def create(self, values_list):
        for values in values_list:
            guide = self.env['alvina.branch.dispatch'].browse(values.get(
                'dispatch_id', self.env.context.get('default_dispatch_id'))).exists()
            if not guide:
                raise ValidationError(_('Seleccione una guía interna existente.'))
            guide._check_receiving_user()
        return super().create(values_list)

    def write(self, values):
        if 'dispatch_id' in values:
            raise AccessError(_('No se reasigna la guía de una confirmación.'))
        for wizard in self:
            wizard.dispatch_id._check_receiving_user()
        return super().write(values)

    def action_confirm(self):
        self.ensure_one()
        self.check_access('write')
        with self.env.cr.savepoint():
            return self._confirm_receipt()

    def _confirm_receipt(self):
        guide = self.dispatch_id
        guide._check_receiving_user()
        # Distinct guides can consume the same buyer purchase/backorder. Lock
        # that commercial origin before selecting a pending receipt, then
        # refresh its relations after any competing confirmation has completed.
        purchase = guide.sudo().request_id.purchase_order_id
        if not purchase:
            raise ValidationError(_('La guía no tiene una compra de sucursal vinculada.'))
        self.env.cr.execute('SELECT id FROM purchase_order WHERE id = %s FOR UPDATE', [purchase.id])
        purchase.invalidate_recordset(['picking_ids', 'state'])
        self.env.cr.execute('SELECT id FROM alvina_branch_dispatch WHERE id = %s FOR UPDATE', [guide.id])
        guide.invalidate_recordset(['receipt_state', 'receipt_confirmation_json'])
        if guide.receipt_state != 'pending':
            raise ValidationError(_('Esta guía ya tiene una confirmación de recepción.'))
        if (len(self.line_ids) != len(guide.line_ids)
                or set(self.line_ids.dispatch_line_id.ids) != set(guide.line_ids.ids)):
            raise ValidationError(_('Confirme únicamente todos los productos de esta guía.'))
        actual = {}
        different = False
        for row in self.line_ids:
            line = row.dispatch_line_id
            try:
                qty = valid_quantity(row.quantity, line.product_uom_id.rounding)
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
            if line.product_uom_id.compare(qty, line.quantity) > 0:
                raise ValidationError(_('La cantidad recibida no puede superar lo documentado en esta guía.'))
            actual[line.request_line_id.id] = qty
            different |= bool(line.product_uom_id.compare(qty, line.quantity))
            if line.product_id.sudo().tracking != 'none':
                raise ValidationError(_(
                    'Este producto exige lotes o números de serie. Se necesita registrar esa '
                    'información con su operación de Inventario; no se inventan identificadores.'))
        note = (self.note or '').strip()
        if different and not note:
            raise ValidationError(_('Explique el motivo de las diferencias de recepción.'))
        if not any(actual.values()):
            # A confirmed non-receipt is evidence of a difference, not a stock
            # operation. Keep the incoming demand pending for manual resolution.
            return self._record_confirmation(guide, actual, different, note)
        request = guide.sudo().request_id
        if purchase.state not in ('purchase', 'done'):
            raise ValidationError(_('La compra debe estar confirmada para disponer de su recepción real.'))
        buyer = guide.sudo().buyer_company_id
        context = {'allowed_company_ids': buyer.ids, 'lang': self.env.lang,
                   'skip_backorder': True, 'picking_ids_not_to_backorder': [],
                   'tracking_disable': True, 'mail_create_nosubscribe': True}
        candidates = purchase.picking_ids.filtered(lambda picking:
            picking.company_id == buyer and picking.picking_type_code == 'incoming'
            and picking.state not in ('done', 'cancel')
            and not picking.alvina_dispatch_id)
        # This release supports a real one-step incoming operation. A receipt
        # chain must not be marked received while goods are still at an input dock.
        candidates = candidates.filtered(lambda picking:
            picking.picking_type_id.warehouse_id.reception_steps == 'one_step'
            and all(move.location_dest_id.usage == 'internal'
                    and move.location_dest_id.company_id == buyer for move in picking.move_ids))
        if len(candidates) != 1:
            raise ValidationError(_(
                'Se necesita una única recepción pendiente de compra con operación de un paso. '
                'Revise las rutas reales y los despachos anteriores; no se crean movimientos ficticios.'))
        picking = candidates.sudo().with_context(context).with_company(buyer)
        # Authorize as the buyer above, then elevate only this verified purchase
        # picking. No generic stock access or company membership is granted.
        self.env.cr.execute('SELECT id FROM stock_picking WHERE id = %s FOR UPDATE', [picking.id])
        picking.invalidate_recordset(['state', 'alvina_dispatch_id', 'move_ids'])
        picking.move_ids.invalidate_recordset(['state', 'quantity', 'product_uom_qty'])
        if picking.state in ('done', 'cancel') or picking.alvina_dispatch_id:
            raise ValidationError(_('La recepción ya fue procesada por otra operación.'))
        for move in picking.move_ids.filtered(lambda item: item.state != 'cancel'):
            line = move.purchase_line_id.alvina_request_line_id
            if (move.purchase_line_id.order_id != purchase or line.request_id != request
                    or move.product_id != line.product_id or move.company_id != buyer):
                raise ValidationError(_('La recepción pendiente contiene productos ajenos a esta guía.'))
        if picking.picking_type_id.create_backorder == 'never' and any(
                move.product_uom.compare(
                    move.purchase_line_id.alvina_request_line_id.product_uom_id._compute_quantity(
                        actual.get(move.purchase_line_id.alvina_request_line_id.id, 0), move.product_uom),
                    move.product_uom_qty) < 0 for move in picking.move_ids if move.state != 'cancel'):
            raise ValidationError(_('Configure la recepción para conservar el saldo pendiente de recibir.'))
        remaining = dict(actual)
        moves = picking.move_ids.filtered(lambda item: item.state != 'cancel').sorted('id')
        last_moves = {move.purchase_line_id.alvina_request_line_id.id: move.id for move in moves}
        for move in moves:
            line = move.purchase_line_id.alvina_request_line_id
            need = move.product_uom._compute_quantity(move.product_uom_qty, line.product_uom_id)
            # Native Inventory can ship more than demand. Receive the documented
            # physical quantity, without increasing the original OC demand.
            take = (remaining.get(line.id, 0) if move.id == last_moves[line.id]
                    else min(remaining.get(line.id, 0), need))
            move.quantity = line.product_uom_id._compute_quantity(take, move.product_uom)
            move.picked = True
            if line.id in remaining:
                remaining[line.id] -= take
        if any(value > 0 for value in remaining.values()):
            raise ValidationError(_('La cantidad recibida supera el saldo real de la compra.'))
        token = _RECEIVING.set(frozenset([picking.id]))
        try:
            picking.alvina_dispatch_id = guide.id
            picking.button_validate()
        finally:
            _RECEIVING.reset(token)
        if picking.state != 'done':
            raise ValidationError(_(
                'Inventario requiere información adicional para validar la recepción. '
                'No se registró conformidad ni se omitieron sus controles.'))
        return self._record_confirmation(guide, actual, different, note, picking.name)

    def _record_confirmation(self, guide, actual, different, note, receipt_name=''):
        received = guide._received_quantities()
        confirmation = {
            'state': 'discrepancy' if different else 'conform',
            'confirmed_by': self.env.user.name, 'confirmed_date': fields.Datetime.to_string(fields.Datetime.now()) + ' UTC',
            'note': note, 'receipt': receipt_name,
            'lines': [{'request_line_id': line.request_line_id.id,
                       'product': line.description, 'shipped': line.quantity,
                       'received': received[line.request_line_id.id],
                       'difference': line.quantity - received[line.request_line_id.id]}
                      for line in guide.sudo().line_ids],
        }
        for line in guide.line_ids:
            if line.product_uom_id.compare(received[line.request_line_id.id], actual[line.request_line_id.id]):
                raise ValidationError(_('La recepción física no coincide con las cantidades confirmadas.'))
        super(BranchDispatch, guide.sudo()).write({
            'receipt_state': confirmation['state'], 'receipt_confirmation_json': confirmation,
            'confirmed_by': self.env.uid, 'confirmed_date': fields.Datetime.now(), 'receipt_note': note,
        })
        return {'type': 'ir.actions.act_window_close'}


class DispatchReceiptLine(models.TransientModel):
    _name = 'alvina.branch.dispatch.receipt.line'
    _description = 'Cantidad real recibida por producto'

    wizard_id = fields.Many2one('alvina.branch.dispatch.receipt', required=True, ondelete='cascade')
    dispatch_line_id = fields.Many2one('alvina.branch.dispatch.line', required=True, readonly=True)
    product_name = fields.Char(related='dispatch_line_id.description')
    shipped = fields.Float(related='dispatch_line_id.quantity', string='Despachado')
    quantity = fields.Float(required=True, digits='Product Unit', string='Realmente recibido')
    product_uom_id = fields.Many2one(related='dispatch_line_id.product_uom_id')

    _one_dispatch_line = models.Constraint('UNIQUE(wizard_id, dispatch_line_id)', 'No repita productos en la recepción.')

    @api.model_create_multi
    def create(self, values_list):
        for values in values_list:
            wizard = self.env['alvina.branch.dispatch.receipt'].browse(values.get(
                'wizard_id', self.env.context.get('default_wizard_id'))).exists()
            if not wizard:
                raise ValidationError(_('La cantidad debe pertenecer a una confirmación existente.'))
            wizard.check_access('write')
            wizard.dispatch_id._check_receiving_user()
            if values.get('dispatch_line_id') not in wizard.dispatch_id.line_ids.ids:
                raise AccessError(_('El producto no pertenece a esta guía interna autorizada.'))
            line = wizard.dispatch_id.line_ids.filtered(
                lambda item: item.id == values['dispatch_line_id'])
            try:
                values['quantity'] = valid_quantity(values.get('quantity', 0), line.product_uom_id.rounding)
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
        return super().create(values_list)

    def write(self, values):
        if set(values) - {'quantity'}:
            raise AccessError(_('Solo se modifica la cantidad realmente recibida.'))
        for line in self:
            line.wizard_id.dispatch_id._check_receiving_user()
            if 'quantity' in values:
                try:
                    valid_quantity(values['quantity'], line.product_uom_id.rounding)
                except ValueError as exc:
                    raise ValidationError(str(exc)) from exc
        return super().write(values)


class DispatchNoteReport(models.AbstractModel):
    _name = 'report.alvina_branch_requisition.dispatch_note'
    _description = 'PDF interno valorizado por despacho'

    @api.model
    def _get_report_values(self, docids, data=None):
        guides = self.env['alvina.branch.dispatch'].browse(docids).exists()
        guides.check_access('read')
        documents = []
        for guide in guides:
            payload = guide._alvina_dispatch_note_payload()
            currency = guide.currency_id
            for row in payload['dispatch']['lines']:
                row['price_display'] = format_amount(self.env, row['price_unit'], currency)
                row['total_display'] = format_amount(self.env, row['total'], currency)
            payload['dispatch']['totals_display'] = {
                key: format_amount(self.env, value, currency)
                for key, value in payload['dispatch']['totals'].items()}
            payload['id'] = guide.id
            documents.append(payload)
        return {'doc_ids': guides.ids, 'doc_model': guides._name, 'documents': documents}


class DispatchNoteReportLayout(models.Model):
    _inherit = 'ir.actions.report'

    def _get_layout(self):
        if len(self) == 1 and self.report_name == 'alvina_branch_requisition.dispatch_note':
            return self.env.ref('alvina_branch_requisition.dispatch_minimal_layout')
        return super()._get_layout()
