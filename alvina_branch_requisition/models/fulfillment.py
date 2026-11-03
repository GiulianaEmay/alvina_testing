"""Physical fulfillment from standard inventory operations, without auto-validation.

The supply service confirms the linked commercial documents automatically.
Reserved quantities and purchase receipts are deliberately distinct from Industry's
actual, net dispatches.  In particular, a return is physical regardless of the
``to_refund`` flag used by the standard invoicing/delivered-quantity computation.
"""
from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError

from .validation import quantity as validated_quantity


class BranchRequestFulfillment(models.Model):
    _inherit = 'alvina.branch.request'

    delivery_state = fields.Selection([
        ('none', 'Sin entrega'),
        ('preparing', 'En preparación'),
        ('partial', 'Entrega parcial'),
        ('done', 'Entregado'),
        ('closed', 'Cerrado con faltantes'),
    ], compute='_compute_delivery_state', store=True, string='Estado de entrega')

    @api.depends('supply_flow', 'line_ids.qty_supplied', 'line_ids.qty_pending',
                 'line_ids.qty_prepared', 'line_ids.qty_cancelled', 'sale_order_id.state')
    def _compute_delivery_state(self):
        for request in self:
            if not request.supply_flow or not request.line_ids:
                request.delivery_state = 'none'
            elif all(line.product_uom_id.compare(line.qty_pending, 0) <= 0
                     for line in request.line_ids):
                request.delivery_state = (
                    'closed' if any(request.line_ids.mapped('qty_cancelled')) else 'done')
            elif any(line.product_uom_id.compare(line.qty_supplied, 0) > 0
                     for line in request.line_ids):
                request.delivery_state = 'partial'
            elif (any(line.product_uom_id.compare(line.qty_prepared, 0) > 0
                      for line in request.line_ids)
                  or request.sale_order_id.state == 'sale'):
                request.delivery_state = 'preparing'
            else:
                request.delivery_state = 'none'

    def _create_internal_dispatch(self):
        """Same-company handling needs an approved, real destination mapping."""
        raise ValidationError(_(
            'La operación dentro de la misma compañía requiere configurar y aprobar '
            'su traslado interno. No se crean empresas, compras ni ventas ficticias.'))

    def _alvina_delivery_pickings(self):
        self.ensure_one()
        return self.line_ids._alvina_delivery_moves().picking_id


