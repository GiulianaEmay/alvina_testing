from odoo import http, _
from odoo.exceptions import UserError
from odoo.http import request, content_disposition


import io
import zipfile

def _get_headers(filename, filetype, content):
    return [
        ('Content-Type', filetype),
        ('Content-Length', len(content)),
        ('Content-Disposition', content_disposition(filename)),
        ('X-Content-Type-Options', 'nosniff'),
    ]
    
def _build_zip_from_data(docs_data):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as zipfile_obj:
        for doc_data in docs_data:
            zipfile_obj.writestr(doc_data['filename'], doc_data['content'])
    return buffer.getvalue()

class AccountDocumentDownloadController(http.Controller):

    @http.route('/account/download_invoice_ticket_documents/<models("account.move"):invoices>/<string:filetype>', type='http', auth='user')
    def download_invoice_ticket_documents_filetype(self, invoices, filetype, allow_fallback=True):
        invoices.check_access('read')
        invoices.line_ids.check_access('read')
        docs_data = []
        for invoice in invoices:
            #todo: all
            if filetype == 'all' and (doc_data := invoice._get_invoice_legal_documents_all(allow_fallback=allow_fallback)):
                docs_data += doc_data
            elif doc_data := invoice._get_invoice_pdf_ticket():
                if (errors := doc_data.get('errors')) and len(invoices) == 1:
                    raise UserError(_("Error while creating XML:\n- %s", '\n- '.join(errors)))
                docs_data.append(doc_data)
        if len(docs_data) == 1:
            doc_data = docs_data[0]
            headers = _get_headers(doc_data['filename'], doc_data['filetype'], doc_data['content'])
            return request.make_response(doc_data['content'], headers)
        if len(docs_data) > 1:
            zip_content = _build_zip_from_data(docs_data)
            headers = _get_headers(_('invoices') + '.zip', 'zip', zip_content)
            return request.make_response(zip_content, headers)
