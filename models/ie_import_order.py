# -*- coding: utf-8 -*-
import base64
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from .ie_const import IMPORT_STATES, IMPORT_UNLOCKED, PAYMENT_METHODS

_logger = logging.getLogger(__name__)


class IeImportOrder(models.Model):
    _name = 'ie.import.order'
    _description = 'Import Order'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'ie.workflow.mixin']
    _order = 'date desc, id desc'
    _check_company_auto = True

    name = fields.Char(string='Import Order No.', required=True, copy=False, readonly=True, default=lambda self: _('New'), index=True)
    date = fields.Date(string='Import Date', required=True, default=fields.Date.context_today, tracking=True)
    business_unit_id = fields.Many2one('ie.business.unit', string='Business Unit', required=True, tracking=True, check_company=True)
    department_id = fields.Many2one('ie.department', string='Requesting Department', required=True, tracking=True, check_company=True)
    requesting_unit_id = fields.Many2one(
        'ie.business.unit', string='Requesting Unit', required=True, tracking=True, check_company=True,
        default=lambda self: self.env.ref('import_export_control.bu_trading', raise_if_not_found=False),
        help='Engineering, Feed, Projects, Food Processing, or Trading.',
    )
    category_id = fields.Many2one('ie.import.category', string='Import Category', required=True, tracking=True)
    partner_id = fields.Many2one(
        'res.partner', string='Supplier', required=True, tracking=True, check_company=True,
        domain="[('ie_supplier_approved', '=', True)]",
    )
    country_id = fields.Many2one('res.country', string='Country of Origin', required=True, tracking=True)
    currency_id = fields.Many2one('res.currency', required=True, default=lambda self: self.env.company.currency_id, tracking=True)
    amount_total = fields.Monetary(string='Total Value', compute='_compute_amount_total', store=True, tracking=True)
    incoterm_id = fields.Many2one('account.incoterms', string='Incoterm', required=True, tracking=True)
    expected_shipment = fields.Date(string='Expected Shipment Date', required=True, tracking=True)
    expected_arrival = fields.Date(string='Expected Arrival Date', required=True, tracking=True)
    payment_method = fields.Selection(PAYMENT_METHODS, string='Payment Method', required=True, tracking=True)
    bank_id = fields.Many2one('res.bank', string='Bank', required=True, tracking=True)
    user_id = fields.Many2one('res.users', string='Responsible Officer', required=True, default=lambda self: self.env.user, tracking=True, check_company=True)
    state = fields.Selection(IMPORT_STATES, default='draft', required=True, tracking=True, copy=False, index=True)
    hold_state = fields.Char(copy=False)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    active = fields.Boolean(default=True)
    proforma_number = fields.Char(string='Proforma Invoice No.', tracking=True, index=True)
    version = fields.Integer(default=1, readonly=True, copy=False)
    duplicate_flag = fields.Boolean(copy=False, readonly=True)
    note = fields.Text()
    line_ids = fields.One2many('ie.import.order.line', 'order_id', copy=True)
    document_line_ids = fields.One2many('ie.document.line', 'import_order_id', copy=False)
    doc_completeness = fields.Float(string='Documentation Completeness %', compute='_compute_doc_completeness', store=True)
    approval_request_ids = fields.One2many('approval.request', 'ie_import_order_id')
    gate_ids = fields.One2many('ie.compliance.gate', 'import_order_id')
    exception_ids = fields.One2many('ie.compliance.exception', 'import_order_id')
    lc_ids = fields.One2many('ie.lc', 'import_order_id')
    shipment_ids = fields.One2many('ie.shipment', 'import_order_id')
    customs_ids = fields.One2many('ie.customs.clearance', 'import_order_id')
    revision_ids = fields.One2many('ie.order.revision', 'import_order_id')
    purchase_ids = fields.One2many('purchase.order', 'ie_import_order_id')
    picking_ids = fields.One2many('stock.picking', 'ie_import_order_id')
    invoice_ids = fields.One2many('account.move', 'ie_import_order_id')
    payment_ids = fields.One2many('account.payment', 'ie_import_order_id')
    landed_cost_ids = fields.One2many('stock.landed.cost', 'ie_import_order_id')
    purchase_count = fields.Integer(compute='_compute_counts')
    picking_count = fields.Integer(compute='_compute_counts')
    invoice_count = fields.Integer(compute='_compute_counts')
    payment_count = fields.Integer(compute='_compute_counts')
    landed_count = fields.Integer(compute='_compute_counts')
    gate_count = fields.Integer(compute='_compute_counts')
    estimated_freight = fields.Monetary()
    estimated_insurance = fields.Monetary()
    estimated_duty = fields.Monetary()
    estimated_other = fields.Monetary()
    estimated_cost = fields.Monetary(compute='_compute_cost_variance', store=True)
    actual_landed_cost = fields.Monetary(compute='_compute_cost_variance', store=True)
    cost_variance = fields.Monetary(string='Import Cost Variance', compute='_compute_cost_variance', store=True)
    qty_ordered = fields.Float(compute='_compute_qty_reconcile')
    qty_received = fields.Float(compute='_compute_qty_reconcile')
    shortage_qty = fields.Float(compute='_compute_qty_reconcile')
    shortage_ack = fields.Boolean(string='Shortage Acknowledged', copy=False)
    finance_signed_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    finance_signed_on = fields.Datetime(readonly=True, copy=False)
    compliance_signed_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    compliance_signed_on = fields.Datetime(readonly=True, copy=False)
    bank_reconciled = fields.Boolean(compute='_compute_bank_reconciled')
    quote_ids = fields.One2many('ie.supplier.quote', 'order_id', string='Supplier Quotations')
    forex_state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('approved', 'Approved'),
        ('refused', 'Refused'),
    ], string='Foreign Currency', default='draft', copy=False, tracking=True)
    forex_amount = fields.Monetary(string='Foreign Currency Amount', currency_field='currency_id', copy=False)
    forex_approved_by_id = fields.Many2one('res.users', string='Foreign Currency Approver', readonly=True, copy=False)
    forex_approved_on = fields.Datetime(readonly=True, copy=False)
    forex_refuse_reason = fields.Char(readonly=True, copy=False)
    technical_result = fields.Selection([
        ('pass', 'Passed'),
        ('fail', 'Failed'),
    ], string='Technical Check', copy=False, tracking=True)
    technical_reviewer_id = fields.Many2one('res.users', string='Technical Reviewer', readonly=True, copy=False)
    technical_note = fields.Text(string='Technical Comments', copy=False)
    technical_on = fields.Datetime(readonly=True, copy=False)
    is_machinery = fields.Boolean(compute='_compute_is_machinery')

    _sql_constraints = [
        ('name_company_uniq', 'unique(name, company_id)', 'The import order number must be unique per company.'),
    ]

    @api.depends('name')
    def _compute_display_name(self):
        for order in self:
            order.display_name = order.name or _('New')

    @api.depends('category_id.code')
    def _compute_is_machinery(self):
        for order in self:
            order.is_machinery = order.category_id.code == 'MCH'

    @api.depends('line_ids.price_subtotal')
    def _compute_amount_total(self):
        for order in self:
            order.amount_total = sum(order.line_ids.mapped('price_subtotal'))

    @api.depends('estimated_freight', 'estimated_insurance', 'estimated_duty', 'estimated_other', 'landed_cost_ids.amount_total', 'landed_cost_ids.state')
    def _compute_cost_variance(self):
        for order in self:
            order.estimated_cost = order.estimated_freight + order.estimated_insurance + order.estimated_duty + order.estimated_other
            posted = order.landed_cost_ids.filtered(lambda cost: cost.state == 'done')
            order.actual_landed_cost = sum(posted.mapped('amount_total'))
            order.cost_variance = order.actual_landed_cost - order.estimated_cost

    def _compute_counts(self):
        for order in self:
            order.purchase_count = len(order.purchase_ids)
            order.picking_count = len(order.picking_ids)
            order.invoice_count = len(order.invoice_ids)
            order.payment_count = len(order.payment_ids)
            order.landed_count = len(order.landed_cost_ids)
            order.gate_count = len(order.gate_ids)

    def _compute_qty_reconcile(self):
        for order in self:
            ordered = sum(order.line_ids.mapped('quantity'))
            received = 0.0
            for picking in order.picking_ids.filtered(lambda pick: pick.state == 'done' and pick.picking_type_id.code == 'incoming'):
                received += sum(picking.move_ids.mapped('quantity'))
            order.qty_ordered = ordered
            order.qty_received = received
            order.shortage_qty = max(ordered - received, 0.0)

    def _compute_bank_reconciled(self):
        """ERP vs bank: every linked statement line on the order payments is reconciled."""
        for order in self:
            lines = order.payment_ids.mapped('ie_statement_line_id')
            order.bank_reconciled = bool(lines) and all(lines.mapped('is_reconciled'))

    @api.constrains('expected_shipment', 'expected_arrival')
    def _check_dates(self):
        for order in self:
            if order.expected_arrival and order.expected_shipment and order.expected_arrival < order.expected_shipment:
                raise ValidationError(_("The expected arrival date cannot be earlier than the expected shipment date."))

    @api.constrains('partner_id')
    def _check_supplier(self):
        for order in self:
            if order.partner_id and not order.partner_id.ie_supplier_approved:
                raise ValidationError(_("Supplier %s is not an approved supplier.", order.partner_id.display_name))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('ie.import.order') or _('New')
        records = super().create(vals_list)
        for record in records:
            if not record.document_line_ids:
                self.env['ie.document.line'].create([
                    {
                        'document_type_id': doc_type.id,
                        'sequence': doc_type.sequence,
                        'blocking_state': doc_type.blocking_state,
                        'required': doc_type.required,
                        'import_order_id': record.id,
                    }
                    for doc_type in record._ie_document_types()
                ])
            record._ie_log('create', detail=record.name)
        return records

    def _ie_document_types(self):
        self.ensure_one()
        return self.env['ie.document.type'].search([
            ('active', '=', True),
            ('order_type', 'in', ('import', 'both')),
            '|', ('company_id', '=', False), ('company_id', '=', self.company_id.id),
        ])

    def _ie_check_duplicate(self):
        self.ensure_one()
        from datetime import timedelta
        company = self.company_id
        window = self.date - timedelta(days=company.ie_duplicate_days or 0)
        domain = [
            ('id', '!=', self.id),
            ('company_id', '=', company.id),
            ('state', '!=', 'cancelled'),
            ('partner_id', '=', self.partner_id.id),
            ('date', '>=', window),
            ('date', '<=', self.date),
        ]
        candidates = self.search(domain)
        product_key = tuple(sorted((line.product_id.id, line.quantity, line.price_subtotal) for line in self.line_ids))
        duplicate = self.browse()
        for other in candidates:
            other_key = tuple(sorted((line.product_id.id, line.quantity, line.price_subtotal) for line in other.line_ids))
            if other_key == product_key and product_key:
                duplicate |= other
        if self.proforma_number:
            duplicate |= self.search([
                ('id', '!=', self.id),
                ('company_id', '=', company.id),
                ('state', '!=', 'cancelled'),
                ('proforma_number', '=', self.proforma_number),
            ])
        if not duplicate:
            self.duplicate_flag = False
            return
        self.with_context(ie_skip_audit=True).write({'duplicate_flag': True})
        message = _("Possible duplicate of %s.", ', '.join(duplicate.mapped('name')))
        if company.ie_duplicate_mode == 'warn':
            self.message_post(body=message)
            self._ie_log('exception', detail=message)
            return
        raise UserError(message)

    def action_submit(self):
        for order in self:
            if order.state != 'draft':
                raise UserError(_("Only a draft import order can be submitted."))
            if not order.line_ids:
                raise UserError(_("Add at least one product line."))
            order._ie_check_duplicate()
            order._ie_enter_state('department', reason=_('Submitted.'))
        return True

    def action_submit_forex(self):
        for order in self:
            if order.payment_method != 'lc':
                raise UserError(_("Foreign-currency approval applies only when the payment method is a letter of credit."))
            if order.forex_state not in ('draft', 'refused'):
                raise UserError(_("The foreign-currency request is already with Finance."))
            order.forex_amount = order.forex_amount or order.amount_total
            order.forex_state = 'submitted'
            order._ie_log('approval', detail=_('Foreign-currency request submitted.'))
        return True

    def action_approve_forex(self):
        for order in self:
            if order.forex_state != 'submitted':
                raise UserError(_("Submit the foreign-currency request before it can be approved."))
            order._ie_check_group('import_export_control.group_ie_finance')
            order._ie_check_not_maker()
            order.write({
                'forex_state': 'approved',
                'forex_approved_by_id': self.env.user.id,
                'forex_approved_on': fields.Datetime.now(),
                'forex_refuse_reason': False,
            })
            order._ie_log('approval', detail=_('Foreign currency approved.'))
        return True

    def action_refuse_forex(self):
        for order in self:
            if order.forex_state != 'submitted':
                raise UserError(_("Only a submitted foreign-currency request can be refused."))
            order._ie_check_group('import_export_control.group_ie_finance')
            order._ie_check_not_maker()
            order.write({'forex_state': 'refused', 'forex_refuse_reason': _('Refused by finance.')})
            order._ie_log('rejection', detail=_('Foreign currency refused.'))
        return True

    def _ie_set_technical(self, result):
        for order in self:
            if order.category_id.code != 'MCH':
                raise UserError(_("A technical check is required only for machinery."))
            if not (self.env.su or self.env.user.has_group('import_export_control.group_ie_quality') or self.env.user.has_group('import_export_control.group_ie_procurement')):
                raise UserError(_("Only Procurement or Quality can record the technical check."))
            order._ie_check_not_maker()
            order.write({
                'technical_result': result,
                'technical_reviewer_id': self.env.user.id,
                'technical_on': fields.Datetime.now(),
            })
            order._ie_log('approval' if result == 'pass' else 'rejection', detail=_('Technical check %s.', result))
        return True

    def action_technical_pass(self):
        return self._ie_set_technical('pass')

    def action_technical_fail(self):
        return self._ie_set_technical('fail')

    def action_transfer_to_unit(self):
        self.ensure_one()
        if self.state not in ('received', 'closed'):
            raise UserError(_("Transfer the goods after they have been received."))
        if not (
            self.env.su
            or self.env.user.has_group('import_export_control.group_ie_warehouse')
            or self.env.user.has_group('import_export_control.group_ie_officer')
        ):
            raise UserError(_("Only a warehouse user or an officer can transfer goods to the requesting unit."))
        unit = self.requesting_unit_id
        if not unit.warehouse_id:
            raise UserError(_("Set a warehouse on requesting unit %s.", unit.display_name))
        source = self.line_ids[:1].warehouse_id or self.env['stock.warehouse'].search([
            ('company_id', '=', self.company_id.id),
        ], limit=1)
        if not source or source == unit.warehouse_id:
            raise UserError(_("The receipt warehouse and the requesting unit warehouse must be different."))
        picking = self.env['stock.picking'].sudo().create({
            'picking_type_id': source.int_type_id.id,
            'location_id': source.lot_stock_id.id,
            'location_dest_id': unit.warehouse_id.lot_stock_id.id,
            'origin': self.name,
            'ie_import_order_id': self.id,
            'ie_is_import': True,
            'company_id': self.company_id.id,
            'move_ids': [(0, 0, {
                'name': line.product_id.display_name,
                'product_id': line.product_id.id,
                'product_uom': line.uom_id.id,
                'product_uom_qty': line.quantity,
                'location_id': source.lot_stock_id.id,
                'location_dest_id': unit.warehouse_id.lot_stock_id.id,
                'company_id': self.company_id.id,
            }) for line in self.line_ids],
        })
        self._ie_log('write', detail=_('Internal transfer %s created for %s.', picking.name, unit.display_name))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Internal Transfer'),
            'res_model': 'stock.picking',
            'res_id': picking.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def _ie_before_enter(self, new_state):
        self.ensure_one()
        if new_state == 'finance' and not self.quote_ids.filtered('selected'):
            raise UserError(_("Select one supplier quotation before procurement approval."))
        if new_state == 'ie_review' and self.category_id.code == 'MCH':
            if self.technical_result != 'pass' or not self.technical_reviewer_id:
                raise UserError(_("Machinery requires a passed technical check before Import & Export review."))
            if self.technical_reviewer_id == self.create_uid:
                raise UserError(_("The technical reviewer cannot be the person who created the import order."))
        if new_state == 'bank_lc' and self.payment_method == 'lc':
            if self.forex_state != 'approved':
                raise UserError(_("Foreign-currency approval is required before bank processing."))
            usable = self.lc_ids.filtered(lambda lc: lc.state in ('issued', 'amended', 'utilised'))
            if not usable:
                raise UserError(_("An issued letter of credit is required before bank processing."))
        if new_state == 'shipment' and not self.shipment_ids:
            raise UserError(_("Create a shipment before moving to the shipment stage."))
        if new_state == 'received':
            done = self.picking_ids.filtered(lambda pick: pick.state == 'done' and pick.picking_type_id.code == 'incoming')
            if not done:
                raise UserError(_("Validate the goods receipt before marking the order as received."))
            cleared = self.customs_ids.filtered(lambda customs: customs.state == 'cleared')
            if not cleared:
                raise UserError(_("Customs clearance must be cleared before the goods are received."))
            failed = self.env['quality.check'].search_count([
                ('ie_import_order_id', '=', self.id),
                ('quality_state', '=', 'fail'),
            ])
            if failed:
                raise UserError(_("A failed quality check blocks receipt of this import order."))
        if new_state == 'closed':
            self._ie_assert_can_close()
        return True

    def _ie_open_compliance_gate(self):
        self.ensure_one()
        if self.gate_ids.filtered(lambda gate: gate.state != 'cancelled'):
            return
        gate = self.env['ie.compliance.gate'].create({
            'import_order_id': self.id,
            'order_type': 'import',
            'company_id': self.company_id.id,
        })
        gate._ie_seed_and_evaluate()

    def _ie_after_enter(self, new_state):
        super()._ie_after_enter(new_state)
        if new_state == 'approved':
            self._ie_ensure_letter_of_credit()

    def _ie_ensure_letter_of_credit(self):
        """Create the letter of credit once the import order is approved.

        Payment method Letter of Credit only. An existing letter of credit is
        left as it is. Issue it when foreign currency is already approved.
        """
        for order in self:
            if order.payment_method != 'lc' or order.lc_ids:
                continue
            if not order.bank_id or not order.partner_id or not order.company_id.partner_id:
                continue
            today = fields.Date.context_today(order)
            lc = self.env['ie.lc'].sudo().create({
                'name': 'LC/%s' % order.name,
                'lc_type': 'sight',
                'bank_id': order.bank_id.id,
                'applicant_id': order.company_id.partner_id.id,
                'beneficiary_id': order.partner_id.id,
                'amount': order.amount_total,
                'currency_id': order.currency_id.id,
                'expiry_date': order.expected_arrival or fields.Date.add(today, days=90),
                'latest_shipment': order.expected_shipment,
                'import_order_id': order.id,
                'company_id': order.company_id.id,
            })
            order._ie_log('write', detail=_('Letter of credit %s created.', lc.name))
            if order.forex_state == 'approved':
                lc.action_issue()

    @api.model
    def _ie_load_missing_letters_of_credit(self):
        orders = self.search([
            ('payment_method', '=', 'lc'),
            ('state', '=', 'approved'),
            ('lc_ids', '=', False),
        ])
        orders._ie_ensure_letter_of_credit()

    @api.model
    def _ie_load_demo_analytic(self):
        """One demo set: Trading, Procurement, and Main Warehouse."""
        Plan = self.env['account.analytic.plan'].sudo()
        Account = self.env['account.analytic.account'].sudo()
        leftover = Account.search([('code', '=', 'BUD-2026-014')])
        if leftover:
            leftover.unlink()
        specs = (
            ('analytic_business_unit_id', 'Business Unit', 'Trading', 'TRD'),
            ('analytic_department_id', 'Department', 'Procurement', 'PROC'),
            ('analytic_warehouse_id', 'Warehouse', 'Main Warehouse', 'WH-MAIN'),
        )
        values = {}
        for field_name, plan_name, account_name, code in specs:
            plan = Plan.search([('name', '=', plan_name), ('parent_id', '=', False)], order='id', limit=1)
            if not plan:
                continue
            account = Account.search([
                ('plan_id', '=', plan.id),
                '|', ('code', '=', code), ('name', '=', account_name),
            ], limit=1)
            if not account:
                account = Account.create({
                    'name': account_name,
                    'code': code,
                    'plan_id': plan.id,
                    'company_id': False,
                })
            values[field_name] = account.id
        order = self.env.ref('import_export_control.demo_import_order', raise_if_not_found=False)
        if not order:
            order = self.search([('proforma_number', '=', 'PI-DEMO')], limit=1)
        if order and len(values) == 3:
            order.write(values)

    def action_complete_compliance(self):
        for order in self:
            if order.state != 'compliance':
                raise UserError(_("The compliance gate is not the current step."))
            order._ie_check_group('import_export_control.group_ie_compliance')
            order._ie_check_not_maker()
            gate = order.gate_ids.filtered(lambda item: item.state != 'cancelled')[:1]
            if gate.state == 'done' and gate.result in ('exception', 'hold'):
                gate.write({'state': 'draft', 'result': False, 'fail_reason': False})
                gate._ie_seed_and_evaluate()
            if not gate or gate.state != 'draft':
                if gate.state == 'done' and gate.result in ('compliant', 'approved'):
                    order._ie_enter_state('management', reason=_('Compliance gate passed.'))
                    continue
                raise UserError(_("Open the compliance gate first."))
            gate.action_finalize()
            if gate.result != 'compliant':
                order.action_hold(gate.fail_reason or _('Compliance exception.'))
                continue
            order._ie_enter_state('management', reason=_('Compliance gate passed.'))
        return True

    def action_open_bank(self):
        for order in self:
            if order.state != 'approved':
                raise UserError(_("The import order must be approved first."))
            order._ie_check_group('import_export_control.group_ie_finance')
            order._ie_enter_state('bank_lc', reason=_('Bank / LC processing started.'))
        return True

    def action_open_shipment(self):
        for order in self:
            if order.state != 'bank_lc':
                raise UserError(_("Bank / LC processing must be completed first."))
            order._ie_check_group('import_export_control.group_ie_officer')
            order._ie_enter_state('shipment', reason=_('Shipment recorded.'))
        return True

    def action_mark_received(self):
        for order in self:
            if order.state != 'shipment':
                raise UserError(_("The order is not in shipment."))
            order._ie_check_group('import_export_control.group_ie_warehouse')
            order._ie_enter_state('received', reason=_('Goods received.'))
        return True

    def action_finance_sign(self):
        for order in self:
            if order.state != 'received':
                raise UserError(_("Finance signs the order after the goods are received."))
            order._ie_check_group('import_export_control.group_ie_finance')
            if order.create_uid == self.env.user and not self.env.su:
                raise UserError(_("The order creator cannot sign the reconciliation."))
            order.write({
                'finance_signed_by_id': self.env.user.id,
                'finance_signed_on': fields.Datetime.now(),
            })
            order._ie_log('approval', detail=_('Finance reconciliation sign-off.'))
        return True

    def action_compliance_sign(self):
        for order in self:
            if order.state != 'received':
                raise UserError(_("Control & Compliance signs the order after the goods are received."))
            order._ie_check_group('import_export_control.group_ie_compliance')
            order._ie_check_not_maker()
            if order.finance_signed_by_id == self.env.user and not self.env.su:
                raise UserError(_("Finance and Control & Compliance must be different people."))
            order.write({
                'compliance_signed_by_id': self.env.user.id,
                'compliance_signed_on': fields.Datetime.now(),
            })
            order._ie_log('approval', detail=_('Control & Compliance reconciliation sign-off.'))
        return True

    def action_close(self):
        for order in self:
            if order.state != 'received':
                raise UserError(_("Only a received import order can be closed."))
            order._ie_check_group('import_export_control.group_ie_finance')
            order._ie_enter_state('closed', reason=_('Closed.'))
        return True

    def _ie_assert_can_close(self):
        self.ensure_one()
        missing = self.document_line_ids.filtered(lambda line: line.required and not line.attachment_ids)
        if missing:
            raise UserError(_("Complete the required documents before closing: %s", ', '.join(missing.mapped('name'))))
        if not self.finance_signed_by_id or not self.compliance_signed_by_id:
            raise UserError(_("Finance and Control & Compliance must both sign off before closure."))
        open_exceptions = self.exception_ids.filtered(lambda item: item.state not in ('resolved', 'cancelled'))
        if open_exceptions:
            raise UserError(_("Resolve open compliance exceptions before closure."))
        if self.payment_method == 'lc' and not self.lc_ids.filtered(lambda lc: lc.state == 'closed'):
            raise UserError(_("Close the letter of credit before closing the import order."))
        tolerance = (self.company_id.ie_recon_tolerance or 0.0) / 100.0
        if self.qty_ordered:
            gap = abs(self.qty_ordered - self.qty_received) / self.qty_ordered
            if gap > tolerance and not self.shortage_ack:
                raise UserError(_("Received quantity does not match the order within the reconciliation tolerance. Acknowledge the shortage or correct the receipt."))
        billed = sum(self.invoice_ids.filtered(lambda move: move.state == 'posted' and move.move_type == 'in_invoice').mapped('amount_total'))
        paid = sum(self.payment_ids.filtered(lambda pay: pay.state in ('in_process', 'paid')).mapped('amount'))
        if self.amount_total and billed:
            if abs(billed - self.amount_total) / self.amount_total > tolerance:
                raise UserError(_("The vendor bill does not match the import order value within tolerance."))
        if billed and paid and abs(paid - billed) / billed > tolerance:
            raise UserError(_("The payment does not match the vendor bill within tolerance."))

    def action_cancel(self, reason):
        if not reason:
            raise UserError(_("A cancellation reason is required."))
        for order in self:
            if order.state in ('closed', 'cancelled'):
                raise UserError(_("This order cannot be cancelled."))
            order._ie_check_group('import_export_control.group_ie_management')
            order._ie_cancel_open_approvals()
            for picking in order.picking_ids.filtered(lambda pick: pick.state not in ('done', 'cancel')):
                picking.action_cancel()
            for purchase in order.purchase_ids.filtered(lambda po: po.state not in ('cancel', 'done')):
                if purchase.state in ('draft', 'sent', 'to approve'):
                    purchase.button_cancel()
            order._ie_set_state('cancelled', reason=reason)
        return True

    def action_amend(self, reason):
        if not reason:
            raise UserError(_("An amendment reason is required."))
        for order in self:
            if order.state in ('draft', 'cancelled', 'closed'):
                raise UserError(_("This order cannot be amended."))
            order._ie_check_group('import_export_control.group_ie_officer')
            snapshot = {
                'header': {
                    'partner_id': order.partner_id.id,
                    'amount_total': order.amount_total,
                    'payment_method': order.payment_method,
                    'expected_arrival': str(order.expected_arrival),
                },
                'lines': [{
                    'product_id': line.product_id.id,
                    'quantity': line.quantity,
                    'price_unit': line.price_unit,
                } for line in order.line_ids],
            }
            self.env['ie.order.revision'].create({
                'import_order_id': order.id,
                'version': order.version,
                'reason': reason,
                'snapshot': snapshot,
                'user_id': self.env.user.id,
            })
            order._ie_cancel_open_approvals()
            order.with_context(ie_state_bypass=True, ie_skip_audit=True).write({'version': order.version + 1})
            order._ie_set_state('draft', reason=reason)
            order._ie_log('write', detail=_('Amendment version %s.', order.version), reason=reason, changes=snapshot)
        return True

    def action_ack_shortage(self):
        self._ie_check_group('import_export_control.group_ie_warehouse')
        self.write({'shortage_ack': True})
        self._ie_log('exception', detail=_('Quantity shortage acknowledged.'))
        return True

    def action_create_purchase(self):
        self.ensure_one()
        if self.state not in IMPORT_UNLOCKED:
            raise UserError(_("Create the purchase order only after the import order is approved."))
        if self.purchase_ids.filtered(lambda po: po.state != 'cancel'):
            return self.action_view_purchase()
        distribution = self._ie_analytic_distribution()
        order = self.env['purchase.order'].create({
            'partner_id': self.partner_id.id,
            'currency_id': self.currency_id.id,
            'company_id': self.company_id.id,
            'origin': self.name,
            'ie_import_order_id': self.id,
            'ie_is_import': True,
            'date_order': fields.Datetime.now(),
            'order_line': [(0, 0, {
                'product_id': line.product_id.id,
                'name': line.product_id.display_name,
                'product_qty': line.quantity,
                'product_uom': line.uom_id.id,
                'price_unit': line.price_unit,
                'date_planned': self.expected_arrival,
                'analytic_distribution': distribution,
            }) for line in self.line_ids],
        })
        if distribution:
            order.order_line.filtered(lambda line: not line.display_type).write({
                'analytic_distribution': distribution,
            })
        self._ie_log('write', detail=_('Purchase order %s created.', order.name))
        return self.action_view_purchase()

    def action_create_landed_cost(self):
        self.ensure_one()
        pickings = self.picking_ids.filtered(lambda pick: pick.state == 'done')
        if not pickings:
            raise UserError(_("Landed costs need a validated receipt."))
        cost = self.env['stock.landed.cost'].create({
            'ie_import_order_id': self.id,
            'picking_ids': [(6, 0, pickings.ids)],
            'company_id': self.company_id.id,
        })
        self._ie_log('write', detail=_('Landed cost %s created.', cost.name))
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'stock.landed.cost',
            'res_id': cost.id,
            'view_mode': 'form',
        }

    @api.model
    def _ie_load_demo_orders(self):
        """One import order per main-path stage, with the documents that stage allows."""
        company = self.env.ref('base.main_company', raise_if_not_found=False) or self.env.company
        orders = self.with_company(company).sudo()
        supplier = self.env.ref('import_export_control.demo_supplier', raise_if_not_found=False)
        product = self.env.ref('import_export_control.demo_product_import', raise_if_not_found=False)
        if not supplier or not product:
            return
        banks = [
            self.env.ref('import_export_control.demo_bank_cbe', raise_if_not_found=False),
            self.env.ref('import_export_control.demo_bank_abyssinia', raise_if_not_found=False),
            self.env.ref('import_export_control.demo_bank_hibret', raise_if_not_found=False),
            self.env.ref('import_export_control.demo_bank_zemen', raise_if_not_found=False),
        ]
        banks = [bank for bank in banks if bank]
        if not banks:
            banks = [self.env.ref('import_export_control.demo_bank')]
        stages = [
            'draft', 'department', 'procurement', 'finance', 'ie_review', 'compliance',
            'management', 'approved', 'bank_lc', 'shipment', 'received', 'closed',
        ]
        late = {'bank_lc', 'shipment', 'received', 'closed'}
        for index, stage in enumerate(stages):
            existing = self.search([('proforma_number', '=', 'PI-STAGE-%s' % stage.upper())], limit=1)
            if existing:
                if stage in ('received', 'closed') and not existing.picking_ids.filtered(lambda pick: pick.state == 'done'):
                    existing._ie_demo_receipt_chain()
                continue
            orders._ie_demo_one(stage, index, stages, banks[index % len(banks)], supplier, product, company, stage in late)

    def _ie_demo_one(self, stage, index, stages, bank, supplier, product, company, uses_lc):
        today = fields.Date.context_today(self)
        order = self.with_company(company).sudo().create({
            'company_id': company.id,
            'partner_id': supplier.id,
            'business_unit_id': self.env.ref('import_export_control.bu_trading').id,
            'department_id': self.env.ref('import_export_control.dept_procurement').id,
            'requesting_unit_id': self.env.ref('import_export_control.bu_trading').id,
            'category_id': self.env.ref('import_export_control.cat_raw').id,
            'country_id': self.env.ref('base.et').id,
            'currency_id': self.env.ref('base.USD').id,
            'incoterm_id': self.env.ref('account.incoterm_FOB').id,
            'expected_shipment': fields.Date.add(today, days=10 + index),
            'expected_arrival': fields.Date.add(today, days=25 + index),
            'payment_method': 'lc' if uses_lc else 'cad',
            'bank_id': bank.id,
            'user_id': self.env.user.id,
            'proforma_number': 'PI-STAGE-%s' % stage.upper(),
            'note': _('Demo import order standing at %s.', stage),
            'estimated_freight': 400 + index,
            'estimated_insurance': 80 + index,
            'estimated_duty': 150 + index,
            'estimated_other': 40 + index,
            'line_ids': [(0, 0, {
                'product_id': product.id,
                'hs_code_id': product.ie_hs_code_id.id,
                'quantity': 100 + index,
                'uom_id': product.uom_id.id,
                'price_unit': 12,
                'country_id': self.env.ref('base.et').id,
                'specification': _('Grade A raw material'),
                'required_date': fields.Date.add(today, days=25 + index),
                'warehouse_id': self.env['stock.warehouse'].search([('company_id', '=', company.id)], limit=1).id,
                'quality_requirement': _('Moisture below 12 percent.'),
            })],
        })
        self.env['ie.supplier.quote'].sudo().create({
            'order_id': order.id,
            'partner_id': supplier.id,
            'currency_id': order.currency_id.id,
            'price': order.amount_total or 1200,
            'incoterm_id': order.incoterm_id.id,
            'selected': True,
            'note': _('Selected demo quotation.'),
        })
        payload = base64.b64encode(b'Demo import document')
        for line in order.document_line_ids:
            attachment = self.env['ir.attachment'].sudo().create({
                'name': '%s.txt' % (line.name or 'document'),
                'datas': payload,
                'res_model': 'ie.document.line',
                'res_id': line.id,
            })
            line.attachment_ids = [(4, attachment.id)]
        for next_state in stages[1:stages.index(stage) + 1]:
            order._ie_set_state(next_state, reason=_('Demo data.'))
        if stages.index(stage) >= stages.index('compliance'):
            order._ie_open_compliance_gate()
            if stage != 'compliance':
                order.gate_ids.sudo().write({
                    'state': 'done',
                    'result': 'compliant',
                    'user_id': self.env.user.id,
                    'check_date': fields.Datetime.now(),
                })
        if uses_lc:
            order.sudo().write({
                'forex_state': 'approved',
                'forex_amount': order.amount_total,
                'forex_approved_by_id': self.env.user.id,
                'forex_approved_on': fields.Datetime.now(),
            })
            self._ie_demo_try(lambda: self.env['ie.lc'].sudo().create({
                'name': 'LC-%s' % order.proforma_number,
                'bank_id': bank.id,
                'applicant_id': company.partner_id.id,
                'beneficiary_id': supplier.id,
                'amount': order.amount_total,
                'currency_id': order.currency_id.id,
                'issue_date': today,
                'expiry_date': fields.Date.add(today, days=90),
                'latest_shipment': order.expected_shipment,
                'import_order_id': order.id,
                'company_id': company.id,
                'state': 'issued',
            }))
        if stages.index(stage) >= stages.index('approved'):
            self._ie_demo_try(order.action_create_purchase)
        if stages.index(stage) >= stages.index('shipment'):
            self.env['ie.shipment'].sudo().create({
                'transport_mode': 'sea',
                'carrier': 'MSC',
                'bl_number': 'BL-%s' % order.proforma_number,
                'container_numbers': 'MSCU%s' % (100000 + index),
                'container_available': True,
                'container_booked': True,
                'booking_reference': 'BK-%s' % order.proforma_number,
                'shipping_line': 'MSC',
                'container_type': 'standard',
                'factory_arrival': order.expected_arrival,
                'port_loading': 'Shanghai',
                'port_destination': 'Djibouti',
                'etd': order.expected_shipment,
                'eta': order.expected_arrival,
                'state': 'in_transit',
                'quantity': order.qty_ordered,
                'import_order_id': order.id,
                'company_id': company.id,
            })
        if stage in ('received', 'closed'):
            self._ie_demo_try(lambda: order._ie_demo_receipt_chain())
            self.env['ie.customs.clearance'].sudo().create({
                'name': 'DEC-%s' % order.proforma_number,
                'direction': 'import',
                'hs_code_id': product.ie_hs_code_id.id,
                'duty_amount': order.estimated_duty,
                'tax_amount': 60,
                'currency_id': order.currency_id.id,
                'state': 'cleared',
                'clearance_date': today,
                'import_order_id': order.id,
                'company_id': company.id,
                'exemption_applicable': True,
                'exemption_type': 'duty',
                'exemption_reference': 'EX-%s' % order.proforma_number,
                'exemption_status': 'granted',
            })
        if stage == 'closed':
            order.sudo().write({
                'finance_signed_by_id': self.env.user.id,
                'finance_signed_on': fields.Datetime.now(),
                'compliance_signed_by_id': self.env.user.id,
                'compliance_signed_on': fields.Datetime.now(),
                'shortage_ack': True,
            })
            order.lc_ids.filtered(lambda lc: lc.state != 'closed').sudo().write({
                'state': 'closed',
                'closure_date': today,
            })
        order._ie_log('write', detail=_('Demo record prepared at %s.', stage))

    def _ie_demo_receipt_chain(self):
        """Confirm the purchase, receive the goods, then bill, pay and land the cost."""
        self.ensure_one()

        def _receive():
            purchase = self.purchase_ids.filtered(lambda po: po.state != 'cancel')[:1]
            if purchase and purchase.state in ('draft', 'sent', 'to approve'):
                purchase.with_context(ie_skip_enforce=True).button_confirm()
            for picking in self.picking_ids.filtered(lambda pick: pick.state not in ('done', 'cancel')):
                picking.sudo().write({'ie_import_order_id': self.id, 'ie_is_import': True})
                for move in picking.move_ids:
                    move.quantity = move.product_uom_qty
                    move.picked = True
                picking.with_context(ie_skip_enforce=True, skip_backorder=True).button_validate()

        def _bill():
            if self.invoice_ids:
                return
            journal = self.env['account.journal'].search([
                ('company_id', '=', self.company_id.id), ('type', '=', 'purchase'),
            ], limit=1)
            if not journal:
                raise UserError(_("No purchase journal is configured for this company."))
            bill = self.env['account.move'].sudo().create({
                'move_type': 'in_invoice',
                'partner_id': self.partner_id.id,
                'journal_id': journal.id,
                'invoice_date': fields.Date.context_today(self),
                'currency_id': self.currency_id.id,
                'company_id': self.company_id.id,
                'ie_import_order_id': self.id,
                'ie_is_import': True,
                'invoice_line_ids': [(0, 0, {
                    'product_id': line.product_id.id,
                    'name': line.product_id.display_name,
                    'quantity': line.quantity,
                    'price_unit': line.price_unit,
                    'tax_ids': [(6, 0, [])],
                    'analytic_distribution': self._ie_analytic_distribution(),
                }) for line in self.line_ids],
            })
            bill.with_context(ie_skip_enforce=True).action_post()

        def _pay():
            self._ie_demo_supplier_payment()

        def _land():
            if self.landed_cost_ids:
                return
            pickings = self.picking_ids.filtered(lambda pick: pick.state == 'done')
            journal = self.env['account.journal'].search([
                ('company_id', '=', self.company_id.id), ('type', 'in', ('general', 'purchase')),
            ], limit=1)
            if not pickings or not journal:
                raise UserError(_("Landed costs need a validated receipt and a journal."))
            self.env['stock.landed.cost'].sudo().create({
                'ie_import_order_id': self.id,
                'picking_ids': [(6, 0, pickings.ids)],
                'company_id': self.company_id.id,
                'account_journal_id': journal.id,
            })

        self._ie_demo_try(_receive)
        self._ie_demo_try(_bill)
        self._ie_demo_try(_pay)
        self._ie_demo_try(_land)

    def _ie_demo_supplier_payment(self):
        """Post the supplier payment for a received import. Existing payments are left as they are."""
        self.ensure_one()
        posted = self.payment_ids.filtered(lambda payment: payment.state in ('paid', 'in_process'))
        if posted or not self.amount_total:
            return
        journal = self.env['ie.compliance.kpi']._ie_demo_bank_journal(self.company_id)
        payment = self.env['account.payment'].sudo().create({
            'payment_type': 'outbound',
            'partner_type': 'supplier',
            'partner_id': self.partner_id.id,
            'amount': self.amount_total,
            'currency_id': self.currency_id.id,
            'journal_id': journal.id,
            'company_id': self.company_id.id,
            'ie_import_order_id': self.id,
            'ie_is_import': True,
            'memo': self.name,
        })
        payment.with_context(ie_skip_enforce=True).action_post()
        return payment

    def _ie_demo_try(self, callback):
        try:
            with self.env.cr.savepoint():
                callback()
        except Exception:
            _logger.warning('Import demo step skipped for a missing accounting or stock setup.', exc_info=True)

    @api.model
    def _ie_load_demo_cost_orders(self):
        """Two received orders on average cost, plus one LC order waiting on foreign currency."""
        company = self.env.ref('base.main_company', raise_if_not_found=False)
        supplier = self.env.ref('import_export_control.demo_supplier', raise_if_not_found=False)
        hs_code = self.env.ref('import_export_control.hs_generic', raise_if_not_found=False)
        if not company or not supplier or not hs_code:
            return
        bank = self.env['res.bank'].search([('name', '=', 'Ahadu Bank')], limit=1)
        if not bank:
            return
        wheat = self._ie_demo_average_product(company, hs_code, 'Demo Average Wheat', 'AVG-WHEAT')
        maize = self._ie_demo_average_product(company, hs_code, 'Demo Average Maize', 'AVG-MAIZE')
        received = (
            ('PI-AVG-WHEAT', wheat, 80, 15, 480),
            ('PI-AVG-MAIZE', maize, 60, 18, 360),
        )
        for proforma, product, quantity, price, freight in received:
            order = self.search([
                ('proforma_number', '=', proforma),
                ('company_id', '=', company.id),
            ], limit=1)
            if not order:
                order = self.with_company(company)._ie_demo_received_lc(
                    proforma, product, quantity, price, bank, supplier, company,
                )
            self._ie_demo_try(lambda order=order: order._ie_demo_receipt_chain())
            self._ie_demo_try(lambda order=order, freight=freight: order._ie_demo_validate_landed(freight))
        if not self.search_count([
            ('proforma_number', '=', 'PI-FOREX-SUBMITTED'),
            ('company_id', '=', company.id),
        ]):
            raw = self.env.ref('import_export_control.demo_product_import', raise_if_not_found=False)
            if raw:
                self.with_company(company)._ie_demo_forex_submitted(raw, bank, supplier, company)

    def _ie_demo_average_product(self, company, hs_code, name, code):
        category = self.env['product.category'].sudo().search([('name', '=', 'Demo Average Cost')], limit=1)
        if not category:
            category = self.env['product.category'].sudo().create({'name': 'Demo Average Cost'})
        category.with_company(company).sudo().write({
            'property_cost_method': 'average',
            'property_valuation': 'manual_periodic',
        })
        product = self.env['product.product'].sudo().search([('default_code', '=', code)], limit=1)
        if not product:
            product = self.env['product.product'].sudo().create({
                'name': name,
                'default_code': code,
                'type': 'consu',
                'is_storable': True,
                'categ_id': category.id,
                'ie_hs_code_id': hs_code.id,
            })
        else:
            product.sudo().write({
                'categ_id': category.id,
                'is_storable': True,
                'ie_hs_code_id': hs_code.id,
            })
        product.with_company(company).sudo().standard_price = 12.0
        return product

    def _ie_demo_received_lc(self, proforma, product, quantity, price, bank, supplier, company):
        """A received letter-of-credit order whose goods can take a landed cost."""
        order = self._ie_demo_header(proforma, product, quantity, price, bank, supplier, company, _(
            'Demo import on average cost so the landed cost can be validated.',
        ))
        stages = [
            'draft', 'department', 'procurement', 'finance', 'ie_review', 'compliance',
            'management', 'approved', 'bank_lc', 'shipment', 'received',
        ]
        self._ie_demo_walk(order, stages, 'received')
        today = fields.Date.context_today(self)
        order.sudo().write({
            'forex_state': 'approved',
            'forex_amount': order.amount_total,
            'forex_approved_by_id': self.env.user.id,
            'forex_approved_on': fields.Datetime.now(),
        })
        self._ie_demo_try(lambda: self.env['ie.lc'].sudo().create({
            'name': 'LC-%s' % proforma,
            'bank_id': bank.id,
            'applicant_id': company.partner_id.id,
            'beneficiary_id': supplier.id,
            'amount': order.amount_total,
            'currency_id': order.currency_id.id,
            'issue_date': today,
            'expiry_date': fields.Date.add(today, days=90),
            'latest_shipment': order.expected_shipment,
            'import_order_id': order.id,
            'company_id': company.id,
            'state': 'issued',
        }))
        self._ie_demo_try(order.action_create_purchase)
        self.env['ie.shipment'].sudo().create({
            'transport_mode': 'sea',
            'carrier': 'MSC',
            'bl_number': 'BL-%s' % proforma,
            'container_numbers': 'MSCU%s' % proforma[-4:],
            'container_available': True,
            'container_booked': True,
            'booking_reference': 'BK-%s' % proforma,
            'shipping_line': 'MSC',
            'container_type': 'standard',
            'factory_arrival': order.expected_arrival,
            'port_loading': 'Shanghai',
            'port_destination': 'Djibouti',
            'etd': order.expected_shipment,
            'eta': order.expected_arrival,
            'state': 'in_transit',
            'quantity': order.qty_ordered,
            'import_order_id': order.id,
            'company_id': company.id,
        })
        self.env['ie.customs.clearance'].sudo().create({
            'name': 'DEC-%s' % proforma,
            'direction': 'import',
            'hs_code_id': product.ie_hs_code_id.id,
            'duty_amount': order.estimated_duty,
            'tax_amount': 60,
            'currency_id': order.currency_id.id,
            'state': 'cleared',
            'clearance_date': today,
            'import_order_id': order.id,
            'company_id': company.id,
            'exemption_applicable': True,
            'exemption_type': 'duty',
            'exemption_reference': 'EX-%s' % proforma,
            'exemption_status': 'granted',
        })
        return order

    def _ie_demo_forex_submitted(self, product, bank, supplier, company):
        """Approved LC order whose foreign-currency request is waiting for Finance."""
        order = self._ie_demo_header(
            'PI-FOREX-SUBMITTED', product, 50, 20, bank, supplier, company,
            _('Foreign currency is submitted. Approve or refuse it from the header.'),
        )
        stages = [
            'draft', 'department', 'procurement', 'finance', 'ie_review',
            'compliance', 'management', 'approved',
        ]
        self._ie_demo_walk(order, stages, 'approved')
        order.sudo().write({
            'forex_state': 'submitted',
            'forex_amount': order.amount_total,
        })
        self._ie_demo_try(order.action_create_purchase)
        return order

    def _ie_demo_header(self, proforma, product, quantity, price, bank, supplier, company, note):
        today = fields.Date.context_today(self)
        warehouse = self.env['stock.warehouse'].search([('company_id', '=', company.id)], limit=1)
        order = self.with_company(company).sudo().create({
            'company_id': company.id,
            'partner_id': supplier.id,
            'business_unit_id': self.env.ref('import_export_control.bu_trading').id,
            'department_id': self.env.ref('import_export_control.dept_procurement').id,
            'requesting_unit_id': self.env.ref('import_export_control.bu_trading').id,
            'category_id': self.env.ref('import_export_control.cat_raw').id,
            'country_id': self.env.ref('base.et').id,
            'currency_id': self.env.ref('base.USD').id,
            'incoterm_id': self.env.ref('account.incoterm_FOB').id,
            'expected_shipment': fields.Date.add(today, days=12),
            'expected_arrival': fields.Date.add(today, days=28),
            'payment_method': 'lc',
            'bank_id': bank.id,
            'user_id': self.env.user.id,
            'proforma_number': proforma,
            'note': note,
            'estimated_freight': 400,
            'estimated_insurance': 80,
            'estimated_duty': 150,
            'estimated_other': 40,
            'line_ids': [(0, 0, {
                'product_id': product.id,
                'hs_code_id': product.ie_hs_code_id.id,
                'quantity': quantity,
                'uom_id': product.uom_id.id,
                'price_unit': price,
                'country_id': self.env.ref('base.et').id,
                'specification': _('Grade A raw material'),
                'required_date': fields.Date.add(today, days=28),
                'warehouse_id': warehouse.id,
                'quality_requirement': _('Moisture below 12 percent.'),
            })],
        })
        self.env['ie.supplier.quote'].sudo().create({
            'order_id': order.id,
            'partner_id': supplier.id,
            'currency_id': order.currency_id.id,
            'price': order.amount_total,
            'incoterm_id': order.incoterm_id.id,
            'selected': True,
            'note': _('Selected demo quotation.'),
        })
        payload = base64.b64encode(b'Demo import document')
        for line in order.document_line_ids:
            attachment = self.env['ir.attachment'].sudo().create({
                'name': '%s.txt' % (line.name or 'document'),
                'datas': payload,
                'res_model': 'ie.document.line',
                'res_id': line.id,
            })
            line.attachment_ids = [(4, attachment.id)]
        return order

    def _ie_demo_walk(self, order, stages, target):
        for next_state in stages[1:stages.index(target) + 1]:
            order._ie_set_state(next_state, reason=_('Demo data.'))
        if stages.index(target) >= stages.index('compliance'):
            order._ie_open_compliance_gate()
            if target != 'compliance':
                order.gate_ids.sudo().write({
                    'state': 'done',
                    'result': 'compliant',
                    'user_id': self.env.user.id,
                    'check_date': fields.Datetime.now(),
                })

    def _ie_demo_validate_landed(self, freight_amount):
        """Post the draft landed cost. The product must use average or FIFO cost."""
        self.ensure_one()
        cost = self.landed_cost_ids.filtered(lambda item: item.state == 'draft')[:1]
        if not cost or cost.cost_lines:
            return
        account = self.env['account.account'].with_company(self.company_id).search([
            ('account_type', '=', 'expense'),
        ], limit=1)
        freight = self.env['product.product'].sudo().search([
            ('landed_cost_ok', '=', True),
            '|', ('company_id', '=', False), ('company_id', '=', self.company_id.id),
        ], limit=1)
        if not freight:
            freight = self.env['product.product'].sudo().create({
                'name': 'Demo Freight',
                'type': 'service',
                'landed_cost_ok': True,
            })
        if not account:
            raise UserError(_("No expense account is configured for this company."))
        self.env['stock.landed.cost.lines'].sudo().create({
            'cost_id': cost.id,
            'product_id': freight.id,
            'name': 'Demo freight',
            'split_method': 'equal',
            'price_unit': freight_amount,
            'account_id': account.id,
        })
        cost.with_company(self.company_id).button_validate()

    def _action_view(self, model, domain_field):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': model,
            'view_mode': 'list,form',
            'domain': [(domain_field, '=', self.id)],
            'context': {'default_%s' % domain_field: self.id, 'default_company_id': self.company_id.id},
        }

    def action_view_purchase(self):
        return self._action_view('purchase.order', 'ie_import_order_id')

    def action_view_pickings(self):
        return self._action_view('stock.picking', 'ie_import_order_id')

    def action_view_invoices(self):
        return self._action_view('account.move', 'ie_import_order_id')

    def action_view_payments(self):
        return self._action_view('account.payment', 'ie_import_order_id')

    def action_view_landed(self):
        return self._action_view('stock.landed.cost', 'ie_import_order_id')

    def action_view_gates(self):
        return self._action_view('ie.compliance.gate', 'import_order_id')

    def action_view_audit(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'ie.audit.log',
            'view_mode': 'list,form',
            'domain': [('model_name', '=', self._name), ('res_id', '=', self.id)],
        }


