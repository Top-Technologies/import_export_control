# -*- coding: utf-8 -*-
"""Shared selections and state machines for import and export orders."""

PAYMENT_METHODS = [
    ('lc', 'Letter of Credit'),
    ('cad', 'CAD'),
    ('advance', 'Advance'),
    ('open_account', 'Open Account'),
    ('other', 'Other'),
]

IMPORT_STATES = [
    ('draft', 'Draft'),
    ('department', 'Department Approval'),
    ('procurement', 'Procurement Verification'),
    ('finance', 'Finance/Budget Verification'),
    ('ie_review', 'Import & Export Review'),
    ('compliance', 'Control & Compliance Check'),
    ('management', 'Management Approval'),
    ('approved', 'Approved'),
    ('bank_lc', 'Bank/LC Processing'),
    ('shipment', 'Shipment'),
    ('received', 'Received'),
    ('closed', 'Closed'),
    ('cancelled', 'Cancelled'),
    ('hold', 'On Hold'),
]

EXPORT_STATES = [
    ('draft', 'Draft'),
    ('sales_request', 'Sales/Export Request'),
    ('stock_verification', 'Stock Verification'),
    ('quality_verification', 'Quality Verification'),
    ('finance', 'Finance/Commercial Review'),
    ('compliance', 'Control & Compliance Check'),
    ('management', 'Management Approval'),
    ('approved', 'Approved for Shipment'),
    ('customs_loading', 'Customs/Loading'),
    ('shipped', 'Shipped'),
    ('proceeds', 'Proceeds Received'),
    ('reconciled', 'Reconciled'),
    ('closed', 'Closed'),
    ('cancelled', 'Cancelled'),
    ('hold', 'On Hold'),
]

# States that create an approval.request. Compliance is a gate, not an Approvals record.
IMPORT_APPROVAL_STATES = ('department', 'procurement', 'finance', 'ie_review', 'management')
EXPORT_APPROVAL_STATES = (
    'sales_request', 'stock_verification', 'quality_verification', 'finance', 'management',
)

IMPORT_NEXT = {
    'draft': 'department',
    'department': 'procurement',
    'procurement': 'finance',
    'finance': 'ie_review',
    'ie_review': 'compliance',
    'compliance': 'management',
    'management': 'approved',
    'approved': 'bank_lc',
    'bank_lc': 'shipment',
    'shipment': 'received',
    'received': 'closed',
}
IMPORT_PREV = {value: key for key, value in IMPORT_NEXT.items()}

EXPORT_NEXT = {
    'draft': 'sales_request',
    'sales_request': 'stock_verification',
    'stock_verification': 'quality_verification',
    'quality_verification': 'finance',
    'finance': 'compliance',
    'compliance': 'management',
    'management': 'approved',
    'approved': 'customs_loading',
    'customs_loading': 'shipped',
    'shipped': 'proceeds',
    'proceeds': 'reconciled',
    'reconciled': 'closed',
}
EXPORT_PREV = {value: key for key, value in EXPORT_NEXT.items()}

# An import transaction is allowed once management has approved the order.
IMPORT_UNLOCKED = ('approved', 'bank_lc', 'shipment', 'received', 'closed')
EXPORT_UNLOCKED = ('approved', 'customs_loading', 'shipped', 'proceeds', 'reconciled', 'closed')

IMPORT_STATE_GROUPS = {
    'department': 'import_export_control.group_ie_officer',
    'procurement': 'import_export_control.group_ie_procurement',
    'finance': 'import_export_control.group_ie_finance',
    'ie_review': 'import_export_control.group_ie_officer',
    'compliance': 'import_export_control.group_ie_compliance',
    'management': 'import_export_control.group_ie_management',
    'bank_lc': 'import_export_control.group_ie_finance',
    'shipment': 'import_export_control.group_ie_officer',
    'received': 'import_export_control.group_ie_warehouse',
    'closed': 'import_export_control.group_ie_finance',
}

EXPORT_STATE_GROUPS = {
    'sales_request': 'import_export_control.group_ie_officer',
    'stock_verification': 'import_export_control.group_ie_warehouse',
    'quality_verification': 'import_export_control.group_ie_quality',
    'finance': 'import_export_control.group_ie_finance',
    'compliance': 'import_export_control.group_ie_compliance',
    'management': 'import_export_control.group_ie_management',
    'customs_loading': 'import_export_control.group_ie_officer',
    'shipped': 'import_export_control.group_ie_officer',
    'proceeds': 'import_export_control.group_ie_finance',
    'reconciled': 'import_export_control.group_ie_finance',
    'closed': 'import_export_control.group_ie_finance',
}
