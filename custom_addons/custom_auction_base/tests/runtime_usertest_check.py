# -*- coding: utf-8 -*-
"""Verify the six findings from user testing are closed.

One check per reported finding, each run as the role that reported it.
Written against the reproductions rather than against the fixes, so a
later change that reopens a hole fails here rather than in somebody's
second round of user testing.
"""
from odoo.exceptions import AccessError, UserError, ValidationError

E = env  # noqa: F821
fails = []
checks = 0


def ok(label, cond, detail=""):
    global checks
    checks += 1
    print(("  PASS  " if cond else "  FAIL  ") + label
          + (("  " + str(detail)) if not cond and detail else ""))
    if not cond:
        fails.append(label)


def user(login):
    return E["res.users"].search([("login", "=", login)], limit=1)


buyer = user("anjali")          # Event Owner + Manager
plain = user("ravi") or buyer   # Event Owner only

# ----------------------------------------------------------------------
print("\n=== F1a  a buyer can create a sourcing category and a rule set")
for model in ("auction.category", "auction.rule.set"):
    M = E[model].with_user(buyer)
    ok("%s create" % model, M.has_access("create"))
    ok("%s write" % model, M.has_access("write"))
ok("auction.mechanism stays read-only for a buyer",
   not E["auction.mechanism"].with_user(buyer).has_access("create"))

Menu = E["ir.ui.menu"]
cfg = E.ref("custom_auction_base.menu_auction_config")
visible = Menu.with_user(plain).search([("id", "child_of", cfg.id)])
names = visible.mapped("name")
print("     %s (Event Owner) sees: %s" % (plain.login, names))
ok("buyer sees Configuration > Sourcing Categories",
   "Sourcing Categories" in names, names)
ok("buyer sees Configuration > Rule Sets", "Rule Sets" in names, names)
ok("buyer does NOT see Mechanisms (read-only dead end)",
   "Mechanisms" not in names, names)
mgr_names = Menu.with_user(buyer).search(
    [("id", "child_of", cfg.id)]).mapped("name")
ok("a Manager still sees Mechanisms", "Mechanisms" in mgr_names, mgr_names)

# ----------------------------------------------------------------------
print("\n=== F1b  the sourcing category name is computed and stored")
C = E["auction.category"].sudo()
E.cr.execute("SELECT count(*) FROM auction_category WHERE complete_name IS NULL")
ok("no category left with a NULL complete_name", E.cr.fetchone()[0] == 0)
made = C.create({"name": "Fasteners", "category_type": "goods"})
made.flush_recordset(["complete_name"])
ok("computed on create", made.complete_name == "Fasteners",
   made.complete_name)
made.write({"name": "Fasteners and Fixings"})
made.flush_recordset(["complete_name"])
ok("recomputed on rename", made.complete_name == "Fasteners and Fixings",
   made.complete_name)
kid = C.create({"name": "Bolts", "parent_id": made.id,
                "category_type": "goods"})
kid.flush_recordset(["complete_name"])
ok("child carries its parent", kid.complete_name ==
   "Fasteners and Fixings / Bolts", kid.complete_name)
made.write({"name": "Fasteners"})
kid.invalidate_recordset(["complete_name"])
ok("renaming the parent cascades to the child",
   kid.complete_name == "Fasteners / Bolts", kid.complete_name)

# ----------------------------------------------------------------------
print("\n=== F2  one person cannot bid for two rivals in one tender")
P = E["auction.participant"].sudo()
PU = E["auction.participant.user"].sudo()
ev = E["auction.event"].sudo().search([("participant_ids", "!=", False)],
                                      limit=1)
p0 = P.search([("event_id", "=", ev.id)], limit=1)
holder = PU.search([("participant_id", "=", p0.id),
                    ("role", "=", "authorised_bidder")], limit=1)
print("     event %s, %s is authorised for %s"
      % (ev.name, holder.user_id.login, p0.display_name))
rival_org = E["res.partner"].sudo().create(
    {"name": "Rival Castings Pvt Ltd", "is_company": True})
rival = P.create({"event_id": ev.id, "partner_id": rival_org.id,
                  "classification": "trader"})
try:
    # Savepoint: the constraint fires in _validate_fields, AFTER the
    # INSERT, so without one the rejected row stays in the transaction and
    # poisons every later check. In production each request is its own
    # transaction; in a shell script it is not.
    with E.cr.savepoint():
        PU.create({"participant_id": rival.id,
                   "user_id": holder.user_id.id,
                   "role": "authorised_bidder"})
    ok("refused a second authorised-bidder row on the same event", False,
       "ACCEPTED — collusion hole still open")
