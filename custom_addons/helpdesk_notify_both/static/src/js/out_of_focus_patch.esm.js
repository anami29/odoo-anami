/* Copyright 2026 RLFB
 * License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl). */

/*
 * While an Odoo tab is open in the background, Odoo shows the desktop pop-up
 * from the page itself: the author's name as the title and the message text
 * as the body. "Ticket created" and stage-change messages have no text, only
 * a subtype and tracked values, so the pop-up came out as just a name, and
 * the browser filled the empty body with the site address.
 *
 * For helpdesk tickets, name the ticket in the title and say what happened in
 * the body. The message is handed on as a plain object so the rest of the
 * service (unread counter in the tab title, permission checks) is unchanged.
 */
import {OutOfFocusService} from "@mail/core/common/out_of_focus_service";
import {patch} from "@web/core/utils/patch";

function helpdeskSummary(message) {
    const parts = [];
    if (message.subtype_description) {
        parts.push(message.subtype_description);
    }
    for (const tracking of message.trackingValues || []) {
        const before = tracking.oldValue?.value;
        const after = tracking.newValue?.value;
        parts.push(
            before === after || before === undefined || before === false || before === ""
                ? `${tracking.changedField}: ${after}`
                : `${tracking.changedField}: ${before} → ${after}`
        );
    }
    return parts;
}

patch(OutOfFocusService.prototype, {
    notify(message, thread) {
        if (message.thread?.model !== "helpdesk.ticket") {
            return super.notify(...arguments);
        }
        const author = message.author;
        const ticket = message.thread.displayName;
        const parts = helpdeskSummary(message);
        // Keep the text of a real message (a reply, a mention) after the summary.
        const text = document.createElement("div");
        text.innerHTML = message.body || "";
        if (text.textContent.trim()) {
            parts.push(text.textContent.trim());
        }
        const escape = (s) =>
            String(s).replace(/[&<>"]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"})[c]);
        return super.notify(
            {
                localId: message.localId,
                thread: message.thread,
                author: author && {
                    name: ticket ? `${author.name} · ${ticket}` : author.name,
                    avatarUrl: author.avatarUrl,
                },
                body: escape(parts.join(" · ") || ticket || ""),
            },
            thread
        );
    },
});
