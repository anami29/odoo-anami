# -*- coding: utf-8 -*-
"""Prove the reporting layer works for the people who will use it.

Administrator passes everything; that is the whole problem with testing as
administrator. Each check below runs as a real seeded operator in their
real groups, because every permission fault found in this build so far was
invisible until somebody who was not the superuser pressed the button.
"""
from odoo import fields
from odoo.exceptions import AccessError

E = env  # noqa: F821
fails = []
checks = 0


def ok(label, cond, detail=""):
    global checks
    checks += 1
    if cond:
        print("  PASS  %s" % label)
    else:
        print("  FAIL  %s  %s" % (label, detail))
        fails.append(label)


def user(login):
    u = E["res.users"].search([("login", "=", login)], limit=1)
    assert u, "seeded user %s missing" % login
    return u


print("\n=== who we are testing as")
for login in ("anjali", "prakash", "vikram", "deepa", "ravi"):
    u = E["res.users"].search([("login", "=", login)], limit=1)
    if u:
        groups = sorted(
            g.name for g in u.groups_id
            if g.category_id.name == "Auctions")
        print("  %-9s %-22s %s" % (login, u.name, ", ".join(groups) or "-"))

# ----------------------------------------------------------------------
print("\n=== 1. the analysis view returns rows, and the right ones")
Analysis = E["auction.analysis"]
total = Analysis.search_count([])
evals = E["auction.evaluation.line"].search_count([])
ok("one analysis row per opened bid line (%d)" % total, total == evals,
   "analysis=%d evaluation=%d" % (total, evals))

rows = Analysis.search([], limit=400)
ok("every row carries an event", all(r.event_id for r in rows))
ok("every row carries a bidder", all(r.participant_id for r in rows))
ok("bid_count is 1 on every row", all(r.bid_count == 1 for r in rows))

# ----------------------------------------------------------------------
print("\n=== 2. ranking agrees with the evaluation model it reads from")
mismatch = []
for r in rows:
    live = E["auction.evaluation.line"].browse(r.id)
    if live.rank and live.rank != r.rank:
        mismatch.append((r.id, live.rank, r.rank))
ok("rank matches auction.evaluation.line.rank",
   not mismatch, "first few: %s" % mismatch[:5])

best = [r for r in rows if r.is_best]
ok("is_best set exactly where rank == 1",
   all(r.rank == 1 for r in best)
   and all(r.is_best for r in rows if r.rank == 1))

bad_dir = [r for r in rows if r.direction not in ("reverse", "forward")]
ok("direction resolves on every row", not bad_dir)

# ----------------------------------------------------------------------
print("\n=== 3. award outcomes join on correctly")
awarded_rows = Analysis.search([("is_awarded", "=", True)])
award_lines = E["auction.award.line"].search(
    [("evaluation_line_id", "!=", False)])
ok("awarded rows == award lines with an opened bid (%d)" % len(awarded_rows),
   len(awarded_rows) == len(award_lines),
   "analysis=%d award=%d" % (len(awarded_rows), len(award_lines)))
ok("award_count mirrors is_awarded",
   all(r.award_count == (1 if r.is_awarded else 0) for r in rows))
ok("override_count only where awarded AND override",
   all(r.override_count == (1 if (r.is_awarded and r.is_override) else 0)
       for r in rows))
ok("saving is zero on rows that were not awarded",
   all(r.saving_value == 0.0 for r in rows if not r.is_awarded))

for r in awarded_rows[:5]:
    print("     %-14s %-22s rank=%s price=%.2f base=%.2f saving=%.2f" % (
        r.event_id.name, (r.line_id.name or "")[:22], r.rank,
        r.price_unit, r.base_price, r.saving_value))

# ----------------------------------------------------------------------
print("\n=== 4. read_group works (the pivot and graph depend on it)")
try:
    grouped = Analysis._read_group(
        [], ["event_id"],
        ["bid_value:sum", "saving_value:sum", "bid_count:sum",
         "bidders_on_line:max"])
    ok("group by event (%d groups)" % len(grouped), bool(grouped))
    for g in grouped[:4]:
        print("     %-26s value=%.2f saving=%.2f bids=%s maxbidders=%s" % (
            g[0].name, g[1] or 0, g[2] or 0, g[3], g[4]))
except Exception as exc:
    ok("group by event", False, repr(exc))

try:
    g2 = Analysis._read_group([], ["partner_id", "is_awarded"],
                              ["bid_count:sum"])
    ok("group by bidder and outcome (%d groups)" % len(g2), bool(g2))
except Exception as exc:
    ok("group by bidder and outcome", False, repr(exc))

try:
    g3 = Analysis._read_group([], ["close_date:month"], ["bid_count:sum"])
    ok("group by close month (%d groups)" % len(g3), bool(g3))
except Exception as exc:
    ok("group by close month", False, repr(exc))

# ----------------------------------------------------------------------
print("\n=== 5. a buyer (not administrator) can read the reports")
buyer = user("anjali")
A_buyer = E["auction.analysis"].with_user(buyer)
try:
    n = A_buyer.search_count([])
    ok("buyer reads the analysis view (%d rows)" % n, n > 0)
    recs = A_buyer.search([], limit=5)
    recs.read(["event_id", "participant_id", "price_unit", "saving_value",
               "rank", "is_awarded", "bidders_on_line"])
    ok("buyer reads every reported column", True)
    ok("display_name resolves", all(r.display_name for r in recs),
       str([r.display_name for r in recs]))