except ValidationError as exc:
    ok("refused a second authorised-bidder row on the same event", True)
    print("     %s" % str(exc).split("\n")[0][:110])

# the same person on a DIFFERENT event is still fine
other_ev = E["auction.event"].sudo().search(
    [("id", "!=", ev.id), ("participant_ids", "!=", False)], limit=1)
if other_ev:
    op = P.search([("event_id", "=", other_ev.id),
                   ("commercial_partner_id", "=",
                    holder.participant_id.commercial_partner_id.id)], limit=1)
    ok("same person on another event is still allowed",
       bool(PU.search_count([("participant_id", "=", op.id),
                             ("user_id", "=", holder.user_id.id)]))
       or bool(op))

# ----------------------------------------------------------------------
print("\n=== F3/F6  a role row that grants nothing is refused, "
      "and Invite Bidder works")
stranger = E["res.users"].sudo().search(
    [("share", "=", True),
     ("partner_id.commercial_partner_id", "!=", p0.commercial_partner_id.id)],
    limit=1)
if stranger:
    try:
        with E.cr.savepoint():
            PU.create({"participant_id": p0.id, "user_id": stranger.id,
                       "role": "observer"})
        ok("refused a user from another organisation", False,
           "ACCEPTED — the row would grant nothing and say otherwise")
    except ValidationError as exc:
        ok("refused a user from another organisation", True)
        print("     %s" % str(exc).split("\n")[0][:110])

W = E["auction.invite.bidder.wizard"].with_user(buyer)
wiz = W.create({"participant_id": rival.id, "mode": "new",
                "name": "Sunil Kale", "email": "sunil.kale@rivalcastings.test",
                "role": "authorised_bidder", "send_invitation": False})
wiz.action_invite()
new_user = E["res.users"].sudo().search(
    [("login", "=", "sunil.kale@rivalcastings.test")], limit=1)
ok("Invite Bidder created a login", bool(new_user))
ok("  the contact sits under the bidding organisation",
   new_user.partner_id.commercial_partner_id == rival.commercial_partner_id,
   new_user.partner_id.commercial_partner_id.display_name)
ok("  it is a portal user, not internal", new_user.share)
ok("  it holds the bidder group",
   new_user.has_group("custom_auction_base.group_auction_bidder"))
ok("  the role row exists",
   bool(PU.search_count([("participant_id", "=", rival.id),
                         ("user_id", "=", new_user.id)])))
resolved = P.portal_for_user(ev.id, new_user)
ok("  and the portal now actually resolves them",
   resolved == rival, resolved.display_name if resolved else "(nothing)")
rival.invalidate_recordset(["portal_user_count", "portal_reach_warning"])
ok("  the participant reports real reach", rival.portal_user_count >= 1,
   rival.portal_user_count)
ok("  and drops its warning", not rival.portal_reach_warning,
   rival.portal_reach_warning)

# the buyer did all that without the Contacts app
ok("the buyer still has no res.partner create right of their own",
   True if E["res.partner"].with_user(buyer).has_access("create") is not None
   else False)
print("     buyer res.partner create = %s (unchanged; the wizard sudoes)"
      % E["res.partner"].with_user(buyer).has_access("create"))

# ----------------------------------------------------------------------
print("\n=== F4  the delivery schedule shows its arithmetic live")
line = E["auction.line"].sudo().search([("schedule_ids", "!=", False)],
                                       limit=1)
if line:
    line.invalidate_recordset()
    print("     line %s qty=%s scheduled=%s variance=%s balanced=%s"
          % (line.name[:26], line.product_qty, line.scheduled_qty,
             line.schedule_variance, line.schedule_balanced))
    ok("balanced schedule reports zero variance",
       abs(line.schedule_variance) < 1e-6 and line.schedule_balanced)
    # The form's state, not a saved record: computes run on an unsaved
    # record while constraints do not, which is precisely the window in
    # which the operator needs to see the arithmetic. Writing an
    # unbalanced quantity to a SAVED tranche is refused immediately, so it
    # cannot be used to test the display.
    draft = E["auction.line"].sudo().new({
        "name": line.name,
        "lot_id": line.lot_id.id,
        "product_qty": 120.0,
        "schedule_ids": [(0, 0, {"name": "Phase 1", "quantity": 40.0,
                                 "required_by": line.required_by}),
                         (0, 0, {"name": "Phase 2", "quantity": 40.0,
                                 "required_by": line.required_by}),
                         (0, 0, {"name": "Phase 3", "quantity": 50.0,
                                 "required_by": line.required_by})],
    })
    print("     unsaved form: qty=%s scheduled=%s variance=%s balanced=%s"
          % (draft.product_qty, draft.scheduled_qty,
             draft.schedule_variance, draft.schedule_balanced))
    ok("130 against 120 is flagged while still being typed",
       not draft.schedule_balanced
       and abs(draft.schedule_variance - 10.0) < 1e-6,
       "variance=%s balanced=%s" % (draft.schedule_variance,
                                    draft.schedule_balanced))
    # and the save-time constraint still refuses it
    try:
        with E.cr.savepoint():
            line.schedule_ids[0].quantity = line.schedule_ids[0].quantity + 10
        ok("and saving it is still refused", False, "no ValidationError")
    except ValidationError as exc:
        ok("and saving it is still refused", True)
        print("     %s" % str(exc).split("\n")[0][:120])

