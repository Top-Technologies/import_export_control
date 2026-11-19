# -*- coding: utf-8 -*-
{
    'name': 'Import & Export Control',
    'version': '18.0.1.0.0',
    'category': 'Operations/Import Export',
    'summary': 'Controlled, traceable import and export from request to reconciliation',
    'description': """
Import & Export Control
=======================
One transaction reference from request through approval, payment or letter of
credit, shipment, customs, quality, warehouse, finance and reconciliation.

Approvals are handled by the standard Approvals app. This module keeps the
operational chain Odoo does not provide: letters of credit, shipments, customs,
export proceeds, the compliance gate and the audit log.
    """,
    'author': 'Top System Solution Technologies Pvt. Ltd.',
    'license': 'LGPL-3',
    'depends': [
        'base',  # partners, users, companies, sequences, attachments
        'mail',  # chatter, activities and email templates
        'product',  # product master used on order lines
        'uom',  # units of measure on order lines
        'stock',  # quants, reservations, receipts and deliveries
        'stock_account',  # valuation layer required by landed costs
        'stock_landed_costs',  # freight, duty, insurance and bank charges
        'purchase',  # import purchase orders
        'purchase_stock',  # receipt created from the import purchase order
        'sale_management',  # export sales orders and quotations
        'sale_pdf_quote_builder',  # Quote Builder page hidden on the sales order
        'sale_stock',  # delivery orders linked to the export sales order
        'account',  # invoices, payments, incoterms and journals
        'account_accountant',  # Enterprise bank reconciliation status
        'quality_control',  # Enterprise quality points, checks and alerts
        'approvals',  # Enterprise multi-approver requests (brings hr)
        # account_reports is intentionally not a hard dependency.
        # documents is a soft integration: used only when that module is installed.
    ],
    'data': [
        'security/ie_security.xml',
        'security/ir.model.access.csv',
        'data/ir_sequence.xml',
        'data/master_data.xml',
        'data/analytic_plan_data.xml',
        'data/approval_category_data.xml',
        'data/document_requirement_data.xml',
        'data/quality_template_data.xml',
        'data/mail_template.xml',
        'data/ir_cron.xml',
        'data/demo_stage_orders.xml',
        'wizard/ie_reason_wizard_views.xml',
        'views/ie_master_views.xml',
        'views/ie_import_order_views.xml',
        'views/ie_export_order_views.xml',
        'views/ie_operation_views.xml',
        'views/ie_compliance_views.xml',
        'views/ie_audit_views.xml',
        'views/ie_dashboard_views.xml',
        'views/inherited_views.xml',
        'views/res_config_settings_views.xml',
        'report/ie_reports.xml',
        'report/ie_report_templates.xml',
        'views/ie_menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'import_export_control/static/src/dashboard/dashboard.scss',
            'import_export_control/static/src/dashboard/dashboard.xml',
            'import_export_control/static/src/dashboard/dashboard.js',
        ],
    },
    'application': True,
    'installable': True,
}
