# -*- coding: utf-8 -*-
"""Compliance gate.

Import result is COMPLIANT or EXCEPTION. Export result is APPROVED FOR EXPORT
or HOLD. Any failed mandatory line forces the negative result and the order
cannot move to management approval until the gate is corrected and finalized again.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError

IMPORT_CHECKS = [
    ('supplier', 'Approved supplier', True),
    ('pi', 'Approved proforma invoice', True),
    ('hs', 'Correct HS code', True),
    ('quantity', 'Correct quantity', True),
    ('price', 'Reasonable price', True),
    ('payment', 'Approved payment method', True),
    ('documents', 'Required documents', True),
    ('budget', 'Budget availability', True),
    ('duplicate', 'No duplicate transaction', True),
    ('regulatory', 'Regulatory compliance', False),
]

EXPORT_CHECKS = [
    ('buyer', 'Approved buyer', True),
    ('contract', 'Valid contract', True),
    ('stock', 'Stock available', True),
    ('quality', 'Quality approved', True),
    ('quantity', 'Correct quantity', True),
    ('price', 'Correct price', True),
    ('payment', 'Payment terms approved', True),
    ('documents', 'Export documents complete', True),
    ('customs', 'Customs requirements fulfilled', True),
    ('critical', 'No outstanding critical compliance issue', True),
]


class IeComplianceGate(models.Model):
    _name = 'ie.compliance.gate'
    _description = 'Compliance Gate'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(required=True, copy=False, default=lambda self: _('New'), readonly=True)
    order_type = fields.Selection([('import', 'Import'), ('export', 'Export')], required=True, index=True)
    import_order_id = fields.Many2one('ie.import.order', ondelete='cascade', index=True)
    export_order_id = fields.Many2one('ie.export.order', ondelete='cascade', index=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    line_ids = fields.One2many('ie.compliance.gate.line', 'gate_id')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('done', 'Done'),
        ('cancelled', 'Cancelled'),
    ], default='draft', tracking=True)
    result = fields.Selection([
        ('compliant', 'Compliant'),
        ('exception', 'Exception'),
        ('approved', 'Approved for Export'),
        ('hold', 'Hold'),
    ], tracking=True)
    fail_reason = fields.Text()
    user_id = fields.Many2one('res.users', string='Checked By', readonly=True)
    check_date = fields.Datetime(readonly=True)

    @api.depends('name', 'order_type')
    def _compute_display_name(self):
        for gate in self:
            gate.display_name = gate.name

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('ie.compliance.gate') or _('New')
        return super().create(vals_list)

    def _ie_order(self):
        self.ensure_one()
        return self.import_order_id or self.export_order_id

    def _ie_seed_and_evaluate(self):
        catalogue = IMPORT_CHECKS if self.order_type == 'import' else EXPORT_CHECKS
        if not self.line_ids:
            self.env['ie.compliance.gate.line'].create([{
                'gate_id': self.id,
                'code': code,
                'name': name,
                'mandatory': mandatory,
            } for code, name, mandatory in catalogue])
        for line in self.line_ids:
            result, remark = self._ie_auto_result(line.code)
            line.write({'result': result, 'remark': remark, 'auto': True})
        self._ie_log_order(_('Compliance gate evaluated.'))

    def _ie_auto_result(self, code):
        order = self._ie_order()
        company = order.company_id
        if code == 'supplier':
            return ('pass', _('Approved supplier.')) if order.partner_id.ie_supplier_approved else ('fail', _('Supplier is not approved.'))
        if code == 'buyer':
            return ('pass', _('Approved buyer.')) if order.partner_id.ie_buyer_approved else ('fail', _('Buyer is not approved.'))
        if code == 'pi':
            return ('pass', order.proforma_number or '') if order.proforma_number else ('fail', _('Proforma invoice number is missing.'))
        if code == 'contract':
            expired = order.contract_expiry and order.contract_expiry < fields.Date.context_today(self)
            if not order.contract_no:
                return ('fail', _('Contract number is missing.'))
            if expired:
                return ('fail', _('Contract is expired.'))
            return ('pass', order.contract_no)
        if code == 'hs':
            bad = order.line_ids.filtered(lambda line: line.hs_code_id != line.product_id.ie_hs_code_id)
            return ('fail', _('HS code does not match the product.')) if bad else ('pass', _('HS codes match the products.'))
        if code == 'quantity':
            return ('pass', _('Quantities are positive.')) if order.line_ids else ('fail', _('No lines.'))
        if code == 'price':
            return self._ie_price_result(order, company)
        if code == 'payment':
            return ('pass', order.payment_method) if order.payment_method else ('fail', _('Payment method is missing.'))
        if code == 'documents':
            missing = order.document_line_ids.filtered(lambda line: line.required and not line.attachment_ids)
            return ('fail', _('Missing %s.', ', '.join(missing.mapped('name')))) if missing else ('pass', _('Required documents are attached.'))
        if code == 'budget':
            budget = order.business_unit_id.budget_amount
            if not budget:
                return ('na', _('No budget is configured on the business unit.'))
            open_amount = sum(self.env['ie.import.order'].search([
                ('business_unit_id', '=', order.business_unit_id.id),
                ('state', 'not in', ('cancelled', 'closed')),
            ]).mapped('amount_total'))
            if open_amount > budget:
                return ('fail', _('Open import value %(open)s exceeds budget %(budget)s.', open=open_amount, budget=budget))
            return ('pass', _('Within budget.'))
        if code == 'duplicate':
            return ('fail', _('A duplicate was detected.')) if order.duplicate_flag else ('pass', _('No duplicate detected.'))
        if code == 'stock':
            try:
                order._ie_assert_stock()
            except UserError as error:
                return ('fail', str(error))
            return ('pass', _('Stock covers the export quantity.'))
        if code == 'quality':
            checks = self.env['quality.check'].search([('ie_export_order_id', '=', order.id)])
            if not checks:
                return ('na', _('Quality checks are created at the quality stage.'))
            if any(check.quality_state == 'fail' for check in checks):
                return ('fail', _('A quality check failed.'))
            if any(check.quality_state == 'none' for check in checks):
                return ('pass', _('Open quality checks are not required.'))
            return ('pass', _('Quality checks passed.'))
        if code == 'customs':
            if order.state in ('draft', 'sales_request', 'stock_verification', 'quality_verification', 'finance', 'compliance', 'management'):
                return ('na', _('Customs is checked again before shipment.'))
            cleared = order.customs_ids.filtered(lambda customs: customs.state == 'cleared')
            return ('pass', _('Customs cleared.')) if cleared else ('fail', _('Customs is not cleared.'))
        if code == 'critical':
            critical = order.exception_ids.filtered(lambda item: item.severity == 'critical' and item.state not in ('resolved', 'cancelled'))
            return ('fail', _('Critical compliance exceptions are open.')) if critical else ('pass', _('No critical exception.'))
        if code == 'regulatory':
            return ('na', _('Confirm regulatory compliance manually.'))
        return ('na', _('Manual check.'))

    def _ie_price_result(self, order, company):
        tolerance = (company.ie_price_tolerance or 0.0) / 100.0
        remarks = []
        failed = False
        for line in order.line_ids:
            if order._name == 'ie.import.order':
                previous = self.env['purchase.order.line'].search([
                    ('product_id', '=', line.product_id.id),
                    ('state', 'in', ('purchase', 'done')),
                    ('order_id.company_id', '=', company.id),
                ], order='id desc', limit=1)
                reference = previous.price_unit if previous else 0.0
            else:
                previous = self.env['sale.order.line'].search([
                    ('product_id', '=', line.product_id.id),
                    ('state', '=', 'sale'),
                    ('order_id.company_id', '=', company.id),
                ], order='id desc', limit=1)
                reference = previous.price_unit if previous else 0.0
            if not reference:
                remarks.append(_('%s has no previous price.', line.product_id.display_name))
                continue
            gap = abs(line.price_unit - reference) / reference
            if gap > tolerance:
                failed = True
                remarks.append(_('%(product)s price %(price)s differs from %(ref)s.', product=line.product_id.display_name, price=line.price_unit, ref=reference))
        if failed:
            return ('fail', ' '.join(remarks))
        if not remarks:
            return ('pass', _('Prices are within tolerance.'))
        return ('na', ' '.join(remarks))

    def action_recompute(self):
        for gate in self:
            if gate.state == 'done':
                raise UserError(_("Reopen is not available on a finalized gate. Create the evaluation again from the order."))
            gate._ie_seed_and_evaluate()
        return True

    def action_finalize(self):
        for gate in self:
            if gate.state != 'draft':
                raise UserError(_("The gate is already finalized."))
            if self.env.user == gate._ie_order().create_uid and not self.env.su:
                raise UserError(_("The order creator cannot finalize the compliance gate."))
            if not self.env.su and not self.env.user.has_group('import_export_control.group_ie_compliance'):
                raise UserError(_("Only Control & Compliance can finalize the gate."))
            pending = gate.line_ids.filtered(lambda line: line.mandatory and line.result not in ('pass', 'fail', 'na'))
            if pending:
                raise UserError(_("Complete every mandatory check."))
            failed = gate.line_ids.filtered(lambda line: line.mandatory and line.result == 'fail')
            if gate.order_type == 'import':
                gate.result = 'exception' if failed else 'compliant'
            else:
                gate.result = 'hold' if failed else 'approved'
            gate.fail_reason = ', '.join(failed.mapped('name')) if failed else False
            gate.write({
                'state': 'done',
                'user_id': self.env.user.id,
                'check_date': fields.Datetime.now(),
            })
            if failed:
                for line in failed:
                    self.env['ie.compliance.exception'].create({
                        'gate_id': gate.id,
                        'import_order_id': gate.import_order_id.id,
                        'export_order_id': gate.export_order_id.id,
                        'name': line.name,
                        'severity': 'major',
                        'company_id': gate.company_id.id,
                        'user_id': self.env.user.id,
                    })
            gate._ie_log_order(_('Compliance result: %s.', gate.result), reason=gate.fail_reason)
        return True

    def _ie_log_order(self, detail, reason=None):
        order = self._ie_order()
        if order:
            self.env['ie.audit.log'].ie_log(order, 'approval', detail=detail, reason=reason)


class IeComplianceGateLine(models.Model):
    _name = 'ie.compliance.gate.line'
    _description = 'Compliance Gate Line'
    _order = 'id'

    gate_id = fields.Many2one('ie.compliance.gate', required=True, ondelete='cascade')
    code = fields.Char(required=True)
    name = fields.Char(required=True, translate=True)
    mandatory = fields.Boolean(default=True)
    result = fields.Selection([
        ('pass', 'Pass'),
        ('fail', 'Fail'),
        ('na', 'N/A'),
    ])
    remark = fields.Char()
    auto = fields.Boolean(readonly=True)
    attachment_ids = fields.Many2many('ir.attachment', string='Evidence')
    user_id = fields.Many2one('res.users')
    check_date = fields.Datetime()
    company_id = fields.Many2one(related='gate_id.company_id', store=True)

    def write(self, vals):
        if 'result' in vals and not self.env.context.get('ie_skip_audit'):
            vals = dict(vals)
            vals.setdefault('user_id', self.env.user.id)
            vals.setdefault('check_date', fields.Datetime.now())
        return super().write(vals)


class IeComplianceException(models.Model):
    _name = 'ie.compliance.exception'
    _description = 'Compliance Exception'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(required=True, tracking=True)
    reference = fields.Char(readonly=True, copy=False, default=lambda self: _('New'))
    gate_id = fields.Many2one('ie.compliance.gate')
    import_order_id = fields.Many2one('ie.import.order', index=True)
    export_order_id = fields.Many2one('ie.export.order', index=True)
    severity = fields.Selection([
        ('critical', 'Critical'),
        ('major', 'Major'),
        ('minor', 'Minor'),
    ], required=True, default='major', tracking=True)
    user_id = fields.Many2one('res.users', string='Owner', default=lambda self: self.env.user, tracking=True)
    due_date = fields.Date()
    resolution = fields.Text()
    state = fields.Selection([
        ('open', 'Open'),
        ('resolved', 'Resolved'),
        ('cancelled', 'Cancelled'),
    ], default='open', tracking=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('reference', _('New')) in (False, _('New')):
                vals['reference'] = self.env['ir.sequence'].next_by_code('ie.compliance.exception') or _('New')
        return super().create(vals_list)

    def action_resolve(self):
        for item in self:
            if not item.resolution:
                raise UserError(_("Describe the resolution before closing the exception."))
            item.state = 'resolved'
            order = item.import_order_id or item.export_order_id
            if order:
                self.env['ie.audit.log'].ie_log(order, 'exception', detail=_('Exception %s resolved.', item.reference), reason=item.resolution)
        return True
