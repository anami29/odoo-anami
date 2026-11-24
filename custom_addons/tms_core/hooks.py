def tms_indian_locale(env):
    """Dates read dd/mm/yyyy and amounts use Indian grouping (₹4,50,000): English (IN) becomes the language of
    new contacts and of internal users still on English (US)."""
    env["res.lang"]._activate_lang("en_IN")
    env["ir.default"].set("res.partner", "lang", "en_IN")
    users = env["res.users"].with_context(active_test=False).search([("share", "=", False), ("lang", "=", "en_US")])
    users.write({"lang": "en_IN"})


def post_init_hook(env):
    """Subscribe the audit rules (BR-AUD-02), give existing branches an LR series and set the Indian locale."""
    env["auditlog.rule"]._tms_ensure_rules()
    for company in env["res.company"].search([]):
        if not company.tms_branch_code:
            company.tms_branch_code = (company.name or "BR")[:3].upper()
    tms_indian_locale(env)
