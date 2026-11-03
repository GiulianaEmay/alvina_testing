"""Private transaction capabilities; JSON-RPC context cannot set these."""
from contextvars import ContextVar

NATIVE_SUPPLY = ContextVar('alvina_native_supply', default=None)


def native_sale_expected():
    scope = NATIVE_SUPPLY.get()
    return bool(scope and scope['phase'] == 'purchase' and scope['purchase_id'])
