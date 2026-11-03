from odoo import api, fields, models, _
from odoo.exceptions import AccessError

from .request import _FLOW


def guard(records):
    if records.env.context.get('_alvina_flow') is not _FLOW:
        protected = records.sudo().filtered(lambda r: r.alvina_request_id)
        if records._name == 'stock.move.line':
            protected |= records.sudo().filtered(lambda r: r.move_id.alvina_request_id)
        if protected:
            raise AccessError(records.env._('Este traslado está controlado por una solicitud ALVIÑA. Use sus acciones y el QR de retiro.'))


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    alvina_request_id = fields.Many2one('alvina.stock.request', string='Solicitud de insumos', readonly=True, copy=False,
                                      check_company=True, ondelete='restrict', index=True)
    _alvina_unique_request = models.Constraint('UNIQUE(alvina_request_id)', 'La solicitud ya tiene un traslado.')

    @api.model_create_multi
    def create(self, vals_list):
        if any(v.get('alvina_request_id', self.env.context.get('default_alvina_request_id')) for v in vals_list) and self.env.context.get('_alvina_flow') is not _FLOW:
            raise AccessError(_('Solo una solicitud puede crear su traslado.'))
        records = super().create(vals_list)
        guard(records)
        return records

    def write(self, vals):
        guard(self)
        if vals.get('alvina_request_id') and self.env.context.get('_alvina_flow') is not _FLOW:
            raise AccessError(_('No se puede vincular manualmente una solicitud.'))
        return super().write(vals)

    def button_validate(self):
        guard(self)
        return super().button_validate()

    def action_cancel(self):
        guard(self)
        return super().action_cancel()

    def action_confirm(self):
        guard(self)
        return super().action_confirm()

    def action_assign(self):
        guard(self)
        return super().action_assign()

    def do_unreserve(self):
        guard(self)
        return super().do_unreserve()

    def unlink(self):
        guard(self)
        return super().unlink()


class StockMove(models.Model):
    _inherit = 'stock.move'

    alvina_request_id = fields.Many2one(related='picking_id.alvina_request_id', string='Solicitud de insumos')
    alvina_request_line_id = fields.Many2one('alvina.stock.request.line', string='Línea de solicitud', readonly=True,
                                           copy=False, check_company=True, ondelete='restrict')

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get('_alvina_flow') is not _FLOW:
            guard(self.env['stock.picking'].browse([v.get('picking_id', self.env.context.get('default_picking_id'))
                  for v in vals_list if v.get('picking_id', self.env.context.get('default_picking_id'))]))
            if any(v.get('alvina_request_line_id', self.env.context.get('default_alvina_request_line_id')) for v in vals_list):
                raise AccessError(_('No se puede asignar una línea de solicitud manualmente.'))
        records = super().create(vals_list)
        guard(records)
        if self.env.context.get('_alvina_flow') is not _FLOW and records.sudo().alvina_request_line_id:
            raise AccessError(_('No se puede asignar una línea de solicitud manualmente.'))
        return records

    def write(self, vals):
        guard(self)
        if self.env.context.get('_alvina_flow') is not _FLOW:
            if vals.get('picking_id'):
                guard(self.env['stock.picking'].browse(vals['picking_id']))
            if vals.get('alvina_request_line_id'):
                raise AccessError(_('No se puede asignar una línea de solicitud manualmente.'))
        return super().write(vals)

    def unlink(self):
        guard(self)
        return super().unlink()


class StockMoveLine(models.Model):
    _inherit = 'stock.move.line'

    alvina_request_id = fields.Many2one(related='picking_id.alvina_request_id', string='Solicitud de insumos')

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get('_alvina_flow') is not _FLOW:
            guard(self.env['stock.picking'].browse([v.get('picking_id', self.env.context.get('default_picking_id'))
                  for v in vals_list if v.get('picking_id', self.env.context.get('default_picking_id'))]))
            guard(self.env['stock.move'].browse([v.get('move_id', self.env.context.get('default_move_id'))
                  for v in vals_list if v.get('move_id', self.env.context.get('default_move_id'))]))
        records = super().create(vals_list)
        guard(records)
        return records

    def write(self, vals):
        guard(self)
        if self.env.context.get('_alvina_flow') is not _FLOW:
            if vals.get('picking_id'):
                guard(self.env['stock.picking'].browse(vals['picking_id']))
            if vals.get('move_id'):
                guard(self.env['stock.move'].browse(vals['move_id']))
        return super().write(vals)

    def unlink(self):
        guard(self)
        return super().unlink()
