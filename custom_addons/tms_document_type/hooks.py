def post_init_hook(env):
    """Existing documents are transport documents: give each its Transport type, and mark the products only
    transport may use."""
    Type = env["tms.document.type"]
    for model, applies, domain in (
            ("sale.order", "sale", []),
            ("purchase.order", "purchase", []),
            ("account.move", "out_invoice", [("move_type", "in", ("out_invoice", "out_refund"))]),
            ("account.move", "in_invoice", [("move_type", "in", ("in_invoice", "in_refund"))])):
        dtype = Type._tms_default_for(applies, transport=True)
        recs = env[model].with_context(active_test=False).search(domain + [("tms_document_type_id", "=", False)])
        if recs and dtype:
            env.cr.execute("UPDATE %s SET tms_document_type_id = %%s WHERE id IN %%s" % env[model]._table,
                           (dtype.id, tuple(recs.ids)))
    env["account.move"].invalidate_model(["tms_document_type_id"])
    env["sale.order"].invalidate_model(["tms_document_type_id"])
    env["purchase.order"].invalidate_model(["tms_document_type_id"])
    products = env["product.template"].with_context(active_test=False).search([("tms_cost_item", "=", True)])
    for company in env["res.company"].search([]):
        products |= company.tms_freight_product_id.product_tmpl_id
    for xmlid in ("tms_core.product_freight", "tms_core.product_vehicle_hire", "tms_core.product_hire_extra",
                  "tms_core.product_broker_commission", "tms_estimate.product_movement"):
        product = env.ref(xmlid, raise_if_not_found=False)
        if product:
            products |= product.product_tmpl_id
    products.write({"tms_transport_only": True})
