from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

class BranchBinding(models.Model):
    _name = 'alvina.branch.binding'
    _description = 'Vínculo con sucursal existente (no crea sucursales)'
    _rec_name = 'name'

    name = fields.Char(compute='_compute_name', string='Sucursal')
    active = fields.Boolean(default=True, string='Activo')
    resource_ref = fields.Reference(selection='_reference_models', required=True, string='Sucursal existente')
    company_id = fields.Many2one('res.company', string='Empresa solicitante', required=True,
                                 default=lambda self: self.env.company)
    supply_company_id = fields.Many2one('res.company', string='Empresa abastecedora', required=True,
                                        default=lambda self: self.env.company)
    warehouse_id = fields.Many2one('stock.warehouse', string='Almacén abastecedor', required=True)
    user_ids = fields.Many2many('res.users', string='Usuarios autorizados')
    generate_purchase_rfq = fields.Boolean(
        string='Crear solicitud de cotización', default=False,
        help='Solo para nuevos envíos. Crea una cotización en borrador en la empresa solicitante.')
    purchase_picking_type_id = fields.Many2one(
        'stock.picking.type', string='Recepción de la sucursal',
        help='Operación de recepción de la empresa solicitante, distinta del almacén abastecedor.')
    _check_company_auto = True
    _unique_resource = models.Constraint('UNIQUE(resource_ref)', 'La sucursal ya está vinculada.')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('warehouse_id') and 'supply_company_id' not in vals:
                warehouse = self.env['stock.warehouse'].browse(vals['warehouse_id'])
                warehouse.check_access('read')
                vals['supply_company_id'] = warehouse.company_id.id
        return super().create(vals_list)

    @api.model
    def _reference_models(self):
        # The supplied Alviña code represents branches as existing contacts.
        # An integration addon may extend this method for another verified model.
        # Never expose every model: a reference name is rendered for requesters.
        return [('res.partner', _('Contacto de sucursal existente'))]

    def _resource_is_available(self):
        """Recheck a configured contact that may have changed since assignment."""
        self.ensure_one()
        target = self.sudo().resource_ref
        allowed_models = {model for model, _label in self._reference_models()}
        return bool(
            target and target._name in allowed_models and target.exists()
            and ('active' not in target._fields or target.active)
            and ('company_id' not in target._fields or not target.company_id
                 or target.company_id in (self.company_id | self.supply_company_id))
        )

    @api.depends('resource_ref')
    def _compute_name(self):
        allowed_models = {model for model, _label in self._reference_models()}
        for rec in self:
            target = rec.sudo().resource_ref
            rec.name = target.display_name if target and target._name in allowed_models and target.exists() else _('Sucursal no disponible')

    @api.constrains('resource_ref', 'company_id', 'supply_company_id', 'warehouse_id', 'user_ids')
    def _check_binding(self):
        for rec in self:
            target = rec.resource_ref
            if not target or not target.exists():
                raise ValidationError(_('Seleccione una sucursal existente.'))
            target.check_access('read')
            if 'active' in target._fields and not target.active:
                raise ValidationError(_('La sucursal vinculada está archivada.'))
            # A partner's company owns its commercial record; it is not necessarily
            # the legal entity that requests the products.
            if 'company_id' in target._fields and target.company_id and target.company_id not in (rec.company_id | rec.supply_company_id):
                raise ValidationError(_('El contacto debe estar compartido o pertenecer a la empresa solicitante o abastecedora.'))
            if rec.warehouse_id.company_id != rec.supply_company_id:
                raise ValidationError(_('El almacén debe pertenecer a la empresa abastecedora.'))
            for user in rec.user_ids:
                if rec.company_id in user.company_ids:
                    continue
                if user.has_group('alvina_branch_requisition.group_keeper') and rec.supply_company_id in user.company_ids:
                    continue
                if user.has_group('sales_team.group_sale_salesman') and rec.supply_company_id in user.company_ids:
                    continue
                raise ValidationError(_('El solicitante debe tener acceso a la empresa solicitante; el almacenero, a la solicitante o abastecedora.'))

    def write(self, vals):
        if 'purchase_picking_type_id' in vals:
            changed = self.filtered(lambda branch: branch.purchase_picking_type_id.id != vals['purchase_picking_type_id'])
            if changed and self.env['alvina.branch.request'].sudo().search_count([
                    ('branch_id', 'in', changed.ids), ('supply_flow', '=', True)]):
                raise ValidationError(_('Cambiar la recepción de una sucursal con historial del flujo Industria requiere una migración controlada.'))
        if set(vals) & {'resource_ref', 'company_id', 'supply_company_id', 'warehouse_id'}:
            if self.env['alvina.branch.request'].sudo().search_count([('branch_id', 'in', self.ids)]):
                raise ValidationError(_('Cambiar una sucursal o almacén con historial requiere una migración controlada.'))
        return super().write(vals)

    @api.constrains('generate_purchase_rfq', 'purchase_picking_type_id', 'company_id', 'supply_company_id')
    def _check_purchase_configuration(self):
        for rec in self:
            if not rec.generate_purchase_rfq:
                continue
            # Portal requesters can submit without general inventory access.
            # Read only the operation already chosen by the administrator.
            operation = rec.sudo().purchase_picking_type_id
            if not operation or not operation.active or operation.code != 'incoming' or operation.company_id != rec.company_id:
                raise ValidationError(_('Seleccione una recepción activa de la empresa solicitante para crear cotizaciones.'))
            vendor = rec.supply_company_id.sudo().partner_id
            if not vendor.active or (vendor.company_id and vendor.company_id != rec.company_id):
                raise ValidationError(_('El contacto proveedor de la empresa abastecedora debe estar activo y disponible para la empresa solicitante.'))

class ProductTemplate(models.Model):
    _inherit = 'product.template'
    alvina_requisition_enabled = fields.Boolean(string='Habilitado para requerimientos Alviña', default=False, groups='alvina_branch_requisition.group_admin')
