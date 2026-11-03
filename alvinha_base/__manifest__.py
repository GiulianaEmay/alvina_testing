# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.


{
    'name': 'Base Alvinha',
    'version': '1.3',
    'category': 'Hidden',
    'description': """
    The kernel of Alviña addons, needed for all installation.
    ===================================================
    - Add relation between sale.order to mrp.production 
    - Add mixing for delete records
    - Add fields to res.partner and product.template 
    
    """,
    'depends': [
        'sale',
        'base',
        'mail', 'uom', 'product',
        'mrp', 'mrp_workorder',
        'account',
        'stock',
        'l10n_pe',
        
        'sale_management', 
        'pos_restaurant', 
        'website','purchase', 'point_of_sale',
        'hr', 'contacts',  'l10n_pe_edi', 'l10n_pe_edi_pos', 'l10n_pe_edi_stock', 'l10n_pe_pos', 
        'l10n_pe_reports', 'l10n_pe_reports_stock',
        'mrp_account_enterprise',
        
    ],
    'data': [
        "security/ir.model.access.csv",
        
        "data/report_paperformat.xml",
        "data/decimal_precision_data.xml",
        "data/freq_day.xml",
        
        
        "views/product_template_view.xml",
        "views/res_partner_view.xml",
        "views/sale_order_views.xml",
    ],
    'assets': {

    },
    'installable': True,
    'auto_install': True,
    'author': 'SoyCalidad',
    'license': 'Other proprietary',
}