"""Explicit central supply configuration; never changes company assignments."""
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class SupplyConfiguration(models.Model):
    _name = 'alvina.supply.configuration'
    _description = 'Abastecimiento central de Industria'

    key = fields.Char(default='industry', required=True, readonly=True, copy=False)
    name = fields.Char(default='Abastecimiento Industria', required=True)
    enabled = fields.Boolean(string='Activar flujo de abastecimiento', default=False)
    company_id = fields.Many2one(
        'res.company', string='Industria Panificadora', required=True,
        help='Seleccione la compañía real que abastece a todas las sucursales.')
    sales_user_id = fields.Many2one(
        'res.users', string='Responsable de ventas de Industria',
        help='Seleccione al responsable existente. Si está ausente o archivado, se usará '
             'el único usuario activo de Logística de Industria autorizado en esa compañía. '
             'No se modifican permisos ni asignaciones de compañías.')
    pricelist_id = fields.Many2one(
        'product.pricelist', string='Lista de precios de Industria',
        help='Lista existente, activa, compartida o de Industria, con una moneda activa.')

    _single_configuration = models.Constraint(
        'UNIQUE(key)', 'Solo puede existir una configuración de abastecimiento de Industria.')
    _industry_key = models.Constraint(
        "CHECK(key = 'industry')", 'La configuración de abastecimiento debe ser la de Industria.')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('key', 'industry') != 'industry':
                raise ValidationError(_('La configuración de abastecimiento debe ser la de Industria.'))
            vals['key'] = 'industry'
        return super().create(vals_list)

    def write(self, vals):
        if 'key' in vals and vals['key'] != 'industry':
            raise ValidationError(_('La configuración de abastecimiento debe ser la de Industria.'))
        if 'company_id' in vals and any(rec.company_id.id != vals['company_id'] for rec in self):
            if self.env['alvina.branch.request'].sudo().search_count([('supply_flow', '=', True)]):
                raise ValidationError(_(
                    'Cambiar Industria con historial de abastecimiento requiere una migración controlada.'))
        return super().write(vals)

    @api.model
    def _get_configuration(self):
        """Internal lookup; configuration is never created from a submission."""
        return self.sudo().search([('key', '=', 'industry')], limit=1)

    def _assert_supported(self):
        """Installed native automation is consumed via its actual PO result."""
        self.ensure_one()
        if self.env['ir.module.module'].sudo().search_count([
                ('name', '=', 'account_inter_company_rules'),
                ('state', 'in', ['to upgrade', 'to remove'])]):
            raise ValidationError(_(
                'Complete la actualización o eliminación pendiente del automatismo '
                'interempresa de Odoo antes de activar el abastecimiento.'))
        return True

    def _native_intercompany_installed(self):
        return bool(self.env['ir.module.module'].sudo().search_count([
            ('name', '=', 'account_inter_company_rules'), ('state', '=', 'installed')]))

    def _get_sales_responsible(self):
        """Resolve an explicit assignee, or one unambiguous authorized successor.

        Resolution never changes the saved selection or a user's companies. An
        active but invalid selection is a configuration error, not a reason to
        silently move responsibility to someone else.
        """
        self.ensure_one()
        configuration = self.sudo()
        selected = configuration.sales_user_id.with_context(active_test=False).exists()
        if selected and selected.active:
            if (selected.share or configuration.company_id not in selected.company_ids
                    or not selected.has_group('sales_team.group_sale_salesman')):
                raise ValidationError(_(
                    'El responsable seleccionado debe ser un usuario interno activo con '
                    'acceso existente a Industria y permiso de Ventas. Revise su selección; '
                    'no se amplían sus permisos automáticamente.'))
            return selected
        logistics = self.env.ref('alvina_branch_requisition.group_industry_logistics')
        candidates = self.env['res.users'].sudo().search([
            ('active', '=', True), ('share', '=', False),
            ('company_ids', 'in', configuration.company_id.ids),
            ('all_group_ids', 'in', logistics.ids),
        ])
        candidates = candidates.filtered(
            lambda user: user.has_group('sales_team.group_sale_salesman'))
        if len(candidates) != 1:
            raise ValidationError(_(
                'Seleccione un responsable activo de ventas de Industria. Cuando no hay '
                'un responsable activo seleccionado, debe existir exactamente un usuario '
                'activo de Logística de Industria autorizado en esa compañía; '
                'se encontraron %(count)s.', count=len(candidates)))
        return candidates

    @api.constrains('enabled', 'company_id', 'sales_user_id', 'pricelist_id')
    def _check_configuration(self):
        for rec in self:
            if not rec.enabled:
                continue
            rec._assert_supported()
            rec._get_sales_responsible()
            if not rec.company_id.account_fiscal_country_id:
                raise ValidationError(_('Configure el país fiscal real de Industria antes de activar el abastecimiento.'))
            pricelist = rec.sudo().pricelist_id
            if (not pricelist or not pricelist.active
                    or pricelist.company_id and pricelist.company_id != rec.company_id
                    or not pricelist.currency_id.active):
                raise ValidationError(_(
                    'Seleccione una lista de precios activa, compartida o de Industria, '
                    'con una moneda activa.'))

    def _validate_for_branch(self, branch):
        """Validate only the explicit companies, contacts and operations supplied."""
        self.ensure_one()
        rec = self.sudo()
        branch = branch.sudo()
        branch.ensure_one()
        if not rec.enabled:
            raise ValidationError(_('El flujo de abastecimiento de Industria no está activo.'))
        rec._check_configuration()
        if not branch.active or not branch.branch_code or not branch._resource_is_available():
            raise ValidationError(_(
                'Configure una sucursal activa, su identificación obligatoria y su contacto existente.'))
        if branch.supply_company_id != rec.company_id:
            raise ValidationError(_('Todas las sucursales deben solicitar sus insumos a Industria.'))
        responsible = rec._get_sales_responsible()
        if (responsible.id not in branch.user_ids.ids
                and not responsible.has_group('alvina_branch_requisition.group_industry_logistics')):
            raise ValidationError(_('Asigne al responsable de ventas de Industria a esta sucursal.'))
        if (not branch.warehouse_id.active
                or branch.warehouse_id.company_id != rec.company_id):
            raise ValidationError(_('El almacén abastecedor debe estar activo y pertenecer a Industria.'))
        if branch.company_id == rec.company_id:
            raise ValidationError(_(
                'Caso interno pendiente de aprobación: esta sucursal comparte compañía con Industria. '
                'No se crearán compras ni ventas interempresa consigo misma.'))
        if not branch.company_id.account_fiscal_country_id:
            raise ValidationError(_('Configure el país fiscal real de la compañía solicitante antes de enviar el requerimiento.'))
        # Use the real buyer company's commercial partner. Its standard
        # intercompany stock properties also identify the shared transit path.
        # The branch contact remains its separate operational identification.
        customer = branch.company_id.partner_id
        if (not customer.active
                or customer.company_id and customer.company_id != rec.company_id):
            raise ValidationError(_(
                'El contacto comercial de la compañía compradora debe estar disponible para Industria. '
                'No se comparte ni modifica automáticamente ningún contacto.'))
        vendor = rec.company_id.partner_id
        if (not vendor.active
                or vendor.company_id and vendor.company_id != branch.company_id):
            raise ValidationError(_(
                'El contacto proveedor de Industria debe estar activo y disponible '
                'para la compañía solicitante.'))
        operation = branch.purchase_picking_type_id
        if (not operation or not operation.active or operation.code != 'incoming'
                or operation.company_id != branch.company_id):
            raise ValidationError(_('Seleccione una recepción activa de la empresa solicitante.'))
        branch._check_purchase_configuration()
        return True


