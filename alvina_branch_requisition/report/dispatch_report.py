"""A read-only dispatch projection, authorized before its narrow cross-company read.

Requesters need not gain sales or inventory access to print their own request.
The report exposes only commercial prices and fulfillment for that request; no
sudo recordsets, customer contacts, costs or valuation layers reach QWeb.
"""
from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError
from odoo.tools import format_amount, format_date
from odoo.tools.image import image_data_uri


class DispatchReportLayout(models.Model):
    _inherit = 'ir.actions.report'

    def _get_layout(self):
        if len(self) == 1 and self.report_name in (
                'alvina_branch_requisition.dispatch_request',
                'alvina_branch_requisition.dispatch_sale',
                'alvina_branch_requisition.dispatch_note'):
            # This A4 report already carries all its styles and a data-URI logo.
            # It does not need an HTTP asset round trip while rendering a PDF.
            return self.env.ref('alvina_branch_requisition.dispatch_minimal_layout')
        return super()._get_layout()


class BranchRequestDispatch(models.Model):
    _inherit = 'alvina.branch.request'

    def _alvina_dispatch_payload(self):
        self.ensure_one()
        self.check_access('read')
        # The request rule checks its assigned branch and allowed companies.
        # Recheck membership even under report rendering contexts; a report
        # action must never turn a guessed request id into a commercial export.
        request = self.sudo()
        if not self.env.su and not self.branch_id._alvina_user_can_attend_supply():
            raise AccessError(_('No tiene acceso al despacho de esta sucursal.'))
        sale = request.sale_order_id
        if sale and (sale.alvina_branch_request_id != request
                     or sale.company_id != request.supply_company_id):
            raise UserError(_('La venta no corresponde a este requerimiento y abastecedor.'))
        company = request.supply_company_id
        currency = sale.currency_id if sale else request.currency_id
        lines = []
        bases = {'requested': [], 'delivered': [], 'pending': [], 'cancelled': []}
        for line in request.line_ids.sorted('id'):
            row = {
                'code': line.product_id.default_code or '',
                'product': line.product_id.name,
                'uom': line.product_uom_id.name,
                'requested': line.product_qty,
                'prepared': line.qty_prepared,
                'delivered': line.qty_supplied,
                'received': line.qty_received,
                'pending': line.qty_pending,
                'cancelled': line.qty_cancelled,
                'price_unit': line.price_unit,
                'discount': 0.0,
                'taxes': '',
            }
            if sale:
                sale_lines = line.sale_line_ids.filtered(
                    lambda item: item.order_id == sale and not item.display_type)
                if len(sale_lines) != 1:
                    raise UserError(_(
                        'Cada producto del despacho debe tener una línea comercial vinculada.'))
                sale_line = sale_lines[0]
                row.update(price_unit=sale_line.price_unit * line.product_uom_id._compute_quantity(
                               1, sale_line.product_uom_id, round=False),
                           discount=sale_line.discount,
                           taxes=', '.join(sale_line.tax_ids.mapped('name')))
                for key in bases:
                    qty = line.product_uom_id._compute_quantity(
                        row[key], sale_line.product_uom_id, round=False)
                    base = sale_line._prepare_base_line_for_taxes_computation(quantity=qty)
                    base['_dispatch_row'] = row
                    bases[key].append(base)
            else:
                # Historical request-only records have a price snapshot, not
                # a reviewed commercial tax treatment. Never invent IGV or
                # pretend they already had an order, receipt or stock transfer.
                for key in bases:
                    row[key + '_untaxed'] = currency.round(row[key] * line.price_unit)
                    row[key + '_total'] = row[key + '_untaxed']
            lines.append(row)

        totals = {}
        if sale:
            Tax = sale.env['account.tax']
            for key, base_lines in bases.items():
                Tax._add_tax_details_in_base_lines(base_lines, company)
                Tax._round_base_lines_tax_details(base_lines, company)
                summary = Tax._get_tax_totals_summary(base_lines, currency, company)
                totals[key] = {
                    'untaxed': currency.round(summary['base_amount_currency']),
                    'tax': currency.round(summary['tax_amount_currency']),
                    'total': currency.round(summary['total_amount_currency']),
                }
                for base in base_lines:
                    details = base['tax_details']
                    base['_dispatch_row'][key + '_untaxed'] = currency.round(details['total_excluded_currency'])
                    base['_dispatch_row'][key + '_total'] = currency.round(details['total_included_currency'])
        else:
            for key in bases:
                value = currency.round(sum(row[key + '_total'] for row in lines))
                totals[key] = {'untaxed': value, 'tax': 0.0, 'total': value}

        pickings = request._alvina_delivery_pickings().sorted('id')
        notes = []
        for picking in pickings:
            for name, label in (('alvina_dispatch_note', _('Observaciones')),
                                ('alvina_shortage_note', _('Motivo de faltantes'))):
                if picking[name]:
                    notes.append({'picking': picking.name, 'label': label,
                                  'text': picking[name]})
        for line in request.line_ids:
            for cancellation in line.cancellation_ids:
                notes.append({'picking': request.name, 'label': _('Cierre de faltante'),
                              'text': '%s: %s %s — %s' % (
                                  line.product_id.display_name, cancellation.quantity,
                                  line.product_uom_id.name, cancellation.reason)})
        delivery_labels = dict(request._fields['delivery_state']._description_selection(self.env))
        payload = {
            'request_id': request.id,
            'request': request.name,
            'purchase': request.purchase_order_id.name or '',
            'sale': sale.name or '',
            'company': company.name,
            'company_vat': company.vat or '',
            'logo': image_data_uri(company.logo) if company.logo and not company.uses_default_logo else '',
            'branch': request.branch_id.name,
            'branch_code': request.branch_id.branch_code or '',
            'requesting_company': request.company_id.name,
            'warehouse': request.warehouse_id.name,
            'request_date': format_date(self.env, fields.Date.to_date(request.request_date), date_format='dd/MM/yyyy'),
            'required_date': format_date(self.env, request.required_date, date_format='dd/MM/yyyy'),
            'printed_date': format_date(self.env, fields.Date.context_today(self), date_format='dd/MM/yyyy'),
            'delivery_state': delivery_labels.get(request.delivery_state, ''),
            'commercial_state': {'draft': 'Cotización', 'sent': 'Cotización enviada',
                                  'sale': 'Orden de venta', 'cancel': 'Cancelada'}.get(sale.state, '') if sale else '',
            'currency': currency.name,
            'commercial_taxes': bool(sale),
            'lines': lines,
            'totals': totals,
            'notes': notes,
            'request_note': request.note or '',
        }
        # Convert only projected monetary fields; no ORM object is returned.
        for row in lines:
            row['price_display'] = format_amount(self.env, row['price_unit'], currency)
            for key in bases:
                row[key + '_display'] = format_amount(self.env, row[key + '_total'], currency)
        for amounts in totals.values():
            for key in ('untaxed', 'tax', 'total'):
                amounts[key + '_display'] = format_amount(self.env, amounts[key], currency)
        return payload


