# -*- encoding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

{
    'name': 'Alvinha - MRP II',
    'version': '1.0',
    'category': 'Supply Chain/Manufacturing',
    'sequence': 51,
    'summary': """Customization for Alvinha. Work Orders, Planning, Stock Reports.""",
    'depends': [
        'alvinha_base',
    ],
    'description': """Customization for Alvinha. Enterprise extension for MRP
* Work order planning.  Check planning by Gantt views grouped by production order / work center
* Traceability report
* Cost Structure report (mrp_account)""",
    'data': [ 
    ],
    'demo': [ 
    ],
    'author': 'SoyCalidad',
    'license': 'Other proprietary',
    'assets': {
        'web.assets_backend': [
            'alvinha_mrp_workorder/static/src/**/*.scss',
            'alvinha_mrp_workorder/static/src/**/*.js',
            'alvinha_mrp_workorder/static/src/**/*.xml',
            # ('remove', 'alvinha_mrp_workorder/static/src/mrp_workorder_gantt_*'),
        ], 
    }
}