except Exception as exc:
    ok("buyer reads the analysis view", False, repr(exc))

# ----------------------------------------------------------------------
print("\n=== 6. the dashboard renders for each role, not just admin")
expected = set(
    "live closing_soon awaiting_opening under_evaluation awards_to_approve "
    "awards_to_generate security_pending questions_open".split())
for login in ("anjali", "prakash", "vikram", "deepa"):
    u = E["res.users"].search([("login", "=", login)], limit=1)
    if not u:
        continue
    try:
        dash = E["auction.dashboard"].with_user(u).create({})
        vals = {k: dash["count_%s" % k] for k in expected}
        ok("dashboard computes for %s" % login, True)
        print("     %-9s %s" % (
            login, "  ".join("%s=%s" % (k, vals[k])
                             for k in sorted(vals) if vals[k])))
        print("            ytd awarded=%.2f saving=%.2f bids=%d avg=%.1f "
              "tiles(sec=%s awd=%s q=%s)" % (
                  dash.awarded_value_ytd, dash.saving_value_ytd,
                  dash.bids_opened_ytd, dash.avg_bidders,
                  dash.show_security, dash.show_awards, dash.show_questions))
    except Exception as exc:
        ok("dashboard computes for %s" % login, False, repr(exc))

# ----------------------------------------------------------------------
print("\n=== 7. every tile count equals the list the tile opens")
from odoo.addons.custom_auction_base.models.auction_dashboard import TILES

dash = E["auction.dashboard"].with_user(user("prakash")).create({})
for key in TILES:
    model = TILES[key][0]
    act = getattr(dash, "action_open_%s" % key)()
    listed = E[model].with_user(user("prakash")).search_count(act["domain"])
    tile = dash["count_%s" % key]
    ok("tile %s: %d == list %d" % (key, tile, listed), tile == listed)

# ----------------------------------------------------------------------
print("\n=== 8. a bidder cannot read the analysis view at all")
bidder = E["res.users"].search(
    [("groups_id", "in",
      E.ref("custom_auction_base.group_auction_bidder").id)], limit=1)
if bidder:
    print("     bidder user: %s" % bidder.login)
    try:
        E["auction.analysis"].with_user(bidder).search_count([])
        ok("bidder is denied the analysis view", False,
           "READ SUCCEEDED — opened prices are exposed")
    except AccessError:
        ok("bidder is denied the analysis view", True)
    except Exception as exc:
        ok("bidder is denied the analysis view", False, repr(exc))
else:
    ok("bidder is denied the analysis view", False, "no bidder user seeded")

# ----------------------------------------------------------------------
print("\n=== 9. the closing-soon window is computed now, not at import")
d1 = dash._tile_domain("closing_soon")
cutoff = [leaf[2] for leaf in d1 if leaf[0] == "bid_close_datetime"][0]
delta = (fields.Datetime.to_datetime(cutoff)
         - fields.Datetime.now()).total_seconds() / 3600.0
ok("cutoff is ~48h ahead (%.1fh)" % delta, 47.0 < delta < 49.0)
ok("no placeholder survives into the domain",
   all("__now_plus_48h__" != leaf[2] for leaf in d1))

# ----------------------------------------------------------------------
print("\n=== 10. menus resolve and nest")
Menu = E["ir.ui.menu"].sudo()
root = E.ref("custom_auction_base.menu_auction_root")
sections = Menu.search([("parent_id", "=", root.id)], order="sequence")
print("     Auctions")
for s in sections:
    kids = Menu.search([("parent_id", "=", s.id)], order="sequence")
    print("       %-14s %s" % (
        s.name, "-> " + (s.action.name if s.action else "(section)")))
    for k in kids:
        print("         %-26s %s" % (
            k.name, k.action.name if k.action else "(no action)"))
ok("root has sections", len(sections) >= 6, "%d" % len(sections))
orphan = [m for m in Menu.search(
    [("id", "child_of", root.id)]) if not m.child_id and not m.action]
ok("no leaf menu without an action", not orphan,
   str([m.name for m in orphan]))

# what a buyer actually sees
visible = Menu.with_user(user("anjali")).search(
    [("id", "child_of", root.id)])
print("     buyer sees %d of %d auction menu entries" % (
    len(visible), len(Menu.search([("id", "child_of", root.id)]))))
ok("buyer sees the dashboard",
   E.ref("custom_auction_base.menu_auction_dashboard") in visible)
ok("buyer sees reporting",
   E.ref("custom_auction_base.menu_auction_report_bids") in visible)

# ----------------------------------------------------------------------
print("\n=== 11. every action in the menu opens")
for m in Menu.search([("id", "child_of", root.id)]):
    if not m.action:
        continue
    act = m.action
    if act._name != "ir.actions.act_window":
        continue
    try:
        model = E[act.res_model].with_user(user("prakash"))
        # Odoo 18 calls it 'list'. Mapping it to 'tree' here is what made
        # this check report twenty false failures on its first run.
        modes = [v for v in (act.view_mode or "").split(",") if v]
        for mode in modes:
            model.get_view(view_type=mode)
        ok("%s -> %s [%s]" % (m.name, act.res_model, act.view_mode), True)
    except AccessError:
        ok("%s (no licence for approver, expected)" % m.name, True)
    except Exception as exc:
        ok("%s -> %s" % (m.name, act.res_model), False, repr(exc))

print("\n==================================================")
print("%d checks, %d failed" % (checks, len(fails)))
for f in fails:
    print("  FAILED: %s" % f)
print("==================================================")
