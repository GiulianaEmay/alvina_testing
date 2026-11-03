# -*- coding: utf-8 -*-
from odoo import http, _
from odoo.http import request
import json
from werkzeug.utils import redirect
from datetime import date, timedelta, datetime, time
from odoo import fields
import pytz
from werkzeug.exceptions import Forbidden

import logging 

_logger = logging.getLogger(__name__)

DAY_XMLIDS = {
    'monday': 'alvinha_base.day_monday',
    'tuesday': 'alvinha_base.day_tuesday',
    'wednesday': 'alvinha_base.day_wednesday',
    'thursday': 'alvinha_base.day_thursday',
    'friday': 'alvinha_base.day_friday',
    'saturday': 'alvinha_base.day_saturday',
    'sunday': 'alvinha_base.day_sunday',
}

class PortalProductionController(http.Controller):
    """
    Controlador para permitir a usuarios portal/web crear órdenes de producción
    a partir de un 'agrupador de producto'.
    """
    
    @http.route('/portal/production/success', type='http', auth='user', website=True)
    def portal_production_success(self, **kw):
        return request.render('alvinha_mrp.website_production_success')

    @http.route('/portal/production/new', type='http', auth='user', website=True)
    def portal_production_form(self, **kw):

        # Ajusta el search() a tu criterio (por ejemplo filtrar solo activos, por compañía, etc.)
        # Filtra por día y horario según la hora local del negocio
        agrupadores = request.env['mrp.category.product'].sudo()._get_available_categories(
            self._get_local_now()
        )
        picking_types = self.env['stock.picking.type'].sudo().search([
            ('code', '=', 'mrp_operation'),
            ('active', '=', True),
        ])

        companies = self.env['res.company'].sudo().search([
            ('id', 'in', picking_types.mapped('company_id').ids),
            ('active', '=', True),
        ])
        

        values = {
            'agrupadores': agrupadores,
            'partners': [],
            'user_partner': request.env.user.partner_id,
            'company_partner': request.env.user.company_id.partner_id,
            'today': (fields.Date.context_today(request.env.user) + timedelta(days=1)).isoformat(),
            'companies': companies,
            'user': request.env.user,
        }
        _logger.info(f"Values controller, {values}")
        return request.render('alvinha_mrp.production_form', values)

    @http.route(['/portal/production/agrupador_products'], type='jsonrpc', auth='user', website=True)
    def portal_agrupador_products(self, agrupador_id=None, **kw):
        if not agrupador_id:
            return {'error': _('No agrupador_id provided')}

        agrupador = request.env['mrp.category.product'].sudo().browse(int(agrupador_id))
        if not agrupador.exists():
            return {'error': _('Agrupador no encontrado')}

        products = []
        for p in agrupador.product_ids:
            products.append({
                'id': p.id,
                'name': p.display_name,
                'default_code': p.default_code or '',
                'uom_id': p.uom_id.id if p.uom_id else False,
            })

        return {'products': products}

    def _get_local_now(self):
        """Fecha y hora actual en la zona horaria del negocio (no la del
        navegador ni la del usuario), para que el horario sea el mismo
        para todas las sucursales."""
        tz_name = request.env.company.sudo().partner_id.tz or 'America/Lima'
        return fields.Datetime.now().replace(tzinfo=pytz.UTC).astimezone(pytz.timezone(tz_name))

    def _get_default_company(self):
        """
        Devuelve la compañia princial
        """
        return request.env['res.company'].sudo().search([
            ('active', '=', True),
            ('name', 'ilike', 'industria panificadora alviña')
        ], limit=1)

    @http.route(['/portal/production/create'], type='jsonrpc', auth='user', website=True, methods=['POST'])
    def portal_create_production(self, **post):
        """
        POST: Procesa el formulario y crea las órdenes de producción.
        Se espera recibir:
            - partner_id: id del contacto seleccionado (opcional)
            - agrupador_id: id del agrupador seleccionado
            - lines: JSON string con lista de líneas [{product_id, qty}, ...] o campos individuales
        Retorna redirect a una vista de confirmación o vuelve al formulario con mensaje.
        """

        # Datos básicos
        partner_id = post.get('partner_id') or False
        agrupador_id = post.get('agrupador_id') or False
        date_order = post.get('date_order') or False
        exist_other_orders = post.get('exist_other_orders', False)
        defult_company = self._get_default_company()
        if not defult_company:
            return {
                'error': "No existe la compañia por defecto",
            }
        if partner_id:
            partner = request.env['res.partner'].sudo().browse(int(partner_id))
        else:
            partner = request.env.user.partner_id
            partner_id = partner.id
        start_date_init = fields.Datetime.now()
        start_date_end = fields.Datetime.now() + timedelta(days=1)
        lines_json = post.get('lines')  # esperamos un JSON desde el frontend
        _logger.info(f"Lines json: {lines_json}")
        _logger.info(f"partner_id: {partner_id}")
        _logger.info(f"agrupador_id: {agrupador_id}")
        # Validaciones mínimas
        if not agrupador_id or not date_order:
            return  {
                'error': _('Debe seleccionar un agrupador o la fecha')
            }
            
        user_tz = request.httprequest.cookies.get('tz') or 'UTC'
        tz = pytz.timezone(user_tz)
        if date_order:
            date_obj = fields.Date.from_string(date_order)

            # rango local del día
            start_local = datetime.combine(date_obj, time.min)
            end_local = datetime.combine(date_obj, time.max)
            start_date_init = tz.localize(start_local).astimezone(pytz.UTC).replace(tzinfo=None)
            start_date_end = tz.localize(end_local).astimezone(pytz.UTC).replace(tzinfo=None)

        # parsear líneas
        _logger.info(f"date_order, {date_order}")

        if not lines_json:
            # si no vienen líneas, devolver error
            return  {
                'error': _('No hay líneas para producir'), 
            }

        Production = request.env['mrp.production'].sudo()
        _logger.info(f"date order start, {start_date_init}")
        _logger.info(f"date order end, {start_date_end}")
        
        picking_type = request.env['stock.picking.type'].sudo().search([
            ('code', '=', 'mrp_operation'),
            ('company_id', '=', defult_company.id)
        ], limit=1)
        if not picking_type.exists():
            return {
                'error': 'No Picking for mrp'
            }
        ProductionLineContact = request.env['mrp.production.contact.line'].sudo()
        ProductSudo = request.env['product.product'].sudo()
        quote_products = []
        category = request.env['mrp.category.product'].sudo().browse(int(agrupador_id))

        # Validar también al enviar: evita pedidos si el formulario quedó
        # abierto y el horario de la categoría ya cerró.
        if not category.exists() or not category._is_available_at(self._get_local_now()):
            return {
                'error': _('La categoría "%s" no está disponible en este horario.', category.name or ''),
            }
        for ln in lines_json:
            product_id = int(ln.get('product_id'))
            product = ProductSudo.browse(product_id)
            qty = float(ln.get('qty') or 0.0)
            if qty <= 0 or not product:
                continue  # omitir cantidades inválidas and product invalid

            # Crea una orden de producción por producto
            # Ajusta los campos según tu flujo (workcenter_id, bom_id, etc.)
            
            last_word_categ = ""
            if category:
                last_word_categ = "/ "
                last_word_categ += category.name
            vals = {
                'product_id': product_id,
                'product_qty': 1,
                #'product_uom_id': ln.get('uom_id') or request.env['product.product'].sudo().browse(product_id).uom_id.id,
                'origin': f'Portal / pedido de producción {last_word_categ}', #Portal / pedido de producción / AM
                'date_start': start_date_init,
                'company_id': defult_company.id,
                'picking_type_id': picking_type.id,
                'category_product_id': agrupador_id,
            }
            
            prod = Production.search([
                ('date_start', '>=', start_date_init),
                ('date_start', '<=', start_date_end),
                ('product_id', '=', product_id),
                ('category_product_id', '=', int(agrupador_id)),
                ('company_id', '=', defult_company.id),
                ('state', '!=', 'cancel')
            ], limit=1)
            _logger.info(f" product_id, {product_id}")
            _logger.info(f" prod_ , {prod}")
            if prod:
                plc = ProductionLineContact.create({
                    'partner_id': partner_id,
                    'quantity': qty,
                    'mrp_production_id': prod.id,
                })
                
            
            else:
                #add workorder_ids workcenters 
                prod = Production.create(vals)
                plc = ProductionLineContact.create({
                    'partner_id': partner_id,
                    'quantity': qty,
                    'mrp_production_id': prod.id,
                })

            quote_products.append({
                'id': product_id,
                'name': product.name,
                'product_qty': qty,
               # 'cost': product.standard_price or 0,
                'mrp_client_line_id': plc.id,
            })
                
            prod.calculate_product_quantity()
            # opcional: confirmar o lanzar el workflow para generar movimientos automáticamente según tu BOM
            # prod.action_confirm()   # descomenta si quieres confirmar inmediatamente (ajusta según versión)
            _logger.info(f"prod, {prod}")
        
        if exist_other_orders:
            self._enviar_notificacion_alerta(partner, date_order)
        self._create_sale_order(defult_company, partner, quote_products, category.display_name if category else 'Portal / Orden de venta', start_date_init)
        return  {
            'redirect_url': '/portal/production/success',
        }
        
    def _create_sale_order(self, company, partner, products, mrp_category_name, date_order):
        #products is a dict {id, name, cost, qty}
        if len(products) ==0:
            _logger.warning(f"No hay productos para crear ordenes")
            return
        OrderSaleSudo = request.env['sale.order'].sudo().with_company(company)
        order_lines = []
        for product in products:
            order_lines.append((0,0, {
                'product_id': product.get('id'),
                'name': product.get('name', ''),
                'product_uom_qty': product.get('product_qty',0),
                #'price_unit': product.get('cost'),
                'mrp_client_line_id': product.get('mrp_client_line_id', False)
            }))
        #cost = production.product_id.standard_price
        warehouse = request.env['stock.warehouse'].sudo().search(
            [
                ('company_id', '=', company.id),
                ('name', 'ilike', 'Producto Terminado')
            ],
            limit=1
        )
        order = OrderSaleSudo.create({
            'partner_id': partner.id,
            'origin': mrp_category_name,
            'company_id': company.id,
            'order_line': order_lines,
            'partner_invoice_id': partner.id,
            'partner_shipping_id': partner.id,
            'date_order': fields.Datetime.now(),
            'warehouse_id': warehouse.id if warehouse else False,
            'commitment_date': date_order,
            'pricelist_id': partner.with_company(company).property_product_pricelist.id,
            'is_from_mrp': True,
        })
        _logger.info(f"OrderSale create {order}")
        return 

    def _enviar_notificacion_alerta(self, partner, date):
        user_id_str = self.env['ir.config_parameter'].sudo().get_param('alvinha_mrp.mrp_warning_user_id')
        
        if user_id_str:
            user_id = int(user_id_str)
            user_to_notify = self.env['res.users'].browse(user_id)
            
            # Verificar que el usuario existe y está activo
            if user_to_notify and user_to_notify.exists():
                # 2. Construir el mensaje
                body_msg = _(
                    "El cliente/sucursal %s ha creado varias pedidos de producción para el día <b>%s</b>. "
                    "Considere revisarlo"
                ) % (partner.display_name, date)
                
                subject_msg = _("Alerta: Posible Duplicidad de Pedidos de Producción")

                # 3. Enviar la notificación
                # message_post sobre el usuario envía una notificación directa a su buzón (campanita)
                
                request.env['mail.thread'].sudo().message_notify(
                    partner_ids=[user_to_notify.partner_id.id],
                    body=body_msg,
                    subject=subject_msg,
                )
                
                
                _logger.info("Notificación enviada al usuario %s por duplicidad de orden.", user_to_notify.name)
            else:
                _logger.warning("El usuario configurado para alertas de MRP no es válido o está inactivo.")
        else:
            _logger.info("No hay usuario configurado en Settings para recibir alertas de MRP.")


    @http.route(['/portal/mrp/check_partner_date'], type="jsonrpc", auth="user", website=True, methods=['POST'])
    def check_mrp_production_exist(self, partner_id, date_str, *args, **kwargs):
        """Verifica que no existan ordenes de produccion (no canceladas ) para el contacto y la fecha"""
        user_tz_name = request.env.context.get('tz') or request.env.user.tz or 'UTC'
        user_tz = pytz.timezone(user_tz_name)
        utc_tz = pytz.UTC


        try:
            # 2. Parsear la fecha ingresada (asumimos que viene en formato YYYY-MM-DD)
            # La marcamos como medianoche en la zona horaria del USUARIO
            naive_date = datetime.strptime(date_str, '%Y-%m-%d')
            local_midnight = user_tz.localize(naive_date, is_dst=None)
            
            # 3. Convertir ese momento a UTC para hacer la query en la base de datos
            utc_start = local_midnight.astimezone(utc_tz)
            # Definir el final del día en UTC (inicio del siguiente día)
            utc_end = (local_midnight + timedelta(days=1)).astimezone(utc_tz)
            
        except ValueError:
            return {
                'error': True,
                'message': 'Formato de fecha inválido. Use YYYY-MM-DD.'
            }

        domain = [
            ('client_line_ids.partner_id', '=', int(partner_id)),
            ('date_start', '>=', utc_start.strftime('%Y-%m-%d %H:%M:%S')),
            ('date_start', '<', utc_end.strftime('%Y-%m-%d %H:%M:%S')),
            ('state', '!=', 'cancel'),
        ]
        
        # 5. Ejecutar la búsqueda
        ProductionOrder = request.env['mrp.production'].sudo()
        orders = ProductionOrder.search(domain)

        if orders:
            order = orders[0]
            return {
                'exists': True,
                'count': len(orders),
                'order_id': order.id,
                'order_name': order.name,
                'state': order.state,
                'date_start_utc': order.date_start,
                'message': f'Se encontró la orden {order.name}',
                'msg_user': f"Se encontró otra orden ya creada para esta fecha, ¿esta seguro de crear una nueva orden?",
            }
        else:
            return {
                'exists': False,
                'count': 0,
                'message': 'No existe orden de producción activa para esa fecha %s y contacto %s.' % (naive_date, partner_id )
            }
