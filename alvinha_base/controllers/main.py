# -*- coding: utf-8 -*-
from odoo import http, _
from odoo.http import request
import json
from werkzeug.utils import redirect
from datetime import date, timedelta, datetime, time
from odoo import fields
import pytz
from werkzeug.exceptions import Forbidden

import logging 

_logger = logging.getLogger(__name__)

class PortalProductionController(http.Controller):
    
    @http.route('/portal/get_partners', type='jsonrpc', auth='public', csrf=False)
    def get_partners(self, company_id=None):

        if not company_id:
            raise Forbidden()

        try:
            company_id = int(company_id)
        except:
            raise Forbidden()

        # Only allow companies (extra validation)
        company = request.env['res.company'].sudo().browse(company_id)

        if not company.exists():
            raise Forbidden()

        # Return only allowed fields
        partners = request.env['res.partner'].sudo().search([
            '|',
            ('company_id', '=', company_id),
            ('company_id', '=', False),
            ('active', '=', True),
            ('is_customer', '=', True),
            ('type_client', '!=', False),  # Solo clientes con tipo de cliente establecido
        ])

        return [{
            'id': p.id,
            'name': p.alias or p.name,
        } for p in partners]
