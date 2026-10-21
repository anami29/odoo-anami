def post_init_hook(env):
    """Subscribe the audit rules (BR-AUD-02) and give existing branches an LR series."""
    env["auditlog.rule"]._tms_ensure_rules()
    for company in env["res.company"].search([]):
        if not company.tms_branch_code:
            company.tms_branch_code = (company.name or "BR")[:3].upper()
