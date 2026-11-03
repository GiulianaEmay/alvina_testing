from odoo import fields, models, _
from odoo.exceptions import AccessError, ValidationError
from .validation import suggested

class Consolidation(models.TransientModel):
    _name = 'alvina.branch.consolidation'
    _description = 'Consulta actual de necesidades de compra'
    line_ids = fields.One2many('alvina.branch.consolidation.line', 'wizard_id', readonly=True)

    def action_branch_detail(self):
        self.ensure_one()
        self.check_access('read')
        if not self.env.user.has_group('alvina_branch_requisition.group_keeper'):
            raise AccessError(_('Acción reservada al almacenero.'))
        return {'type': 'ir.actions.act_window', 'name': _('Pendientes por sucursal'),
                'res_model': 'alvina.branch.request.line', 'view_mode': 'list',
                'domain': [('request_id.state', 'in', ['pending', 'review', 'partial']),
                           ('qty_pending', '>', 0), '|',
                           ('request_id.sale_order_id', '=', False),
                           ('request_id.sale_order_id.state', '!=', 'cancel')],
                'context': {'group_by': ['supply_company_id', 'warehouse_id', 'branch_id', 'product_id', 'product_uom_id'],
                            'create': False, 'edit': False, 'delete': False}}

    def action_refresh(self):
        self.ensure_one()
        if not self.env.user.has_group('alvina_branch_requisition.group_keeper'):
            raise AccessError(_('Acción reservada al almacenero.'))
        self.line_ids.unlink()
        lines = self.env['alvina.branch.request.line'].search([
            ('request_id.state', 'in', ['pending', 'review', 'partial']), ('qty_pending', '>', 0),
            '|', ('request_id.sale_order_id', '=', False), ('request_id.sale_order_id.state', '!=', 'cancel'),
            ('supply_company_id', 'in', self.env.companies.ids)])
        groups = {}
        for line in lines:
            if line.product_uom_id != line.product_id.uom_id:
                raise ValidationError(_('La unidad de un producto cambió después del requerimiento. Revise sus solicitudes antes de consolidar.'))
            key = (line.product_id, line.warehouse_id, line.supply_company_id)
            groups.setdefault(key, self.env['alvina.branch.request.line'])
            groups[key] |= line
        for (product, warehouse, company), detail in groups.items():
            stock_keys = {'location', 'warehouse', 'warehouse_id', 'search_warehouse',
                          'search_location', 'from_date', 'to_date', 'lot_id', 'owner_id',
                          'package_id', 'owners', 'strict'}
            stock_context = {key: value for key, value in self.env.context.items() if key not in stock_keys}
            stock = product.with_context(stock_context).with_company(company).with_context(
                warehouse_id=warehouse.id, allowed_company_ids=company.ids)
            demand = sum(detail.mapped('qty_pending'))
            free, incoming = stock.free_qty, stock.incoming_qty
            # free_qty already excludes reservations, including reservations
            # for the very requests being consolidated. Credit those once,
            # otherwise confirming a sale would manufacture a new shortage.
            moves = detail.filtered('request_id.supply_flow').sale_line_ids.move_ids
            reserved_lines = self.env['stock.move.line'].search([
                ('move_id', 'in', moves.ids), ('state', 'not in', ['draft', 'done', 'cancel']),
                ('company_id', '=', company.id), ('product_id', '=', product.id),
                ('location_id', 'child_of', warehouse.view_location_id.id),
                ('location_id.usage', '=', 'internal'),
            ]) if product.is_storable and moves else self.env['stock.move.line']
            reserved = min(demand, sum(reserved_lines.mapped('quantity_product_uom')),
                           max(stock.qty_available - free, 0), max(stock.qty_available, 0))
            self.env['alvina.branch.consolidation.line'].create({
                'wizard_id': self.id, 'product_id': product.id, 'warehouse_id': warehouse.id,
                'company_id': company.id, 'product_qty': demand, 'free_qty': free,
                'incoming_qty': incoming, 'reserved_qty': reserved,
                'suggested_qty': suggested(demand - reserved, free, incoming),
                'source_line_ids': [(6, 0, detail.ids)]})
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id,
                'view_mode': 'form', 'target': 'current'}

class ConsolidationLine(models.TransientModel):
    _name = 'alvina.branch.consolidation.line'
    _description = 'Necesidad por producto y almacén'
    wizard_id = fields.Many2one('alvina.branch.consolidation', required=True, ondelete='cascade')
    product_id = fields.Many2one('product.product', readonly=True)
    product_uom_id = fields.Many2one(related='product_id.uom_id')
    warehouse_id = fields.Many2one('stock.warehouse', readonly=True)
    company_id = fields.Many2one('res.company', string='Empresa abastecedora', readonly=True)
    product_qty = fields.Float(string='Pendiente', readonly=True, digits='Product Unit')
    free_qty = fields.Float(string='Stock libre', readonly=True, digits='Product Unit')
    incoming_qty = fields.Float(string='Entradas confirmadas', readonly=True, digits='Product Unit')
    reserved_qty = fields.Float(string='Reservado para estos requerimientos', readonly=True, digits='Product Unit')
    suggested_qty = fields.Float(string='Compra orientativa', readonly=True, digits='Product Unit')
    source_line_ids = fields.Many2many('alvina.branch.request.line', readonly=True)
