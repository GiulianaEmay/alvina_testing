from odoo import fields, api, models 
from odoo.exceptions import UserError

from dateutil.relativedelta import relativedelta
from datetime import datetime, time
import pytz 

import logging 

_logger = logging.getLogger(__name__)
class SaleOrder(models.Model):
    _inherit = "sale.order"

    def unlink(self):
        for rec in self:
            for line in rec.order_line:
                if line.mrp_client_line_id and line.mrp_client_line_id.mrp_production_id.state == 'draft':
                    mrp = line.mrp_client_line_id.mrp_production_id
                    line.mrp_client_line_id.unlink()
                    mrp.calculate_product_quantity()

        return super().unlink()

    def write(self, vals):
        res = super().write(vals)
        _logger.info(f"vals wirte {vals}")
        if 'partner_id' in vals:
            for order in self:
                _logger.info(f"Order {order} - partner {order.partner_id}")
                for line in order.order_line:
                    _logger.info(f"Order line {line}")

                    if line.mrp_client_line_id and line.mrp_client_line_id.mrp_production_id.state == 'draft':
                        _logger.info(f"Updated")
                        line.mrp_client_line_id.partner_id = order.partner_id

        if 'commitment_date' in vals:
            for order in self:
                if order.is_created_mrp:
                    for line in order.order_line:
                        if line.mrp_client_line_id and line.mrp_client_line_id.mrp_production_id.state == 'draft':
                            line.mrp_client_line_id.mrp_production_id.date_start = vals['commitment_date']

                elif order.is_from_mrp:
                    ProductionLineContact = self.env['mrp.production.contact.line'].sudo()
                    Production = self.env['mrp.production'].sudo()

                    mrp_categ = self.env['mrp.category.product'].search([
                        ('name', '=', order.origin)
                    ], limit=1)
                    user_tz = pytz.timezone(self.env.user.tz or 'UTC')
                    dt = fields.Datetime.context_timestamp(self, order.commitment_date)
                    start_local = datetime.combine(dt.date(), time.min)
                    end_local = start_local + relativedelta(days=1)
                    start_utc = user_tz.localize(start_local).astimezone(pytz.UTC).replace(tzinfo=None)
                    end_utc = user_tz.localize(end_local).astimezone(pytz.UTC).replace(tzinfo=None)
                    if mrp_categ:
                        for line in order.order_line:
                            if not line.mrp_client_line_id or line.mrp_client_line_id.mrp_production_id.state != 'draft':
                                continue
                            
                            #find new production and move lines
                            prod = Production.with_company(order.company_id).search([
                                ('date_start', '>=', start_utc),
                                ('date_start', '<=', end_utc),
                                ('product_id', '=', line.product_id),
                                ('category_product_id', '=', mrp_categ.id),
                                ('state', '=', 'draft')
                            ], limit=1)
                            if prod:
                                line.mrp_client_line_id.mrp_production_id = prod 
                            else:
                                #create new prod 
                                prod = Production.create({
                                    'product_id': line.product_id.id,
                                    'product_qty': 1,
                                    'origin': f'Portal / pedido de producción {mrp_categ.name}', 
                                    'date_start': order.commitment_date or fields.Datetime.now(),
                                    'company_id': order.company_id.id,
                                    'category_product_id': mrp_categ.id,
                                })
                                line.mrp_client_line_id.mrp_production_id = prod



        if 'origin' in vals:
            for order in self:
                if order.is_created_mrp:
                    self.env['mrp.production'].search([
                        ('sale_order_id', '=', order.id),
                    ]).write({'origin': vals['origin']})
                else:
                    Production = self.env['mrp.production'].sudo()

                    line_mrps = self.env['mrp.production.contact.line']
                    mrp_categ = self.env['mrp.category.product'].search([
                        ('name', '=', vals['origin'])
                    ], limit=1)
                    if mrp_categ:
                        user_tz = pytz.timezone(self.env.user.tz or 'UTC')
                        dt = fields.Datetime.context_timestamp(self, order.commitment_date)
                        start_local = datetime.combine(dt.date(), time.min)
                        end_local = start_local + relativedelta(days=1)
                        start_utc = user_tz.localize(start_local).astimezone(pytz.UTC).replace(tzinfo=None)
                        end_utc = user_tz.localize(end_local).astimezone(pytz.UTC).replace(tzinfo=None)
                        for line in order.order_line:
                            if not line.mrp_client_line_id or line.mrp_client_line_id.mrp_production_id.state != 'draft' or not line.product_id:
                                continue
                            
                            #find new production and move lines
                            prod = Production.with_company(order.company_id).search([
                                ('date_start', '>=', start_utc),
                                ('date_start', '<=', end_utc),
                                ('product_id', '=', line.product_id),
                                ('category_product_id', '=', mrp_categ.id),
                                ('state', '=', 'draft')
                            ], limit=1)
                            if prod:
                                line.mrp_client_line_id.mrp_production_id = prod 
                            else:
                                #create new prod 
                                prod = Production.create({
                                    'product_id': line.product_id.id,
                                    'product_qty': 1,
                                    'origin': f'Portal / pedido de producción {mrp_categ.name}', 
                                    'date_start': order.commitment_date or fields.Datetime.now(),
                                    'company_id': order.company_id.id,
                                    'category_product_id': mrp_categ.id,
                                })
                                line.mrp_client_line_id.mrp_production_id = prod 

        return res 

    def action_create_mrp_production(self):
        self.ensure_one()

        if self.state != 'draft':
            raise UserError("La orden debe estar en borrador para crear las ordenes de fabricación")

        if self.already_created_mrp:
            raise UserError("Ya se crearon ordenes de producción ")

        if not self.order_line.filtered(lambda line: line.product_id.type == 'consu'):
            raise UserError("No tiene ningún producto para crear ordenes de fabricación ")
        MrpProduction = self.env['mrp.production'].sudo()
        MrpProductionLine = self.env['mrp.production.contact.line'].sudo()
        
        for line in self.order_line:
            if line.product_id.type != 'consu':
                continue

            if line.mrp_client_line_id:
                continue

            mo = MrpProduction.create({
                'product_id': line.product_id.id,
                'product_qty': line.product_uom_qty,
                'product_uom_id': line.product_uom.id,
                'origin': self.origin,
                'sale_order_id': self.id,
                'company_id': self.company_id.id,
                'date_start': self.date_order,
            })
            mo.message_post(
                body=f"""
                    Producción generada desde la Orden de Venta:
                    <a href="#" data-oe-model="sale.order" data-oe-id="{self.id}">
                        {self.name}
                    </a>
                """,
                body_is_html=True,
            )

            line_mrp = MrpProductionLine.create({
                'partner_id': self.partner_id.id,
                'quantity': line.product_uom_qty,
                'mrp_production_id': mo.id,
            })
            line.mrp_client_line_id = line_mrp 

        self.is_created_mrp = True 
        self.already_created_mrp = True 
        return True

  
    def action_cancel(self):
        res = super().action_cancel()

        for order in self:
            #cancel mrp create from sale_order
            if order.is_created_mrp:
                mos = self.env['mrp.production'].search([
                    ('sale_order_id', '=', order.id),
                    ('state', 'not in', ['done', 'cancel']),
                ])

                for mo in mos:
                    mo.action_cancel()

            else:
                for line in order.order_line:
                    if line.mrp_client_line_id and line.mrp_client_line_id.mrp_production_id.state == 'draft':
                        mrp = line.mrp_client_line_id.mrp_production_id
                        line.mrp_client_line_id.unlink()
                        mrp.calculate_product_quantity()

        return res


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    mrp_client_line_id = fields.Many2one(
        comodel_name='mrp.production.contact.line',
        string="Line mrp"
    )

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        
        for line in lines:
            if line.order_id.is_from_mrp ==True:
                continue 
            if line.product_id.type != 'consu':
                continue

            if line.mrp_client_line_id:
                continue

            bom_by_product  = self.env['mrp.bom']._bom_find(
                products=line.product_id,
                company_id=line.order_id.company_id.id,
            )
            bom = bom_by_product.get(line.product_id)

            # Si no existe BOM -> no crear MO
            if not bom:
                _logger.warning(f"No existe el la lista de materiales para el producto {line.product_id}")
                continue 
            
            self._create_mrp_production(line)
            

        return lines 

    def _create_mrp_production(self, line):
        MrpProduction = self.env['mrp.production'].sudo()
        MrpProductionLine = self.env['mrp.production.contact.line'].sudo()

        warehouse = line.order_id.warehouse_id

        picking_type = self.env['stock.picking.type'].search([
            ('warehouse_id', '=', warehouse.id),
            ('code', '=', 'mrp_operation'),
            ('company_id', '=', line.order_id.company_id.id),
        ], limit=1)

        if not picking_type:
            raise UserError(
                "No se puede crear la Orden de Fabricación.\n\n"
                "No existe un tipo de operación de fabricación "
                "para el almacén '%s' de la compañía '%s'."
                % (
                    warehouse.display_name,
                    line.order_id.company_id.display_name,
                )
            ) 
        mo = MrpProduction.create({
            'product_id': line.product_id.id,
            'product_qty': line.product_uom_qty,
            'product_uom_id': line.product_uom_id.id,

            'origin': line.order_id.origin,
            'sale_order_id': line.order_id.id,
            'company_id': line.order_id.company_id.id,
            'date_start': line.order_id.commitment_date or line.order_id.date_order,
            'picking_type_id': picking_type.id,
        })
        mo.message_post(
            body=f"""
                Producción generada desde la Orden de Venta:
                <a href="#" data-oe-model="sale.order" data-oe-id="{self.id}">
                    {line.order_id.display_name}
                </a>
            """,
            body_is_html=True,
        )

        line_mrp = MrpProductionLine.create({
            'partner_id': line.order_id.partner_id.id,
            'quantity': line.product_uom_qty,
            'mrp_production_id': mo.id,
        })
        line.mrp_client_line_id = line_mrp 

    def write(self, vals):
        res = super().write(vals)

        if 'product_uom_qty' in vals and not self.env.context.get('no_update_mrp'):
            for line in self:
                if line.mrp_client_line_id:
                    line.mrp_client_line_id.quantity = line.product_uom_qty

                    production = line.mrp_client_line_id.mrp_production_id

                    if production.state == 'draft':
                        production.calculate_product_quantity()

        if 'product_id' in vals and self.order_id.is_created_mrp:
            #update product of mrp
            for line in self:
                bom_by_product  = self.env['mrp.bom']._bom_find(
                    products=line.product_id,
                    company_id=line.order_id.company_id.id,
                )
                bom = bom_by_product.get(line.product_id)

                if not bom:
                    _logger.warning(f"No existe el bom para el producto {line.product_id}")
                    continue 
                if line.mrp_client_line_id:
                    production = line.mrp_client_line_id.mrp_production_id
                    if production.state == 'draft':
                        production.product_id = line.product_id

                else:
                    self._create_mrp_production(line)

        return res
 
    def unlink(self):

        for line in self:
            if line.order_id.is_created_mrp:
                if line.mrp_client_line_id:
                    production = line.mrp_client_line_id.mrp_production_id 
                    production.action_cancel()
                continue
            if line.mrp_client_line_id and line.order_id.is_from_mrp:
                line.mrp_client_line_id.active = False 
                production = line.mrp_client_line_id.mrp_production_id 
                if production.state == 'draft':
                    production.calculate_product_quantity()


        return super().unlink()            
    

