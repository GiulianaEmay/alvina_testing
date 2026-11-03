"""Separate branch move links from QR links without guessing shared row IDs."""


def migrate(cr, version):
    cr.execute("SELECT 1 FROM information_schema.columns WHERE table_name='stock_move' AND column_name='alvina_request_line_id'")
    if not cr.fetchone():
        return
    cr.execute("SELECT 1 FROM ir_module_module WHERE name='alvina_stock_request' AND state IN ('installed', 'to upgrade', 'to remove')")
    qr_installed = bool(cr.fetchone())
    cr.execute("SELECT 1 FROM information_schema.columns WHERE table_name='stock_move' AND column_name='alvina_branch_request_line_id'")
    new_exists = bool(cr.fetchone())
    if qr_installed:
        # The old name belongs to the installed QR module. A QR move is only
        # proven by its picking's QR request and that request's actual line.
        cr.execute("SELECT to_regclass('alvina_stock_request_line')")
        if not cr.fetchone()[0]:
            raise RuntimeError('Falta el esquema del módulo QR instalado; complete su actualización antes de continuar.')
        cr.execute('''SELECT m.id FROM stock_move m
                      LEFT JOIN stock_picking p ON p.id=m.picking_id
                      LEFT JOIN alvina_stock_request_line q ON q.id=m.alvina_request_line_id
                     WHERE m.alvina_request_line_id IS NOT NULL
                       AND (q.id IS NULL OR p.alvina_request_id IS NULL
                            OR q.request_id != p.alvina_request_id OR q.product_id != m.product_id)
                     LIMIT 1''')
        if cr.fetchone():
            raise RuntimeError(
                'Hay vínculos históricos compartidos entre QR y sucursales que no se pueden '
                'clasificar de forma inequívoca. Se conserva el historial; hace falta revisar esos movimientos.')
        # Remove only this addon's obsolete external ID. Never unlink the
        # shared ir.model.fields row or the QR column/data it still owns.
        cr.execute("DELETE FROM ir_model_data WHERE module='alvina_branch_requisition' AND name='field_stock_move__alvina_request_line_id' AND model='ir.model.fields'")
        return
    # No QR installation owns the old column: it is the former branch field.
    # Preserve every value and its metadata by renaming rather than dropping.
    if not new_exists:
        cr.execute('ALTER TABLE stock_move RENAME COLUMN alvina_request_line_id TO alvina_branch_request_line_id')
        cr.execute("UPDATE ir_model_fields SET name='alvina_branch_request_line_id' WHERE model='stock.move' AND name='alvina_request_line_id'")
        cr.execute("UPDATE ir_model_data SET name='field_stock_move__alvina_branch_request_line_id' WHERE module='alvina_branch_requisition' AND name='field_stock_move__alvina_request_line_id' AND model='ir.model.fields'")
    else:
        cr.execute('''SELECT id FROM stock_move WHERE alvina_request_line_id IS NOT NULL
                       AND alvina_branch_request_line_id IS NOT NULL
                       AND alvina_request_line_id != alvina_branch_request_line_id LIMIT 1''')
        if cr.fetchone():
            raise RuntimeError('Los dos campos históricos de sucursales tienen valores diferentes; revise su migración.')
        cr.execute('''UPDATE stock_move SET alvina_branch_request_line_id=alvina_request_line_id
                      WHERE alvina_branch_request_line_id IS NULL AND alvina_request_line_id IS NOT NULL''')
        cr.execute("DELETE FROM ir_model_data WHERE module='alvina_branch_requisition' AND name='field_stock_move__alvina_request_line_id' AND model='ir.model.fields'")
