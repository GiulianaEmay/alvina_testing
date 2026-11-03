from odoo import models , fields
from odoo.tools import float_round

import math
import pytz 
from datetime import timedelta 
import logging 
from collections import defaultdict


_logger = logging.getLogger(__name__)

class MrpCategoryProductGuideReport(models.AbstractModel):
    _name = 'report.alvinha_mrp.guide_template'
    _description = 'Reporte Guia PDF Productos a Fabricar'

    def _get_report_values(self, docids, data=None,):
        data = data or {}

        user = self.env.user
        user_tz = pytz.timezone(user.tz or 'UTC')
        wizards = self.env['mrp.category.product.wizard'].browse(docids)
        
        
        wizard_lines = {} # partner_id 
        for index , wizard in enumerate(wizards):
            data_wizard = {}
            # Fecha recibida o fecha de hoy según zona horaria del usuario
            # if data.get('date'):
            #     report_date = fields.Date.from_string(data['date'])
            # else:
            #     now_utc = fields.Datetime.now()
            #     now_user = pytz.utc.localize(now_utc).astimezone(user_tz)
            #     report_date = now_user.date()

            # productions = self.env['mrp.production'].search([
            #     ('date_start', '>=', wizard.date),
            #     ('date_start', '<', wizard.date + timedelta(days=1)),
            #     ('state', 'not in', ['done', 'cancel']),
            #     ('category_product_id', '=', wizard.mrp_category_id.id),
            # ])
            production_lines = self.env['mrp.production.contact.line'].search([
                ('mrp_production_id.date_start', '>=', wizard.date),
                ('mrp_production_id.date_start', '<', wizard.date + timedelta(days=1)),
                ('mrp_production_id.state', 'not in', ['done', 'cancel']),
                ('mrp_production_id.category_product_id', '=', wizard.mrp_category_id.id),
            ])
            
            for production_line in production_lines:
                product = production_line.mrp_production_id.product_id
                if production_line.partner_id and production_line.partner_id.id in data_wizard:
                    
                    # if product.id in data_wizard[production_line.partner_id.id]['lines']:
                    #     data_wizard[production_line.partner_id.id]['lines'][product.id] += production_line.mrp_production_id.product_qty 
                    # else:
                    #     data_wizard[production_line.partner_id.id]['lines'][product.id] = production_line.mrp_production_id.product_qty 
                        
                    data_wizard[production_line.partner_id.id]['lines'].setdefault(product, 0)
                    data_wizard[production_line.partner_id.id]['lines'][product] += production_line.quantity
                    data_wizard[production_line.partner_id.id]['total'] += production_line.quantity
                
                if production_line.partner_id and not production_line.partner_id.id in data_wizard:
                    data_wizard[production_line.partner_id.id] = {
                        'mrp_id': production_line.mrp_production_id.id, 
                        'partner_name': production_line.partner_id.alias or production_line.partner_id.display_name,
                        'lines' : {},
                        'partner_id':  production_line.partner_id.id,
                        'total': production_line.quantity
                    }
                    # data_wizard[production_line.partner_id.id]['lines'][product.id] = production_line.mrp_production_id.product_qty 
                    data_wizard[production_line.partner_id.id]['lines'].setdefault(product, 0)
                    data_wizard[production_line.partner_id.id]['lines'][product] += production_line.quantity
                    
            wizard_lines[index] = data_wizard.values()


        return {
            'doc_ids': docids,
            'docs': wizards,
            'doc_model': 'mrp.category.product.wizard',
            'wizard_lines': wizard_lines,
        }
        

