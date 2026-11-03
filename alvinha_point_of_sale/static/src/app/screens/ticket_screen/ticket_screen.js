/** @odoo-module */

import { TicketScreen } from "@point_of_sale/app/screens/ticket_screen/ticket_screen";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";

patch(TicketScreen.prototype, {
    async print(order) {
        await this.pos.printReceipt({ basic: this.pos.config.basic_receipt, order: order });
    }
})
