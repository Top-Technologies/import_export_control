# -*- coding: utf-8 -*-
import base64
import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.float_utils import float_compare

from .ie_const import EXPORT_STATES, EXPORT_UNLOCKED, PAYMENT_METHODS

_logger = logging.getLogger(__name__)


class IeExportOrder(models.Model):
    _name = 'ie.export.order'
    _description = 'Export Order'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'ie.workflow.mixin']
    _order = 'date desc, id desc'
    _check_company_auto = True

    name = fields.Char(string='Export Order No.', required=True, copy=False, readonly=True, default=lambda self: _('New'), index=True)
    analytic_export_id = fields.Many2one(
        'account.analytic.account', string='Export Reference', copy=False, readonly=True, check_company=True,
        help='Analytic account in the Export plan. Its reference is this export number.',
    )
    date = fields.Date(string='Export Date', required=True, default=fields.Date.context_today, tracking=True)
    business_unit_id = fields.Many2one('ie.business.unit', string='Business Unit', required=True, tracking=True, check_company=True)
    product_id = fields.Many2one('product.product', string='Export Product', required=True, tracking=True, check_company=True)
    partner_id = fields.Many2one(
        'res.partner', string='Buyer', required=True, tracking=True, check_company=True,
        domain="[('ie_buyer_approved', '=', True)]",
    )
    agent_id = fields.Many2one('res.partner', string='Agent', tracking=True, check_company=True)
    end_customer_id = fields.Many2one('res.partner', string='End Customer', tracking=True, help='Ultimate customer, for example in the Middle East. This party is not invoiced.')
    country_id = fields.Many2one('res.country', string='Destination Country', required=True, tracking=True)
    currency_id = fields.Many2one('res.currency', required=True, default=lambda self: self.env.company.currency_id, tracking=True)
    contract_no = fields.Char(string='Contract No.', required=True, tracking=True, index=True)
    contract_expiry = fields.Date(string='Contract Expiry', tracking=True)
    payment_method = fields.Selection(PAYMENT_METHODS, string='Payment Method', required=True, tracking=True)
    incoterm_id = fields.Many2one('account.incoterms', string='Incoterm', required=True, tracking=True)
    shipment_date = fields.Date(string='Shipment Date', required=True, tracking=True)
    port_loading = fields.Char(string='Port of Loading', required=True, tracking=True)
    port_destination = fields.Char(string='Destination Port', required=True, tracking=True)
    user_id = fields.Many2one('res.users', string='Responsible Officer', required=True, default=lambda self: self.env.user, tracking=True, check_company=True)
    warehouse_id = fields.Many2one('stock.warehouse', required=True, check_company=True, tracking=True)
    state = fields.Selection(EXPORT_STATES, default='draft', required=True, tracking=True, copy=False, index=True)
    hold_state = fields.Char(copy=False)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    active = fields.Boolean(default=True)
    version = fields.Integer(default=1, readonly=True, copy=False)
    duplicate_flag = fields.Boolean(copy=False, readonly=True)
    amount_total = fields.Monetary(string='Total Value', compute='_compute_amount_total', store=True, tracking=True)
    note = fields.Text()
    line_ids = fields.One2many('ie.export.order.line', 'order_id', copy=True)
    document_line_ids = fields.One2many('ie.document.line', 'export_order_id', copy=False)
    doc_completeness = fields.Float(string='Documentation Completeness %', compute='_compute_doc_completeness', store=True)
    approval_request_ids = fields.One2many('approval.request', 'ie_export_order_id')
    gate_ids = fields.One2many('ie.compliance.gate', 'export_order_id')
    exception_ids = fields.One2many('ie.compliance.exception', 'export_order_id')
    lc_ids = fields.One2many('ie.lc', 'export_order_id')
    shipment_ids = fields.One2many('ie.shipment', 'export_order_id')
    customs_ids = fields.One2many('ie.customs.clearance', 'export_order_id')
    proceeds_ids = fields.One2many('ie.export.proceeds', 'export_order_id')
    revision_ids = fields.One2many('ie.order.revision', 'export_order_id')
    sale_ids = fields.One2many('sale.order', 'ie_export_order_id')
    picking_ids = fields.One2many('stock.picking', 'ie_export_order_id')
    invoice_ids = fields.One2many('account.move', 'ie_export_order_id')
    payment_ids = fields.One2many('account.payment', 'ie_export_order_id')
    reservation_picking_id = fields.Many2one('stock.picking', copy=False, readonly=True)
    stock_override = fields.Boolean(copy=False, readonly=True)
    stock_override_reason = fields.Text(readonly=True, copy=False)
    stock_override_user_id = fields.Many2one('res.users', readonly=True, copy=False)
    qty_on_hand = fields.Float(compute='_compute_stock_panel', digits='Product Unit of Measure')
    qty_reserved = fields.Float(compute='_compute_stock_panel', digits='Product Unit of Measure')
    qty_order = fields.Float(compute='_compute_stock_panel', digits='Product Unit of Measure')
    qty_balance = fields.Float(compute='_compute_stock_panel', digits='Product Unit of Measure')
    qty_shipped = fields.Float(compute='_compute_qty_shipped')
    proceeds_expected = fields.Monetary(compute='_compute_proceeds')
    proceeds_received = fields.Monetary(compute='_compute_proceeds')
    proceeds_outstanding = fields.Monetary(compute='_compute_proceeds')
    sale_count = fields.Integer(compute='_compute_counts')
    picking_count = fields.Integer(compute='_compute_counts')
    invoice_count = fields.Integer(compute='_compute_counts')
    proceeds_count = fields.Integer(compute='_compute_counts')
    gate_count = fields.Integer(compute='_compute_counts')
    finance_signed_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    finance_signed_on = fields.Datetime(readonly=True, copy=False)
    compliance_signed_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    compliance_signed_on = fields.Datetime(readonly=True, copy=False)
    advance_amount = fields.Monetary(string='Agreed Advance', currency_field='currency_id')
    advance_payment_id = fields.Many2one(
        'account.payment', string='USD Advance', copy=False,
        domain="[('payment_type', '=', 'inbound'), ('partner_id', '=', partner_id)]",
    )
    export_line_type = fields.Selection([
        ('live_animal', 'Live Animal'),
        ('teff', 'Teff'),
        ('other', 'Other'),
    ], string='Export Line', default='other', required=True, tracking=True)
    animal_count = fields.Integer(string='Animal Count')
    feed_cost = fields.Monetary(string='Feed Cost')
    veterinary_cost = fields.Monetary(string='Veterinary Cost')
    holding_cost = fields.Monetary(string='Holding Cost')
    raw_material_cost = fields.Monetary(string='Raw Material Cost')
    processing_cost = fields.Monetary(string='Processing Cost')
    cost_total = fields.Monetary(compute='_compute_margin', string='Cost')
    cost_per_animal = fields.Monetary(compute='_compute_margin', string='Cost per Animal')
    margin_amount = fields.Monetary(compute='_compute_margin', string='Margin')
    opportunity_id = fields.Many2one('ie.export.opportunity', string='Opportunity', copy=False, readonly=True)
    bank_reconciled = fields.Boolean(compute='_compute_bank_reconciled')

    _sql_constraints = [
        ('name_company_uniq', 'unique(name, company_id)', 'The export order number must be unique per company.'),
    ]

    @api.depends('name')
    def _compute_display_name(self):
        for order in self:
            order.display_name = order.name or _('New')

    @api.depends(
        'export_line_type', 'animal_count', 'feed_cost', 'veterinary_cost', 'holding_cost',
        'raw_material_cost', 'processing_cost', 'amount_total',
    )
    def _compute_margin(self):
        for order in self:
            if order.export_line_type == 'live_animal':
                cost = order.feed_cost + order.veterinary_cost + order.holding_cost
                order.cost_per_animal = cost / order.animal_count if order.animal_count else 0.0
            elif order.export_line_type == 'teff':
                cost = order.raw_material_cost + order.processing_cost
                order.cost_per_animal = 0.0
            else:
                cost = 0.0
                order.cost_per_animal = 0.0
            order.cost_total = cost
            order.margin_amount = order.amount_total - cost

    @api.depends('line_ids.price_subtotal')
    def _compute_amount_total(self):
        for order in self:
            order.amount_total = sum(order.line_ids.mapped('price_subtotal'))

    def _compute_counts(self):
        for order in self:
            order.sale_count = len(order.sale_ids)
            order.picking_count = len(order.picking_ids)
            order.invoice_count = len(order.invoice_ids)
            order.proceeds_count = len(order.proceeds_ids)
            order.gate_count = len(order.gate_ids)

    def _compute_stock_panel(self):
        for order in self:
            on_hand = reserved = ordered = 0.0
            for line in order.line_ids:
                on_hand += line.qty_on_hand
                reserved += line.qty_reserved
                ordered += line.quantity
            order.qty_on_hand = on_hand
            order.qty_reserved = reserved
            order.qty_order = ordered
            order.qty_balance = on_hand - reserved - max(ordered - reserved, 0.0)

    def _compute_qty_shipped(self):
        for order in self:
            shipped = 0.0
            pickings = order.picking_ids.filtered(lambda pick: pick.state == 'done' and pick.picking_type_id.code == 'outgoing')
            for picking in pickings:
                shipped += sum(picking.move_ids.mapped('quantity'))
            order.qty_shipped = shipped

    @api.depends('proceeds_ids.amount_received', 'proceeds_ids.amount_expected', 'amount_total')
    def _compute_proceeds(self):
        for order in self:
            expected = sum(order.proceeds_ids.mapped('amount_expected')) or order.amount_total
            received = sum(order.proceeds_ids.mapped('amount_received'))
            order.proceeds_expected = expected
            order.proceeds_received = received
            order.proceeds_outstanding = expected - received

    def _ie_proceeds_payments(self):
        """Posted customer receipts linked to this export order."""
        self.ensure_one()
        return self.payment_ids.filtered(
            lambda payment: payment.payment_type == 'inbound'
            and payment.partner_type == 'customer'
            and payment.state in ('paid', 'in_process')
        )

    def _compute_bank_reconciled(self):
        for order in self:
            lines = order.proceeds_ids.mapped('statement_line_id')
            order.bank_reconciled = bool(lines) and all(line.is_reconciled for line in lines)

    @api.constrains('partner_id')
    def _check_buyer(self):
        for order in self:
            if order.partner_id and not order.partner_id.ie_buyer_approved:
                raise ValidationError(_("Buyer %s is not an approved buyer.", order.partner_id.display_name))

    @api.constrains('product_id', 'line_ids')
    def _check_header_product(self):
        for order in self:
            if order.line_ids and order.product_id not in order.line_ids.product_id:
                raise ValidationError(_("The export product must appear on at least one line."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('ie.export.order') or _('New')
        records = super().create(vals_list)
        for record in records:
            if not record.document_line_ids:
                types = self.env['ie.document.type'].search([
                    ('active', '=', True),
                    ('order_type', 'in', ('export', 'both')),
                    '|', ('company_id', '=', False), ('company_id', '=', record.company_id.id),
                ])
                self.env['ie.document.line'].create([{
                    'document_type_id': doc_type.id,
                    'sequence': doc_type.sequence,
                    'blocking_state': doc_type.blocking_state,
                    'required': doc_type.required,
                    'export_order_id': record.id,
                } for doc_type in types])
            record._ie_log('create', detail=record.name)
        records._ie_sync_export_reference()
        return records

    def _ie_sync_export_reference(self):
        """Keep one Export-plan account whose reference is this export number."""
        plan = self._ie_analytic_plan('import_export_control.analytic_plan_export', ['Export'])
        if not plan:
            return
        accounts = self.env['account.analytic.account'].sudo()
        for order in self:
            number = order.name
            if not number or number == _('New'):
                continue
            account = accounts.search([
                ('plan_id', '=', plan.id),
                ('code', '=', number),
            ], limit=1)
            if not account:
                account = accounts.create({
                    'name': number,
                    'code': number,
                    'plan_id': plan.id,
                    'company_id': order.company_id.id,
                })
            if order.analytic_export_id != account:
                order.with_context(ie_skip_audit=True).write({'analytic_export_id': account.id})

    @api.model
    def _ie_load_export_references(self):
        self.search([])._ie_sync_export_reference()

    @api.model
    def _ie_load_demo_export_analytic(self):
        """One shipped export with every analytic account, its sales order, and its invoice."""
        accounts = self.env['account.analytic.account'].sudo()

        def _account(plan_name, code):
            return accounts.search([
                ('plan_id.name', '=', plan_name),
                ('plan_id.parent_id', '=', False),
                ('code', '=', code),
            ], limit=1)

        trading = _account('Business Unit', 'TRD')
        procurement = _account('Department', 'PROC')
        warehouse = _account('Warehouse', 'WH-MAIN')
        if not trading or not procurement or not warehouse:
            return
        order = self.search([
            ('contract_no', '=', 'CT-STAGE-SHIPPED'),
            ('sale_ids', '!=', False),
            ('invoice_ids', '!=', False),
        ], limit=1)
        if not order:
            order = self.search([
                ('sale_ids', '!=', False),
                ('invoice_ids', '!=', False),
            ], order='id', limit=1)
        if not order:
            return
        order.with_context(ie_skip_audit=True).write({
            'analytic_business_unit_id': trading.id,
            'analytic_department_id': procurement.id,
            'analytic_warehouse_id': warehouse.id,
        })
        order._ie_sync_export_reference()
        distribution = order._ie_analytic_distribution()
        if not distribution:
            return
        sales = order.sale_ids.filtered(lambda sale: sale.state != 'cancel')
        sales.order_line.filtered(lambda line: not line.display_type).write({
            'analytic_distribution': distribution,
        })
        lines = order.invoice_ids.filtered(lambda move: move.state != 'cancel').invoice_line_ids.filtered(
            lambda line: line.display_type == 'product'
        )
        if lines:
            lines.sudo().write({'analytic_distribution': distribution})

    def _ie_check_duplicate(self):
        self.ensure_one()
        window = self.date - timedelta(days=self.company_id.ie_duplicate_days or 0)
        duplicate = self.search([
            ('id', '!=', self.id),
            ('company_id', '=', self.company_id.id),
            ('state', '!=', 'cancelled'),
            ('contract_no', '=', self.contract_no),
        ])
        product_key = tuple(sorted((line.product_id.id, line.quantity, line.price_subtotal) for line in self.line_ids))
        others = self.search([
            ('id', '!=', self.id),
            ('company_id', '=', self.company_id.id),
            ('state', '!=', 'cancelled'),
            ('partner_id', '=', self.partner_id.id),
            ('date', '>=', window),
        ])
        for other in others:
            other_key = tuple(sorted((line.product_id.id, line.quantity, line.price_subtotal) for line in other.line_ids))
            if product_key and other_key == product_key:
                duplicate |= other
        if not duplicate:
            return
        self.with_context(ie_skip_audit=True).write({'duplicate_flag': True})
        message = _("Possible duplicate of %s.", ', '.join(duplicate.mapped('name')))
        if self.company_id.ie_duplicate_mode == 'warn':
            self.message_post(body=message)
            self._ie_log('exception', detail=message)
            return
        raise UserError(message)

    def _ie_assert_stock(self):
        self.ensure_one()
        if self.stock_override:
            return
        for line in self.line_ids:
            free_qty = line._ie_free_qty()
            rounding = line.uom_id.rounding or 0.01
            if float_compare(line.quantity, free_qty, precision_rounding=rounding) > 0:
                raise UserError(_(
                    "Export quantity %(qty)s of %(product)s exceeds available stock %(free)s. Approval and reservation are blocked.",
                    qty=line.quantity, product=line.product_id.display_name, free=free_qty,
                ))

    def _ie_reserve_stock(self):
        self.ensure_one()
        if self.reservation_picking_id and self.reservation_picking_id.state not in ('cancel', 'done'):
            return self.reservation_picking_id
        self._ie_assert_stock()
        warehouse = self.warehouse_id
        picking_type = warehouse.out_type_id
        if not picking_type:
            raise UserError(_("The warehouse has no delivery operation type."))
        location = picking_type.default_location_src_id or warehouse.lot_stock_id
        location_dest = picking_type.default_location_dest_id
        picking = self.env['stock.picking'].create({
            'picking_type_id': picking_type.id,
            'location_id': location.id,
            'location_dest_id': location_dest.id,
            'origin': self.name,
            'partner_id': self.partner_id.id,
            'company_id': self.company_id.id,
            'ie_export_order_id': self.id,
            'ie_is_export': True,
            'move_ids': [(0, 0, {
                'name': line.product_id.display_name,
                'product_id': line.product_id.id,
                'product_uom_qty': line.quantity,
                'product_uom': line.uom_id.id,
                'location_id': (line.location_id or location).id,
                'location_dest_id': location_dest.id,
                'company_id': self.company_id.id,
            }) for line in self.line_ids],
        })
        picking.action_confirm()
        picking.action_assign()
        if not self.stock_override and any(move.state not in ('assigned', 'done') for move in picking.move_ids):
            picking.action_cancel()
            raise UserError(_("Stock could not be reserved in full. The export order was not committed."))
        self.with_context(ie_skip_audit=True).write({'reservation_picking_id': picking.id})
        self._ie_log('write', detail=_('Stock reserved on %s.', picking.name))
        return picking

    def _ie_release_reservation(self):
        for order in self:
            picking = order.reservation_picking_id
            if picking and picking.state not in ('done', 'cancel'):
                picking.action_cancel()
                order._ie_log('write', detail=_('Stock reservation %s released.', picking.name))

    def _ie_release_side_effects(self, target_state):
        if target_state in ('draft', 'sales_request'):
            self._ie_release_reservation()
        return True

    def _ie_assert_quality_passed(self):
        self.ensure_one()
        checks = self.env['quality.check'].search([('ie_export_order_id', '=', self.id)])
        if not checks:
            raise UserError(_("Create the quality checks before quality approval."))
        if any(check.quality_state == 'fail' for check in checks):
            raise UserError(_("A failed quality check blocks shipment approval."))

    def _ie_before_enter(self, new_state):
        self.ensure_one()
        if new_state == 'stock_verification':
            self._ie_reserve_stock()
        if new_state == 'quality_verification':
            self._ie_create_quality_checks()
        if new_state == 'shipped':
            self._ie_validate_shipment()
        if new_state == 'closed':
            self._ie_assert_can_close()
        return True

    def _ie_create_quality_checks(self):
        self.ensure_one()
        existing = self.env['quality.check'].search([('ie_export_order_id', '=', self.id)])
        if existing:
            return existing
        team = self.env['quality.alert.team'].search([
            '|', ('company_id', '=', self.company_id.id), ('company_id', '=', False),
        ], limit=1)
        if not team:
            raise UserError(_("Create a quality team before quality verification."))
        test_type = self.env.ref('quality_control.test_type_passfail', raise_if_not_found=False)
        if not test_type:
            test_type = self.env['quality.point.test_type'].search([('technical_name', '=', 'passfail')], limit=1)
        if not test_type:
            raise UserError(_("The Pass/Fail quality test type is not installed."))
        checks = self.env['quality.check']
        for line in self.line_ids:
            check = self.env['quality.check'].create({
                'title': _('Export quality %s', line.product_id.display_name),
                'product_id': line.product_id.id,
                'team_id': team.id,
                'test_type_id': test_type.id,
                'company_id': self.company_id.id,
                'lot_id': line.lot_id.id,
                'ie_export_order_id': self.id,
                'ie_moisture': line.moisture,
                'ie_purity': line.purity,
                'ie_foreign_matter': line.foreign_matter,
                'ie_certificate_no': line.certificate_no,
            })
            line.quality_check_id = check
            checks |= check
        return checks

    def _ie_prepare_customs_clearance(self):
        """Create or clear the export declaration when the order is marked shipped."""
        self.ensure_one()
        if self.customs_ids.filtered(lambda customs: customs.state == 'cleared'):
            return
        pending = self.customs_ids.filtered(lambda customs: customs.state == 'pending')
        if pending:
            pending.action_clear()
            return
        if self.customs_ids.filtered(lambda customs: customs.state == 'held'):
            return
        product = self.product_id or self.line_ids[:1].product_id
        hs = product.ie_hs_code_id if product else False
        if not hs:
            hs = self.line_ids.filtered('hs_code_id')[:1].hs_code_id
        customs = self.env['ie.customs.clearance'].create({
            'name': 'DEC-%s' % self.name,
            'direction': 'export',
            'hs_code_id': hs.id if hs else False,
            'duty_amount': 0.0,
            'tax_amount': 0.0,
            'currency_id': self.currency_id.id,
            'export_order_id': self.id,
            'company_id': self.company_id.id,
        })
        customs.action_clear()
        self._ie_log('write', detail=_('Customs clearance %s prepared and cleared.', customs.name))

    def _ie_prepare_shipment(self):
        """Create the export shipment from the order when none is recorded."""
        self.ensure_one()
        if self.shipment_ids:
            return
        reference = self.contract_no or self.name
        shipment = self.env['ie.shipment'].create({
            'transport_mode': 'sea',
            'bl_number': self.name,
            'container_available': True,
            'container_booked': True,
            'booking_reference': reference,
            'shipping_line': reference,
            'container_type': 'standard',
            'factory_arrival': self.shipment_date,
            'port_loading': self.port_loading,
            'port_destination': self.port_destination,
            'etd': self.shipment_date,
            'eta': fields.Date.add(self.shipment_date, days=12) if self.shipment_date else False,
            'atd': self.shipment_date,
            'state': 'in_transit',
            'quantity': self.qty_order,
            'export_order_id': self.id,
            'company_id': self.company_id.id,
        })
        self._ie_log('write', detail=_('Shipment %s created.', shipment.name))

    def _ie_validate_shipment(self):
        self.ensure_one()
        self._ie_assert_quality_passed()
        picking = self.reservation_picking_id
        if not picking or picking.state in ('cancel',):
            raise UserError(_("A stock reservation is required before shipment."))
        if picking.state != 'done':
            for move in picking.move_ids:
                if not move.quantity:
                    move.quantity = move.product_uom_qty
                move.picked = True
            result = picking.with_context(skip_backorder=True).button_validate()
            if isinstance(result, dict) or picking.state != 'done':
                raise UserError(_("The reserved delivery could not be validated. Complete the transfer from the warehouse screen."))
        self._ie_prepare_customs_clearance()
        if not self.customs_ids.filtered(lambda customs: customs.state == 'cleared'):
            raise UserError(_("Customs clearance must be cleared before the export is shipped."))
        self._ie_prepare_shipment()
        if not self.shipment_ids:
            raise UserError(_("Record the shipment before marking the order as shipped."))
        sea = self.shipment_ids.filtered(lambda shipment: shipment.transport_mode == 'sea')
        if sea and not all(shipment._ie_container_ready() for shipment in sea):
            raise UserError(_("Complete the container checklist on each sea shipment: availability, booking, shipping line, container type, booking reference, and arrival at the factory."))
        self._ie_assert_quota(include_current=True)

    def _ie_open_compliance_gate(self):
        self.ensure_one()
        if self.gate_ids.filtered(lambda gate: gate.state != 'cancelled'):
            return
        gate = self.env['ie.compliance.gate'].create({
            'export_order_id': self.id,
            'order_type': 'export',
            'company_id': self.company_id.id,
        })
        gate._ie_seed_and_evaluate()

    def _ie_assert_advance(self):
        """Advance exports cannot leave draft until the USD advance is received."""
        self.ensure_one()
        if self.payment_method != 'advance':
            return
        if not self.advance_amount:
            raise UserError(_("Set the agreed USD advance before submitting this export order."))
        payment = self.advance_payment_id
        usd = self.env.ref('base.USD', raise_if_not_found=False)
        if not payment or payment.state not in ('paid', 'in_process'):
            raise UserError(_("Link a received USD customer advance before submitting this export order."))
        if usd and payment.currency_id != usd:
            raise UserError(_("The advance payment must be in USD."))
        if float_compare(payment.amount, self.advance_amount, precision_rounding=payment.currency_id.rounding) < 0:
            raise UserError(_("The received advance is less than the agreed USD amount."))

    def _ie_assert_quota(self, include_current):
        """Block the order when this year's government allocation would be exceeded."""
        self.ensure_one()
        year = self.date.year
        Quota = self.env['ie.export.quota']
        Line = self.env['ie.export.order.line']
        for product in self.line_ids.product_id:
            quota = Quota.search([
                ('company_id', '=', self.company_id.id),
                ('product_id', '=', product.id),
                ('year', '=', year),
            ], limit=1)
            if not quota:
                continue
            shipped = Line.search([
                ('order_id.company_id', '=', self.company_id.id),
                ('order_id.state', 'in', ('shipped', 'proceeds', 'reconciled', 'closed')),
                ('product_id', '=', product.id),
                ('order_id.date', '>=', '%s-01-01' % year),
                ('order_id.date', '<=', '%s-12-31' % year),
            ])
            used = sum(shipped.mapped('quantity'))
            extra = sum(self.line_ids.filtered(lambda line: line.product_id == product).mapped('quantity')) if include_current or self.state not in ('shipped', 'proceeds', 'reconciled', 'closed') else 0.0
            if used + extra > quota.allocated_qty:
                raise UserError(_(
                    "Export quota for %(product)s in %(year)s is %(allocated)s. Already shipped: %(used)s. This order would exceed it.",
                    product=product.display_name, year=year, allocated=quota.allocated_qty, used=used,
                ))

    def action_submit(self):
        for order in self:
            if order.state != 'draft':
                raise UserError(_("Only a draft export order can be submitted."))
            if not order.line_ids:
                raise UserError(_("Add at least one product line."))
            order._ie_check_duplicate()
            order._ie_assert_advance()
            order._ie_assert_quota(include_current=False)
            order._ie_assert_stock()
            order._ie_enter_state('sales_request', reason=_('Submitted.'))
        return True

    def action_complete_compliance(self):
        for order in self:
            if order.state != 'compliance':
                raise UserError(_("The compliance gate is not the current step."))
            order._ie_check_compliance_user()
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
            if gate.result != 'approved':
                order.action_hold(gate.fail_reason or _('Export compliance hold.'))
                continue
            order._ie_enter_state('management', reason=_('Compliance gate passed.'))
        return True

    def action_open_customs(self):
        for order in self:
            if order.state != 'approved':
                raise UserError(_("The export order must be approved for shipment."))
            order._ie_check_group('import_export_control.group_ie_officer')
            order._ie_enter_state('customs_loading', reason=_('Customs and loading started.'))
        return True

    def action_mark_shipped(self):
        for order in self:
            if order.state != 'customs_loading':
                raise UserError(_("The order is not in customs / loading."))
            order._ie_check_group('import_export_control.group_ie_officer')
            order._ie_enter_state('shipped', reason=_('Shipped.'))
        return True

    def action_mark_proceeds(self):
        for order in self:
            if order.state != 'shipped':
                raise UserError(_("Record proceeds after shipment."))
            order._ie_check_group('import_export_control.group_ie_finance')
            if not order.proceeds_ids:
                raise UserError(_("Create an export proceeds record first."))
            order._ie_enter_state('proceeds', reason=_('Proceeds recorded.'))
        return True

    def action_finance_sign(self):
        for order in self:
            if order.state != 'proceeds':
                raise UserError(_("Finance signs after proceeds are recorded."))
            order._ie_check_group('import_export_control.group_ie_finance')
            if order.create_uid == self.env.user and not self.env.su:
                raise UserError(_("The order creator cannot sign the reconciliation."))
            order.write({'finance_signed_by_id': self.env.user.id, 'finance_signed_on': fields.Datetime.now()})
            order._ie_log('approval', detail=_('Finance reconciliation sign-off.'))
        return True

    def action_compliance_sign(self):
        for order in self:
            if order.state != 'proceeds':
                raise UserError(_("Control & Compliance signs after proceeds are recorded."))
            order._ie_check_group('import_export_control.group_ie_compliance')
            order._ie_check_not_maker()
            if order.finance_signed_by_id == self.env.user and not self.env.su:
                raise UserError(_("Finance and Control & Compliance must be different people."))
            order.write({'compliance_signed_by_id': self.env.user.id, 'compliance_signed_on': fields.Datetime.now()})
            order._ie_log('approval', detail=_('Control & Compliance reconciliation sign-off.'))
            if order.finance_signed_by_id and order.compliance_signed_by_id:
                order._ie_set_state('reconciled', reason=_('Dual sign-off complete.'))
        return True

    def action_close(self):
        for order in self:
            if order.state != 'reconciled':
                raise UserError(_("Close the export order only after reconciliation."))
            order._ie_check_group('import_export_control.group_ie_finance')
            order._ie_enter_state('closed', reason=_('Closed.'))
        return True

    def _ie_assert_can_close(self):
        self.ensure_one()
        if not self.finance_signed_by_id or not self.compliance_signed_by_id:
            raise UserError(_("Finance and Control & Compliance must both sign off before closure."))
        open_exceptions = self.exception_ids.filtered(lambda item: item.state not in ('resolved', 'cancelled'))
        if open_exceptions:
            raise UserError(_("Resolve open compliance exceptions before closure."))
        tolerance = (self.company_id.ie_recon_tolerance or 0.0) / 100.0
        if self.qty_order and abs(self.qty_order - self.qty_shipped) / self.qty_order > tolerance:
            raise UserError(_("Shipped quantity does not match the export order within tolerance."))
        if self.proceeds_expected and abs(self.proceeds_outstanding) / self.proceeds_expected > tolerance:
            raise UserError(_("Export proceeds are still outstanding beyond the reconciliation tolerance."))

    def action_cancel(self, reason):
        if not reason:
            raise UserError(_("A cancellation reason is required."))
        for order in self:
            if order.state in ('closed', 'cancelled'):
                raise UserError(_("This order cannot be cancelled."))
            order._ie_check_group('import_export_control.group_ie_management')
            order._ie_cancel_open_approvals()
            order._ie_release_reservation()
            for sale in order.sale_ids.filtered(lambda so: so.state not in ('cancel', 'done')):
                if sale.state in ('draft', 'sent'):
                    sale.action_cancel()
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
                'header': {'partner_id': order.partner_id.id, 'amount_total': order.amount_total, 'contract_no': order.contract_no},
                'lines': [{'product_id': line.product_id.id, 'quantity': line.quantity, 'price_unit': line.price_unit} for line in order.line_ids],
            }
            self.env['ie.order.revision'].create({
                'export_order_id': order.id,
                'version': order.version,
                'reason': reason,
                'snapshot': snapshot,
                'user_id': self.env.user.id,
            })
            order._ie_cancel_open_approvals()
            order._ie_release_reservation()
            order.with_context(ie_state_bypass=True, ie_skip_audit=True).write({'version': order.version + 1, 'stock_override': False})
            order._ie_set_state('draft', reason=reason)
            order._ie_log('write', detail=_('Amendment version %s.', order.version), reason=reason, changes=snapshot)
        return True

    def action_stock_override(self, reason):
        if not reason:
            raise UserError(_("A management reason is required to override the stock check."))
        for order in self:
            order._ie_check_group('import_export_control.group_ie_management')
            if order.create_uid == self.env.user and not self.env.su:
                raise UserError(_("The order creator cannot override the stock check."))
            order.write({
                'stock_override': True,
                'stock_override_reason': reason,
                'stock_override_user_id': self.env.user.id,
            })
            order._ie_log('override', detail=_('Stock over-commitment override.'), reason=reason)
        return True

    def action_create_sale(self):
        self.ensure_one()
        if self.state not in EXPORT_UNLOCKED:
            raise UserError(_("Create the sales order only after the export order is approved for shipment."))
        if not self.reservation_picking_id or self.reservation_picking_id.state == 'cancel':
            raise UserError(_("Reserved stock is required before the sales order."))
        if self.sale_ids.filtered(lambda sale: sale.state != 'cancel'):
            return self.action_view_sales()
        distribution = self._ie_analytic_distribution()
        sale = self.env['sale.order'].create({
            'partner_id': self.partner_id.id,
            'currency_id': self.currency_id.id,
            'company_id': self.company_id.id,
            'origin': self.name,
            'warehouse_id': self.warehouse_id.id,
            'ie_export_order_id': self.id,
            'ie_is_export': True,
            'client_order_ref': self.contract_no,
            'order_line': [(0, 0, {
                'product_id': line.product_id.id,
                'product_uom_qty': line.quantity,
                'product_uom': line.uom_id.id,
                'price_unit': line.price_unit,
                'analytic_distribution': distribution,
            }) for line in self.line_ids],
        })
        if distribution:
            sale.order_line.filtered(lambda line: not line.display_type).write({
                'analytic_distribution': distribution,
            })
        self._ie_log('write', detail=_('Sales order %s created.', sale.name))
        return self.action_view_sales()

    def _action_view(self, model, field_name):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': model,
            'view_mode': 'list,form',
            'domain': [(field_name, '=', self.id)],
            'context': {'default_%s' % field_name: self.id},
        }

    def action_view_sales(self):
        return self._action_view('sale.order', 'ie_export_order_id')

    def action_view_pickings(self):
        return self._action_view('stock.picking', 'ie_export_order_id')

    def action_view_invoices(self):
        return self._action_view('account.move', 'ie_export_order_id')

    def action_view_proceeds(self):
        return self._action_view('ie.export.proceeds', 'export_order_id')

    def action_view_gates(self):
        return self._action_view('ie.compliance.gate', 'export_order_id')

    def action_view_audit(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'ie.audit.log',
            'view_mode': 'list,form',
            'domain': [('model_name', '=', self._name), ('res_id', '=', self.id)],
        }

    @api.model
    def _ie_load_demo_orders(self):
        """One export order per main-path stage, with the documents that stage allows."""
        company = self.env.ref('base.main_company', raise_if_not_found=False) or self.env.company
        buyer = self.env.ref('import_export_control.demo_buyer', raise_if_not_found=False)
        product = self.env.ref('import_export_control.demo_product_soy', raise_if_not_found=False)
        warehouse = self.env['stock.warehouse'].search([('company_id', '=', company.id)], limit=1)
        if not buyer or not product or not warehouse:
            return
        stages = [
            'draft', 'sales_request', 'stock_verification', 'quality_verification', 'finance',
            'compliance', 'management', 'approved', 'customs_loading', 'shipped',
            'proceeds', 'reconciled', 'closed',
        ]
        pending = [stage for stage in stages if not self.search_count([('contract_no', '=', 'CT-STAGE-%s' % stage.upper())])]
        if not pending:
            late = self.search([
                ('contract_no', '=like', 'CT-STAGE-%'),
                ('state', 'in', ('shipped', 'proceeds', 'reconciled', 'closed')),
            ])
            for order in late.filtered(lambda item: not item.invoice_ids):
                self._ie_demo_export_invoice(order)
            return
        location = warehouse.lot_stock_id
        if location:
            self.env['stock.quant'].sudo()._update_available_quantity(product, location, 20000)
        orders = self.with_company(company).sudo()
        for index, stage in enumerate(stages):
            if stage not in pending:
                continue
            orders._ie_demo_export_one(stage, index, stages, buyer, product, company, warehouse)

    def _ie_demo_export_one(self, stage, index, stages, buyer, product, company, warehouse):
        today = fields.Date.context_today(self)
        order = self.with_company(company).sudo().create({
            'company_id': company.id,
            'partner_id': buyer.id,
            'agent_id': buyer.id,
            'end_customer_id': buyer.id,
            'business_unit_id': self.env.ref('import_export_control.bu_trading').id,
            'product_id': product.id,
            'country_id': self.env.ref('base.ae').id,
            'currency_id': company.currency_id.id,
            'contract_no': 'CT-STAGE-%s' % stage.upper(),
            'contract_expiry': fields.Date.add(today, days=180),
            'payment_method': 'cad',
            'incoterm_id': self.env.ref('account.incoterm_FOB').id,
            'shipment_date': fields.Date.add(today, days=20 + index),
            'port_loading': 'Djibouti',
            'port_destination': 'Jebel Ali',
            'warehouse_id': warehouse.id,
            'user_id': self.env.user.id,
            'export_line_type': 'other',
            'note': _('Demo export order standing at %s.', stage),
            'line_ids': [(0, 0, {
                'product_id': product.id,
                'hs_code_id': product.ie_hs_code_id.id,
                'quantity': 200 + index,
                'uom_id': product.uom_id.id,
                'price_unit': 3.5,
                'grade': 'FAQ',
                'quality_specification': _('Soybean meal, sound merchantable quality.'),
                'packaging': '50 kg bags',
                'net_weight': 200 + index,
                'gross_weight': (200 + index) * 1.02,
                'location_id': warehouse.lot_stock_id.id,
            })],
        })
        payload = base64.b64encode(b'Demo export document')
        for line in order.document_line_ids:
            attachment = self.env['ir.attachment'].sudo().create({
                'name': '%s.txt' % (line.name or 'document'),
                'datas': payload,
                'res_model': 'ie.document.line',
                'res_id': line.id,
            })
            line.attachment_ids = [(4, attachment.id)]
        target = stages.index(stage)
        for next_state in stages[1:target + 1]:
            self._ie_demo_export_prepare(order, next_state)
            order._ie_set_state(next_state, reason=_('Demo data.'))
            if next_state in order._ie_approval_states():
                self._ie_demo_try(lambda state_code=next_state: self._ie_demo_export_approval(
                    order, state_code, pending=state_code == stage,
                ))
        if target >= stages.index('compliance'):
            order._ie_open_compliance_gate()
            if stage != 'compliance':
                order.gate_ids.sudo().write({
                    'state': 'done',
                    'result': 'approved',
                    'user_id': self.env.user.id,
                    'check_date': fields.Datetime.now(),
                })
        if target >= stages.index('approved'):
            self._ie_demo_try(order.action_create_sale)
        if target >= stages.index('shipped'):
            self._ie_demo_export_invoice(order)
        if target >= stages.index('proceeds'):
            bank = self.env.ref('import_export_control.demo_bank_cbe', raise_if_not_found=False)
            proceeds = self.env['ie.export.proceeds'].sudo().create({
                'export_order_id': order.id,
                'amount_expected': order.amount_total,
                'amount_received': order.amount_total,
                'bank_id': bank.id if bank else False,
                'receipt_date': today,
                'expected_date': fields.Date.add(today, days=-5),
                'fx_rate': 1.0,
                'invoice_id': order.invoice_ids[:1].id,
                'payment_id': order.payment_ids[:1].id,
            })
            if target >= stages.index('reconciled'):
                self._ie_demo_try(lambda proceeds=proceeds: order._ie_demo_reconcile_proceeds(proceeds))
        if target >= stages.index('reconciled'):
            order.sudo().write({
                'finance_signed_by_id': self.env.user.id,
                'finance_signed_on': fields.Datetime.now(),
                'compliance_signed_by_id': self.env.user.id,
                'compliance_signed_on': fields.Datetime.now(),
            })
        order._ie_log('write', detail=_('Demo record prepared at %s.', stage))

    def _ie_demo_export_prepare(self, order, next_state):
        if next_state == 'stock_verification':
            self._ie_demo_try(order._ie_reserve_stock)
        if next_state == 'quality_verification':
            self._ie_demo_export_quality(order, passed=True)
        if next_state == 'customs_loading':
            self._ie_demo_export_shipment(order)
            self._ie_demo_export_customs(order, cleared=False)
        if next_state == 'shipped':
            self._ie_demo_export_quality(order, passed=True)
            self._ie_demo_export_shipment(order)
            order.shipment_ids.sudo().write({'state': 'in_transit', 'atd': order.shipment_date})
            self._ie_demo_export_customs(order, cleared=True)
            self._ie_demo_try(order._ie_validate_shipment)

    def _ie_demo_export_quality(self, order, passed=False):
        for line in order.line_ids:
            line.write({
                'moisture': 10.0,
                'purity': 96.0,
                'foreign_matter': 0.5,
                'certificate_no': 'QC-%s' % order.contract_no,
            })
            for param in line.parameter_ids:
                value = {'moisture': 10.0, 'purity': 96.0, 'foreign_matter': 0.5}.get(param.code, param.min_value or param.max_value or 0.0)
                param.write({'value': value, 'result': 'pass'})
        def _checks():
            team = self.env['quality.alert.team'].sudo().search([
                '|', ('company_id', '=', order.company_id.id), ('company_id', '=', False),
            ], limit=1)
            if not team:
                self.env['quality.alert.team'].sudo().create({
                    'name': 'Demo Quality',
                    'company_id': order.company_id.id,
                })
            order._ie_create_quality_checks()
            if passed:
                checks = self.env['quality.check'].sudo().search([('ie_export_order_id', '=', order.id)])
                checks.write({'quality_state': 'pass', 'control_date': fields.Datetime.now()})
                for line in order.line_ids.filtered('quality_check_id'):
                    line.parameter_ids.write({'quality_check_id': line.quality_check_id.id})
        self._ie_demo_try(_checks)

    def _ie_demo_export_shipment(self, order):
        if order.shipment_ids:
            return
        self.env['ie.shipment'].sudo().create({
            'transport_mode': 'sea',
            'carrier': 'MSC',
            'bl_number': 'BL-%s' % order.contract_no,
            'container_numbers': 'MSCU%s' % order.id,
            'container_available': True,
            'container_booked': True,
            'booking_reference': 'BK-%s' % order.contract_no,
            'shipping_line': 'MSC',
            'container_type': 'standard',
            'factory_arrival': order.shipment_date,
            'port_loading': order.port_loading,
            'port_destination': order.port_destination,
            'etd': order.shipment_date,
            'eta': fields.Date.add(order.shipment_date, days=12),
            'state': 'booked',
            'quantity': order.qty_order,
            'export_order_id': order.id,
            'company_id': order.company_id.id,
        })

    def _ie_demo_export_customs(self, order, cleared):
        product = order.product_id
        customs = order.customs_ids[:1]
        vals = {
            'state': 'cleared' if cleared else 'pending',
            'clearance_date': fields.Date.context_today(self) if cleared else False,
            'exemption_applicable': True,
            'exemption_type': 'duty',
            'exemption_reference': 'EX-%s' % order.contract_no,
            'exemption_status': 'granted',
        }
        if customs:
            customs.sudo().write(vals)
            return
        vals.update({
            'name': 'DEC-%s' % order.contract_no,
            'direction': 'export',
            'hs_code_id': product.ie_hs_code_id.id,
            'duty_amount': 0.0,
            'tax_amount': 0.0,
            'currency_id': order.currency_id.id,
            'export_order_id': order.id,
            'company_id': order.company_id.id,
        })
        self.env['ie.customs.clearance'].sudo().create(vals)

    def _ie_demo_export_invoice(self, order):
        def _invoice():
            if order.invoice_ids:
                return
            journal = self.env['account.journal'].search([
                ('company_id', '=', order.company_id.id), ('type', '=', 'sale'),
            ], limit=1)
            if not journal:
                return
            invoice = self.env['account.move'].sudo().create({
                'move_type': 'out_invoice',
                'partner_id': order.partner_id.id,
                'journal_id': journal.id,
                'invoice_date': fields.Date.context_today(self),
                'currency_id': order.currency_id.id,
                'company_id': order.company_id.id,
                'ie_export_order_id': order.id,
                'ie_is_export': True,
                'invoice_line_ids': [(0, 0, {
                    'product_id': line.product_id.id,
                    'name': line.product_id.display_name,
                    'quantity': line.quantity,
                    'price_unit': line.price_unit,
                    'tax_ids': [(6, 0, [])],
                    'analytic_distribution': order._ie_analytic_distribution(),
                }) for line in order.line_ids],
            })
            invoice.with_context(ie_skip_enforce=True).action_post()

        def _payment():
            if order._ie_proceeds_payments():
                return
            ratio = 0.4 if order.state == 'shipped' else 1.0
            order._ie_demo_customer_payment(ratio)

        self._ie_demo_try(_invoice)
        self._ie_demo_try(_payment)

    def _ie_demo_reconcile_proceeds(self, proceeds):
        """Match the received proceeds to a bank statement line."""
        self.ensure_one()
        journal = self.env['account.journal'].search([
            ('company_id', '=', self.company_id.id), ('type', '=', 'bank'),
        ], limit=1)
        if not journal:
            return
        statement = self.env['account.bank.statement.line'].sudo().create({
            'journal_id': journal.id,
            'amount': self.amount_total,
            'payment_ref': self.contract_no,
            'partner_id': self.partner_id.id,
            'date': fields.Date.context_today(self),
        })
        wizard = self.env['bank.rec.widget'].sudo().with_context(default_st_line_id=statement.id).new({})
        receivable_lines = self.invoice_ids.line_ids.filtered(
            lambda line: line.account_id.account_type == 'asset_receivable' and not line.reconciled
        )
        if receivable_lines:
            wizard._action_add_new_amls(receivable_lines)
        wizard._action_validate()
        proceeds.sudo().write({'statement_line_id': statement.id})

    def _ie_demo_export_approval(self, order, state_code, pending):
        stage = self.env['ie.approval.stage'].sudo().search([
            ('order_type', '=', 'export'),
            ('state_code', '=', state_code),
            ('company_id', '=', order.company_id.id),
            ('active', '=', True),
        ], limit=1)
        if not stage or not stage.category_id:
            return
        approver = self._ie_demo_approver(order.company_id)
        if not approver or approver == order.create_uid:
            return
        request = self.env['approval.request'].sudo().create({
            'name': '%s / %s' % (order.name, stage.name),
            'category_id': stage.category_id.id,
            'request_owner_id': order.create_uid.id,
            'partner_id': order.partner_id.id,
            'amount': order.amount_total,
            'reference': order.name,
            'reason': stage.name,
            'ie_export_order_id': order.id,
            'ie_stage_code': state_code,
            'ie_approval_stage_id': stage.id,
            'date_confirmed': fields.Datetime.now(),
        })
        self.env['approval.approver'].sudo().create({
            'request_id': request.id,
            'user_id': approver.id,
            'status': 'pending' if pending else 'approved',
            'required': True,
        })
        order._ie_log(
            'approval',
            detail=_('Demo approval %s for %s.', request.name, stage.name),
            new_state=state_code,
        )

    @api.model
    def _ie_demo_approver(self, company):
        user = self.env['res.users'].sudo().search([('login', '=', 'demo.export.approver')], limit=1)
        if user:
            return user
        groups = self.env['res.groups'].sudo().browse([
            self.env.ref('base.group_user').id,
            self.env.ref('import_export_control.group_ie_officer').id,
            self.env.ref('import_export_control.group_ie_warehouse').id,
            self.env.ref('import_export_control.group_ie_quality').id,
            self.env.ref('import_export_control.group_ie_finance').id,
            self.env.ref('import_export_control.group_ie_compliance').id,
            self.env.ref('import_export_control.group_ie_management').id,
        ])
        return self.env['res.users'].sudo().with_context(no_reset_password=True).create({
            'name': 'Demo Export Approver',
            'login': 'demo.export.approver',
            'company_id': company.id,
            'company_ids': [(6, 0, [company.id])],
            'groups_id': [(6, 0, groups.ids)],
        })

    @api.model
    def _ie_load_demo_proceeds(self):
        """Proceeds rows for export orders that are already past shipment."""
        company = self.env.ref('base.main_company', raise_if_not_found=False)
        if not company:
            return
        bank = self.env.ref('import_export_control.demo_bank_cbe', raise_if_not_found=False)
        today = fields.Date.context_today(self)
        orders = self.search([
            ('company_id', '=', company.id),
            ('state', 'in', ('proceeds', 'reconciled', 'closed')),
            ('proceeds_ids', '=', False),
        ])
        for order in orders:
            proceeds = self.env['ie.export.proceeds'].sudo().create({
                'export_order_id': order.id,
                'amount_expected': order.amount_total,
                'amount_received': order.amount_total,
                'bank_id': bank.id if bank else False,
                'receipt_date': today,
                'expected_date': fields.Date.add(today, days=-5),
                'fx_rate': 1.0,
                'invoice_id': order.invoice_ids[:1].id,
                'payment_id': order.payment_ids[:1].id,
            })
            if order.state in ('reconciled', 'closed'):
                self._ie_demo_try(lambda proceeds=proceeds, order=order: order._ie_demo_reconcile_proceeds(proceeds))

    def _ie_demo_customer_payment(self, ratio=1.0):
        """Post a customer payment for this export order. Existing posted payments are left as they are."""
        self.ensure_one()
        if self._ie_proceeds_payments() or not self.amount_total:
            return
        journal = self.env['ie.compliance.kpi']._ie_demo_bank_journal(self.company_id)
        payment = self.env['account.payment'].sudo().create({
            'payment_type': 'inbound',
            'partner_type': 'customer',
            'partner_id': self.partner_id.id,
            'amount': self.amount_total * ratio,
            'currency_id': self.currency_id.id,
            'journal_id': journal.id,
            'company_id': self.company_id.id,
            'ie_export_order_id': self.id,
            'ie_is_export': True,
            'memo': self.contract_no,
        })
        payment.with_context(ie_skip_enforce=True).action_post()
        return payment

    @api.model
    def _ie_load_demo_payments(self):
        """Posted payments for the export proceeds stage and the import receipt stage."""
        company = self.env.ref('base.main_company', raise_if_not_found=False)
        if not company:
            return
        self.env['ie.compliance.kpi']._ie_demo_bank_journal(company)
        exports = self.search([
            ('company_id', '=', company.id),
            ('state', 'in', ('shipped', 'proceeds', 'reconciled', 'closed')),
        ])
        for order in exports:
            ratio = 0.4 if order.state == 'shipped' else 1.0
            self._ie_demo_try(lambda order=order, ratio=ratio: order._ie_demo_customer_payment(ratio))
        imports = self.env['ie.import.order'].search([
            ('company_id', '=', company.id),
            ('state', 'in', ('received', 'closed')),
        ])
        for order in imports:
            self._ie_demo_try(lambda order=order: order._ie_demo_supplier_payment())

    def _ie_demo_try(self, callback):
        try:
            with self.env.cr.savepoint():
                callback()
        except Exception:
            _logger.warning('Export demo step skipped for a missing stock, quality, or accounting setup.', exc_info=True)


