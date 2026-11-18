# -*- coding: utf-8 -*-
from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    ie_hs_code_id = fields.Many2one('ie.hs.code', string='HS Code', tracking=True)
    ie_quality_template_id = fields.Many2one('ie.quality.template', string='Export Quality Template')


class ProductProduct(models.Model):
    _inherit = 'product.product'

    ie_hs_code_id = fields.Many2one(
        related='product_tmpl_id.ie_hs_code_id',
        string='HS Code',
        store=True,
        readonly=False,
    )
    ie_quality_template_id = fields.Many2one(
        related='product_tmpl_id.ie_quality_template_id',
        string='Export Quality Template',
        store=True,
        readonly=False,
    )
