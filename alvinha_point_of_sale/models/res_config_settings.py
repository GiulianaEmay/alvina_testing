from odoo import fields, models 


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"
    
    pos_ballot_journal_id = fields.Many2one(related='pos_config_id.ballot_journal_id', readonly=False)
    pos_credit_note_journal_id = fields.Many2one(related='pos_config_id.credit_note_journal_id', readonly=False)
    pos_credit_note_ballot_journal_id = fields.Many2one(related='pos_config_id.credit_note_ballot_journal_id', readonly=False)
    pos_pm_start_hour = fields.Float(
        related='pos_config_id.pm_start_hour',
        readonly=False,
        help='Hora desde la cual una sesión será considerada PM.'
    )
    
    
    