class IeExportOrderLine(models.Model):
    _name = 'ie.export.order.line'
    _description = 'Export Order Line'

    order_id = fields.Many2one('ie.export.order', required=True, ondelete='cascade', index=True)
    product_id = fields.Many2one('product.product', required=True, check_company=True)
    product_code = fields.Char(related='product_id.default_code', string='Product Code')
    hs_code_id = fields.Many2one('ie.hs.code', string='HS Code', required=True)
    grade = fields.Char()
    quality_specification = fields.Text(string='Quality Specification')
    quantity = fields.Float(required=True, digits='Product Unit of Measure')
    uom_id = fields.Many2one('uom.uom', required=True)
    price_unit = fields.Float(required=True, digits='Product Price')
    currency_id = fields.Many2one(related='order_id.currency_id')
    price_subtotal = fields.Monetary(compute='_compute_subtotal', store=True)
    packaging = fields.Char()
    net_weight = fields.Float(digits='Product Unit of Measure')
    gross_weight = fields.Float(digits='Product Unit of Measure')
    lot_id = fields.Many2one('stock.lot', string='Lot/Batch', domain="[('product_id', '=', product_id)]", check_company=True)
    location_id = fields.Many2one('stock.location', string='Warehouse Location', domain="[('usage', '=', 'internal')]", check_company=True)
    moisture = fields.Float(string='Moisture %')
    purity = fields.Float(string='Purity %')
    foreign_matter = fields.Float(string='Foreign Matter %')
    certificate_no = fields.Char(string='Quality Certificate No.')
    parameter_ids = fields.One2many('ie.quality.parameter', 'line_id')
    quality_template_id = fields.Many2one(
        related='product_id.ie_quality_template_id', string='Quality Template',
    )
    quality_check_id = fields.Many2one('quality.check', copy=False)
    company_id = fields.Many2one(related='order_id.company_id', store=True)
    qty_on_hand = fields.Float(compute='_compute_stock', digits='Product Unit of Measure')
    qty_reserved = fields.Float(compute='_compute_stock', digits='Product Unit of Measure')
    qty_balance = fields.Float(compute='_compute_stock', digits='Product Unit of Measure')

    _sql_constraints = [
        ('qty_positive', 'CHECK(quantity > 0)', 'Quantity must be greater than zero.'),
        ('price_positive', 'CHECK(price_unit >= 0)', 'Unit price cannot be negative.'),
    ]

    @api.depends('quantity', 'price_unit')
    def _compute_subtotal(self):
        for line in self:
            line.price_subtotal = line.quantity * line.price_unit

    def _ie_location(self):
        self.ensure_one()
        return self.location_id or self.order_id.warehouse_id.lot_stock_id

    def _ie_free_qty(self):
        """Quantity still free to promise, adding back this order's own reservation."""
        self.ensure_one()
        location = self._ie_location()
        if not location or not self.product_id:
            return 0.0
        free = self.env['stock.quant']._get_available_quantity(
            self.product_id, location, lot_id=self.lot_id or None, strict=False,
        )
        return free + self._ie_own_reserved()

    def _ie_own_reserved(self):
        self.ensure_one()
        picking = self.order_id.reservation_picking_id
        if not picking or picking.state in ('cancel', 'done'):
            return 0.0
        moves = picking.move_ids.filtered(lambda move: move.product_id == self.product_id and move.state not in ('cancel', 'done'))
        return sum(moves.mapped('product_uom_qty'))

    def _compute_stock(self):
        Quant = self.env['stock.quant']
        for line in self:
            location = line._ie_location() if line.order_id.warehouse_id else False
            on_hand = 0.0
            if line.product_id and location:
                domain = [('product_id', '=', line.product_id.id), ('location_id', 'child_of', location.id)]
                if line.lot_id:
                    domain.append(('lot_id', '=', line.lot_id.id))
                grouped = Quant._read_group(domain, [], ['quantity:sum'])
                on_hand = grouped[0][0] if grouped else 0.0
            reserved = line._ie_own_reserved()
            line.qty_on_hand = on_hand
            line.qty_reserved = reserved
            line.qty_balance = on_hand - reserved

    @api.onchange('product_id')
    def _onchange_product_id(self):
        if not self.product_id:
            return
        self.uom_id = self.product_id.uom_id
        self.hs_code_id = self.product_id.ie_hs_code_id
        template = self.product_id.ie_quality_template_id
        if template and not self.parameter_ids:
            self.parameter_ids = [(0, 0, {
                'template_line_id': param.id,
                'code': param.code,
                'name': param.name,
                'min_value': param.min_value,
                'max_value': param.max_value,
                'uom': param.uom,
            }) for param in template.line_ids]
            for param in template.line_ids:
                if param.code == 'moisture':
                    self.moisture = param.max_value
                elif param.code == 'purity':
                    self.purity = param.min_value
                elif param.code == 'foreign_matter':
                    self.foreign_matter = param.max_value

    @api.constrains('product_id', 'hs_code_id')
    def _check_hs(self):
        for line in self:
            if not line.product_id.ie_hs_code_id or line.product_id.ie_hs_code_id != line.hs_code_id:
                raise ValidationError(_("HS code on the line must be the HS code set on product %s.", line.product_id.display_name))

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        for line in lines:
            template = line.product_id.ie_quality_template_id
            if template and not line.parameter_ids:
                self.env['ie.quality.parameter'].create([{
                    'line_id': line.id,
                    'template_line_id': param.id,
                    'code': param.code,
                    'name': param.name,
                    'min_value': param.min_value,
                    'max_value': param.max_value,
                    'uom': param.uom,
                } for param in template.line_ids])
        return lines


