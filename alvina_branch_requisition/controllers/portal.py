import json
import secrets
from psycopg2 import IntegrityError
from odoo import http, Command, _
from odoo.http import request
from odoo.exceptions import AccessError, ValidationError
from ..models.validation import CATEGORIES, natural_key, quantity

class RequisitionPortal(http.Controller):
    def _authorize(self):
        if not request.env.user.has_group('alvina_branch_requisition.group_requester'):
            raise AccessError(_('No tiene permiso para solicitar productos.'))
        # Website narrows allowed_company_ids to the website company or the
        # user's default company. An assigned branch may belong to another of
        # the user's permitted companies. Restore only this user's real company
        # scope for our routes; never change the session, standard RPC context,
        # or the user's company memberships. env.user is the current identity
        # in Odoo's privileged user-information environment, not a sudoed model
        # used to search branches or create requests.
        company_ids = list(request.env.user._get_company_ids())
        default_company_id = request.env.user.company_id.id
        if default_company_id in company_ids:
            company_ids.remove(default_company_id)
            company_ids.insert(0, default_company_id)
        request.update_context(allowed_company_ids=company_ids)

    def _branches(self):
        return request.env['alvina.branch.binding'].search([
            ('user_ids', 'in', request.env.uid), ('company_id', 'in', request.env.companies.ids)]).filtered(
                lambda branch: branch._resource_is_available())

    def _products(self, company, branch=None):
        # Narrow sudo: expose only name/code/UoM and IDs of explicitly enabled products.
        # Portal users have no general product catalog access. Creation uses normal ACLs.
        domain = [
            ('active', '=', True), ('product_tmpl_id.active', '=', True),
            ('alvina_requisition_enabled', '=', True), ('categ_id.name', 'in', CATEGORIES),
            ('company_id', 'in', [False, company.id])]
        configuration = request.env['alvina.supply.configuration']._get_configuration()
        uniform_flow = configuration and configuration.enabled
        if branch and (branch.generate_purchase_rfq or uniform_flow):
            # A standard purchase in the requesting company cannot reference a
            # product private to the supplier. No product ownership is changed.
            domain += [('company_id', '=', False), ('purchase_ok', '=', True)]
        if uniform_flow:
            domain += [('sale_ok', '=', True), ('type', '=', 'consu')]
        return request.env['product.product'].sudo().search(domain)

    def _render(self, error=None):
        branches = self._branches()
        token = secrets.token_urlsafe(32)
        tokens = request.session.get('alvina_form_tokens', [])[-9:]
        request.session['alvina_form_tokens'] = tokens + [token]
        catalogs = {}
        for branch in branches:
            products = sorted(self._products(branch.supply_company_id, branch), key=lambda p: natural_key(p.default_code, p.name, p.id))
            catalogs[str(branch.id)] = [{'id': p.id, 'name': p.display_name, 'category': p.categ_id.name,
                'uom': p.uom_id.name, 'rounding': p.uom_id.rounding} for p in products]
        return request.render('alvina_branch_requisition.form', {
            'branches': branches, 'categories': CATEGORIES, 'catalogs': json.dumps(catalogs),
            'submission_key': token, 'error': error,
            'minimum_required_date': request.env['alvina.branch.request']._minimum_required_date()})

    @http.route('/branch/requisition/new', type='http', auth='user', website=True, methods=['GET'])
    def new(self, **kw):
        self._authorize()
        return self._render()

    @http.route('/branch/requisition/submit', type='http', auth='user', website=True, methods=['POST'], csrf=True)
    def submit(self, **post):
        self._authorize()
        token = post.get('submission_key', '')
        if token not in request.session.get('alvina_form_tokens', []):
            return self._render(_('Formulario vencido o ya enviado.'))
        try:
            branch = self._branches().filtered(lambda b: str(b.id) == post.get('branch_id'))
            if len(branch) != 1:
                raise ValidationError(_('Sucursal no autorizada.'))
            raw = json.loads(post.get('lines', '[]'))
            if not isinstance(raw, list) or len(raw) > 10000:
                raise ValueError('Detalle inválido')
            products = {p.id: p for p in self._products(branch.supply_company_id, branch)}
            seen, commands = set(), []
            for item in raw:
                pid = item['id']
                if type(pid) is not int or pid in seen or pid not in products:
                    raise ValueError('Producto inválido o repetido')
                seen.add(pid)
                qty = quantity(item['qty'], products[pid].uom_id.rounding)
                if qty:
                    commands.append(Command.create({'product_id': pid, 'product_qty': qty}))
            with request.env.cr.savepoint():
                rec = request.env['alvina.branch.request'].with_company(branch.company_id).create({
                    'branch_id': branch.id, 'required_date': post.get('required_date'),
                    'note': post.get('note', ''), 'submission_key': token, 'line_ids': commands})
            request.session['alvina_last_request'] = rec.id
        except IntegrityError:
            return self._render(_('El formulario ya fue registrado. No vuelva a enviarlo.'))
        except (ValueError, TypeError, KeyError, ValidationError) as exc:
            return self._render(str(exc))
        request.session['alvina_form_tokens'] = [t for t in request.session.get('alvina_form_tokens', []) if t != token]
        return request.redirect('/branch/requisition/success')

    @http.route('/branch/requisition/success', type='http', auth='user', website=True, methods=['GET'])
    def success(self, **kw):
        self._authorize()
        rec = request.env['alvina.branch.request'].browse(request.session.get('alvina_last_request', 0)).exists()
        rec.check_access('read')
        return request.render('alvina_branch_requisition.success', {'record': rec})
