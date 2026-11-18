# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class IeBusinessUnit(models.Model):
    _name = 'ie.business.unit'
    _description = 'Business Unit'
    _inherit = ['mail.thread']
    _order = 'name'

    name = fields.Char(required=True, translate=True, tracking=True)
    code = fields.Char(required=True, tracking=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    warehouse_id = fields.Many2one(
        'stock.warehouse', string='Warehouse', check_company=True,
        help='Warehouse that receives imported goods transferred to this unit.',
    )
    budget_amount = fields.Monetary(
        string='Import Budget',
        currency_field='currency_id',
        help='Zero means the budget check is not configured and the compliance gate records N/A.',
    )
    currency_id = fields.Many2one('res.currency', related='company_id.currency_id')
    active = fields.Boolean(default=True)
    note = fields.Text(translate=True)

    _sql_constraints = [
        ('code_company_uniq', 'unique(code, company_id)', 'The business unit code must be unique per company.'),
    ]

    @api.depends('code', 'name')
    def _compute_display_name(self):
        for unit in self:
            unit.display_name = '[%s] %s' % (unit.code, unit.name) if unit.code else unit.name


class IeDepartment(models.Model):
    _name = 'ie.department'
    _description = 'Requesting Department'
    _order = 'name'

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True)
    business_unit_id = fields.Many2one('ie.business.unit', check_company=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ('code_company_uniq', 'unique(code, company_id)', 'The department code must be unique per company.'),
    ]


class IeImportCategory(models.Model):
    _name = 'ie.import.category'
    _description = 'Import Category'
    _order = 'name'

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True)
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ('code_uniq', 'unique(code)', 'The import category code must be unique.'),
    ]


class IeHsCode(models.Model):
    _name = 'ie.hs.code'
    _description = 'HS Code'
    _order = 'code'

    code = fields.Char(required=True, index=True)
    name = fields.Char(required=True, translate=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', index=True)

    _sql_constraints = [
        ('code_uniq', 'unique(code)', 'The HS code must be unique.'),
    ]

    @api.depends('code', 'name')
    def _compute_display_name(self):
        for hs in self:
            hs.display_name = '%s %s' % (hs.code, hs.name)


class IeDocumentType(models.Model):
    _name = 'ie.document.type'
    _description = 'Document Requirement'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    order_type = fields.Selection([
        ('import', 'Import'),
        ('export', 'Export'),
        ('both', 'Both'),
    ], required=True, default='import')
    blocking_state = fields.Char(
        help='Workflow state that cannot be entered while this required document is missing.',
    )
    required = fields.Boolean(default=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', index=True)

    _sql_constraints = [
        ('code_type_uniq', 'unique(code, order_type, company_id)', 'This document code already exists for the order type.'),
    ]


class IeQualityTemplate(models.Model):
    _name = 'ie.quality.template'
    _description = 'Product Quality Parameter Template'
    _order = 'name'

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True)
    line_ids = fields.One2many('ie.quality.template.line', 'template_id', copy=True)
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ('code_uniq', 'unique(code)', 'The quality template code must be unique.'),
    ]


class IeQualityTemplateLine(models.Model):
    _name = 'ie.quality.template.line'
    _description = 'Quality Template Parameter'
    _order = 'sequence, id'

    template_id = fields.Many2one('ie.quality.template', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    code = fields.Char(required=True)
    name = fields.Char(required=True, translate=True)
    min_value = fields.Float()
    max_value = fields.Float()
    uom = fields.Char(translate=True)

    @api.constrains('min_value', 'max_value')
    def _check_range(self):
        for line in self:
            if line.max_value and line.min_value and line.max_value < line.min_value:
                raise ValidationError(_("The maximum value cannot be lower than the minimum for %s.", line.name))
