# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.


{
    'name': 'Alviña Manufacturing',
    'version': '2.1',
    'category': 'Supply Chain/Manufacturing',
    'summary': 'Manufacturing Orders & BOMs for Alviña',
    'depends': [
        'alvinha_base',
        'mrp', 
        'website',
    ],
    'data': [
        'security/ir.model.access.csv',
        
        'wizard/mrp_category_product_wizard_views.xml',
        'views/mrp_category_product_view.xml',
        'views/mrp_production_view.xml',
        'views/res_config_settings_view.xml',
        'views/sale_order_views.xml',
        'views/menu.xml',
        'views/portal_production_templates.xml',
        
        'report/mrp_category_product_reports.xml',
        'report/mrp_category_product_templates.xml',
        
    ],
    'assets': {
        'web.assets_frontend': [
            'alvinha_mrp/static/src/js/public_widget.js',
        ],
        'web.assets_backend': [
            #'alvinha_mrp/static/src/**/*',
        ],
        'web.report_assets_common': [
            'alvinha_base/static/src/scss/base.scss',
        ],
        'web.report_assets_pdf': [
            'alvinha_base/static/src/scss/base.scss',
        ],
        'web.assets_backend': [
            'alvinha_mrp/static/src/components/*',
        ],
    },
    'application': False,
    'author': 'Soy Calidad',
    'license': 'Other proprietary',
}
