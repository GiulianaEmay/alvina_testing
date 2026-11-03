import hashlib
import math
import re
import secrets
import uuid

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.float_utils import float_compare

# An identity marker cannot be forged through a JSON-RPC context. Never use a boolean bypass.
_FLOW = object()


class StockRequest(models.Model):
    _name = 'alvina.stock.request'
    _description = 'Solicitud interna de insumos'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _check_company_auto = True
    _order = 'id desc'

    name = fields.Char(string='Referencia', default='Nuevo', required=True, readonly=True, copy=False)
    company_id = fields.Many2one('res.company', string='Empresa', required=True, default=lambda s: s.env.company)
    requester_id = fields.Many2one('res.users', string='Solicitante', required=True, default=lambda s: s.env.user, readonly=True,
                                    help='Usuario que crea la solicitud; se asigna automáticamente.')
    department_id = fields.Many2one('alvina.stock.department', string='Área solicitante', required=True, check_company=True)
    line_ids = fields.One2many('alvina.stock.request.line', 'request_id', string='Productos solicitados', copy=True)
    note = fields.Text(string='Motivo')
    state = fields.Selection([('draft', 'Borrador'), ('submitted', 'Solicitada'),
                              ('done', 'Entregada'),
                              ('cancelled', 'Anulada')], default='draft', required=True,
                             string='Estado', readonly=True, copy=False, tracking=True)
    picking_id = fields.Many2one('stock.picking', string='Transferencia interna', readonly=True, copy=False, check_company=True,
                                 groups='alvina_stock_request.group_warehouse')
    source_location_id = fields.Many2one('stock.location', string='Ubicación de origen', readonly=True, copy=False, check_company=True,
                                        help='Se asigna automáticamente al enviar la solicitud.')
    destination_location_id = fields.Many2one('stock.location', string='Ubicación de destino', readonly=True, copy=False, check_company=True,
                                             help='Se asigna automáticamente según el área al enviar la solicitud.')
    picking_type_id = fields.Many2one('stock.picking.type', string='Tipo de traslado interno', readonly=True, copy=False, check_company=True,
                                     help='Se asigna automáticamente según el área al enviar la solicitud.')
    submitted_at = fields.Datetime(string='Fecha de solicitud', readonly=True, copy=False)
    delivered_by = fields.Many2one('res.users', string='Entregado por', readonly=True, copy=False)
    delivered_at = fields.Datetime(string='Fecha de entrega', readonly=True, copy=False)
    cancelled_by = fields.Many2one('res.users', string='Anulado por', readonly=True, copy=False)
    cancelled_at = fields.Datetime(string='Fecha de anulación', readonly=True, copy=False)
    cancellation_reason = fields.Text(string='Motivo de anulación', readonly=True, copy=False)
    token_secret = fields.Char(string='Secreto QR', readonly=True, copy=False, groups='base.group_no_one')
    token_digest = fields.Char(string='Resumen criptográfico QR', readonly=True, copy=False, groups='base.group_no_one')
    token_uid = fields.Char(string='Identificador QR', readonly=True, copy=False, groups='base.group_no_one')
    code128_secret = fields.Char(string='Secreto Code 128', readonly=True, copy=False, groups='base.group_no_one')
    code128_digest = fields.Char(string='Resumen criptográfico Code 128', readonly=True, copy=False,
                                 index=True, groups='base.group_no_one')

    _picking_unique = models.Constraint('UNIQUE(picking_id)', 'Un traslado solo puede pertenecer a una solicitud.')
    _token_uid_unique = models.Constraint('UNIQUE(token_uid)', 'Identificador QR duplicado.')
    _code128_digest_unique = models.Constraint('UNIQUE(code128_digest)', 'Identificador Code 128 duplicado.')

    @api.model
    def _new_code128_values(self):
        # 10**40 equiprobable values provide ~133 bits. Digits allow compact
        # Code128 set C; leading zeros are part of the credential.
        secret = f'{secrets.randbelow(10**40):040d}'
        return {'code128_secret': secret,
                'code128_digest': hashlib.sha256(secret.encode('ascii')).hexdigest()}

    def _ensure_code128(self):
        """Idempotent legacy backfill; never rotate a printed credential."""
        self.ensure_one()
        self._lock()
        secured = self.sudo()
        if self.state != 'submitted':
            raise UserError(_('Esta solicitud no tiene códigos vigentes.'))
        try:
            valid_uid = str(uuid.UUID(secured.token_uid or '')) == secured.token_uid
        except ValueError:
            valid_uid = False
        qr_secret = secured.token_secret or ''
        qr_digest = secured.token_digest or ''
        if (not valid_uid or not re.fullmatch(r'[A-Za-z0-9_-]{43}', qr_secret)
                or not secrets.compare_digest(qr_digest, hashlib.sha256(qr_secret.encode()).hexdigest())):
            raise UserError(_('Esta solicitud no tiene un QR vigente.'))
        secret, digest = secured.code128_secret, secured.code128_digest
        if not secret and not digest:
            self._set(self._new_code128_values())
        elif (not secret or not digest or not re.fullmatch(r'[0-9]{40}', secret)
              or not secrets.compare_digest(digest, hashlib.sha256(secret.encode()).hexdigest())):
            raise UserError(_('La credencial Code 128 es inconsistente; contacte al administrador.'))
        return True

    def _warehouse_check(self):
        if self.env.user._is_public() or not self.env.user.has_group('alvina_stock_request.group_warehouse'):
            raise AccessError(_('Solo un almacenero autenticado puede autorizar el retiro.'))
        self.check_access('write')

    def _lock(self):
        self.check_access('write')
        # Flush before locking, then discard potentially stale ORM values. Under Odoo's
        # REPEATABLE READ a conflicting transaction is retried by its RPC dispatcher.
        self.flush_recordset()
        for record in self.sorted('id'):
            self.env.cr.execute('SELECT id FROM alvina_stock_request WHERE id = %s FOR UPDATE', [record.id])
        self.invalidate_recordset()
        self.check_access('write')

    def _set(self, vals):
        return super(StockRequest, self.sudo()).write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        allowed = {'company_id', 'department_id', 'line_ids', 'note'}
        for vals in vals_list:
            if set(vals) - allowed:
                raise AccessError(_('No se pueden asignar estado, solicitante ni datos de auditoría.'))
            company_id = vals.get('company_id', self.env.company.id)
            if company_id not in self.env.companies.ids:
                raise AccessError(_('Empresa no autorizada.'))
            vals['name'] = self.env['ir.sequence'].with_company(company_id).next_by_code('alvina.stock.request') or 'Nuevo'
            vals['requester_id'] = self.env.user.id
            vals['state'] = 'draft'
            vals['company_id'] = company_id
        safe_context = {key: value for key, value in self.env.context.items()
                        if not key.startswith('default_') or key[8:] in allowed}
        return super(StockRequest, self.with_context(safe_context)).create(vals_list)

    def write(self, vals):
        if set(vals) - {'department_id', 'company_id', 'line_ids', 'note'}:
            raise AccessError(_('Los campos de control solo se actualizan mediante acciones autorizadas.'))
        self._lock()
        if 'company_id' in vals and vals['company_id'] not in self.env.companies.ids:
            raise AccessError(_('Empresa no autorizada.'))
        if any(r.state != 'draft' or r.requester_id != self.env.user for r in self):
            raise AccessError(_('Solo el solicitante puede editar su borrador.'))
        return super().write(vals)

    def unlink(self):
        self._lock()
        if any(r.state != 'draft' or r.requester_id != self.env.user for r in self):
            raise AccessError(_('Solo se pueden eliminar borradores propios.'))
        return super().unlink()

    def action_submit(self):
        self.ensure_one()
        self._lock()
        if self.state != 'draft' or self.requester_id != self.env.user:
            raise UserError(_('Solo el solicitante puede enviar su borrador.'))
        dep = self.department_id
        dep._check_routes()
        if not dep.active or not self.line_ids:
            raise UserError(_('Seleccione un departamento activo y al menos un producto.'))
        self.line_ids._check_quantity()
        token = secrets.token_urlsafe(32)
        self._set({**self._new_code128_values(), 'state': 'submitted', 'submitted_at': fields.Datetime.now(),
                   'token_secret': token, 'token_digest': hashlib.sha256(token.encode()).hexdigest(),
                   'token_uid': str(uuid.uuid4()),
                   'source_location_id': dep.source_location_id.id,
                   'destination_location_id': dep.location_id.id,
                   'picking_type_id': dep.picking_type_id.id})
        self.message_post(body=_('Solicitud enviada; QR y Code 128 disponibles para el solicitante hasta la entrega o anulación.'))
        return self.action_show_qr()

    def _check_full_reservation(self):
        self.ensure_one()
        self.department_id._check_routes()
        picking = self.picking_id
        moves = picking.move_ids
        if (picking.state != 'assigned' or picking.company_id != self.company_id
                or self.source_location_id != self.department_id.source_location_id
                or self.destination_location_id != self.department_id.location_id
                or self.picking_type_id != self.department_id.picking_type_id
                or len(moves) != len(self.line_ids)
                or picking.location_id != self.source_location_id
                or picking.location_dest_id != self.destination_location_id
                or picking.picking_type_id != self.picking_type_id):
            raise UserError(_('El traslado debe estar completamente reservado y conservar la ruta solicitada.'))
        for line in self.line_ids:
            move = moves.filtered(lambda m: m.alvina_request_line_id == line)
            if (len(move) != 1 or move.product_id != line.product_id
                    or move.product_uom != line.product_uom_id
                    or move.location_id != self.source_location_id
                    or move.location_dest_id != self.destination_location_id
                    or float_compare(move.product_uom_qty, line.quantity, precision_rounding=line.product_uom_id.rounding)
                    or float_compare(move.quantity, line.quantity, precision_rounding=line.product_uom_id.rounding)):
                raise UserError(_('No se permiten retiros parciales ni cambios de producto o cantidad.'))

    def _create_and_reserve_picking(self):
        self.ensure_one()
        self.department_id._check_routes()
        if not self.picking_id:
            picking = self.env['stock.picking'].with_context(_alvina_flow=_FLOW).create({
                'company_id': self.company_id.id, 'picking_type_id': self.picking_type_id.id,
                'move_type': 'one',
                'location_id': self.source_location_id.id,
                'location_dest_id': self.destination_location_id.id,
                'origin': self.name, 'alvina_request_id': self.id,
                'move_ids': [fields.Command.create({
                    'product_id': line.product_id.id, 'product_uom_qty': line.quantity,
                    'product_uom': line.product_uom_id.id,
                    'location_id': self.source_location_id.id,
                    'location_dest_id': self.destination_location_id.id,
                    'company_id': self.company_id.id, 'alvina_request_line_id': line.id,
                }) for line in self.line_ids],
            })
            self._set({'picking_id': picking.id})
            picking.action_confirm()
        self.picking_id.with_context(_alvina_flow=_FLOW).action_assign()
        if (self.picking_id.state != 'assigned'
                or any(move.state != 'assigned' for move in self.picking_id.move_ids)):
            raise UserError(_('Existencias insuficientes. No se entregó stock ni se consumieron los códigos; puede volver a intentarlo tras reponer.'))
        self._check_full_reservation()

    def action_show_qr(self):
        self.ensure_one()
        self.check_access('read')
        if self.requester_id != self.env.user or self.state != 'submitted':
            raise AccessError(_('Solo el solicitante puede consultar los códigos de su solicitud pendiente.'))
        wizard = self.env['alvina.stock.qr.wizard'].create({'request_id': self.id})
        return {'type': 'ir.actions.act_window', 'res_model': wizard._name,
                'res_id': wizard.id, 'view_mode': 'form', 'target': 'new',
                'context': {'dialog_size': 'large'}}

    def _verify_qr(self, token_identity, token):
        self.ensure_one()
        self._warehouse_check()
        secured = self.sudo()
        if (self.state != 'submitted' or secured.token_uid != token_identity
                or not secured.token_digest
                or not secrets.compare_digest(secured.token_digest, hashlib.sha256(token.encode()).hexdigest())):
            raise UserError(_('QR inválido, anulado o ya utilizado.'))

    def _verify_code128(self, token):
        self.ensure_one()
        self._warehouse_check()
        secured = self.sudo()
        digest, stored_secret = secured.code128_digest, secured.code128_secret
        if (self.state != 'submitted' or not isinstance(token, str)
                or not re.fullmatch(r'[0-9]{40}', token) or not digest
                or not stored_secret or not re.fullmatch(r'[0-9]{40}', stored_secret)
                or not secrets.compare_digest(stored_secret, token)
                or not secrets.compare_digest(digest, hashlib.sha256(token.encode('ascii')).hexdigest())):
            raise UserError(_('Código inválido, anulado o ya utilizado.'))

    def _redeem(self, token_identity, token, code_type='qr'):
        self.ensure_one()
        self._warehouse_check()
        self._lock()
        if code_type == 'qr':
            self._verify_qr(token_identity, token)
        elif code_type == 'code128':
            self._verify_code128(token)
        else:
            raise UserError(_('Tipo de código inválido.'))
        with self.env.cr.savepoint():
            self.line_ids._check_quantity()
            self._create_and_reserve_picking()
            picking = self.picking_id.with_context(_alvina_flow=_FLOW)
            picking.move_ids.write({'picked': True})
            picking.button_validate()
            if picking.state != 'done':
                raise UserError(_('Odoo requiere completar la información de lotes/series antes del retiro.'))
            self._set({'state': 'done', 'delivered_by': self.env.user.id,
                       'delivered_at': fields.Datetime.now(), 'token_digest': False,
                       'token_uid': False, 'token_secret': False,
                       'code128_secret': False, 'code128_digest': False})
            self.message_post(body=_('Retiro autorizado por %s. QR y Code 128 invalidados; traslado %s completado.',
                                     self.env.user.display_name, picking.name))
            return True

    def _cancel(self, reason):
        self.ensure_one()
        # Cancellation and delivery share the row lock; recheck the winner's
        # state before invalidating the QR or writing cancellation audit data.
        self._lock()
        if self.requester_id != self.env.user:
            raise AccessError(_('Solo el solicitante puede anular su solicitud.'))
        if self.state not in ('draft', 'submitted'):
            raise UserError(_('Solo se pueden anular solicitudes que aún no hayan sido entregadas ni anuladas.'))
        if not isinstance(reason, str) or not reason.strip():
            raise UserError(_('Indique el motivo de anulación.'))
        with self.env.cr.savepoint():
            self._set({'state': 'cancelled', 'cancelled_by': self.env.user.id,
                       'cancelled_at': fields.Datetime.now(), 'cancellation_reason': reason.strip(),
                       'token_digest': False, 'token_uid': False, 'token_secret': False,
                       'code128_secret': False, 'code128_digest': False})
            self.message_post(body=_('Solicitud anulada por %s. Motivo: %s. QR y Code 128 de retiro invalidados.',
                                     self.env.user.display_name, reason.strip()))
        return True


