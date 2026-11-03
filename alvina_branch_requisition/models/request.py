from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError
from contextvars import ContextVar
import math
_CREATING = ContextVar("alvina_creating", default=False)

from .validation import CATEGORIES, pending, quantity
from .dates import minimum_required_date

class BranchRequest(models.Model):
    _name = 'alvina.branch.request'
    _description = 'Requerimiento de sucursal'
    _order = 'request_date desc, id desc'
    _check_company_auto = True

    name = fields.Char(string='Referencia', default='Nuevo', readonly=True, copy=False)
    user_id = fields.Many2one('res.users', string='Solicitante', required=True, default=lambda self: self.env.user, readonly=True)
    branch_id = fields.Many2one('alvina.branch.binding', string='Sucursal', required=True, check_company=True, ondelete='restrict')
    company_id = fields.Many2one(related='branch_id.company_id', store=True, string='Empresa solicitante')
    supply_company_id = fields.Many2one(related='branch_id.supply_company_id', store=True, string='Empresa abastecedora')
    warehouse_id = fields.Many2one(related='branch_id.warehouse_id', store=True, string='Almacén abastecedor')
    request_date = fields.Datetime(string='Fecha de solicitud', default=fields.Datetime.now, required=True, readonly=True)
    required_date = fields.Date(string='Fecha requerida', required=True, default=lambda self: self._minimum_required_date())
    note = fields.Text(string='Observaciones')
    state = fields.Selection([('pending', 'Pendiente'), ('review', 'En revisión'),
        ('partial', 'Parcialmente atendido'), ('done', 'Atendido'), ('cancel', 'Cancelado')], string='Estado', default='pending', required=True, readonly=True)
    line_ids = fields.One2many('alvina.branch.request.line', 'request_id', copy=False)
    currency_id = fields.Many2one(related='company_id.currency_id', store=True, string='Moneda')
    amount_total = fields.Monetary(compute='_compute_amount_total', store=True, string='Valor solicitado')
    price_snapshot_set = fields.Boolean(compute='_compute_amount_total', store=True,
                                        string='Precio registrado al solicitar')
    submission_key = fields.Char(required=True, readonly=True, copy=False)
    _submission_unique = models.Constraint('UNIQUE(submission_key)', 'Este formulario ya fue enviado.')

    @api.depends('line_ids.price_subtotal', 'line_ids.price_snapshot_set')
    def _compute_amount_total(self):
        for rec in self:
            rec.amount_total = sum(rec.line_ids.mapped('price_subtotal'))
            rec.price_snapshot_set = bool(rec.line_ids) and all(rec.line_ids.mapped('price_snapshot_set'))

    def _submission_values(self, branch):
        """Server-only extension point, after identity and assignment checks."""
        return {}

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if set(vals) - {'branch_id', 'required_date', 'note', 'submission_key', 'line_ids',
                            'state', 'user_id', 'name', 'request_date'}:
                raise AccessError(_('Campos de solicitud no autorizados.'))
            branch = self.env['alvina.branch.binding'].browse(vals.get('branch_id')).exists()
            if (not branch or not branch.active
                    or self.env.uid not in branch.sudo().user_ids.ids
                    or branch.company_id not in self.env.companies):
                raise AccessError(_('Sucursal no autorizada.'))
            if not branch._resource_is_available():
                raise ValidationError(_('La sucursal vinculada ya no está disponible en esta compañía.'))
            if vals.get('state', self.env.context.get('default_state', 'pending')) != 'pending' or vals.get('user_id', self.env.uid) != self.env.uid:
                raise AccessError(_('Datos de solicitud no autorizados.'))
            if not vals.get('line_ids'):
                raise ValidationError(_('Ingrese al menos un producto.'))
            vals.update(user_id=self.env.uid, state='pending', company_id=branch.company_id.id,
                        supply_company_id=branch.supply_company_id.id, warehouse_id=branch.warehouse_id.id,
                        request_date=fields.Datetime.now(),
                        name=self.env['ir.sequence'].sudo().next_by_code('alvina.branch.request'))
            vals.update(self._submission_values(branch))
        token = _CREATING.set(True)
        try:
            clean_context = {key: value for key, value in self.env.context.items()
                             if not key.startswith('default_')}
            records = super(BranchRequest, self.with_context(clean_context)).create(vals_list)
            if any(not rec.line_ids for rec in records):
                raise ValidationError(_('Ingrese al menos un producto.'))
            return records
        finally:
            _CREATING.reset(token)

    @api.model
    def _minimum_required_date(self):
        return minimum_required_date(fields.Datetime.now())

    @api.constrains('required_date')
    def _check_date(self):
        minimum = self._minimum_required_date()
        for rec in self:
            if not rec.required_date or rec.required_date < minimum:
                raise ValidationError(_(
                    'La fecha requerida debe ser el %(date)s o posterior '
                    '(mínimo dos días calendario según la hora de Perú).',
                    date=minimum.strftime('%d/%m/%Y')))

    def write(self, vals):
        raise AccessError(_('Los requerimientos enviados son inmutables; use las acciones autorizadas.'))

    def _set_supply_configuration(self, configuration):
        """Only the authorized send service calls this inside its savepoint."""
        self.ensure_one()
        return super(BranchRequest, self.sudo()).write({
            'supply_flow': True, 'supply_config_id': configuration.id,
            'operation_mode': 'intercompany',
        })

    def unlink(self):
        raise AccessError(_('Archive la sucursal o cancele el requerimiento; no elimine su historial.'))

    def _manager(self):
        self.check_access('write')
        if not self.env.user.has_group('alvina_branch_requisition.group_keeper'):
            raise AccessError(_('Acción reservada al almacenero.'))

    def action_review(self):
        self._manager()
        if any(r.state != 'pending' for r in self):
            raise ValidationError(_('Solo puede revisar solicitudes pendientes.'))
        return super().write({'state': 'review'})

    def action_cancel(self):
        self._manager()
        if any(r.state not in ('pending', 'review') or any(r.line_ids.mapped('qty_supplied')) for r in self):
            raise ValidationError(_('No se puede cancelar una solicitud atendida.'))
        return super().write({'state': 'cancel'})

