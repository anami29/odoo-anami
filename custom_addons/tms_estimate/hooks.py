def post_init_hook(env):
    """BR-AUD-02: create, write and delete with old and new values on the quoting models."""
    env["auditlog.rule"]._tms_ensure_rules([
        "tms.estimate", "tms.estimate.line", "tms.estimate.amendment", "sale.order", "sale.order.line",
        "crm.lead", "product.supplierinfo", "product.pricelist.item",
    ])
