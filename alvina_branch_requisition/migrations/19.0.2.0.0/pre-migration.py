"""Preserve recorded legacy fulfillment before switching to Inventory computes."""
def migrate(cr, version):
    # A separately published alternative used this verified relation and the
    # same version number. Do not silently discard its keeper assignments.
    cr.execute("SELECT to_regclass('alvina_binding_keeper_rel')")
    if cr.fetchone()[0]:
        raise RuntimeError(
            'Se detectó la variante feature/central-supply-multicompany. '
            'Se requiere una migración específica antes de actualizar; '
            'no se modificarán sus asignaciones ni su historial.')
    cr.execute('ALTER TABLE alvina_branch_request_line ADD COLUMN IF NOT EXISTS legacy_qty_supplied double precision')
    cr.execute('''UPDATE alvina_branch_request_line
                     SET legacy_qty_supplied = COALESCE(qty_supplied, 0)
                   WHERE legacy_qty_supplied IS NULL''')
