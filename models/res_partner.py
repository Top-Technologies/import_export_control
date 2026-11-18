# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    ie_supplier_status = fields.Selection([
        ('draft', 'Draft'),
        ('approved', 'Approved'),
        ('expired', 'Expired'),
    ], string='Import Supplier Status', default='draft', tracking=True)
    ie_supplier_expiry = fields.Date(string='Supplier Approval Expiry', tracking=True)
    ie_buyer_status = fields.Selection([
        ('draft', 'Draft'),
        ('approved', 'Approved'),
        ('expired', 'Expired'),
    ], string='Export Buyer Status', default='draft', tracking=True)
    ie_buyer_expiry = fields.Date(string='Buyer Approval Expiry', tracking=True)
    ie_supplier_approved = fields.Boolean(compute='_compute_ie_approved', store=True)
    ie_buyer_approved = fields.Boolean(compute='_compute_ie_approved', store=True)

    @api.depends('ie_supplier_status', 'ie_supplier_expiry', 'ie_buyer_status', 'ie_buyer_expiry')
    def _compute_ie_approved(self):
        today = fields.Date.context_today(self)
        for partner in self:
            supplier_ok = partner.ie_supplier_status == 'approved' and (
                not partner.ie_supplier_expiry or partner.ie_supplier_expiry >= today
            )
            buyer_ok = partner.ie_buyer_status == 'approved' and (
                not partner.ie_buyer_expiry or partner.ie_buyer_expiry >= today
            )
            partner.ie_supplier_approved = supplier_ok
            partner.ie_buyer_approved = buyer_ok

    @api.model
    def _cron_ie_expire_partners(self):
        today = fields.Date.context_today(self)
        suppliers = self.search([
            ('ie_supplier_status', '=', 'approved'),
            ('ie_supplier_expiry', '!=', False),
            ('ie_supplier_expiry', '<', today),
        ])
        suppliers.write({'ie_supplier_status': 'expired'})
        buyers = self.search([
            ('ie_buyer_status', '=', 'approved'),
            ('ie_buyer_expiry', '!=', False),
            ('ie_buyer_expiry', '<', today),
        ])
        buyers.write({'ie_buyer_status': 'expired'})
