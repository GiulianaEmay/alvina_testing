"""Mark historical committed benefits when upgrading from the initial release."""


def migrate(cr, version):
    cr.execute("""
        UPDATE pos_order AS orders
           SET alvina_confirmed = TRUE
         WHERE orders.state IN ('paid', 'done', 'invoiced')
           AND EXISTS (
               SELECT 1 FROM alvina_employee_discount AS usage
                WHERE usage.order_id = orders.id AND usage.state = 'consumed'
           )
    """)
