# -*- coding: utf-8 -*-
{
    'name': 'Agreement Management',
    'summary': 'Template-versioned agreements with sequential Party A → Party B e-signing, executed PDF, distribution and supplementary annexures',
    'description': """
Agreement Management Module — Odoo 18 Community
================================================
* Agreement templates with versions (Draft → Active → Superseded, one Active version per template)
* Template-specific dynamic fields (Odoo Properties) and configurable signature locations
* Agreements created only from the Active version; version and content locked on confirmation
* Sequential electronic signing: Party A signs all required areas before Party B; multiple areas across pages
* Final executed PDF generated and stored as the official copy; print / download; email distribution with audit
* Supplementary Annexures linked to executed agreements, with their own numbering, fields, signing and PDF
* Search, filters, grouping, history, audit trail (chatter tracking), security groups and record rules
""",
    'version': '18.0.1.0.12',
    'category': 'Productivity/Documents',
    'author': 'Riamona Luxury and Fashion Brands',
    'website': 'https://www.rlfb.in',
    'license': 'LGPL-3',
    'depends': ['base', 'mail', 'contacts', 'web'],
    'data': [
        'security/agreement_security.xml',
        'security/ir.model.access.csv',
        'data/agreement_sequence_data.xml',
        'data/agreement_type_data.xml',
        'data/agreement_font_data.xml',
        'data/mail_template_data.xml',
        'report/agreement_report_templates.xml',
        'report/agreement_report.xml',
        'wizard/agreement_sign_wizard_views.xml',
        'views/agreement_type_views.xml',
        'views/agreement_font_views.xml',
        'views/agreement_template_views.xml',
        'views/agreement_signature_views.xml',
        'views/agreement_distribution_views.xml',
        'views/agreement_annexure_views.xml',
        'views/agreement_views.xml',
        'views/res_config_settings_views.xml',
        'views/agreement_menus.xml',
    ],
    'demo': [
        'demo/agreement_demo.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'agreement_management/static/src/scss/agreement_document.scss',
            'agreement_management/static/src/js/agreement_editor.js',
        ],
    },
    'application': True,
    'installable': True,
    'auto_install': False,
}
