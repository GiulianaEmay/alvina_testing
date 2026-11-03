from odoo import models, fields, api


class PosConfig(models.Model):
    _inherit = "pos.config"
    
    
    ballot_journal_id  = fields.Many2one(
        'account.journal', 
        string='Boletas',
        domain=[('type', 'in', ('general', 'sale'))],
        check_company=True,
        help="Accounting journal (Ballot) used to post POS session journal entries and POS invoice payments.",
        default=lambda self: self._default_sale_journal(),
        ondelete='restrict',
    )
    credit_note_journal_id = fields.Many2one(
        'account.journal', 
        string='Nota de Credito (F)',
        domain=[('type', 'in', ('general', 'sale'))],
        check_company=True,
        help="Accounting journal (Credit Note Invoice) used to post POS session journal entries and POS invoice payments.",
        default=lambda self: self._default_sale_journal(),
        ondelete='restrict',
    )
    credit_note_ballot_journal_id = fields.Many2one(
        'account.journal', 
        string='Nota de Credito (B)',
        domain=[('type', 'in', ('general', 'sale'))],
        check_company=True,
        help="Accounting journal (Credit Note Ballot) used to post POS session journal entries and POS invoice payments.",
        default=lambda self: self._default_sale_journal(),
        ondelete='restrict',
    )
    
    def _default_sale_journal(self):
        return self.env['account.journal']._ensure_company_account_journal()
    
    def _get_journal(self, partner, refunded_order_id):
        if len(partner.vat or '')==8 and self.ballot_journal_id and not refunded_order_id:
            return self.ballot_journal_id
        if len(partner.vat or '')==8 and self.ballot_journal_id and refunded_order_id:
            return self.credit_note_ballot_journal_id
        
        if not refunded_order_id:
            return self.invoice_journal_id
        else:
            return self.credit_note_journal_id
    

    pm_start_hour = fields.Float(
        string='Hora inicio PM',
        default=12.0,
        help='Hora desde la cual una sesión será considerada PM.'
    )

    @api.model
    def _load_pos_data_read(self, records, config):
        read_records = super()._load_pos_data_read(records, config)
        if read_records and self.env.company.country_id.code == "PE":
            cf = self.env.ref('l10n_pe_pos.partner_pe_cf', raise_if_not_found=False)
            if cf and cf.vat == "00000000":
                read_records[0]['_consumidor_final_anonimo_id'] = cf.id
            else:
                partner = self.env['res.partner'].search([
                    ('active','=', True),
                    ('vat', '=', '00000000'),
                    ('l10n_latam_identification_type_id', '=', self.env.ref('l10n_pe.it_DNI').id),
                ], limit=1)
                if partner:
                    read_records[0]['_consumidor_final_anonimo_id'] = partner.id
        return read_records
    
