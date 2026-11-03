{
    "name": "ALVIÑA - Acceso a pedidos",
    "summary": "Acceso web a pedidos y política reutilizable de fechas Lima",
    "version": "19.0.1.3.0",
    "category": "Website",
    "author": "Grupo ALVIÑA",
    "license": "AGPL-3",
    "depends": ["website", "alvinha_mrp"],
    "data": ["views/website_templates.xml", "views/production_form.xml"],
    "assets": {
        "web.assets_frontend": [
            "alvina_website_order_entry/static/src/scss/order_entry.scss",
        ],
    },
    "installable": True,
    "application": False,
}
