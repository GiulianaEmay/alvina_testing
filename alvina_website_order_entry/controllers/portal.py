"""Extend the controller actually supplied by ALVIÑA, without copying its flow."""

from odoo import http
from odoo.http import request
from odoo.addons.alvinha_mrp.controllers.main import PortalProductionController

from ..services.order_date import (
    InvalidOrderDate,
    new_order_date_values,
    validate_new_order_date,
)


class AlvinaOrderEntryController(PortalProductionController):
    @property
    def env(self):
        # The supplied upstream controller calls self.env in the GET and in
        # notifications. Standard http.Controller only exposes request.env.
        # Keep those inherited methods working without altering the source addon.
        return request.env

    @http.route(auth="user")
    def portal_production_form(self, **kw):
        response = super().portal_production_form(**kw)
        # request.render is lazy: replace only this new-form value before QWeb.
        response.qcontext["today"] = new_order_date_values()["default_date"]
        return response

    @http.route(auth="user", methods=["POST"])
    def portal_create_production(self, **post):
        # The original frontend already supplies csrf_token in its JSON-RPC
        # payload. JSON-RPC does not use the HTTP form dispatcher CSRF check,
        # so validate it explicitly before invoking any inherited business code.
        token = post.get("csrf_token")
        try:
            valid_token = isinstance(token, str) and request.validate_csrf(token)
        except (TypeError, ValueError):
            valid_token = False
        if not valid_token:
            return {"error": "La sesión del formulario venció. Recarga la página e intenta nuevamente."}
        try:
            selected = validate_new_order_date(post.get("date_order"))
        except InvalidOrderDate as exc:
            # Existing JS displays result.error and retains all form selections.
            return {"error": str(exc)}
        post["date_order"] = selected.isoformat()
        return super().portal_create_production(**post)