class MrpCategoryProductConsolidadoReport(models.AbstractModel):
    _name = 'report.alvinha_mrp.consolidated_template'
    _description = 'Reporte Consolidado PDF Productos a Fabricar'

    def _get_report_values(self, docids, data=None,):
        data = data or {}

        user = self.env.user
        user_tz = pytz.timezone(user.tz or 'UTC')
        wizards = self.env['mrp.category.product.wizard'].browse(docids)
        
        workcenters = self.env['mrp.workcenter'].sudo().search([], order="name asc")
        
        
        
        wizard_lines = {} # {wizard.id: {data}} 
        wizard_conformidad = {} # {wizard.id : []}
        for wizard in wizards:
            # Estructura:
            # {
            #   workcenter_id: {
            #       'workcenter': record,
            #       'tables': {'sucursal': {
            #       'products': {product_id: record},
            #       'users': {user_id: record},
            #       'matrix': {(product_id, user_id): qty}
            #    
            #       }}
            #   }
            # }
            conformidades = []
            result = {}
            for wc in workcenters:
                result[wc.id] = {
                    'workcenter': wc,
                    'tables': {
                        'branch': {
                            'products': {},
                            'users': {},
                            'matrix': defaultdict(float),
                        },
                        'store': {
                            'products': {},
                        'users': {},
                        'matrix': defaultdict(float),
                        }
                    },
                }

            productions = self.env['mrp.production'].search([
                ('date_start', '>=', wizard.date),
                ('date_start', '<', wizard.date + timedelta(days=1)),
                ('state', 'not in', ['cancel',]),
                ('category_product_id', '=', wizard.mrp_category_id.id),
            ])
            
            for prod in productions:
                # Obtener centros de trabajo desde las operaciones
                for workorder in prod.workorder_ids:
                    wc = workorder.workcenter_id

                    if wc.id not in result:
                        continue

                    product = prod.product_id
                    
                    
                    #sucursal
                    for line in prod.client_line_ids.filtered(lambda p: p.partner_id.type_client=='branch').sorted(key=lambda p: p.partner_id.distribution_priority):
                        user = line.partner_id
                        qty = line.quantity

                        result[wc.id]['tables']['branch']['products'][product.id] = product
                        result[wc.id]['tables']['branch']['users'][user.id] = user

                        key = (product.id, user.id)
                        result[wc.id]['tables']['branch']['matrix'][key] += qty
                        result[wc.id]['tables']['branch']['matrix'][(0,user.id)] += qty
                        result[wc.id]['tables']['branch']['matrix'][(product.id,0)] += qty
                        result[wc.id]['tables']['branch']['matrix'][(0,0)] += qty
                        
                    for line in prod.client_line_ids.filtered(lambda p: p.partner_id.type_client=='store').sorted(key=lambda p: p.partner_id.distribution_priority):
                        user = line.partner_id
                        qty = line.quantity

                        result[wc.id]['tables']['store']['products'][product.id] = product
                        result[wc.id]['tables']['store']['users'][user.id] = user

                        key = (product.id, user.id)
                        result[wc.id]['tables']['store']['matrix'][key] += qty
                        result[wc.id]['tables']['store']['matrix'][(0,user.id)] += qty
                        result[wc.id]['tables']['store']['matrix'][(product.id,0)] += qty
                        result[wc.id]['tables']['store']['matrix'][(0,0)] += qty
                    
                        
                if prod.observation:
                    conformidades.append({
                        'product_name': prod.product_id.display_name ,
                        'qty': prod.product_qty,
                        'observation': prod.observation,
                    })
            
            wizard_lines[wizard.id] = result.values()
            wizard_conformidad[wizard.id] = conformidades
         
        _logger.info(f"wizard_lines , {wizard_lines}")
        _logger.info(f"wizard_conformidad , {wizard_conformidad}")
        return {
            'doc_ids': docids,
            'docs': wizards,
            'doc_model': 'mrp.category.product.wizard',
            'wizard_lines': wizard_lines,
            'wizard_conformidad': wizard_conformidad,
        }
        

