ODC_AUDITED = ["tms.estimate.piece", "tms.estimate.route", "tms.trip.equipment", "tms.odc.permit"]


def post_init_hook(env):
    """BR-AUD-02: the ODC records keep old and new values."""
    env["auditlog.rule"]._tms_ensure_rules(ODC_AUDITED)
