"""BR-NFR-13 (DPDP Act 2023): drivers' licence data is visible only to the Fleet Manager, Dispatchers and
managers, and a former driver's personal data is anonymised after the retention period."""
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models

PII_GROUPS = ("tms_core.group_fleet_manager,tms_core.group_dispatcher,tms_core.group_branch_manager,"
              "tms_core.group_operations_head,tms_core.group_tms_admin")


class TmsDriver(models.Model):
    _inherit = "tms.driver"

    driver_license_number = fields.Char(groups=PII_GROUPS)
    driver_license_file = fields.Binary(groups=PII_GROUPS)
    tms_anonymised = fields.Boolean(readonly=True, copy=False, string="Anonymised")

    @api.model
    def _cron_anonymise_drivers(self):
        """Former drivers untouched for the retention period lose their personal data."""
        icp = self.env["ir.config_parameter"].sudo()
        years = int(icp.get_param("tms_core.driver_retention_years") or 8)
        limit = fields.Datetime.now() - relativedelta(years=years)
        drivers = self.sudo().with_context(active_test=False).search([
            ("is_active", "=", False), ("tms_anonymised", "=", False), ("write_date", "<", limit)])
        for driver in drivers:
            if driver.trips_ids.filtered(lambda t: t.state_code not in ("done", "cancel")):
                continue  # still referenced by an open trip
            driver.write({
                "name": _("Former driver %s", driver.id),
                "phone": False, "mobile": False, "email": False,
                "street": False, "street2": False, "zip": False, "city": False,
                "driver_license_number": False, "driver_license_file": False,
                "tms_anonymised": True,
            })
            docs = driver.tms_compliance_document_ids.sudo()
            docs.write({"number": _("anonymised"), "attachment": False})
            # the audit trail and chatter keep the old values: purge them for this driver (DPDP erasure)
            self._tms_purge_trail("tms.driver", driver.ids)
            self._tms_purge_trail("tms.compliance.document", docs.ids)
            self._tms_purge_trail("res.partner", driver.partner_id.ids)

    @api.model
    def _tms_purge_trail(self, model, ids):
        if not ids:
            return
        logs = self.env["auditlog.log"].sudo().search([("model_id.model", "=", model), ("res_id", "in", ids)])
        logs.with_context(tms_auditlog_vacuum=True).mapped("line_ids").unlink()
        logs.with_context(tms_auditlog_vacuum=True).unlink()
        messages = self.env["mail.message"].sudo().search([("model", "=", model), ("res_id", "in", ids)])
        messages.mapped("tracking_value_ids").sudo().unlink()