class MrpCategoryProductKittingReport(models.AbstractModel):
    _name = 'report.alvinha_mrp.kitting_template'
    _description = 'Reporte Kitting PDF Productos a Fabricar'

    def _get_report_values(self, docids, data=None,):
        data = data or {}

        user = self.env.user
        user_tz = pytz.timezone(user.tz or 'UTC')
        wizards = self.env['mrp.category.product.wizard'].browse(docids)
        
        workcenters = self.env['mrp.workcenter'].sudo().search([])
        round_precision = self.env['decimal.precision'].precision_get('Alvinha Report Decimal')
        
        
        
        wizard_lines = {} # {wizard.id: {data}} 
        for wizard in wizards:

            mos = self.env["mrp.production"].search([
                ("date_start", ">=", wizard.date),
                ('date_start', '<=', wizard.date),
                ("category_product_id", "=", wizard.mrp_category_id.id),
                ('state', 'not in', ['cancel',]),
            ])

            # --------------------------------
            # 1. CONSOLIDADO DE INSUMOS
            # --------------------------------
            material_totals = defaultdict(lambda: {
                "qty": 0.0,
                "uom": ""
            })

            for mo in mos:
                for move in mo.move_raw_ids:
                    product = move.product_id
                    material_totals[product.id]["product"] = product.display_name
                    material_totals[product.id]["uom"] = product.uom_id.name
                    if move.product_uom_qty:
                        material_totals[product.id]["qty"] += float_round(move.product_uom_qty, precision_digits=round_precision)
                        material_totals[product.id]["qty"] = float_round(material_totals[product.id]["qty"], precision_digits=round_precision)
                    
                        


            materials = list(material_totals.values())

            # --------------------------------
            # 2. MATRICES POR CENTRO
            # --------------------------------
            workcenters_data = []
            workcenters = mos.mapped("workorder_ids.workcenter_id")
            for wc in workcenters:
                wc_mos = mos.filtered(
                    lambda m: wc in m.workorder_ids.mapped("workcenter_id")
                )

                # obtener insumos únicos
                inputs = wc_mos.mapped("move_raw_ids.product_id")
                inputs_data = [
                    {
                        "id": p.id,
                        "name": p.name,
                        "uom": p.uom_id.name
                    }
                    for p in inputs
                ]

                rows = []
                column_totals = [0.0] * len(inputs)
                for mo in wc_mos:
                    row_values = []
                    row_total = 0
                    for i, inp in enumerate(inputs):
                        qty = float_round(sum(
                            mo.move_raw_ids
                            .filtered(lambda m: m.product_id.id == inp.id)
                            .mapped("product_uom_qty")
                        ), precision_digits=round_precision)
                        row_total += qty 
                        row_values.append(qty)
                        column_totals[i] += qty
                        column_totals[i] = float_round(column_totals[i], precision_digits=round_precision)

                    row_total = float_round(row_total, precision_digits=round_precision)
                    rows.append({
                        "product": mo.product_id.display_name,
                        "values": row_values,
                        "total": row_total,
                    })

                grand_total = float_round(sum(column_totals), precision_digits=round_precision)
                workcenters_data.append({
                    "name": wc.name,
                    "inputs": inputs_data,
                    "rows": rows,
                    "column_totals": column_totals,
                    "grand_total": grand_total,
                })
         
            wizard_lines[wizard.id] = {
                "materials": materials,
                "workcenters": workcenters_data,
            }
        _logger.info(f"wizard_lines , {wizard_lines}")
        return {
            'doc_ids': docids,
            'docs': wizards,
            'doc_model': 'mrp.category.product.wizard',
            'wizard_lines': wizard_lines,
        }
        
   
