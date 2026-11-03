/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/services/pos_store";


patch(PosStore.prototype, {
    async validateOrderFast(paymentMethod) {
        if (paymentMethod.auto_invoice) {
            
            const order = this.getOrder();
            if (order) {
                order.setToInvoice(true);
            }
        }

        return super.validateOrderFast(paymentMethod);
    },
});