class IeImportOrderLine(models.Model):
    _name = 'ie.import.order.line'
    _description = 'Import Order Line'

    order_id = fields.Many2one('ie.import.order', required=True, ondelete='cascade', index=True)
    product_id = fields.Many2one('product.product', required=True, check_company=True)
    product_code = fields.Char(related='product_id.default_code', string='Product Code')
    categ_id = fields.Many2one(related='product_id.categ_id', string='Product Category')
    hs_code_id = fields.Many2one('ie.hs.code', string='HS Code', required=True)
    quantity = fields.Float(required=True, digits='Product Unit of Measure')
    uom_id = fields.Many2one('uom.uom', required=True)
    price_unit = fields.Float(required=True, digits='Product Price')
    currency_id = fields.Many2one(related='order_id.currency_id')
    price_subtotal = fields.Monetary(compute='_compute_subtotal', store=True)
    country_id = fields.Many2one('res.country', string='Country of Origin')
    specification = fields.Text()
    required_date = fields.Date(string='Required Date')
    warehouse_id = fields.Many2one('stock.warehouse', check_company=True)
    quality_requirement = fields.Text(string='Quality Requirement')
    company_id = fields.Many2one(related='order_id.company_id', store=True)

    _sql_constraints = [
        ('qty_positive', 'CHECK(quantity > 0)', 'Quantity must be greater than zero.'),
        ('price_positive', 'CHECK(price_unit >= 0)', 'Unit price cannot be negative.'),
    ]

    @api.depends('quantity', 'price_unit')
    def _compute_subtotal(self):
        for line in self:
            line.price_subtotal = line.quantity * line.price_unit

    @api.onchange('product_id')
    def _onchange_product_id(self):
        if self.product_id:
            self.uom_id = self.product_id.uom_id
            self.hs_code_id = self.product_id.ie_hs_code_id
            self.country_id = self.order_id.country_id

    @api.constrains('product_id', 'hs_code_id')
    def _check_hs(self):
        for line in self:
            if not line.product_id.ie_hs_code_id or line.product_id.ie_hs_code_id != line.hs_code_id:
                raise ValidationError(_("HS code on the line must be the HS code set on product %s.", line.product_id.display_name))

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        for line in lines:
            if line.order_id:
                self.env['ie.audit.log'].ie_log(line.order_id, 'write', detail=_('Line added for %s.', line.product_id.display_name))
        return lines


class IeSupplierQuote(models.Model):
    _name = 'ie.supplier.quote'
    _description = 'Supplier Quotation'
    _order = 'id'

    order_id = fields.Many2one('ie.import.order', required=True, ondelete='cascade', index=True)
    partner_id = fields.Many2one('res.partner', string='Supplier', required=True)
    currency_id = fields.Many2one('res.currency', required=True, default=lambda self: self.env.company.currency_id)
    price = fields.Monetary(required=True)
    incoterm_id = fields.Many2one('account.incoterms', string='Incoterm')
    selected = fields.Boolean(string='Selected')
    note = fields.Char()
    company_id = fields.Many2one(related='order_id.company_id', store=True)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records.filtered('selected')._ie_select_exclusive()
        return records

    def write(self, vals):
        result = super().write(vals)
        if vals.get('selected'):
            self.filtered('selected')._ie_select_exclusive()
        return result

    def _ie_select_exclusive(self):
        for quote in self:
            others = quote.order_id.quote_ids - quote
            others.filtered('selected').write({'selected': False})
