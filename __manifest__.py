# -*- coding: utf-8 -*-
{
    'name': "Reporte Riesgo de cliente",

    'summary': """
        Reporte Riesgo de cliente""",

    'description': """
        Reporte Riesgo de cliente 
    """,

    'author': "GonzaOdoo",
    'website': "http://www.yourcompany.com",

    # Categories can be used to filter modules in modules listing
    # Check https://github.com/odoo/odoo/blob/master/odoo/addons/base/module/module_data.xml
    # for the full list
    'category': 'Uncategorized',
    'version': '1.0',

    # any module necessary for this one to work correctly
    'depends': ['account'],

    # always loaded
    "data": ["security/ir.model.access.csv",
             'views/report_views.xml',
            ],
}
