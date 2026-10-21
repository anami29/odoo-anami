CONTRACT_MODELS = [
    "tms.contract", "tms.contract.rate", "tms.contract.item.rate", "tms.contract.extension",
    "tms.contract.excess", "tms.contract.reservation", "tms.contract.escalation",
]


def post_init_hook(env):
    """BR-AUD-01, BR-AUD-02: the contract models are audited with old and new values."""
    env["auditlog.rule"]._tms_ensure_rules(CONTRACT_MODELS)
