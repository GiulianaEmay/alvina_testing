
{
    'name': 'Alvinha - Sales',
    'version': '1.0',
    'category': 'Sales/Sales',
    'sequence': 5,
    'summary': 'Custom for alvinha. From quotations to invoices',
    'description': """
    Manage sales quotations and orders
    ==================================

    """,
    'website': 'https://www.odoo.com/app/sales',
    'depends': ['sale', 'digest', 'alvinha_base'],
    'data': [
        "views/sale_order_view.xml",
        "views/sale_portal_templates.xml",
        
    ],
    'demo': [
    ],
    'assets': {
    },
    'application': False,
    'author': 'SoyCalidad',
    'license': 'LGPL-3',
}
