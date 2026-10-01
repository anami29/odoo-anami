#!/usr/bin/env python3
"""Walk the award loop and assert every hop exists.

Two breaks were found by doing this rather than by reasoning about it: no
transition ever set bidding_closed, and the staging lookup could never
match. This asserts each hop against the actual source.
"""
import os
import re
import sys
import xml.etree.ElementTree as ET

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
FAIL = []


def src(rel):
    return open(os.path.join(ROOT, rel), encoding="utf-8").read()


ALL_PY = "\n".join(
    src("models/%s" % f) for f in sorted(os.listdir(os.path.join(ROOT, "models")))
    if f.endswith(".py"))
ALL_XML = "\n".join(
    src("views/%s" % f) for f in sorted(os.listdir(os.path.join(ROOT, "views")))
    if f.endswith(".xml"))


def hop(label, ok, detail=""):
    print("  %-56s %s%s" % (label, "OK" if ok else "BREAK",
                            "" if ok else "   <- " + detail))
    if not ok:
        FAIL.append(label)


def sets_state(state):
    """Some method somewhere writes this state."""
    return bool(re.search(r'["\']state["\']\s*:\s*["\']%s["\']' % state, ALL_PY))


def has_button(method):
    return ('name="%s"' % method) in ALL_XML and "type=\"object\"" in ALL_XML


print("\nAward loop — every hop must exist in source\n")

# 1. reach a live event
hop("draft -> published (action_publish)",
    "def action_publish" in ALL_PY and has_button("action_publish"))
hop("published -> live (action_open_bidding)",
    "def action_open_bidding" in ALL_PY and has_button("action_open_bidding"))

# 2. close bidding — the first break that was found
hop("live -> bidding_closed (manual)",
    "def action_close_bidding" in ALL_PY and sets_state("bidding_closed")
    and has_button("action_close_bidding"),
    "nothing sets bidding_closed, so envelopes can never open")
hop("live -> bidding_closed (scheduled)",
    "_cron_close_expired_bidding" in ALL_PY
    and "_cron_close_expired_bidding" in src("data/auction_data.xml"))

# 3. dual-control opening
hop("opening requested (first opener)",
    "def action_request_opening" in ALL_PY
    and has_button("action_request_opening"))
hop("opening confirmed (second, different opener)",
    "def action_confirm_opening" in ALL_PY
    and has_button("action_confirm_opening"))
hop("confirm refuses the same person twice",
    "opening_requested_by == self.env.user" in ALL_PY)
hop("execute_dual exists and takes two users",
    re.search(r"def execute_dual\(self, event, opener_a, opener_b",
              ALL_PY) is not None)
hop("no dead call to the share-based execute()",
    "combine_shares" not in ALL_PY,
    "execute(share_a, share_b) can never succeed: nothing splits the key")

# 4. plaintext reaches a readable place
hop("staging writes to auction.evaluation.line",
    'env["auction.evaluation.line"]' in ALL_PY)
hop("staging is not a stub",
    "_logger.info(\"staged %d bytes\"" not in ALL_PY,
    "the stub discarded the plaintext")
hop("staging recovers ids through _payload_id",
    "_payload_id(item.get(\"line\"))" in ALL_PY)
hop("staging refuses to write nothing silently",
    "if items and not staged" in ALL_PY)
hop("unsealed events stage too (_stage_from_clear)",
    "def _stage_from_clear" in ALL_PY and "_stage_from_clear(bid" in ALL_PY)
_stage_body = ALL_PY.split("def _stage_evaluation_values", 1)[-1] \
                    .split("\n    def ", 1)[0]
_stage_code = re.sub(r'""".*?"""', "", _stage_body, flags=re.S)
_stage_code = re.sub(r"#.*", "", _stage_code)
hop("staging writes no ledger column",
    not re.search(r"\bbid(_line)?\b[\w.]*\.(write|envelope\s*=|price_unit\s*=)",
                  _stage_code)
    and "bid.write(" not in _stage_code
    and "bid_line.write(" not in _stage_code,
    "decrypting in place would destroy the sealed evidence")
hop("ledger write() still refuses unconditionally",
    re.search(r"def write\(self, vals\):\s*\n\s*raise AccessError",
              src("models/auction_bid.py")) is not None)

# 5. a person can see the prices
acl = src("security/ir.model.access.csv")
hop("evaluation lines have ACL rows",
    "model_auction_evaluation_line" in acl)
hop("bidders have NO ACL on evaluation lines",
    not re.search(r"model_auction_evaluation_line,group_auction_bidder", acl),
    "competitors' opened prices would be visible to bidders")
hop("evaluation list view exists",
    "auction.evaluation.line.list" in ALL_XML)
hop("evaluation action is on a menu",
    "action_auction_evaluation_line" in src("views/auction_menus.xml"))
hop("evaluation views are in the manifest",
    "views/auction_evaluation_views.xml" in src("__manifest__.py"))
hop("event form links to the opened bids",
    has_button("action_view_evaluation"))

# 6. pick the winner
hop("award line can reference an opened bid",
    "evaluation_line_id" in src("models/auction_award.py"))
hop("picking one fills price and quantity",
    "_onchange_evaluation_line" in src("models/auction_award.py"))
hop("awarded price must equal the opened price",
    "_check_price_matches_opening" in src("models/auction_award.py"),
    "an award could name a number nobody bid")
hop("one-click award from the price grid",
    "def action_award_this" in src("models/auction_evaluation.py")
    and 'name="action_award_this"' in ALL_XML)
hop("award form exposes the picker",
    "evaluation_line_id" in src("views/auction_award_views.xml"))

# 7. generate downstream with linkage
hop("approval gate before generation",
    "def action_approve" in src("models/auction_award.py")
    and has_button("action_approve"))
hop("generation is a separate act",
    "def action_generate_documents" in src("models/auction_award.py")
    and has_button("action_generate_documents"))
hop("purchase orders generated",
    "_generate_purchase_orders" in src("models/auction_award.py"))
hop("sale orders generated for disposal",
    "_generate_sale_orders" in src("models/auction_award.py"))
down = src("models/downstream.py")
hop("linkage level 1: order -> event",
    "auction_event_id" in down)
hop("linkage level 2: order line -> bid line",
    "auction_bid_line_id" in down)
hop("linkage level 3: award line between them",
    "auction_award_id" in down)
hop("one PO line per delivery tranche",
    "date_planned" in src("models/auction_award.py"))

# 8. registered in the module
init = src("models/__init__.py")
for mod in ("auction_evaluation", "auction_award", "downstream"):
    hop("models/%s imported" % mod, ("from . import %s" % mod) in init)

print("\n%d break(s)\n" % len(FAIL))
sys.exit(1 if FAIL else 0)
