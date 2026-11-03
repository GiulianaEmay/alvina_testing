# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
{
    'name': 'Alvinha - Invoicing',
    'version': '1.4',
    'summary': 'Customization of Invoices & Payments',
    'description': """
Invoicing & Payments
====================
The specific and easy-to-use Invoicing system in Odoo allows you to keep track of your accounting, even when you are not an accountant. It provides an easy way to follow up on your vendors and customers.

You could use this simplified accounting in case you work with an (external) account to keep your books, and you still want to keep track of payments. This module also offers you an easy method of registering payments, without having to encode complete abstracts of account.
    """,
    'category': 'Accounting/Accounting',
    'depends': [
        'alvinha_base',
            
    ],
    'data': [
        'data/l10n_latam_document_type_data.xml',
        
        'report/account_move_reports.xml',
        'report/account_move_templates.xml',

        'views/account_move_views.xml',
    ],
    'demo': [
    ],
    'installable': True,
    'assets': {
        'web.assets_backend': [
            'alvinha_account/static/src/services/*.js',
        ],
    },
    'author': 'SoyCalidad',
    'license': 'LGPL-3',
}
