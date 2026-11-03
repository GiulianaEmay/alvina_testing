/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
patch(PaymentScreen.prototype, {
    get tipoRecibo() {
        const client = this.currentOrder?.getPartner()
        if (client && client.vat && client.vat.length==11) {
            return 'Factura'
        }
        else if (client && client.vat && (client.vat.length==8 || client.vat.length==9)) {
            return 'Boleta'
        } else if (!client) {
            return 'Sin cliente';
        }

        else {
            return 'Recibo'
        }
    }
})