class IeQualityParameter(models.Model):
    _name = 'ie.quality.parameter'
    _description = 'Export Quality Parameter'
    _order = 'sequence, id'

    line_id = fields.Many2one('ie.export.order.line', required=True, ondelete='cascade')
    template_line_id = fields.Many2one(
        'ie.quality.template.line', string='Parameter',
        domain="[('template_id', '=', line_id.quality_template_id)]",
    )
    sequence = fields.Integer(default=10)
    code = fields.Char(required=True)
    name = fields.Char(required=True, translate=True)
    min_value = fields.Float()
    max_value = fields.Float()
    value = fields.Float()
    uom = fields.Char()
    result = fields.Selection([
        ('pass', 'Pass'),
        ('fail', 'Fail'),
        ('conditional', 'Conditional'),
    ])
    quality_check_id = fields.Many2one('quality.check')

    @api.onchange('template_line_id')
    def _onchange_template_line_id(self):
        line = self.template_line_id
        if not line:
            return
        self.code = line.code
        self.name = line.name
        self.min_value = line.min_value
        self.max_value = line.max_value
        self.uom = line.uom
        self.sequence = line.sequence

    @api.model_create_multi
    def create(self, vals_list):
        lines = self.env['ie.quality.template.line'].browse([
            vals['template_line_id'] for vals in vals_list if vals.get('template_line_id')
        ])
        by_id = {line.id: line for line in lines}
        for vals in vals_list:
            line = by_id.get(vals.get('template_line_id'))
            if not line:
                continue
            vals.setdefault('code', line.code)
            vals.setdefault('name', line.name)
            vals.setdefault('min_value', line.min_value)
            vals.setdefault('max_value', line.max_value)
            vals.setdefault('uom', line.uom)
            vals.setdefault('sequence', line.sequence)
        return super().create(vals_list)

    @api.onchange('value', 'min_value', 'max_value')
    def _onchange_value(self):
        if self.value is False:
            return
        if (self.min_value and self.value < self.min_value) or (self.max_value and self.value > self.max_value):
            self.result = 'fail'
        else:
            self.result = 'pass'