class StockRequestLine(models.Model):
    _name = 'alvina.stock.request.line'
    _description = 'Línea de solicitud interna'
    _check_company_auto = True

    request_id = fields.Many2one('alvina.stock.request', string='Solicitud', required=True, ondelete='cascade')
    company_id = fields.Many2one(related='request_id.company_id', string='Empresa', store=True)
    product_id = fields.Many2one('product.product', string='Producto', required=True, check_company=True,
                                domain="[('is_storable', '=', True)]")
    product_uom_id = fields.Many2one(related='product_id.uom_id', string='Unidad de medida', readonly=True, store=True)
    quantity = fields.Float(string='Cantidad', required=True, default=1, digits='Product Unit')

    available_quantity = fields.Float(compute='_compute_available_quantity', digits='Product Unit',
                                      groups='alvina_stock_request.group_warehouse', string='Existencias disponibles')

    @api.depends('product_id', 'request_id.source_location_id')
    @api.depends_context('allowed_company_ids')
    def _compute_available_quantity(self):
        for line in self:
            line.available_quantity = 0
            if line.product_id and line.request_id.source_location_id:
                line.available_quantity = self.env['stock.quant']._get_available_quantity(
                    line.product_id, line.request_id.source_location_id)

    _positive_quantity = models.Constraint('CHECK(quantity > 0)', 'La cantidad debe ser positiva.')
    _unique_product = models.Constraint('UNIQUE(request_id, product_id)', 'Use una sola línea por producto.')

    @api.constrains('product_id', 'quantity')
    def _check_quantity(self):
        for line in self:
            if (not line.product_id.is_storable or not line.product_id.active
                    or not math.isfinite(line.quantity) or line.quantity <= 0
                    or float_compare(line.quantity, 0, precision_rounding=line.product_uom_id.rounding) <= 0):
                raise ValidationError(_('Seleccione un producto almacenable activo y una cantidad positiva.'))

    def _editable(self, requests):
        requests._lock()
        if any(r.state != 'draft' or r.requester_id != self.env.user for r in requests):
            raise AccessError(_('Las líneas solo se editan en borradores propios.'))

    @api.model_create_multi
    def create(self, vals_list):
        if any(set(v) - {'request_id', 'product_id', 'quantity'} for v in vals_list):
            raise AccessError(_('Campos de línea no autorizados.'))
        for vals in vals_list:
            request_id = vals.get('request_id', self.env.context.get('default_request_id'))
            if not isinstance(request_id, int) or request_id <= 0:
                raise ValidationError(_('Debe indicar una solicitud válida para la línea.'))
            vals['request_id'] = request_id
            quantity = vals.get('quantity', self.env.context.get('default_quantity', 1))
            vals['quantity'] = quantity
            if not isinstance(quantity, (int, float)) or not math.isfinite(quantity) or quantity <= 0:
                raise ValidationError(_('La cantidad debe ser finita y positiva.'))
        requests = self.env['alvina.stock.request'].browse([v.get('request_id') for v in vals_list])
        self._editable(requests)
        safe_context = {key: value for key, value in self.env.context.items()
                        if not key.startswith('default_') or key[8:] in {'request_id', 'product_id', 'quantity'}}
        return super(StockRequestLine, self.with_context(safe_context)).create(vals_list)

    def write(self, vals):
        if set(vals) - {'product_id', 'quantity'}:
            raise AccessError(_('No se permite reasignar líneas.'))
        if 'quantity' in vals and (not isinstance(vals['quantity'], (int, float))
                                   or not math.isfinite(vals['quantity']) or vals['quantity'] <= 0):
            raise ValidationError(_('La cantidad debe ser finita y positiva.'))
        self._editable(self.request_id)
        return super().write(vals)

    def unlink(self):
        self._editable(self.request_id)
        return super().unlink()
