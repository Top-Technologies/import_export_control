# -*- coding: utf-8 -*-
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    ie_enforcement = fields.Boolean(
        string='Enforce No-Order-No-Transaction',
        default=True,
        help='When enabled, import and export documents cannot be confirmed or posted without an approved order.',
    )
    ie_duplicate_mode = fields.Selection([
        ('block', 'Block'),
        ('warn', 'Warn'),
    ], string='Duplicate Detection', default='block', required=True)
    ie_duplicate_days = fields.Integer(string='Duplicate Window (days)', default=7)
    ie_price_tolerance = fields.Float(string='Price Tolerance (%)', default=10.0)
    ie_recon_tolerance = fields.Float(string='Reconciliation Tolerance (%)', default=1.0)
    ie_backreg_days = fields.Integer(string='Back-registration Days', default=5)
    ie_lc_alert_days = fields.Integer(string='LC Expiry Alert (days)', default=7)
    ie_approval_delay_days = fields.Integer(string='Approval Escalation (days)', default=2)
    ie_proceeds_alert_days = fields.Integer(string='Proceeds Delay Alert (days)', default=7)
    ie_kpi_doc_target = fields.Float(string='Documentation Target (%)', default=98.0)
    ie_kpi_stock_target = fields.Float(string='Stock Accuracy Target (%)', default=99.0)
    ie_kpi_reconcile_target = fields.Float(string='Reconciliation Target (%)', default=100.0)


class ResUsers(models.Model):
    _inherit = 'res.users'

    ie_department_id = fields.Many2one('ie.department', string='Import/Export Department', check_company=True)
