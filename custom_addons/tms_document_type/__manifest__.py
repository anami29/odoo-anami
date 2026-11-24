# Copyright 2026 RLFB (Riamona Luxury and Fashion Brands), https://www.riamona.com
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
{
    "name": "TMS Document Types (Premier Transport)",
    "summary": "Transport and Standard document types on sales orders, purchase orders, invoices and bills: "
    "plain Odoo for standard, scrap, vehicle and office AMC business, the transport rules for the rest",
    "version": "18.0.1.5.0",
    "category": "TMS",
    "license": "LGPL-3",
    "author": "RLFB (Riamona Luxury and Fashion Brands)",
    "maintainers": ["RLFB"],
    "website": "https://www.riamona.com",
    "depends": ["tms_contract", "tms_odc", "tms_profitability", "purchase_requisition"],
    "data": [
        "security/tms_document_type_security.xml",
        "security/ir.model.access.csv",
        "data/tms_document_type_data.xml",
        "views/tms_document_type_views.xml",
        "views/sale_order_views.xml",
        "views/purchase_order_views.xml",
        "views/account_move_views.xml",
        "views/purchase_requisition_views.xml",
        "views/res_users_views.xml",
        "views/product_views.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
}
