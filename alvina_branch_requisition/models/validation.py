"""Pure validation shared by the controller and executable local tests."""
import math
import re
from decimal import Decimal, InvalidOperation

CATEGORIES = ('Comerciales', 'Útiles de escritorio', 'Útiles de limpieza', 'Envases y embalajes')

def natural_key(code, name, identifier):
    return (not bool(code), tuple((0, int(p), len(p)) if p.isdigit() else (1, p.casefold())
            for p in re.split(r'(\d+)', code or '') if p), name.casefold(), identifier)

def quantity(value, rounding):
    try:
        q, step = Decimal(str(value)), Decimal(str(rounding))
        if not q.is_finite() or q < 0 or not step.is_finite() or step <= 0:
            raise ValueError('Cantidad inválida')
        if q % step:
            raise ValueError('La cantidad no respeta la unidad de medida')
        result = float(q)
        if not math.isfinite(result):
            raise ValueError('Cantidad fuera de rango')
        return result
    except (InvalidOperation, TypeError):
        raise ValueError('Cantidad inválida') from None

def pending(requested, supplied):
    return max(requested - supplied, 0)

def suggested(demand, free_stock, incoming):
    return max(demand - max(free_stock, 0) - max(incoming, 0), 0)