class DispatchRequestReport(models.AbstractModel):
    _name = 'report.alvina_branch_requisition.dispatch_request'
    _description = 'Orden de despacho valorizada por requerimiento'

    @api.model
    def _get_report_values(self, docids, data=None):
        requests = self.env['alvina.branch.request'].browse(docids).exists()
        requests.check_access('read')
        return {'doc_ids': requests.ids, 'doc_model': 'alvina.branch.request',
                'documents': [dict(request._alvina_dispatch_payload(), report_id=request.id)
                              for request in requests]}


class DispatchSaleReport(models.AbstractModel):
    _name = 'report.alvina_branch_requisition.dispatch_sale'
    _description = 'Orden de despacho valorizada desde ventas'

    @api.model
    def _get_report_values(self, docids, data=None):
        sales = self.env['sale.order'].browse(docids).exists()
        sales.check_access('read')
        documents = []
        for sale in sales:
            # Access the explicitly linked request only. Its normal rule and
            # assignment remain mandatory even for a standard sales manager.
            request = sale.sudo().alvina_branch_request_id
            if not request or request.sale_order_id != sale:
                raise UserError(_('La venta no tiene un requerimiento Alviña vinculado.'))
            if not self.env.su and not request.branch_id.with_env(
                    self.env)._alvina_user_can_attend_supply():
                raise AccessError(_('No tiene acceso al despacho de esta sucursal.'))
            documents.append(dict(request._alvina_dispatch_payload(), report_id=sale.id))
        return {'doc_ids': sales.ids, 'doc_model': 'sale.order', 'documents': documents}