class RequestLineFulfillment(models.Model):
    _inherit = 'alvina.branch.request.line'

    sale_line_ids = fields.One2many(
        'sale.order.line', 'alvina_request_line_id', readonly=True,
        string='Líneas de venta vinculadas',
        groups='alvina_branch_requisition.group_keeper')
    purchase_line_ids = fields.One2many(
        'purchase.order.line', 'alvina_request_line_id', readonly=True,
        string='Líneas de compra vinculadas',
        groups='alvina_branch_requisition.group_keeper')
    internal_move_ids = fields.One2many(
        'stock.move', 'alvina_branch_request_line_id', readonly=True,
        string='Movimientos internos vinculados',
        groups='alvina_branch_requisition.group_keeper')
    cancellation_ids = fields.One2many(
        'alvina.branch.balance.cancellation', 'request_line_id', readonly=True,
        string='Cierres de saldo', groups='alvina_branch_requisition.group_keeper')
    qty_cancelled = fields.Float(
        compute='_compute_cancelled_quantity', store=True, readonly=True,
        digits='Product Unit', string='Faltante cerrado con motivo')
    qty_supplied = fields.Float(
        compute='_compute_fulfillment_quantities', store=True,
        readonly=True, digits='Product Unit', string='Entregado neto')
    legacy_qty_supplied = fields.Float(default=0, readonly=True, digits='Product Unit',
                                        string='Atendido histórico anterior al flujo')
    qty_prepared = fields.Float(
        compute='_compute_fulfillment_quantities', store=True,
        readonly=True, digits='Product Unit', string='Reservado para despacho',
        help='Cantidad de las salidas finales activas; no es una entrega validada.')
    qty_received = fields.Float(
        compute='_compute_fulfillment_quantities', store=True,
        readonly=True, digits='Product Unit', string='Recibido por la sucursal',
        help='Recepciones validadas en la compañía solicitante, menos sus devoluciones.')

    # Standard Inventory permits over-delivery. Keep the real quantity rather
    # than silently clipping it to demand or rejecting an otherwise valid move.
    _positive = models.Constraint(
        'CHECK(product_qty > 0 AND qty_supplied >= 0)', 'Cantidades inválidas.')

    @api.depends('cancellation_ids.quantity')
    def _compute_cancelled_quantity(self):
        for line in self:
            line.qty_cancelled = sum(line.cancellation_ids.mapped('quantity'))

    @api.depends('product_qty', 'qty_supplied', 'qty_cancelled')
    def _compute_pending(self):
        for line in self:
            line.qty_pending = max(line.product_qty - line.qty_supplied - line.qty_cancelled, 0)

    def action_cancel_balance(self):
        self.ensure_one()
        self._alvina_check_balance_cancellation_access()
        return {
            'type': 'ir.actions.act_window', 'name': _('Cerrar saldo con motivo'),
            'res_model': 'alvina.branch.balance.cancellation',
            'view_mode': 'form', 'target': 'new',
            'context': {'default_request_line_id': self.id,
                        'default_quantity': self.qty_pending},
        }

    def _alvina_check_balance_cancellation_access(self):
        self.ensure_one()
        self.request_id.check_access('read')
        if (not self.env.user.has_group('alvina_branch_requisition.group_keeper')
                or not self.branch_id._alvina_user_can_attend_supply()
                or self.supply_company_id not in self.env.companies):
            raise AccessError(_('El cierre de saldo corresponde al almacenero autorizado de Industria.'))
        if not self.request_id.supply_flow or self.request_id.state == 'cancel':
            raise ValidationError(_('Solo se pueden cerrar saldos del flujo de abastecimiento activo.'))

    def _alvina_delivery_moves(self):
        """Only moves explicitly linked through this request's commercial lines.

        Standard returns retain their sale/internal link and backorders copy it.
        Traverse returned_move_ids as well, so an existing physically linked
        return is included even if another integration removed its sale link.
        """
        moves = self.sale_line_ids.move_ids | self.internal_move_ids
        frontier = moves
        while frontier:
            frontier = frontier.returned_move_ids - moves
            moves |= frontier
        return moves

    def _alvina_move_direction(self, move):
        self.ensure_one()
        if (move.company_id != self.supply_company_id
                or move.product_id != self.product_id
                or move.scrap_id or move.is_inventory):
            return 0
        if self.request_id.operation_mode == 'intercompany':
            source_out = move.location_id._is_outgoing()
            destination_out = move.location_dest_id._is_outgoing()
        else:
            # No implicit location is inferred from a partner or company. This
            # path is dormant until a real internal-location mapping is approved.
            branch = self.branch_id
            destination = (branch.internal_location_id
                           if 'internal_location_id' in branch._fields else False)
            if not destination or destination.company_id != self.supply_company_id:
                return 0
            source_out = move.location_id._child_of(destination)
            destination_out = move.location_dest_id._child_of(destination)
        return int(destination_out) - int(source_out)

    @api.depends(
        'request_id.supply_flow', 'request_id.operation_mode', 'product_uom_id', 'legacy_qty_supplied',
        'sale_line_ids.move_ids.state', 'sale_line_ids.move_ids.quantity',
        'sale_line_ids.move_ids.product_uom', 'sale_line_ids.move_ids.location_id',
        'sale_line_ids.move_ids.location_dest_id',
        'sale_line_ids.move_ids.returned_move_ids.state',
        'sale_line_ids.move_ids.returned_move_ids.quantity',
        'internal_move_ids.state', 'internal_move_ids.quantity',
        'internal_move_ids.product_uom', 'internal_move_ids.location_id',
        'internal_move_ids.location_dest_id',
        'internal_move_ids.returned_move_ids.state',
        'internal_move_ids.returned_move_ids.quantity',
        'purchase_line_ids.move_ids.state', 'purchase_line_ids.move_ids.quantity',
        'purchase_line_ids.move_ids.product_uom',
        'purchase_line_ids.move_ids.location_id',
        'purchase_line_ids.move_ids.location_dest_id',
        'purchase_line_ids.move_ids.returned_move_ids.state',
        'purchase_line_ids.move_ids.returned_move_ids.quantity',
    )
    def _compute_fulfillment_quantities(self):
        for line in self:
            supplied = line.legacy_qty_supplied if not line.request_id.supply_flow else 0.0
            prepared = received = 0.0
            if line.request_id.supply_flow:
                for move in line._alvina_delivery_moves():
                    direction = line._alvina_move_direction(move)
                    if not direction:
                        continue
                    actual = move.product_uom._compute_quantity(
                        move.quantity, line.product_uom_id, rounding_method='HALF-UP')
                    if move.state == 'done':
                        supplied += direction * actual
                    elif direction > 0 and move.state not in ('draft', 'cancel'):
                        prepared += actual
                receipt_moves = line.purchase_line_ids.move_ids
                frontier = receipt_moves
                while frontier:
                    frontier = frontier.returned_move_ids - receipt_moves
                    receipt_moves |= frontier
                for move in receipt_moves:
                    if (move.state != 'done' or move.company_id != line.company_id
                            or move.product_id != line.product_id
                            or move.scrap_id or move.is_inventory):
                        continue
                    # Other companies normally use a shared transit location,
                    # rather than a location with usage='supplier'. Count only
                    # the physical boundary into/out of the buyer's locations.
                    source_internal = (move.location_id.usage == 'internal'
                                       and move.location_id.company_id == line.company_id)
                    destination_internal = (move.location_dest_id.usage == 'internal'
                                            and move.location_dest_id.company_id == line.company_id)
                    direction = int(destination_internal) - int(source_internal)
                    if direction:
                        received += direction * move.product_uom._compute_quantity(
                            move.quantity, line.product_uom_id, rounding_method='HALF-UP')
            # Returns cannot turn requested demand into a negative delivery.
            # Over-delivery, however, remains visible in its true quantity.
            line.qty_supplied = max(supplied, 0.0)
            line.qty_prepared = max(prepared, 0.0)
            line.qty_received = max(received, 0.0)


