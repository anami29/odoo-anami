from odoo import _, api, models
from odoo.exceptions import UserError

RETENTION_DAYS = 8 * 366  # BR-AUD-03, BR-NFR-06: kept 8 years

AUDITED_MODELS = [
    "tms.consignment", "tms.consignment.line", "tms.ewaybill", "tms.order", "tms.trip.consignment",
    "tms.trip.assignment", "tms.trip.breakdown", "tms.trip.incident", "tms.trip.charge", "tms.trip.expense",
    "tms.driver.advance", "tms.driver.settlement", "tms.damage.claim", "tms.vendor.bill.match",
    "tms.compliance.document", "tms.compliance.document.type", "res.company", "tms.gta.option",
    "purchase.order", "tms.market.vehicle",
]


class AuditlogLog(models.Model):
    _inherit = "auditlog.log"

    def write(self, vals):
        if not self.env.context.get("tms_auditlog_internal"):
            raise UserError(_("Audit records are read-only (BR-AUD-03)."))
        return super().write(vals)

    def unlink(self):
        if not self.env.context.get("tms_auditlog_vacuum"):
            raise UserError(_("Audit records are kept 8 years and cannot be deleted (BR-AUD-03)."))
        return super().unlink()


class AuditlogLogLine(models.Model):
    _inherit = "auditlog.log.line"

    def write(self, vals):
        raise UserError(_("Audit records are read-only (BR-AUD-03)."))

    def unlink(self):
        if not self.env.context.get("tms_auditlog_vacuum"):
            raise UserError(_("Audit records are kept 8 years and cannot be deleted (BR-AUD-03)."))
        return super().unlink()


class AuditlogAutovacuum(models.TransientModel):
    _inherit = "auditlog.autovacuum"

    @api.model
    def autovacuum(self, days, chunk_size=None):
        return super(AuditlogAutovacuum, self.with_context(tms_auditlog_vacuum=True)).autovacuum(
            max(days, RETENTION_DAYS), chunk_size=chunk_size)


class AuditlogRule(models.Model):
    _inherit = "auditlog.rule"

    @api.model
    def _tms_ensure_rules(self, model_names=None):
        """BR-AUD-02: create, write and delete with old and new values on the TMS models.

        Rules are marked subscribed in SQL so that auditlog patches the ORM methods once, at the end of
        registry loading, on the final model classes. Patching from an install hook would pin the methods
        of modules installed later in the same run."""
        Rule = self.sudo().with_context(tms_no_patch=True)
        ids = []
        for model_name in model_names or AUDITED_MODELS:
            model = self.env["ir.model"].sudo().search([("model", "=", model_name)], limit=1)
            if not model:
                continue
            rule = Rule.search([("model_id", "=", model.id)], limit=1)
            if not rule:
                rule = Rule.create({
                    "name": "TMS: %s" % model.name,
                    "model_id": model.id,
                    "log_create": True,
                    "log_write": True,
                    "log_unlink": True,
                    "log_read": False,
                    "log_type": "full",
                })
            ids.append(rule.id)
        if ids:
            self.env.cr.execute("UPDATE auditlog_rule SET state = 'subscribed' WHERE id IN %s", (tuple(ids),))
            self.invalidate_model(["state"])

    def _register_hook(self):
        if self.env.context.get("tms_no_patch"):
            return False
        return super()._register_hook()


class MailTrackingValue(models.Model):
    """BR-AUD-03: field tracking in the chatter is part of the audit trail and is not edited."""

    _inherit = "mail.tracking.value"

    def write(self, vals):
        if not self.env.su:
            raise UserError(_("Tracked changes are read-only (BR-AUD-03)."))
        return super().write(vals)

    def unlink(self):
        if not self.env.su:
            raise UserError(_("Tracked changes are kept and cannot be deleted (BR-AUD-03)."))
        return super().unlink()