class BranchBinding(models.Model):
    _inherit = 'alvina.branch.binding'

    branch_code = fields.Char(
        string='Identificación de sucursal', copy=False,
        help='Código único y obligatorio para el nuevo flujo de abastecimiento. No se inventa automáticamente.')
    internal_location_id = fields.Many2one(
        'stock.location', string='Ubicación interna de la sucursal',
        help='Ubicación real existente para sucursales que comparten compañía con Industria.')

    _branch_code_unique = models.Constraint(
        'UNIQUE(branch_code)', 'La identificación de sucursal ya está en uso.')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if 'branch_code' in vals:
                vals['branch_code'] = (vals['branch_code'] or '').strip().upper() or False
        return super().create(vals_list)

    def write(self, vals):
        if 'branch_code' in vals:
            vals = dict(vals)
            vals['branch_code'] = (vals['branch_code'] or '').strip().upper() or False
            changed = self.filtered(lambda rec: (rec.branch_code or False) != vals['branch_code'])
            if changed and self.env['alvina.branch.request'].sudo().search_count([
                    ('branch_id', 'in', changed.ids), ('supply_flow', '=', True)]):
                raise ValidationError(_(
                    'Cambiar la identificación de una sucursal con abastecimientos requiere una migración controlada.'))
        return super().write(vals)
