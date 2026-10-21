"""Approval and status fields change only through their actions (BR-AUD-06, RL-06).

Odoo makes a field read-only in the view only; a client can still send it in ``write``, ``create``,
``web_save`` or an import. Every remote call is inspected before it runs:

* a CRUD call (write, create, web_save, load) may not set a guarded field, including through one2many
  commands nested in the values;
* creating a document from the client needs the role that #4 gives that right (BR-BRN-07), and deleting
  a TMS document from the client needs the TMS Administrator (BR-BRN-08);
* the remote context may not carry the internal ``tms_*`` keys that the server sets for overrides
  (credit override reason, amendment, stage move ...), except the keys views and actions send.

Server-side code (buttons, wizards, crons) is unaffected: it never passes through this check.
"""
import odoo
from odoo import _, api, models
from odoo.exceptions import AccessError

CRUD_METHODS = {"write", "create", "web_save", "web_save_multi", "load", "copy", "name_create"}

GUARDED_FIELDS = {
    "tms.consignment": {"state", "pod_status", "typed_rate_approved_by_id"},
    # BR-LR-06: the line's charged weight is computed; overrides go through the LR's override with a reason
    "tms.consignment.line": {"charged_weight"},
    "tms.consignment.amendment": {"state", "approved_by_id", "requested_by_id"},
    "tms.ewaybill.update": {"state", "done_by_id"},
    "tms.order": {"stage_id", "payload_approved_by_id", "pod_status"},
    "tms.trip.consignment": {"state", "pod_status", "uploaded_by_id", "verified_by_id"},
    "tms.trip.assignment": {"state", "gate_result"},
    "tms.trip.breakdown": {"state"},
    "tms.trip.charge": {"state", "approved_by_id", "approved_on", "self_approved"},
    "tms.trip.expense": {"state", "approved_by_id"},
    "tms.driver.advance": {"state"},
    "tms.driver.settlement": {"state"},
    "tms.damage.claim": {"state", "approved_by_id", "cap_override_by_id",
                         "owner_risk_accepted_by_id", "timebar_admitted_by_id"},
    "tms.insurer.claim": {"state"},
    "tms.hire.advance": {"state"},
    "tms.vendor.bill.match": {"state", "override_by_id"},
    "tms.vendor.bill.match.line": {"approved"},
    "purchase.order": {"tms_cancel_approved_by_id"},
    "purchase.order.line": {"tms_extra_approved", "tms_extra_approved_by_id"},
    "tms.compliance.document": {"verified_by_id"},
    "tms.override.log": {"user_id", "kind", "reason"},
}

_G = "tms_core.group_%s"
CREATE_ROLES = {
    "tms.consignment": ["booking_clerk"],
    "tms.order": ["dispatcher", "planner"],
    "tms.trip.dsr": ["control_room", "dispatcher"],
    "tms.trip.incident": ["control_room", "dispatcher"],
    "tms.trip.breakdown": ["dispatcher", "control_room"],
    "tms.trip.charge": ["dispatcher", "branch_accountant", "branch_manager"],
    "tms.trip.expense": ["dispatcher", "branch_accountant"],
    "tms.driver.advance": ["dispatcher", "branch_accountant"],
    "tms.driver.settlement": ["dispatcher", "branch_accountant"],
    "tms.damage.claim": ["claims_officer"],
    "tms.insurer.claim": ["claims_officer"],
    "tms.compliance.document": ["fleet_manager"],
    "tms.market.vehicle": ["fleet_manager"],
    "tms.vehicle.class": ["fleet_manager"],
    "tms.driver": ["fleet_manager"],
    "fleet.vehicle": ["fleet_manager"],
    "tms.tds.declaration": ["branch_accountant", "finance_head"],
}
# TMS documents only the TMS Administrator deletes from the client (lines inside a form stay editable)
ADMIN_UNLINK = {
    "tms.consignment", "tms.order", "tms.trip.charge", "tms.trip.expense", "tms.driver.advance",
    "tms.driver.settlement", "tms.damage.claim", "tms.insurer.claim", "tms.vendor.bill.match",
    "tms.compliance.document", "tms.market.vehicle", "tms.trip.dsr", "tms.trip.incident", "tms.trip.breakdown",
    "tms.ewaybill", "tms.consignment.amendment", "tms.override.log",
}

ADMIN_UNLINK_ALL = ADMIN_UNLINK  # extended in place by other TMS modules if needed

# one2many lines a form legitimately creates for a role that does not create the comodel on its own
NESTED_CREATE_EXEMPT = {("tms.order", "market_document_ids")}

# context keys that views and returned actions legitimately send back to the server
CONTEXT_WHITELIST = {"tms_reason_method", "tms_reason_title", "tms_show_waive", "tms_trip_id",
                     "tms_kpi_company_ids", "tms_kpi_partner_id", "tms_kpi_vehicle_id", "tms_kpi_date_from"}


