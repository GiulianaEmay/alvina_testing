{
    'name': 'ALVIÑA — Descuento de Colaboradores',
    'version': '19.0.1.1.2',
    'category': 'Sales/Point of Sale',
    'summary': 'Beneficio semanal centralizado de colaboradores, con reservas transaccionales',
    'author': 'ALVIÑA',
    'license': 'LGPL-3',
    'depends': ['point_of_sale', 'hr'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/defaults.xml',
        'views/hr_employee_views.xml',
        'views/employee_discount_views.xml',
        'views/res_config_settings_views.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'alvina_pos_employee_discount/static/src/app/*.js',
            'alvina_pos_employee_discount/static/src/app/*.xml',
            'alvina_pos_employee_discount/static/src/app/*.scss',
        ],
    },
    'installable': True,
    'application': False,
}
