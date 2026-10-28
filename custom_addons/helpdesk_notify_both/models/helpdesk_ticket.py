# Copyright 2026 RLFB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from odoo import models
from odoo.tools.misc import clean_context

# Above this many e-mails in one go, leave them to the mail queue instead of
# sending at the end of the request (core uses the same default).
FORCE_SEND_LIMIT = 100


class HelpdeskTicket(models.Model):
    _inherit = "helpdesk.ticket"

    def _track_subtype(self, init_values):
        """A stage change is posted as "Stage Changed" rather than an internal
        note, so followers who have that subtype are notified."""
        self.ensure_one()
        if "stage_id" in init_values and self.stage_id:
            return self.env.ref("helpdesk_notify_both.hlp_tck_stage")
        return super()._track_subtype(init_values)

    def _notify_thread_by_email(
        self,
        message,
        recipients_data,
        msg_vals=False,
        mail_auto_delete=True,
        model_description=False,
        force_email_company=False,
        force_email_lang=False,
        subtitles=None,
        resend_existing=False,
        force_send=True,
        send_after_commit=True,
        **kwargs,
    ):
        res = super()._notify_thread_by_email(
            message,
            recipients_data,
            msg_vals=msg_vals,
            mail_auto_delete=mail_auto_delete,
            model_description=model_description,
            force_email_company=force_email_company,
            force_email_lang=force_email_lang,
            subtitles=subtitles,
            resend_existing=resend_existing,
            force_send=force_send,
            send_after_commit=send_after_commit,
            **kwargs,
        )
        self._notify_inbox_users_by_email(
            message,
            recipients_data,
            msg_vals=msg_vals,
            mail_auto_delete=mail_auto_delete,
            model_description=model_description,
            force_email_company=force_email_company,
            force_email_lang=force_email_lang,
            subtitles=subtitles,
            force_send=force_send,
            send_after_commit=send_after_commit,
        )
        return res

    def _notify_inbox_users_by_email(
        self,
        message,
        recipients_data,
        msg_vals=False,
        mail_auto_delete=True,
        model_description=False,
        force_email_company=False,
        force_email_lang=False,
        subtitles=None,
        force_send=True,
        send_after_commit=True,
    ):
        """Send the e-mail copy to internal users who handle notifications in
        Odoo. They already have their inbox notification (and desktop pop-up).

        Odoo allows one <mail.notification> per message and partner, and the
        inbox one exists, so this builds the e-mail the same way core does
        but creates no second notification row. The e-mail is therefore not
        tracked in the message's delivery status; it is in Settings >
        Technical > Emails like any other.
        """
        inbox_users = [
            dict(rdata, notif="email")
            for rdata in recipients_data
            if rdata["notif"] == "inbox" and rdata["active"] and not rdata["share"]
        ]
        if not inbox_users:
            return self.env["mail.mail"]
        with_email = set(
            self.env["res.partner"]
            .sudo()
            .browse([rdata["id"] for rdata in inbox_users])
            .filtered("email")
            .ids
        )
        inbox_users = [rdata for rdata in inbox_users if rdata["id"] in with_email]
        if not inbox_users:
            return self.env["mail.mail"]

        base_mail_values = self._notify_by_email_get_base_mail_values(
            message, additional_values={"auto_delete": mail_auto_delete}
        )
        SafeMail = self.env["mail.mail"].sudo().with_context(clean_context(self._context))
        emails = SafeMail
        for _lang, render_values, recipients_group in (
            self._notify_get_classified_recipients_iterator(
                message,
                inbox_users,
                msg_vals=msg_vals,
                model_description=model_description,
                force_email_company=force_email_company,
                force_email_lang=force_email_lang,
                subtitles=subtitles,
            )
        ):
            body = self._notify_by_email_render_layout(
                message,
                recipients_group,
                msg_vals=msg_vals,
                render_values=render_values,
            )
            recipient_ids = recipients_group.pop("recipients")
            if not recipient_ids:
                continue
            emails |= SafeMail.create(
                self._notify_by_email_get_final_mail_values(
                    recipient_ids, base_mail_values, additional_values={"body_html": body}
                )
            )

        # Same rule as core: send now when there are few, otherwise the queue
        # picks them up; never during a command-line update of the database.
        force_send = self.env.context.get("mail_notify_force_send", force_send)
        if emails and force_send and len(emails) < FORCE_SEND_LIMIT and not self.pool._init:
            if send_after_commit:
                emails.send_after_commit()
            else:
                emails.send()
        return emails
