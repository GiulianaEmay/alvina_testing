from odoo import models, fields 

class MrpCategoryProductWizard(models.TransientModel):
    _name = "mrp.category.product.wizard"
    _description = "Wizard for reports of mrp.category.product"
    
    mrp_category_id = fields.Many2one(
        comodel_name='mrp.category.product',
        string="Categoria mrp",
    )
    date = fields.Date(default=fields.Date.today(), string="Fecha")
    
    def action_generete_report(self):
        self.ensure_one()
        
        report_ref = self.env.ref('alvinha_mrp.action_report_guide')
        if report_ref:
            return report_ref.report_action(self)
        
    def action_generete_report_consolid(self):
        self.ensure_one()
        report_ref = self.env.ref('alvinha_mrp.mrp_by_center_worker_report')
        if report_ref:
            return report_ref.report_action(self)
        
    def action_generete_report_kitting(self):
        self.ensure_one()
        report_ref = self.env.ref('alvinha_mrp.mrp_production_kitting_report')
        if report_ref:
            return report_ref.report_action(self)
        
    def action_generete_report_production(self):
        self.ensure_one()
        report_ref = self.env.ref('alvinha_mrp.mrp_production_production_report')
        if report_ref:
            return report_ref.report_action(self)
    
    def action_generete_report_bakery(self):
        self.ensure_one()
        report_ref = self.env.ref('alvinha_mrp.mrp_production_bakery_report')
        if report_ref:
            return report_ref.report_action(self)
