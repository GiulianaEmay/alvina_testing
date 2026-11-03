from odoo import fields, models, api 


class ProductProduct(models.Model):
    _inherit = "product.product"

    unit_per_can_count = fields.Float(string="Unidades por lata")
    unit_weight = fields.Float(string="Peso unitario (g)")
    units_produced_per_batch = fields.Float(string="Unidades producidas por lote (Kg)", help="Este campo es relativo a la cantidad de harina(Kg), en el reporte de producción")
    


class ProductTemplate(models.Model):
    _inherit = "product.template"
    
    #production_line = fields.Char(string="Linea de producción")
    unit_per_can = fields.Many2one('uom.uom', string="Unidades de lata")
    unit_per_can_count = fields.Float(string="Unidades por lata")
    unit_weight = fields.Float(string="Peso unitario (g)")
    units_produced_per_batch = fields.Float(string="Unidades producidas por lote (Kg)", help="Este campo es relativo a la cantidad de harina(Kg), en el reporte de producción")
    
    def _get_related_fields_variant_template(self):
        """ Return a list of fields present on template and variants models and that are related"""
        fields = super()._get_related_fields_variant_template()
        fields.append('unit_per_can_count')
        fields.append('unit_weight')
        fields.append('units_produced_per_batch')
        return fields
    