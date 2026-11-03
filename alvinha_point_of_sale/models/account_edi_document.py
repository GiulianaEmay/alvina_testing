from odoo import models

import logging 

_logger = logging.getLogger(__name__)
class AccountEdiDocument(models.Model):
    _inherit = 'account.edi.document'

    def _process_documents_web_services(self, with_commit=True, job_count=None):
        
        if self.env.context.get('skip_pe_pos_immediate_send'):
            return
        return super()._process_documents_web_services(with_commit=with_commit, job_count=job_count)