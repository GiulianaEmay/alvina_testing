from odoo import fields, models, api
from odoo.tools.image import image_data_uri
from odoo.exceptions import UserError

import base64

class AccountMove(models.Model):
    _name = "account.move"
    _inherit = ["ab.unlink.mixin", "account.move"]
    


    def action_remove_taxes(self):
        for rec in self:
            if rec.state != 'draft':
                continue 
            if rec.l10n_latam_document_type_id and rec.l10n_latam_document_type_id.id == self.env.ref('alvinha_account.document_type_recibo').id:
                rec._clean_taxes()


    def _clean_taxes(self):
        for line in self.invoice_line_ids:
            line.tax_ids = False

    @api.onchange('l10n_latam_document_type_id')
    def _onchange_l10n_latam_document_type_id(self):
        if self.l10n_latam_document_type_id and self.l10n_latam_document_type_id.id == self.env.ref('alvinha_account.document_type_recibo').id:
            self._clean_taxes()
    
    def _get_invoice_ticket_pdf_report_filename(self):
        """ Get the filename of the generated ticket PDF invoice report. """
        self.ensure_one()
        return f"{self._get_move_display_name().replace(' ', '_').replace('/', '_')}_ticket.pdf"
    
    
    def _get_invoice_pdf_ticket(self):
        """ Generate the ticket of the invoice.
        :return dict: the Ticket's data such as
        {'filename': 'INV_2024_0001_proforma.pdf', 'filetype': 'pdf', 'content': ...}
        """
        self.ensure_one()
        filename = self._get_invoice_ticket_pdf_report_filename()
        content, report_type = self.env['ir.actions.report']._pre_render_qweb_pdf('alvinha_account.action_report_account_move_ticket', self.ids, data={'proforma': True})
        content_by_id = self.env['ir.actions.report']._get_splitted_report('alvinha_account.action_report_account_move_ticket', content, report_type)
        return {
            'filename': filename,
            'filetype': 'pdf',
            'content': content_by_id[self.id],
        }
        
    def action_invoice_download_ticket_pdf(self, target = "download"):
        return {
            'type': 'ir.actions.act_url',
            'url': f'/account/download_invoice_ticket_documents/{",".join(map(str, self.ids))}/pdf',
            'target': target,
        }
    
    def _get_starting_sequence(self):
        # OVERRIDE of l10n_pe_edi
        if self.l10n_pe_edi_is_required and self.l10n_latam_document_type_id:
            doc_mapping = {'01': 'FFI', '03': 'BOL', '07': 'CNE', '08': 'NDI'}
            middle_code = doc_mapping.get(self.l10n_latam_document_type_id.code, self.journal_id.code)
            # TODO: maybe there is a better method for finding decent 2nd journal default invoice names
            if self.journal_id.code != 'INV':
                middle_code = self.journal_id.code[:3]
            return "%s%s-00000000" % (self.l10n_latam_document_type_id.doc_code_prefix, middle_code)

        return super()._get_starting_sequence()
    
    def _generate_portal_qr(self):
        self.ensure_one()
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        portal_url = self.get_portal_url()
        barcode = self.env['ir.actions.report'].barcode(barcode_type="QR", value=f"{base_url}{portal_url}", width=128, height=128, quiet=False)
        return image_data_uri(base64.b64encode(barcode))
    