"""Create only explicitly configured draft RFQs after a validated submission."""
from datetime import datetime, time

from odoo import api, Command, fields, models, _
from odoo.exceptions import ValidationError


class BranchRequestPurchase(models.Model):
    _inherit = 'alvina.branch.request'

    purchase_order_ids = fields.One2many('purchase.order', 'alvina_branch_request_id', readonly=True)
    purchase_order_id = fields.Many2one(
        'purchase.order', compute='_compute_purchase_order', store=True, readonly=True,
        string='Solicitud de cotización', copy=False)

    @api.depends('purchase_order_ids')
    def _compute_purchase_order(self):
        for rec in self:
            rec.purchase_order_id = rec.purchase_order_ids[:1]

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            if rec.supply_flow:
                rec._create_supply_documents()
            elif rec.branch_id.generate_purchase_rfq:
                rec._create_purchase_rfq()
        return records

    def _create_purchase_rfq(self):
        self.ensure_one()
        self.check_access('read')
        # This private method is called only after the real user's request has
        # passed assignment, company, product, quantity and duplicate checks.
        if (self.user_id != self.env.user or self.state != 'pending'
                or self.env.uid not in self.branch_id.sudo().user_ids.ids
                or self.company_id not in self.env.companies):
            raise ValidationError(_('Requerimiento no autorizado para crear una cotización.'))
        branch = self.branch_id
        if not branch.generate_purchase_rfq:
            raise ValidationError(_('La creación de cotizaciones no está habilitada para esta sucursal.'))
        branch._check_purchase_configuration()
        if self.sudo().purchase_order_id:
            return self.purchase_order_id
        for line in self.line_ids:
            product = line.product_id.sudo()
            if product.company_id or not product.purchase_ok:
                raise ValidationError(_(
                    'Para crear una cotización entre empresas, los productos deben estar compartidos y habilitados para compras. '
                    'No se modifican ni duplican productos automáticamente.'))
        # Discard default_* and Website contexts before the narrow elevated write.
        # No standard purchase permissions or user company assignments are changed.
        company = self.company_id.sudo()
        vendor = self.supply_company_id.sudo().partner_id
        order_model = self.env['purchase.order'].sudo().with_context({
            'allowed_company_ids': company.ids, 'lang': self.env.lang,
            'tracking_disable': True, 'mail_create_nosubscribe': True,
            'mail_create_nolog': True,
        }).with_company(company)
        commands = []
        for line in self.line_ids:
            product = line.product_id.sudo().with_company(company)
            taxes = product.supplier_taxes_id._filter_taxes_by_company(company)
            commands.append(Command.create({
                'product_id': product.id, 'name': product.display_name,
                'product_qty': line.product_qty, 'product_uom_id': line.product_uom_id.id,
                'price_unit': line.price_unit, 'technical_price_unit': line.price_unit,
                'discount': 0, 'tax_ids': [Command.set(taxes.ids)],
                'date_planned': datetime.combine(self.required_date, time(12)),
            }))
        # Atomic with the request transaction: an invalid RFQ rolls back the
        # submission. A unique reverse link prevents another RFQ for this request.
        return order_model.create({
            'alvina_branch_request_id': self.id, 'company_id': company.id,
            'partner_id': vendor.id, 'currency_id': self.currency_id.id,
            'picking_type_id': branch.purchase_picking_type_id.id,
            'state': 'draft', 'user_id': False, 'origin': self.name,
            'date_order': self.request_date, 'order_line': commands,
        })

    def action_cancel(self):
        self._manager()
        if any(rec.sudo().purchase_order_id and rec.sudo().purchase_order_id.state != 'cancel' for rec in self):
            raise ValidationError(_('Cancele primero la solicitud de cotización vinculada desde Compras.'))
        return super().action_cancel()


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    alvina_branch_request_id = fields.Many2one(
        'alvina.branch.request', string='Requerimiento de sucursal',
        readonly=True, copy=False, ondelete='restrict', index=True)
    _alvina_request_unique = models.Constraint(
        'UNIQUE(alvina_branch_request_id)', 'El requerimiento ya tiene una solicitud de cotización.')

    @api.model_create_multi
    def create(self, vals_list):
        # Only the private, elevated bridge may attach an RFQ to an immutable
        # request. Ordinary purchase users keep the unmodified standard workflow.
        if not self.env.su and any(
                vals.get('alvina_branch_request_id', self.env.context.get('default_alvina_branch_request_id'))
                for vals in vals_list):
            raise ValidationError(_('El vínculo con el requerimiento lo genera el formulario.'))
        return super().create(vals_list)

    def write(self, vals):
        if 'alvina_branch_request_id' in vals:
            raise ValidationError(_('No se permite reasignar el requerimiento de una cotización.'))
        return super().write(vals)

    @api.constrains('alvina_branch_request_id', 'company_id', 'partner_id')
    def _check_alvina_identity(self):
        for order in self:
            request = order.sudo().alvina_branch_request_id
            if request and (order.company_id != request.company_id
                            or order.partner_id != request.supply_company_id.partner_id):
                raise ValidationError(_('La cotización vinculada debe conservar la empresa solicitante y su proveedor abastecedor.'))

    def unlink(self):
        self.check_access('unlink')
        if any(order.sudo().alvina_branch_request_id for order in self):
            raise ValidationError(_('Cancele la cotización vinculada en lugar de eliminar su trazabilidad.'))
        return super().unlink()