class IeExportOpportunity(models.Model):
    _name = 'ie.export.opportunity'
    _description = 'Export Opportunity'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(required=True, copy=False, readonly=True, default=lambda self: _('New'))
    partner_id = fields.Many2one('res.partner', string='Customer', required=True, tracking=True)
    agent_id = fields.Many2one('res.partner', string='Agent', tracking=True)
    end_customer_id = fields.Many2one('res.partner', string='End Customer', tracking=True)
    country_id = fields.Many2one('res.country', string='Destination Country')
    product_id = fields.Many2one('product.product', string='Product', required=True)
    quantity = fields.Float(required=True, digits='Product Unit of Measure', default=1.0)
    uom_id = fields.Many2one('uom.uom', string='Unit')
    price_unit = fields.Float(digits='Product Price')
    currency_id = fields.Many2one('res.currency', default=lambda self: self.env.company.currency_id)
    business_unit_id = fields.Many2one('ie.business.unit', string='Business Unit')
    warehouse_id = fields.Many2one('stock.warehouse', string='Warehouse')
    contract_no = fields.Char(string='Contract No.')
    payment_method = fields.Selection(PAYMENT_METHODS, string='Payment Method', default='cad')
    incoterm_id = fields.Many2one('account.incoterms', string='Incoterm')
    shipment_date = fields.Date(string='Shipment Date')
    port_loading = fields.Char(string='Port of Loading')
    port_destination = fields.Char(string='Destination Port')
    state = fields.Selection([
        ('new', 'New'),
        ('qualified', 'Qualified'),
        ('won', 'Won'),
        ('lost', 'Lost'),
    ], default='new', required=True, tracking=True)
    export_order_id = fields.Many2one('ie.export.order', string='Export Order', readonly=True, copy=False)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    user_id = fields.Many2one('res.users', string='Responsible', default=lambda self: self.env.user)
    note = fields.Text()

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('ie.export.opportunity') or _('New')
        return super().create(vals_list)

    @api.onchange('product_id')
    def _onchange_product(self):
        if self.product_id:
            self.uom_id = self.product_id.uom_id

    def action_qualify(self):
        self.filtered(lambda opp: opp.state == 'new').write({'state': 'qualified'})
        return True

    def action_lose(self):
        if any(opp.state == 'won' for opp in self):
            raise UserError(_("A won opportunity cannot be marked lost."))
        self.write({'state': 'lost'})
        return True

    def action_win(self):
        for opp in self:
            if opp.state == 'won':
                raise UserError(_("This opportunity is already won."))
            if not opp.partner_id.ie_buyer_approved:
                raise UserError(_("Approve %s as a buyer before winning this opportunity.", opp.partner_id.display_name))
            missing = []
            for label, value in (
                (_('Destination country'), opp.country_id),
                (_('Business unit'), opp.business_unit_id),
                (_('Warehouse'), opp.warehouse_id),
                (_('Contract number'), opp.contract_no),
                (_('Incoterm'), opp.incoterm_id),
                (_('Shipment date'), opp.shipment_date),
                (_('Port of loading'), opp.port_loading),
                (_('Destination port'), opp.port_destination),
                (_('Unit of measure'), opp.uom_id or opp.product_id.uom_id),
                (_('HS code on the product'), opp.product_id.ie_hs_code_id),
            ):
                if not value:
                    missing.append(label)
            if opp.quantity <= 0:
                missing.append(_('Quantity'))
            if missing:
                raise UserError(_("Complete the opportunity before creating the export order: %s.", ', '.join(missing)))
            order = self.env['ie.export.order'].create({
                'partner_id': opp.partner_id.id,
                'agent_id': opp.agent_id.id,
                'end_customer_id': opp.end_customer_id.id,
                'country_id': opp.country_id.id,
                'product_id': opp.product_id.id,
                'business_unit_id': opp.business_unit_id.id,
                'warehouse_id': opp.warehouse_id.id,
                'currency_id': opp.currency_id.id,
                'contract_no': opp.contract_no,
                'payment_method': opp.payment_method or 'cad',
                'incoterm_id': opp.incoterm_id.id,
                'shipment_date': opp.shipment_date,
                'port_loading': opp.port_loading,
                'port_destination': opp.port_destination,
                'company_id': opp.company_id.id,
                'user_id': opp.user_id.id,
                'opportunity_id': opp.id,
                'line_ids': [(0, 0, {
                    'product_id': opp.product_id.id,
                    'hs_code_id': opp.product_id.ie_hs_code_id.id,
                    'quantity': opp.quantity,
                    'uom_id': (opp.uom_id or opp.product_id.uom_id).id,
                    'price_unit': opp.price_unit,
                })],
            })
            opp.write({'state': 'won', 'export_order_id': order.id})
        return True


class IeExportQuota(models.Model):
    _name = 'ie.export.quota'
    _description = 'Export Quota'
    _order = 'year desc, product_id'

    product_id = fields.Many2one('product.product', required=True, check_company=True)
    year = fields.Integer(required=True, default=lambda self: fields.Date.context_today(self).year)
    allocated_qty = fields.Float(string='Allocated Quantity', required=True, digits='Product Unit of Measure')
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)

    _sql_constraints = [
        ('quota_unique', 'unique(product_id, year, company_id)', 'One quota per product, year, and company.'),
        ('qty_positive', 'CHECK(allocated_qty > 0)', 'Allocated quantity must be greater than zero.'),
    ]
