# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

{
    'name': 'Alvinha - Point of Sale',
    'version': '1.0.1',
    'category': 'Sales/Point of Sale',
    'summary': 'Handle journal for POS. Handle checkouts and payments for shops and restaurants. Added dinamic cunsumidor final ',
    'depends': [
        'alvinha_base',
    ],
    'data': [
        'views/res_config_settings_views.xml',
        'views/pos_config_views.xml',
        'views/pos_session_views.xml',
        'views/pos_payment_method_views.xml',

        'data/ir_cron.xml',
    ],
    'demo': [
    ],
    'installable': True,
    'assets': {
        # Main PoS assets, they are loaded in the PoS UI
        'point_of_sale._assets_pos': [
            "alvinha_point_of_sale/static/src/**",
            # account
            'alvinha_account/static/src/services/account_move_service.js',
        ],
    },
    'author': 'SoyCalidad',
    'license': 'LGPL-3',
}