class StockMove(models.Model):
    _inherit = 'stock.move'

    alvina_branch_request_line_id = fields.Many2one(
        'alvina.branch.request.line', string='Detalle de requerimiento Alviña',
        readonly=True, copy=True, index=True, ondelete='restrict')

    @api.model
    def _prepare_merge_moves_distinct_fields(self):
        return super()._prepare_merge_moves_distinct_fields() + ['alvina_branch_request_line_id']

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and any(values.get('alvina_branch_request_line_id', self.env.context.get('default_alvina_branch_request_line_id')) for values in vals_list):
            raise AccessError(_('El movimiento interno vinculado lo prepara el servicio de abastecimiento.'))
        return super().create(vals_list)

    def write(self, vals):
        if 'alvina_branch_request_line_id' in vals:
            raise AccessError(_('No se permite reasignar el origen de un movimiento interno.'))
        return super().write(vals)

    @api.constrains('alvina_branch_request_line_id', 'company_id', 'product_id')
    def _check_alvina_internal_identity(self):
        for move in self:
            line = move.sudo().alvina_branch_request_line_id
            if line and (not line.request_id.supply_flow
                         or line.request_id.operation_mode != 'internal'
                         or move.company_id != line.supply_company_id
                         or move.product_id != line.product_id):
                raise ValidationError(_(
                    'El movimiento interno debe conservar la compañía abastecedora '
                    'y el producto de su requerimiento.'))

    def _action_done(self, cancel_backorder=False):
        # Requests may exceed stock. A validated physical dispatch may not
        # silently manufacture stock in the source location.
        quantities = {}
        for move in self:
            line = move.sudo().sale_line_id.alvina_request_line_id or move.sudo().alvina_branch_request_line_id
            if not line or not line.request_id.supply_flow or not move.product_id.is_storable:
                continue
            for detail in move.move_line_ids:
                if detail.location_id.usage != 'internal' or not detail.quantity:
                    continue
                key = (detail.product_id.id, detail.location_id.id,
                       detail.lot_id.id, detail.package_id.id, detail.owner_id.id)
                quantities[key] = quantities.get(key, 0) + detail.product_uom_id._compute_quantity(
                    detail.quantity, detail.product_id.uom_id)
        for (product_id, location_id, lot_id, package_id, owner_id), amount in sorted(quantities.items()):
            quants = self.env['stock.quant'].sudo().search([
                ('product_id', '=', product_id), ('location_id', '=', location_id),
                ('lot_id', '=', lot_id), ('package_id', '=', package_id), ('owner_id', '=', owner_id)])
            if quants:
                # Serialize physical checks with Inventory's quant updates. A
                # concurrent dispatch must not pass using the same old stock.
                self.env.cr.execute('SELECT id FROM stock_quant WHERE id = ANY(%s) ORDER BY id FOR UPDATE', (quants.ids,))
                quants.invalidate_recordset(['quantity'])
            product = self.env['product.product'].sudo().browse(product_id)
            if product.uom_id.compare(sum(quants.mapped('quantity')), amount) < 0:
                raise ValidationError(_('La cantidad preparada supera las existencias físicas en la ubicación de salida. Registre una entrega parcial.'))
        return super()._action_done(cancel_backorder=cancel_backorder)


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    alvina_dispatch_note = fields.Text(string='Observaciones del despacho')
    alvina_shortage_note = fields.Text(string='Motivo de faltantes')
    alvina_request_ids = fields.Many2many('alvina.branch.request', compute='_compute_alvina_requests',
                                          store=True, string='Requerimientos de sucursal')
    alvina_branch_names = fields.Char(compute='_compute_alvina_requests', store=True, string='Sucursales')

    @api.depends('move_ids.sale_line_id.alvina_request_line_id',
                 'move_ids.purchase_line_id.alvina_request_line_id',
                 'move_ids.alvina_branch_request_line_id')
    def _compute_alvina_requests(self):
        for picking in self:
            lines = (picking.move_ids.sale_line_id.alvina_request_line_id
                     | picking.move_ids.purchase_line_id.alvina_request_line_id
                     | picking.move_ids.alvina_branch_request_line_id)
            picking.alvina_request_ids = lines.request_id
            picking.alvina_branch_names = ', '.join(lines.request_id.branch_id.mapped('name'))


