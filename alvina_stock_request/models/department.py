from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError, UserError


class Department(models.Model):
    _name = 'alvina.stock.department'
    _description = 'Departamento receptor de insumos'
    _check_company_auto = True

    name = fields.Char(string='Nombre del área', required=True)
    active = fields.Boolean(string='Activo', default=True)
    company_id = fields.Many2one('res.company', string='Empresa', required=True, default=lambda s: s.env.company)
    warehouse_id = fields.Many2one('stock.warehouse', string='Almacén de insumos', required=True, check_company=True,
                                  domain="[('code', '=', 'INS'), ('company_id', '=', company_id)]")
    picking_type_id = fields.Many2one('stock.picking.type', string='Tipo de traslado interno', required=True, check_company=True,
                                     domain="[('code', '=', 'internal'), ('warehouse_id', '=', warehouse_id)]")
    location_id = fields.Many2one('stock.location', string='Ubicación de destino', required=True, check_company=True,
                                 domain="[('usage', '=', 'internal'), ('company_id', '=', company_id)]")
    source_location_id = fields.Many2one(related='warehouse_id.lot_stock_id', string='Ubicación de origen', readonly=True)

    @api.constrains('warehouse_id', 'picking_type_id', 'location_id', 'company_id')
    def _check_routes(self):
        for dep in self:
            if (dep.warehouse_id.code != 'INS'
                    or dep.warehouse_id.company_id != dep.company_id
                    or dep.picking_type_id.code != 'internal'
                    or dep.picking_type_id.warehouse_id != dep.warehouse_id
                    or dep.picking_type_id.company_id != dep.company_id
                    or not dep.picking_type_id.active
                    or dep.location_id.company_id != dep.company_id
                    or dep.location_id.usage != 'internal'
                    or not dep.location_id.active
                    or dep.source_location_id.name != 'Existencias'
                    or dep.source_location_id.location_id != dep.warehouse_id.view_location_id
                    or dep.source_location_id.usage != 'internal'
                    or dep.source_location_id.company_id != dep.company_id
                    or dep.location_id == dep.source_location_id):
                raise ValidationError(_('Use el almacén INS, su traslado interno existente y un destino interno de la misma empresa distinto de Existencias.'))

    def write(self, vals):
        if 'company_id' in vals and vals['company_id'] not in self.env.companies.ids:
            raise AccessError(_('Empresa no autorizada.'))
        if {'warehouse_id', 'picking_type_id', 'location_id', 'company_id'} & vals.keys():
            # Requests snapshot the route; prevent silent rerouting of active documents.
            if self.env['alvina.stock.request'].sudo().search_count([
                    ('department_id', 'in', self.ids), ('state', '=', 'submitted')]):
                raise UserError(_('Hay solicitudes activas para este departamento.'))
        return super().write(vals)
