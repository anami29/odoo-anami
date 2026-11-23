import logging
import math

from odoo import api

_logger = logging.getLogger(__name__)


def roundup10(weight):
    """BR-LR-06: actual weight rounded up to 10 kg."""
    if not weight:
        return 0.0
    return float(10 * math.ceil(weight / 10.0))


def notify_detached(env, res_model, res_id, user_ids, summary, note):
    """Schedule activities in a separate transaction, so that they survive the
    UserError that blocks the user's action (BR-NTF-14, BR-NTF-17)."""
    if not user_ids or not res_id:
        return
    try:
        with env.registry.cursor() as cr:
            env2 = api.Environment(cr, env.uid, dict(env.context))
            record = env2[res_model].sudo().browse(res_id)
            if not record.exists():
                return
            for uid in set(user_ids):
                record.activity_schedule("mail.mail_activity_data_todo", user_id=uid, summary=summary, note=note)
    except Exception:  # noqa: BLE001 - notifying must never mask the business error
        _logger.exception("Could not schedule detached activity on %s,%s", res_model, res_id)


def users_in_group(env, xmlid, company=None):
    group = env.ref(xmlid, raise_if_not_found=False)
    if not group:
        return env["res.users"]
    users = group.users.filtered(lambda u: u.active and not u.share)
    if company:
        users = users.filtered(lambda u: company in u.company_ids)
    return users


def schedule(record, users, summary, note=None):
    """Schedule one to-do activity per user on record (no duplicates of the same summary)."""
    if not users:
        return
    existing = record.activity_ids.filtered(lambda a: a.summary == summary).mapped("user_id")
    for user in users - existing:
        record.sudo().activity_schedule(
            "mail.mail_activity_data_todo", user_id=user.id, summary=summary, note=note or summary
        )


def inr(amount):
    """Rupees in the Indian grouping used on every TMS message: 480000.5 -> "₹4,80,000.50"."""
    sign = "-" if amount < 0 else ""
    whole, frac = ("%.2f" % abs(amount)).split(".")
    head, tail = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return "%s₹%s.%s" % (sign, ",".join(groups + [tail]), frac)


def ratio_read_group(parent, ratios, domain, fields, groupby, **kw):
    """read_group where each ratio column of a group row is sum(numerators) / sum(denominators) x factor,
    never the average of its rows. ``ratios`` maps a column to (numerator fields, denominator fields, factor)."""
    wanted = {name: ratios[name] for name in {spec.split(":")[0] for spec in fields} if name in ratios}
    sums = sorted({fname for num, den, _f in wanted.values() for fname in num + den})
    specs = list(fields) + ["tms_rsum_%s:sum(%s)" % (fname, fname) for fname in sums]
    groups = parent.read_group(domain, specs, groupby, **kw)
    for group in groups:
        for name, (num, den, factor) in wanted.items():
            numerator = sum(group.get("tms_rsum_%s" % f) or 0.0 for f in num)
            denominator = sum(group.get("tms_rsum_%s" % f) or 0.0 for f in den)
            group[name] = numerator / denominator * factor if denominator else 0.0
        for fname in sums:
            group.pop("tms_rsum_%s" % fname, None)
    return groups
