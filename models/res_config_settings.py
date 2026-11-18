# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    ie_enforcement = fields.Boolean(related='company_id.ie_enforcement', readonly=False)
    ie_duplicate_mode = fields.Selection(related='company_id.ie_duplicate_mode', readonly=False)
    ie_duplicate_days = fields.Integer(related='company_id.ie_duplicate_days', readonly=False)
    ie_price_tolerance = fields.Float(related='company_id.ie_price_tolerance', readonly=False)
    ie_recon_tolerance = fields.Float(related='company_id.ie_recon_tolerance', readonly=False)
    ie_backreg_days = fields.Integer(related='company_id.ie_backreg_days', readonly=False)
    ie_lc_alert_days = fields.Integer(related='company_id.ie_lc_alert_days', readonly=False)
    ie_approval_delay_days = fields.Integer(related='company_id.ie_approval_delay_days', readonly=False)
    ie_proceeds_alert_days = fields.Integer(related='company_id.ie_proceeds_alert_days', readonly=False)
    ie_kpi_doc_target = fields.Float(related='company_id.ie_kpi_doc_target', readonly=False)
    ie_kpi_stock_target = fields.Float(related='company_id.ie_kpi_stock_target', readonly=False)
    ie_kpi_reconcile_target = fields.Float(related='company_id.ie_kpi_reconcile_target', readonly=False)
