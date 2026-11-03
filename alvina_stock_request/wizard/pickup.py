import base64
import io
import hashlib
import re
import qrcode
from odoo import api, fields, models, _
from odoo.exceptions import UserError, AccessError


class QrWizard(models.TransientModel):
    _name = 'alvina.stock.qr.wizard'
    _description = 'Códigos de retiro (confidenciales)'
    _transient_max_hours = 1

    request_id = fields.Many2one('alvina.stock.request', string='Solicitud', required=True, readonly=True)
    company_id = fields.Many2one(string='Empresa', related='request_id.company_id', store=True)
    code = fields.Char(string='Código de retiro', required=True, readonly=True)
    image = fields.Binary(string='QR de retiro', compute='_compute_image')
    # Old transient receipts legitimately have no 1D value. New receipts always
    # fill it in create(); avoid a NOT NULL failure while retaining old QR popups.
    code128_code = fields.Char(string='Identificador Code 128', readonly=True)
    code128_image = fields.Binary(string='Código de barras Code 128', compute='_compute_code128_image')
    department_id = fields.Many2one(related='request_id.department_id', string='Área solicitante', readonly=True)
    line_ids = fields.One2many(related='request_id.line_ids', string='Productos solicitados', readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if set(vals) - {'request_id'}:
                raise AccessError(_('Los códigos los genera el servidor.'))
            request_id = vals.get('request_id', self.env.context.get('default_request_id'))
            if not isinstance(request_id, int) or request_id <= 0:
                raise UserError(_('Seleccione una solicitud propia enviada.'))
            request = self.env['alvina.stock.request'].browse(request_id)
            request.check_access('read')
            if request.requester_id != self.env.user or request.state != 'submitted':
                raise AccessError(_('Solo puede consultar el QR de su solicitud pendiente.'))
            request._ensure_code128()
            secured = request.sudo()
            if not secured.token_uid or not secured.token_secret:
                raise UserError(_('Esta solicitud no tiene un QR vigente.'))
            vals.update(request_id=request.id,
                        code=f'alvina-stock-request:{secured.token_uid}:{secured.token_secret}',
                        code128_code=secured.code128_secret)
        context = {key: value for key, value in self.env.context.items() if not key.startswith('default_')}
        return super(QrWizard, self.with_context(context)).create(vals_list)

    def write(self, vals):
        raise AccessError(_('El asistente QR es de solo lectura.'))

    @api.depends('code')
    def _compute_image(self):
        for wizard in self:
            stream = io.BytesIO()
            qrcode.make(wizard.code).save(stream, format='PNG')
            wizard.image = base64.b64encode(stream.getvalue())

    @api.depends('code128_code')
    def _compute_code128_image(self):
        for wizard in self:
            wizard.code128_image = base64.b64encode(self.env['ir.actions.report'].barcode(
                'Code128', wizard.code128_code, width=900, height=180, quiet=1, humanreadable=1,
            )) if wizard.code128_code else False


class PickupWizard(models.TransientModel):
    _name = 'alvina.stock.pickup.wizard'
    _description = 'Revisar y autorizar retiro'
    _check_company_auto = True

    code = fields.Char(required=True, string='Código escaneado')
    company_id = fields.Many2one('res.company', string='Empresa', required=True, readonly=True, default=lambda s: s.env.company)
    request_id = fields.Many2one('alvina.stock.request', string='Solicitud', readonly=True, check_company=True)
    requester_id = fields.Many2one(string='Solicitante', related='request_id.requester_id', readonly=True)
    department_id = fields.Many2one(string='Área solicitante', related='request_id.department_id', readonly=True)
    source_location_id = fields.Many2one(string='Ubicación de origen', related='request_id.source_location_id', readonly=True)
    destination_location_id = fields.Many2one(string='Ubicación de destino', related='request_id.destination_location_id', readonly=True)
    line_ids = fields.One2many(string='Productos solicitados', related='request_id.line_ids', readonly=True)
    state = fields.Selection(related='request_id.state', string='Estado de solicitud', readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        allowed = {'code', 'company_id'}
        for vals in vals_list:
            if set(vals) - allowed:
                raise AccessError(_('El resultado del escaneo no puede asignarse manualmente.'))
            vals['company_id'] = vals.get('company_id', self.env.company.id)
            if vals['company_id'] not in self.env.companies.ids:
                raise AccessError(_('Empresa no autorizada.'))
        context = {key: value for key, value in self.env.context.items()
                   if not key.startswith('default_') or key[8:] in allowed}
        return super(PickupWizard, self.with_context(context)).create(vals_list)

    def write(self, vals):
        if set(vals) - {'code'}:
            raise AccessError(_('Solo puede cambiar el código; debe volver a revisarlo antes de autorizar.'))
        return super().write(dict(vals, request_id=False))

    def _lookup_request(self):
        self.ensure_one()
        self.check_access('write')
        if not self.env.user.has_group('alvina_stock_request.group_warehouse') or self.env.user._is_public():
            raise AccessError(_('Se requiere un almacenero autenticado.'))
        code = (self.code or '').strip()
        if re.fullmatch(r'[0-9]{40}', code):
            digest = hashlib.sha256(code.encode('ascii')).hexdigest()
            found = self.env['alvina.stock.request'].sudo().search([('code128_digest', '=', digest)], limit=1)
            if not found:
                raise UserError(_('Código inválido, anulado o ya utilizado.'))
            request = self.env['alvina.stock.request'].browse(found.id)
            request._verify_code128(code)
            return request, None, code, 'code128'
        parts = code.split(':')
        if len(parts) != 3 or parts[0] != 'alvina-stock-request' or len(parts[1]) != 36 or not re.fullmatch(r'[A-Za-z0-9_-]{43}', parts[2]):
            raise UserError(_('Formato inválido. Use el QR completo o los 40 dígitos del Code 128.'))
        # Only the lookup bypasses field visibility. Stock operations use normal ACLs.
        found = self.env['alvina.stock.request'].sudo().search([('token_uid', '=', parts[1])], limit=1)
        if not found:
            raise UserError(_('QR inválido, anulado o ya utilizado.'))
        request = self.env['alvina.stock.request'].browse(found.id)
        request._verify_qr(parts[1], parts[2])
        return request, parts[1], parts[2], 'qr'

    def action_scan(self):
        self.ensure_one()
        request, token_identity, token, code_type = self._lookup_request()
        # Review is advisory and creates no stock reservation. Authorization acquires
        # the row lock and revalidates the QR and live stock in its own transaction.
        super(PickupWizard, self.sudo()).write({
            'request_id': request.id, 'company_id': request.company_id.id,
        })
        request.line_ids.invalidate_recordset(['available_quantity'])
        request.message_post(body=_('Código revisado por %s para comprobar productos y existencias; entrega pendiente.',
                                   self.env.user.display_name))
        return {'type': 'ir.actions.act_window', 'res_model': self._name,
                'res_id': self.id, 'view_mode': 'form', 'target': 'new'}

    def action_redeem(self):
        self.ensure_one()
        self.check_access('write')
        if not self.request_id:
            raise UserError(_('Lea el código y revise los productos y existencias antes de autorizar.'))
        request, token_identity, token, code_type = self._lookup_request()
        if request != self.request_id:
            raise UserError(_('El código cambió. Debe escanear y revisar nuevamente.'))
        request._redeem(token_identity, token, code_type=code_type)
        self.unlink()
        return {'type': 'ir.actions.act_window_close'}


class CancelWizard(models.TransientModel):
    _name = 'alvina.stock.cancel.wizard'
    _description = 'Anular solicitud con motivo'

    request_id = fields.Many2one('alvina.stock.request', string='Solicitud', required=True, readonly=True)
    company_id = fields.Many2one(string='Empresa', related='request_id.company_id', store=True)
    reason = fields.Text(string='Motivo de anulación', required=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if set(vals) - {'request_id', 'reason'}:
                raise AccessError(_('Campos del asistente no autorizados.'))
            request_id = vals.get('request_id', self.env.context.get('default_request_id'))
            if not isinstance(request_id, int) or request_id <= 0:
                raise UserError(_('Seleccione una solicitud propia pendiente.'))
            request = self.env['alvina.stock.request'].browse(request_id)
            request.check_access('write')
            if request.requester_id != self.env.user or request.state not in ('draft', 'submitted'):
                raise AccessError(_('Solo puede anular una solicitud propia que aún no haya sido entregada ni anulada.'))
            vals['request_id'] = request.id
        context = {key: value for key, value in self.env.context.items()
                   if not key.startswith('default_') or key == 'default_reason'}
        return super(CancelWizard, self.with_context(context)).create(vals_list)

    def write(self, vals):
        if set(vals) - {'reason'}:
            raise AccessError(_('Solo puede cambiar el motivo de anulación.'))
        return super().write(vals)

    def action_cancel(self):
        self.ensure_one()
        self.check_access('write')
        self.request_id._cancel(self.reason)
        return {'type': 'ir.actions.act_window_close'}
