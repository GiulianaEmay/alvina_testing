from odoo import models, fields, _, api
from odoo.exceptions import ValidationError, UserError
from odoo.tools import float_is_zero, float_compare

import logging
from datetime import timedelta
from random import randrange

_logger = logging.getLogger(__name__)

class PosOrder(models.Model):
    _inherit = "pos.order"
    
    is_pending_invoice = fields.Boolean(
        default=False,
        string="Esta pendiente de generar la factura",
    )


    # def _process_saved_order(self, draft):
    #     self.ensure_one()

    #     if any(p.payment_method_id.auto_invoice for p in self.payment_ids) and len(self.payment_ids)==1:
    #         self.is_pending_invoice = True
    #     return super()._process_saved_order(draft)

    def _generate_pos_order_invoice(self):
        return super(PosOrder,  self.with_context(skip_pe_pos_immediate_send=True, generate_pdf=False))._generate_pos_order_invoice()


    def _prepare_invoice_vals(self):
        vals = super()._prepare_invoice_vals()

    
        vals['journal_id'] = self.session_id.config_id._get_journal(self.partner_id, self.refunded_order_id).id
        
            
        if vals.get('move_type') == 'out_invoice' and self.country_code == 'PE':
            if (len(self.partner_id.vat)==8 or len(self.partner_id.vat)==9) and self.session_id.config_id.ballot_journal_id:
                doc_ballot = self.env.ref('l10n_pe.document_type02')
                vals['l10n_latam_document_type_id'] = doc_ballot.id if doc_ballot else False
            else:
                doc_invoice = self.env.ref('l10n_pe.document_type01')
                vals['l10n_latam_document_type_id'] = doc_invoice.id if doc_invoice else False
                
                
        if vals.get('move_type') == 'out_refund' and self.country_code == 'PE':
            if self.refunded_order_id.account_move:
                
                doc_nota_credito = self.env.ref('l10n_pe.document_type07')
                if self.refunded_order_id.account_move.l10n_latam_document_type_id.id == self.env.ref('l10n_pe.document_type02').id: 
                    doc_nota_credito = self.env.ref('l10n_pe.document_type07b') #Nota de Crédito Boleta electrónica
                # vals['l10n_pe_edi_refund_document_type_id'] = doc_nota_credito.id if doc_nota_credito else False
                vals['l10n_latam_document_type_id'] = doc_nota_credito.id if doc_nota_credito else False
                vals['l10n_pe_edi_refund_reason'] = "01"
                
        # _logger.info(f"Vals , {vals}")
        return vals


    @api.model
    def cron_generate_auto_invoices(self):
        """Genera automáticamente los comprobantes de órdenes POS que:
        - estén pagadas y sin comprobante,
        - tengan marcado "Facturar" (to_invoice),
        - y usen un método de pago con facturación automática
          (o estén marcadas como pendientes de facturar).
        Las órdenes sin to_invoice no se facturan ni se envían a SUNAT.
        """
        date_from = fields.Datetime.now() - timedelta(days=3)
        LIMIT_ORDERS = 20

        processed_ids = set()
        failed_order_ids = set()

        base_domain = [
            ("state", "in", ["paid", "done"]),
            ("account_move", "=", False),
            ("date_order", ">=", date_from),
            ("to_invoice", "=", True),
            "|",
            ("is_pending_invoice", "=", True),
            ("payment_ids.payment_method_id.auto_invoice", "=", True),
        ]

        while True:
            domain = list(base_domain)
            if processed_ids:
                # Excluir todo lo ya procesado en esta ejecución (con o sin
                # error) para que ninguna orden se repita y el bucle termine.
                domain.append(("id", "not in", list(processed_ids)))

            orders = self.search(domain, limit=LIMIT_ORDERS, order="id")
            if not orders:
                break

            for order in orders:
                processed_ids.add(order.id)
                try:
                    with self.env.cr.savepoint():
                        order.action_pos_order_invoice()
                    if order.account_move:
                        order.is_pending_invoice = False
                    else:
                        failed_order_ids.add(order.id)
                        _logger.warning(
                            "La orden %s (%s) se procesó pero no generó comprobante",
                            order.name,
                            order.id,
                        )
                except Exception:
                    failed_order_ids.add(order.id)
                    _logger.exception(
                        "No fue posible generar la factura de la orden %s (%s)",
                        order.name,
                        order.id,
                    )

            self.env.cr.commit()

        if failed_order_ids:
            _logger.warning(
                "Cron POS Auto Invoice: No fue posible generar la factura para las órdenes POS con IDs: %s",
                sorted(failed_order_ids),
            )
