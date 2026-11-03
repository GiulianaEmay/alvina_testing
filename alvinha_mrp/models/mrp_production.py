from odoo import fields, models, api
from odoo.exceptions import UserError

import logging

_logger = logging.getLogger(__name__)

class MrpProductionContactLine(models.Model):
    _name = "mrp.production.contact.line"
    _description = "Contactos que necesitan el producto"
    
    active = fields.Boolean(default=True, string="Activo")
    partner_id = fields.Many2one('res.partner', string="Cliente/Sucursal")
    quantity = fields.Integer(string="Cantidad")
    mrp_production_id = fields.Many2one(
        comodel_name='mrp.production',
        string="Orden de producción",
    )
    observation = fields.Char(string="Observación")

    # Relación inversa con la línea de venta (sale.order.line.mrp_client_line_id)
    sale_line_ids = fields.One2many(
        comodel_name='sale.order.line',
        inverse_name='mrp_client_line_id',
        string="Líneas de venta",
    )
    sale_order_id = fields.Many2one(
        comodel_name='sale.order',
        string="Orden de venta",
        compute='_compute_sale_order_id',
        store=True,
        index=True,
    )

    @api.depends('sale_line_ids.order_id')
    def _compute_sale_order_id(self):
        for line in self:
            line.sale_order_id = line.sale_line_ids[:1].order_id

    def action_open_sale_order(self):
        self.ensure_one()
        if not self.sale_order_id:
            return False
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'sale.order',
            'res_id': self.sale_order_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def write(self, vals):
        res = super().write(vals)
        if 'quantity' in vals:
            for rec in self:
                try:
                    order_line = rec.sudo().sale_line_ids
                    if len(order_line) == 1:
                        order_line.with_context(no_update_mrp=True).product_uom_qty = rec.quantity
                    elif len(order_line) > 1:
                        _logger.warning(f"Se encontro mas de una line de venta haga el ajuste manual {order_line}")
                except Exception as e:
                    _logger.exception(str(e))
        return res

    

class MrpProduction(models.Model):
    """ Manufacturing Orders """
    _inherit = 'mrp.production'
    
    client_line_ids = fields.One2many(
        comodel_name='mrp.production.contact.line',
        inverse_name='mrp_production_id'
    )
    
    category_product_id = fields.Many2one(
        comodel_name='mrp.category.product',
        string="Categoria de producción"
    )
    observation = fields.Char(readonly=True, string="Observaciones del producto antes de mandar a producir", help="Este valor se calcula si el producto tiene el número por bandeja. Es la cantidad positiva para completar la lata", compute="_compute_observation")
    obs_negative = fields.Char(readonly=True, string="Observaciones del producto antes de mandar a producir (negativo)", help="Este valor se calcula si el producto tiene el número por bandeja. Es la cantidad negativa para completar la lata", compute="_compute_observation")
    
    can_edit_lines = fields.Boolean(
        string="Puede editar lineas", 
        compute="_compute_can_edit_lines",
        store=False     
    )

    def _compute_can_edit_lines(self):
        user = self.env.user 
        is_admin = user.has_group('mrp.group_mrp_manager')
        for rec in self:
            if is_admin:
                rec.can_edit_lines = True 
            elif rec.create_uid.id == user.id:
                rec.can_edit_lines = True 
            else:
                rec.can_edit_lines = False 
    

    def calculate_product_quantity(self):
        self.ensure_one()
        if not self.client_line_ids:
            return
        quantity = 0
        for line in self.client_line_ids:
            if line.quantity:
                quantity += line.quantity
        self.product_qty = quantity
    
    def _get_quantity_for_round_can(self, qty, can):
        #Retorna el la cantidad que falta completar la lata y para redondear la lata. Arriba y abajo.
        mod = qty % can
        q = qty // can

        return mod, can-mod            
        
        
        
    
    @api.depends('product_id.unit_per_can_count', 'product_qty')
    def _compute_observation(self):
        for record in self:
            if record.product_id and record.product_id.unit_per_can_count:
                inferior, superior = self._get_quantity_for_round_can(record.product_qty, record.product_id.unit_per_can_count)
                    
                if  inferior != 0 and record.product_qty!=1:
                    record.observation = f"Agregar {int(superior)} unidades para completar una lata"
                    record.obs_negative = f"Quitar {int(inferior)} unidades para completar una lata"
                else:
                    record.observation = False
                    record.obs_negative = False
            else:
                record.observation = False
                record.obs_negative = False
     
    def _get_sucursal_add_quiet(self):
        res_settings = self.env['res.config.settings'].search([])

        if not res_settings:
            return False
        
        contact_id = res_settings.mrp_contact_id
        if not contact_id:
            return False 
        
        return self.client_line_ids.filtered(lambda client: client.partner_id==contact_id )[:1]
     
    def action_increment_can(self):
        #Aumenta para completar la lata, este aumento se hace a la sucursal center
        self.ensure_one() 
        contact = self._get_sucursal_add_quiet()
        if not contact:
            raise UserError("No existe el contacto para aumentar en la lista de pedidos")
        inferior, superior = self._get_quantity_for_round_can(self.product_qty, self.product_id.unit_per_can_count)
        
        contact.quantity += superior
        contact.observation = f"Se aumento {int(superior)} para completar la lata"
        self.calculate_product_quantity()
        
    def action_decrement_can(self):
        #Disminuye para completar la lata, este aumento se hace a la sucursal center
        self.ensure_one() 
        contact = self._get_sucursal_add_quiet()
        if not contact:
            raise UserError("No existe el contacto para aumentar en la lista de pedidos")
        inferior, superior = self._get_quantity_for_round_can(self.product_qty, self.product_id.unit_per_can_count)
        
        
        contact.quantity -= inferior
        contact.observation = f"Se quito {int(inferior)} para completar la lata"
        self.calculate_product_quantity()
