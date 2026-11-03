from odoo import fields, models , api 


class SaleReport(models.Model):
    _inherit = 'sale.report'

    partner_type_client = fields.Selection(
        related='partner_id.type_client',
    )
    commitment_date = fields.Datetime(
        string="Fecha de entrega", copy=False,
        help="This is the delivery date promised to the customer. "
             "If set, the delivery order will be scheduled based on "
             "this date rather than product lead times.")

    origin = fields.Char(string="Documento origen")

    def _select_additional_fields(self):
        res = super()._select_additional_fields()
        res['origin'] = "s.origin"
        res['commitment_date'] = "s.commitment_date"
        return res
        