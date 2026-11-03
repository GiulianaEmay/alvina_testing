import { patch } from "@web/core/utils/patch";
import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";
import { EmployeeDiscountPopup } from "./employee_discount_popup";

patch(ControlButtons.prototype, {
    async clickFiscalPosition() {
        const order = this.currentOrder;
        const previous = order.fiscal_position_id?.id;
        const result = await super.clickFiscalPosition(...arguments);
        if (previous !== order.fiscal_position_id?.id) {
            order.alvinaInvalidate();
        }
        return result;
    },
    alvinaDiscount() {
        this.props.close?.();
        this.dialog.add(EmployeeDiscountPopup, { pos: this.pos, order: this.pos.getOrder() });
    },
});