class MrpCategoryProductProductionReport(models.AbstractModel):
    _name = 'report.alvinha_mrp.production_template'
    _description = 'Reporte Production PDF Productos a Fabricar'

    def _get_report_values(self, docids, data=None,):
        data = data or {}

        user = self.env.user
        user_tz = pytz.timezone(user.tz or 'UTC')
        wizards = self.env['mrp.category.product.wizard'].browse(docids)
        
        workcenters = self.env['mrp.workcenter'].sudo().search([])
        decimal_precision = self.env['decimal.precision'].precision_get('Alvinha Report Decimal')
        
        
        wizard_lines = {} # {wizard.id: {data}} 
        for wizard in wizards:

            productions = self.env['mrp.production'].search([
                ('date_start', '>=', wizard.date),
                ('date_start', '<=', wizard.date),
                ('state', 'not in', ['cancel', ]),
                ('category_product_id', '=', wizard.mrp_category_id.id),

            ])

            # Tabla general
            products = {}
            total_product = [0.0]*3
            for prod in productions:
                product = prod.product_id
                qty = prod.product_qty
                if prod.product_id.id in products:
                    kilos = (float_round(products[prod.product_id.id]['units'] / product.units_produced_per_batch, precision_digits=decimal_precision)) if product.units_produced_per_batch!=0 else 0
                    products[prod.product_id.id]['units'] += qty
                    products[prod.product_id.id]['can'] = math.ceil(products[prod.product_id.id]['units'] / product.unit_per_can_count) if product.unit_per_can_count!=0 else 0
                    products[prod.product_id.id]['kilos'] = kilos
                else:
                    kilos = (float_round(prod.product_qty / product.units_produced_per_batch, precision_digits=decimal_precision)) if product.units_produced_per_batch!=0 else 0
                    products[prod.product_id.id] = {
                        'product_code': product.default_code or '',
                        'product_name': product.display_name,
                        'unit_per_can': product.unit_per_can_count or 0,
                        'units': prod.product_qty,
                        'can': math.ceil(prod.product_qty / product.unit_per_can_count) if product.unit_per_can_count != 0 else 0,
                        'kilos': kilos,
                    }
                total_product[0] += prod.product_qty
                total_product[1] += math.ceil(prod.product_qty / product.unit_per_can_count) if product.unit_per_can_count!=0 else 0
                total_product[2] += kilos
                total_product[2] = float_round(total_product[2], precision_digits=decimal_precision)

            total_product[0] = int(total_product[0])
            total_product[1] = int(total_product[1])
            
            # Ordenar productos de la tabla general por código
            products = dict(
                sorted(
                    products.items(),
                    key=lambda item: item[1]['product_code']
                )
            )
            
            # Tablas por centro de trabajo
            workcenter_data = {}

            workorders = self.env['mrp.workorder'].search([
                ('production_id', 'in', productions.ids),
                #('state', '=', 'done')
            ])

            for wo in workorders:
                wc = wo.workcenter_id
                product = wo.production_id.product_id
                qty = wo.production_id.product_qty

                if wc.id not in workcenter_data:
                    workcenter_data[wc.id] = {
                        "wc_name": wc.display_name, 
                        "products": [],
                        "total_products": [0.0]*3,
                    }
                
                kilos = float_round((qty / product.units_produced_per_batch )if  product.units_produced_per_batch else 0, precision_digits=decimal_precision)
                workcenter_data[wc.id]["products"].append({
                    "product_code": product.default_code or "",
                    "product_name": product.display_name,
                    "unit_weight": float_round(product.unit_weight , precision_digits=decimal_precision),
                    "unit_per_can": int(product.unit_per_can_count or 0), 
                    "qty": int(qty),
                    "cans": math.ceil(qty / product.unit_per_can_count) if product.unit_per_can_count!=0 else 0,
                    "kilos":  kilos,
                })

                workcenter_data[wc.id]['total_products'][0] +=  int(qty)
                workcenter_data[wc.id]['total_products'][1] += math.ceil(qty / product.unit_per_can_count) if product.unit_per_can_count!=0 else 0
                workcenter_data[wc.id]['total_products'][2] += kilos
                workcenter_data[wc.id]['total_products'][2] = float_round(workcenter_data[wc.id]['total_products'][2], precision_digits=decimal_precision)
                
            # Ordenar productos de cada centro de trabajo por código
            for wc_data in workcenter_data.values():
                wc_data["products"].sort(
                    key=lambda p: p["product_code"]
                )

            wizard_lines[wizard.id] = {
                'products': products,
                'total_product': total_product,
                'workcenters': workcenter_data,
            }
        _logger.info(f"wizard_lines , {wizard_lines}")
        return {
            'doc_ids': docids,
            'docs': wizards,
            'doc_model': 'mrp.category.product.wizard',
            'wizard_lines': wizard_lines,
        }
        
   
class ReportMrpProductionDaily(models.AbstractModel):
    _name = 'report.alvinha_mrp.report_bakery_template'
    _description = 'Reporte pasteleria'


    def _get_report_values(self, docids, data=None):
        wizards = self.env['mrp.category.product.wizard'].browse(docids)
        
        # --- Manejo de Zona Horaria UTC-5 ---
        user = self.env.user
        user_tz = pytz.timezone(user.tz or 'UTC')
        wizard_data = {}
        for wizard in wizards:
            productions = self.env['mrp.production'].search([
                ('date_start', '>=', wizard.date),
                ('date_start', '<=', wizard.date),
                ('state', 'not in', ['cancel', ]),
                ("category_product_id", "=", wizard.mrp_category_id.id),

            ])


            summary = {}
            total = 0
            for line in productions:
                p_id = line.product_id.id
                if p_id not in summary:
                    summary[p_id] = {
                        'name': line.product_id.display_name,
                        'qty': 0.0,
                    }
                summary[p_id]['qty'] += line.product_qty
                total += line.product_qty


            wizard_data[wizard.id] = {
                'products': list(summary.values()),
                'total': int(total), 
            }

        return {
            'doc_ids': docids,
            'doc_model': 'mrp.production.report.wizard',
            'docs': wizards,
            'wizard_data': wizard_data,
        }