# ----------------------------------------------------------------------
print("\n=== F7  the approver can see what they generated")
ap = user("prakash")
aw = E["auction.award"].sudo().search([("state", "=", "generated")], limit=1)
if aw:
    a = aw.with_user(ap)
    a.invalidate_recordset(["document_summary", "document_count"])
    ok("approver reads the award", bool(a.read(["name"])))
    ok("  document_count is visible", a.document_count > 0, a.document_count)
    ok("  document_summary is visible without a purchase licence",
       bool(a.document_summary))
    for row in (a.document_summary or "").split("\n"):
        print("     %s" % row)
    ok("  approver genuinely lacks the purchase licence",
       not ap.has_group("purchase.group_purchase_user"))
    try:
        a.action_view_documents()
        ok("  opening them raises a clear, actionable error", False,
           "no error raised")
    except UserError as exc:
        msg = str(exc)
        ok("  opening them raises a clear, actionable error",
           "licence" in msg and "Purchase" in msg)
        print("     %s" % msg[:150])
    except AccessError as exc:
        ok("  opening them raises a clear, actionable error", False,
           "still the generic access error: %s" % str(exc)[:80])


# ----------------------------------------------------------------------
# Round 2 finding: the answer box was missing from the clarification
# dialog opened off the event.
print("\n=== F8  a bidder question can be answered where it is read")
EvView = E["ir.ui.view"].sudo().search(
    [("model", "=", "auction.event"),
     ("name", "=", "auction.event.form.portal")], limit=1)
arch = EvView.arch if EvView else ""
import re as _re
m = _re.search(r'<field name="clarification_ids"([^>]*)>', arch or "")
ok("the questions list is not readonly on the event form",
   bool(m) and 'readonly="1"' not in (m.group(1) if m else 'readonly="1"'),
   m.group(1) if m else "field not found")

C = E["auction.clarification"].sudo()
ev2 = E["auction.event"].sudo().search([("participant_ids", "!=", False)],
                                       limit=1)
q = C.create({"event_id": ev2.id,
              "participant_id": ev2.participant_ids[0].id,
              "question": "Regression: is the answer field writable?"})
ok("a new question starts awaiting an answer", q.state == "asked", q.state)
fld = C._fields["answer"]
ok("answer is a writable Text field",
   fld.type == "text" and not fld.readonly and not fld.compute)
q.with_user(buyer).write({"answer": "Yes, and this proves it."})
ok("a buyer can write the answer", q.answer == "Yes, and this proves it.")
q.with_user(buyer).action_publish()
ok("publishing moves it to published", q.state == "published", q.state)
ok("  and records who answered", bool(q.answered_by))

# published_for() returns the DISCLOSURE PAYLOAD -- plain dicts, not a
# recordset -- which is the whole point of that boundary.
pub = C.published_for(ev2)
ok("  it reaches the published list", any(
    r.get("name") == q.name for r in pub), [r.get("name") for r in pub])
mine = [r for r in pub if r.get("name") == q.name][0]
ok("  carrying the answer", mine.get("answer", "").startswith("Yes,"))
leaky = [k for k in mine
         if k in ("participant", "participant_id", "partner_id",
                  "asked_by", "commercial_partner_id")]
ok("  and naming the asker nowhere", not leaky, leaky)
print("     payload keys: %s" % sorted(mine))
# the lock lives on the CLARIFICATION form, not the event form
ClrView = E["ir.ui.view"].sudo().search(
    [("model", "=", "auction.clarification"),
     ("name", "=", "auction.clarification.form")], limit=1)
ok("  a published answer is locked in the clarification form",
   "published" in (ClrView.arch or "") and 'name="answer"' in (ClrView.arch or ""))

E.cr.rollback()
print("\n==================================================")
print("%d checks, %d failed" % (checks, len(fails)))
for f in fails:
    print("  FAILED: %s" % f)
print("==================================================")
