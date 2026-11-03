# -*- coding: utf-8 -*-
from odoo import fields, models

class ReportPosOrder(models.Model):
    _inherit = 'report.pos.order'

    customer_count = fields.Integer(string='Comensales  ', readonly=True)
    pos_root_categ_id = fields.Many2one(
        'pos.category',
        string='Categoria principal POS',
        readonly=True,
    )
    session_horario = fields.Selection(
        [
            ('am', 'AM'),
            ('pm', 'PM'),
        ],
        string='Horario',
        related='session_id.horario',
        readonly=True,
    )

    def _select(self):
        select = super()._select()
        select += """,
            s.customer_count AS customer_count,
            ps.horario AS session_horario,
            COALESCE(pc3.id, pc2.id, pc1.id, pc.id) AS pos_root_categ_id
        """
        return select

    def _from(self):
        return super()._from() + """
            LEFT JOIN pos_category pc ON (fpc.id = pc.id)
            LEFT JOIN pos_category pc1 ON (pc.parent_id = pc1.id)
            LEFT JOIN pos_category pc2 ON (pc1.parent_id = pc2.id)
            LEFT JOIN pos_category pc3 ON (pc2.parent_id = pc3.id)
        """