class BalanceCancellation(models.Model):
    _name = 'alvina.branch.balance.cancellation'
    _description = 'Cierre auditable del saldo de un requerimiento'
    _order = 'date desc, id desc'

    request_line_id = fields.Many2one(
        'alvina.branch.request.line', required=True, ondelete='restrict',
        readonly=True, string='Detalle del requerimiento')
    company_id = fields.Many2one(
        related='request_line_id.supply_company_id', store=True,
        string='Empresa abastecedora')
    quantity = fields.Float(required=True, digits='Product Unit', string='Saldo a cerrar')
    reason = fields.Text(required=True, string='Motivo del faltante')
    user_id = fields.Many2one('res.users', readonly=True, required=True,
                             default=lambda self: self.env.user, string='Responsable')
    date = fields.Datetime(readonly=True, required=True,
                          default=fields.Datetime.now, string='Fecha')

    _quantity_positive = models.Constraint('CHECK(quantity > 0)', 'La cantidad debe ser positiva.')

    @api.model_create_multi
    def create(self, vals_list):
        planned = {}
        for values in vals_list:
            if set(values) - {'request_line_id', 'quantity', 'reason'}:
                raise AccessError(_('Campos de cierre de saldo no autorizados.'))
            line = self.env['alvina.branch.request.line'].browse(
                values.get('request_line_id')).exists()
            if not line:
                raise ValidationError(_('Seleccione un detalle de requerimiento existente.'))
            line._alvina_check_balance_cancellation_access()
            # Serialize competing cancellations and refresh the stored balance
            # after waiting for any earlier transaction on this same detail.
            line.flush_recordset(['product_qty', 'qty_supplied', 'qty_cancelled', 'qty_pending'])
            self.env.cr.execute(
                'SELECT id FROM alvina_branch_request_line WHERE id = %s FOR UPDATE',
                [line.id])
            line.invalidate_recordset(['cancellation_ids', 'qty_cancelled', 'qty_pending'])
            try:
                quantity = validated_quantity(values.get('quantity'), line.product_uom_id.rounding)
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
            remaining = line.qty_pending - planned.get(line.id, 0)
            if quantity <= 0 or line.product_uom_id.compare(quantity, remaining) > 0:
                raise ValidationError(_('El cierre debe ser positivo y no superar el saldo pendiente.'))
            reason = str(values.get('reason') or '').strip()
            if not reason:
                raise ValidationError(_('Indique el motivo del faltante.'))
            moves = line.sudo()._alvina_delivery_moves() | line.sudo().purchase_line_ids.move_ids
            if moves.filtered(lambda move: move.state not in ('done', 'cancel')):
                raise ValidationError(_(
                    'Cancele primero las operaciones pendientes de este producto desde Inventario. '
                    'El cierre de saldo no cancela ni valida movimientos automáticamente.'))
            planned[line.id] = planned.get(line.id, 0) + quantity
            values.update(quantity=quantity, reason=reason,
                          user_id=self.env.uid, date=fields.Datetime.now())
        clean_context = {key: value for key, value in self.env.context.items()
                         if not key.startswith('default_')}
        return super(BalanceCancellation, self.with_context(clean_context)).create(vals_list)

    def write(self, vals):
        raise AccessError(_('Los cierres de saldo forman parte del historial y no se modifican.'))

    def unlink(self):
        raise AccessError(_('Los cierres de saldo forman parte del historial y no se eliminan.'))