class RequestLine(models.Model):
    _name = 'alvina.branch.request.line'
    _description = 'Detalle de requerimiento'
    _check_company_auto = True

    request_id = fields.Many2one('alvina.branch.request', required=True, ondelete='cascade', check_company=True)
    branch_id = fields.Many2one(related='request_id.branch_id', store=True)
    company_id = fields.Many2one(related='request_id.company_id', store=True, string='Empresa solicitante')
    supply_company_id = fields.Many2one(related='request_id.supply_company_id', store=True, string='Empresa abastecedora')
    warehouse_id = fields.Many2one(related='request_id.warehouse_id', store=True, string='Almacén abastecedor')
    product_id = fields.Many2one('product.product', string='Producto', required=True)
    product_uom_id = fields.Many2one('uom.uom', string='Unidad', required=True, readonly=True, ondelete='restrict')
    product_qty = fields.Float(string='Cantidad solicitada', required=True, digits='Product Unit')
    qty_supplied = fields.Float(default=0, readonly=True, digits='Product Unit')
    qty_pending = fields.Float(string='Pendiente por despachar', compute='_compute_pending', store=True, digits='Product Unit')
    currency_id = fields.Many2one(related='request_id.currency_id', store=True, string='Moneda')
    price_unit = fields.Float(string='Precio de venta registrado', readonly=True, digits='Product Price')
    price_snapshot_set = fields.Boolean(default=False, readonly=True, string='Precio registrado al solicitar')
    price_subtotal = fields.Monetary(compute='_compute_price_subtotal', store=True, string='Valor solicitado')
    _product_unique = models.Constraint('UNIQUE(request_id, product_id)', 'No repita productos.')
    _positive = models.Constraint('CHECK(product_qty > 0 AND qty_supplied >= 0 AND qty_supplied <= product_qty)', 'Cantidades inválidas.')

    @api.depends('product_qty', 'qty_supplied')
    def _compute_pending(self):
        for line in self:
            line.qty_pending = pending(line.product_qty, line.qty_supplied)

    @api.depends('price_unit', 'product_qty', 'currency_id')
    def _compute_price_subtotal(self):
        for line in self:
            line.price_subtotal = line.currency_id.round(line.price_unit * line.product_qty) if line.currency_id else 0

    def _snapshot_price(self, product, request, qty):
        company = request.company_id
        sale_product = product.with_context({})
        return sale_product.currency_id._convert(
            sale_product.lst_price, company.currency_id, company,
            fields.Date.to_date(request.request_date), round=False)

    @api.model_create_multi
    def create(self, vals_list):
        if not _CREATING.get():
            raise AccessError(_('Agregue líneas únicamente al enviar un requerimiento nuevo.'))
        for vals in vals_list:
            if set(vals) - {'request_id', 'product_id', 'product_qty'}:
                raise AccessError(_('Campos de detalle no autorizados.'))
            product = self.env['product.product'].sudo().browse(vals.get('product_id')).exists()
            if not product:
                raise ValidationError(_('Producto inexistente.'))
            try:
                q = quantity(vals.get('product_qty'), product.uom_id.rounding)
                if not q:
                    raise ValueError('La cantidad debe ser positiva')
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
            vals['product_uom_id'] = product.uom_id.id
            request = self.env['alvina.branch.request'].browse(vals.get('request_id'))
            if request.user_id != self.env.user or request.state != 'pending':
                raise AccessError(_('Requerimiento no autorizado.'))
            # Capture the actual product sale price (including variant extras),
            # in its source currency. Never substitute the inventory cost or a
            # vendor price. Shared products use the main company's sale currency.
            price = self._snapshot_price(product, request, q)
            if not math.isfinite(price) or price < 0:
                raise ValidationError(_('El producto tiene un precio de venta inválido.'))
            vals.update(price_unit=price, price_snapshot_set=True)
        return super().create(vals_list)

    @api.constrains('product_id', 'product_qty', 'request_id', 'supply_company_id')
    def _validate_product(self):
        for line in self:
            p = line.product_id.sudo()
            if not p.active or not p.product_tmpl_id.active or not p.alvina_requisition_enabled or p.categ_id.name not in CATEGORIES:
                raise ValidationError(_('Producto no habilitado o categoría no permitida.'))
            if p.company_id and p.company_id != line.supply_company_id:
                raise ValidationError(_('El producto debe estar compartido o pertenecer a la empresa abastecedora.'))
            try:
                quantity(line.product_qty, p.uom_id.rounding)
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc

    def write(self, vals):
        raise AccessError(_('El detalle enviado es inmutable.'))

    def unlink(self):
        raise AccessError(_('El detalle enviado es inmutable.'))
