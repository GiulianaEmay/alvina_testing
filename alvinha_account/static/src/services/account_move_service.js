import { AccountMoveService } from "@account/services/account_move_service";
import { patch } from "@web/core/utils/patch";
import { isIosApp, isIOS } from "@web/core/browser/feature_detection";

patch(AccountMoveService.prototype, {
    async downloadTicketPdf(accountMoveId, target = "download") {
        if (isIosApp() || isIOS()) {
            target = "_blank";
        }
        const downloadAction = await this.orm.call(
            "account.move",
            "action_invoice_download_ticket_pdf",
            [accountMoveId, target]
        );
        await this.action.doAction(downloadAction);
    },
});
