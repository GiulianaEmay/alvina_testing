/** @odoo-module */

import OrderPaymentValidation from "@point_of_sale/app/utils/order_payment_validation";
import { AlertDialog, ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import { serializeDateTime } from "@web/core/l10n/dates";



patch(OrderPaymentValidation.prototype, {
    async finalizeValidation() {
            if (this.order.isPaidWithCash() || this.order.change) {
                this.pos.hardwareProxy.openCashbox();
            }
    
            this.order.date_order = serializeDateTime(luxon.DateTime.now());
            for (const line of this.paymentLines) {
                if (!line.amount === 0) {
                    this.order.removePaymentline(line);
                }
            }
    
            this.pos.addPendingOrder([this.order.id]);
            this.order.state = "paid";
    
            try {
                // 1. Save order to server.
                const syncOrderResult = await this.pos.syncAllOrders({ throw: true });
                if (!syncOrderResult) {
                    return false;
                }
    
                // 2. Invoice, should not stop the validation process but a dialog is shown if an
                // error occured.
                if (this.shouldDownloadInvoice() && this.order.isToInvoice()) {
                    if (this.order.raw.account_move) {
                        await this.pos.env.services.account_move.downloadTicketPdf(
                            this.order.raw.account_move
                        );
                    } else {
                        // this.pos.dialog.add(AlertDialog, {
                        //     title: _t("Backend Invoice"),
                        //     body: _t(
                        //         "An error occurred while generating an invoice. You can try again from the order list."
                        //     ),
                        // });
                    }
                }
    
                // 3. Post process.
                const postPushOrders = syncOrderResult.filter((order) => order.waitForPushOrder());
                if (postPushOrders.length > 0) {
                    await this.postPushOrderResolve(postPushOrders.map((order) => order.id));
                }
    
                return await this.afterOrderValidation(!!syncOrderResult && syncOrderResult.length > 0);
            } catch (error) {
                return this.handleValidationError(error);
            }
    },
    async afterOrderValidation() {
        // Always show the next screen regardless of error since pos has to
        // continue working even offline.
        if (!this.pos.config.module_pos_restaurant) {
            this.pos.checkPreparationStateAndSentOrderInPreparation(this.order, {
                orderDone: true,
            });
        }

        if (this.order.nb_print === 0 && this.pos.config.iface_print_auto) {
            const invoiced_finalized = this.order.isToInvoice() ? this.order.finalized : true;
            if (invoiced_finalized) {
                await this.pos.printReceipt({
                    basic: this.pos.config.basic_receipt, 
                    order: this.order,
                });

            }
        }
    }
});