class TmsRpcGuard(models.AbstractModel):
    _name = "tms.rpc.guard"
    _description = "TMS remote call guard"

    @api.model
    def _tms_guarded_fields(self):
        """Model name -> set of field names; other TMS modules extend it."""
        return {model: set(names) for model, names in GUARDED_FIELDS.items()}

    @api.model
    def _tms_context_whitelist(self):
        return set(CONTEXT_WHITELIST)

    @api.model
    def _tms_create_roles(self):
        """Model name -> role groups (xmlids) that create it from the client; extended by other modules."""
        return {model: [_G % role for role in roles] for model, roles in CREATE_ROLES.items()}

    @api.model
    def _tms_admin_unlink_models(self):
        return set(ADMIN_UNLINK)

    @api.model
    def _tms_check_create_role(self, model_name):
        roles = self._tms_create_roles().get(model_name)
        user = self.env.user
        if not roles or user.has_group("tms_core.group_tms_admin"):
            return
        if not any(user.has_group(xmlid) for xmlid in roles):
            names = ", ".join(self.env.ref(xmlid).name for xmlid in roles if self.env.ref(xmlid, False))
            raise AccessError(_("Only %(roles)s create %(model)s records (BR-BRN-07).",
                                roles=names, model=self.env[model_name]._description))

    @api.model
    def _tms_check_vals(self, model_name, vals, creating, guarded, path=""):
        if not isinstance(vals, dict):
            return
        Model = self.env.get(model_name)
        if Model is None:
            return
        names = guarded.get(model_name, set())
        for fname, value in vals.items():
            field = Model._fields.get(fname)
            if field is None:
                continue
            if fname in names:
                if creating and not value:
                    continue
                if creating:
                    default = Model.default_get([fname]).get(fname)
                    if value == default:
                        continue
                raise AccessError(_(
                    "%(field)s on %(model)s changes only through its action button (BR-AUD-06).",
                    field=field.string, model=Model._description))
            if field.type in ("one2many", "many2many") and isinstance(value, (list, tuple)):
                for command in value:
                    if isinstance(command, (list, tuple)) and len(command) >= 3 and command[0] in (0, 1):
                        if command[0] == 0 and (model_name, fname) not in NESTED_CREATE_EXEMPT:
                            self._tms_check_create_role(field.comodel_name)
                        self._tms_check_vals(field.comodel_name, command[2], command[0] == 0, guarded)

    @api.model
    def _tms_check_call(self, model_name, method, args, kwargs):
        if self.env.su:
            return
        context = kwargs.get("context") if isinstance(kwargs, dict) else None
        if isinstance(context, dict):
            allowed = self._tms_context_whitelist()
            for key in [k for k in context if k.startswith("tms_") and k not in allowed]:
                context.pop(key)
        if method == "unlink" and model_name in self._tms_admin_unlink_models() \
                and not self.env.user.has_group("tms_core.group_tms_admin"):
            raise AccessError(_("Only the TMS Administrator deletes %s records (BR-BRN-08).",
                                self.env[model_name]._description))
        if model_name == "base_import.import" and method == "execute_import":
            # the import wizard calls load() server-side: check the target model here
            record = self.env["base_import.import"].browse(args[0] if args else [])
            target = record[:1].res_model
            if target:
                self._tms_check_create_role(target)
                self._tms_check_call(target, "load", [args[1] if len(args) > 1 else kwargs.get("fields") or []],
                                     {})
            return
        if method not in CRUD_METHODS:
            return
        if method in ("create", "copy", "name_create") or (
                method in ("web_save", "web_save_multi") and not (args and args[0])):
            self._tms_check_create_role(model_name)
        guarded = self._tms_guarded_fields()
        if method == "name_create":
            return
        if method == "copy":
            default = args[1] if len(args) > 1 else kwargs.get("default")
            self._tms_check_vals(model_name, default or {}, True, guarded)
            return
        if method == "load":
            fields_list = args[0] if args else kwargs.get("fields") or []
            names = guarded.get(model_name, set())
            hit = [f for f in fields_list if f.split("/")[0] in names]
            if hit:
                raise AccessError(_("Status and approval fields cannot be imported (BR-AUD-06): %s", ", ".join(hit)))
            return
        if method == "create":
            vals_list = args[0] if args else kwargs.get("vals_list") or kwargs.get("vals")
            if isinstance(vals_list, dict):
                vals_list = [vals_list]
            for vals in vals_list or []:
                self._tms_check_vals(model_name, vals, True, guarded)
            return
        # write / web_save / web_save_multi: (ids, vals, ...)
        ids = args[0] if args else []
        vals = args[1] if len(args) > 1 else kwargs.get("vals")
        if method == "web_save_multi" and isinstance(vals, list):
            for item in vals:
                self._tms_check_vals(model_name, item, not ids, guarded)
            return
        self._tms_check_vals(model_name, vals, method == "web_save" and not ids, guarded)


class Base(models.AbstractModel):
    """BR-BRN-08: only the TMS Administrator deletes TMS documents, also through form one2many deletes."""

    _inherit = "base"

    def unlink(self):
        if self and not self.env.su and self._name in ADMIN_UNLINK_ALL \
                and not self.env.user.has_group("tms_core.group_tms_admin"):
            raise AccessError(_("Only the TMS Administrator deletes %s records (BR-BRN-08).", self._description))
        return super().unlink()


def tms_guard_rpc(env, model_name, method, args, kwargs):
    guard = env.get("tms.rpc.guard")
    if guard is not None:
        guard._tms_check_call(model_name, method, list(args or []), kwargs if kwargs is not None else {})


_original_call_kw = odoo.api.call_kw


def _guarded_call_kw(model, name, args, kwargs):
    """XML-RPC / JSON-RPC ``execute_kw`` entry point (odoo.service.model.execute_cr)."""
    tms_guard_rpc(model.env, model._name, name, args, kwargs)
    return _original_call_kw(model, name, args, kwargs)


if not getattr(odoo.api.call_kw, "_tms_guarded", False):
    _guarded_call_kw._tms_guarded = True
    odoo.api.call_kw = _guarded_call_kw
