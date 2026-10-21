PNL_AUDITED_MODELS = ["tms.std.rate", "tms.truck.category", "tms.prepaid.schedule", "tms.pnl.close"]


def post_init_hook(env):
    """BR-AUD-02 audit rules on the profitability models; BR-LED-01 analytic accounts for existing
    vehicles, branches and confirmed trips."""
    env["auditlog.rule"]._tms_ensure_rules(PNL_AUDITED_MODELS)
    for vehicle in env["fleet.vehicle"].with_context(active_test=False).search([]):
        vehicle._tms_analytic_account()
    for company in env["res.company"].search([]):
        company._tms_analytic_account()
    trips = env["tms.order"].search([("state_code", "not in", ("draft", "cancel", False))])
    for trip in trips:
        trip._tms_analytic_account()
