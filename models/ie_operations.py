# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.float_utils import float_compare


class IeLc(models.Model):
    _name = 'ie.lc'
    _description = 'Letter of Credit'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(string='LC Number', required=True, tracking=True, index=True)
    lc_type = fields.Selection([
        ('sight', 'Sight'),
        ('usance', 'Usance'),
        ('standby', 'Standby'),
    ], required=True, default='sight', tracking=True)
    bank_id = fields.Many2one('res.bank', required=True, tracking=True)
    applicant_id = fields.Many2one('res.partner', string='Applicant', required=True)
    beneficiary_id = fields.Many2one('res.partner', string='Beneficiary', required=True)
    amount = fields.Monetary(required=True, tracking=True)
    currency_id = fields.Many2one('res.currency', required=True)
    issue_date = fields.Date(tracking=True)
    expiry_date = fields.Date(tracking=True)
    latest_shipment = fields.Date(string='Latest Shipment Date', tracking=True)
    state = fields.Selection([
        ('applied', 'Applied'),
        ('issued', 'Issued'),
        ('amended', 'Amended'),
        ('utilised', 'Utilised'),
        ('expired', 'Expired'),
        ('closed', 'Closed'),
    ], default='applied', required=True, tracking=True)
    margin_amount = fields.Monetary(string='Margin')
    charge_amount = fields.Monetary(string='Charges')
    import_order_id = fields.Many2one('ie.import.order', index=True)
    export_order_id = fields.Many2one('ie.export.order', index=True)
    amendment_ids = fields.One2many('ie.lc.amendment', 'lc_id')
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    closure_date = fields.Date(readonly=True)

    _sql_constraints = [
        ('name_company_uniq', 'unique(name, company_id)', 'The LC number must be unique per company.'),
    ]

    @api.constrains('issue_date', 'expiry_date', 'latest_shipment')
    def _check_dates(self):
        for lc in self:
            if lc.issue_date and lc.expiry_date and lc.expiry_date < lc.issue_date:
                raise ValidationError(_("The LC expiry cannot be earlier than the issue date."))
            if lc.latest_shipment and lc.expiry_date and lc.latest_shipment > lc.expiry_date:
                raise ValidationError(_("The latest shipment date cannot be after the LC expiry."))

    def action_issue(self):
        for lc in self:
            order = lc.import_order_id
            if order and order.payment_method == 'lc' and order.forex_state != 'approved':
                raise UserError(_("Foreign-currency approval is required before this letter of credit can be issued."))
        self.write({'state': 'issued', 'issue_date': fields.Date.context_today(self)})
        self._ie_touch(_('LC issued.'))

    def action_utilise(self):
        self.write({'state': 'utilised'})
        self._ie_touch(_('LC utilised.'))

    def action_close(self):
        self.write({'state': 'closed', 'closure_date': fields.Date.context_today(self)})
        self._ie_touch(_('LC closed.'))

    def _ie_touch(self, detail):
        for lc in self:
            order = lc.import_order_id or lc.export_order_id
            if order:
                self.env['ie.audit.log'].ie_log(order, 'write', detail='%s %s' % (lc.name, detail))

    def _ie_dollar_balance(self):
        """The dollar account this letter of credit is drawn from."""
        self.ensure_one()
        Balance = self.env['ie.dollar.balance']
        balance = Balance.search([
            ('company_id', '=', self.company_id.id),
            ('bank_id', '=', self.bank_id.id),
        ], limit=1)
        if not balance:
            balance = Balance.search([
                ('company_id', '=', self.company_id.id),
                ('bank_id', '=', False),
            ], limit=1)
        if not balance:
            raise UserError(_("Create a dollar balance for %(bank)s before recording this letter of credit.", bank=self.bank_id.display_name))
        return balance

    def _ie_retention_amount(self, balance):
        self.ensure_one()
        return balance._ie_to_currency(
            self.amount, self.currency_id, balance.retention_currency_id, self.issue_date,
        )

    def _ie_draw_dollar_balance(self, sign):
        """Reduce the dollar balance when sign is -1, and put it back when sign is 1."""
        for lc in self:
            balance = lc._ie_dollar_balance()
            balance._ie_add_retention(sign * lc._ie_retention_amount(balance), lc.issue_date)

    def _ie_sync_landed_cost_name(self):
        """Keep each linked landed cost number equal to this letter of credit."""
        if self.env.context.get('ie_skip_lc_sync'):
            return
        Landed = self.env['stock.landed.cost']
        for lc in self.filtered('import_order_id'):
            costs = Landed.search([
                ('ie_import_order_id', '=', lc.import_order_id.id),
                ('name', '!=', lc.name),
            ])
            if costs:
                costs.with_context(ie_skip_lc_sync=True).write({'name': lc.name})

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._ie_draw_dollar_balance(-1)
        records._ie_sync_landed_cost_name()
        return records

    def write(self, vals):
        watched = {'amount', 'currency_id', 'bank_id', 'company_id'} & set(vals)
        if watched:
            self._ie_draw_dollar_balance(1)
        result = super().write(vals)
        if watched:
            self._ie_draw_dollar_balance(-1)
        if 'name' in vals:
            self._ie_sync_landed_cost_name()
        return result

    def unlink(self):
        self._ie_draw_dollar_balance(1)
        return super().unlink()

    @api.model
    def _cron_expiry_alert(self):
        today = fields.Date.context_today(self)
        for company in self.env['res.company'].search([]):
            limit = today + timedelta(days=company.ie_lc_alert_days or 0)
            records = self.search([
                ('company_id', '=', company.id),
                ('state', 'in', ('issued', 'amended')),
                ('expiry_date', '!=', False),
                ('expiry_date', '<=', limit),
            ])
            template = self.env.ref('import_export_control.mail_template_lc_expiry', raise_if_not_found=False)
            for lc in records:
                if lc.expiry_date < today and lc.state != 'expired':
                    lc.state = 'expired'
                lc.activity_schedule('mail.mail_activity_data_todo', summary=_('LC expiry'), user_id=lc.create_uid.id)
                if template:
                    template.send_mail(lc.id, force_send=False)


class IeLcAmendment(models.Model):
    _name = 'ie.lc.amendment'
    _description = 'LC Amendment'
    _order = 'id desc'

    lc_id = fields.Many2one('ie.lc', required=True, ondelete='cascade')
    name = fields.Char(required=True)
    date = fields.Date(default=fields.Date.context_today, required=True)
    amount = fields.Monetary()
    currency_id = fields.Many2one(related='lc_id.currency_id')
    note = fields.Text()
    company_id = fields.Many2one(related='lc_id.company_id', store=True)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records.mapped('lc_id').filtered(lambda lc: lc.state == 'issued').write({'state': 'amended'})
        return records


class IeDollarBalance(models.Model):
    _name = 'ie.dollar.balance'
    _description = 'Dollar Balance'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name, id'

    name = fields.Char(required=True, tracking=True)
    bank_id = fields.Many2one('res.bank', string='Bank', tracking=True)
    retention_currency_id = fields.Many2one(
        'res.currency', string='Retention Currency', required=True,
        default=lambda self: self.env.ref('base.USD', raise_if_not_found=False),
    )
    retention_balance = fields.Monetary(
        string='Retention Balance', currency_field='retention_currency_id', default=0.0, tracking=True,
    )
    current_currency_id = fields.Many2one(
        'res.currency', string='Current Currency', required=True,
        default=lambda self: self.env.company.currency_id,
    )
    current_balance = fields.Monetary(
        string='Current Balance', currency_field='current_currency_id', default=0.0, tracking=True,
    )
    amount = fields.Monetary(
        string='Amount', currency_field='retention_currency_id',
        compute='_compute_amount', store=True,
    )
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    line_ids = fields.One2many('ie.dollar.balance.line', 'balance_id', string='Receipts')

    @api.depends('line_ids.amount', 'line_ids.currency_id', 'line_ids.paid_date', 'retention_currency_id')
    def _compute_amount(self):
        for balance in self:
            balance.amount = sum(line._ie_retention_value() for line in balance.line_ids)

    @api.constrains('company_id', 'bank_id')
    def _check_bank_unique(self):
        for balance in self:
            duplicate = self.search([
                ('id', '!=', balance.id),
                ('company_id', '=', balance.company_id.id),
                ('bank_id', '=', balance.bank_id.id),
            ], limit=1)
            if duplicate:
                raise ValidationError(_("A dollar balance already exists for this bank."))

    def _ie_to_currency(self, amount, from_currency, to_currency, on_date):
        self.ensure_one()
        if not amount or not from_currency or not to_currency or from_currency == to_currency:
            return amount or 0.0
        return from_currency._convert(
            amount, to_currency, self.company_id, on_date or fields.Date.context_today(self),
        )

    def _ie_add_retention(self, delta, on_date):
        """Change the editable balances. A receipt adds; a letter of credit subtracts."""
        self.ensure_one()
        if not delta:
            return
        current_delta = self._ie_to_currency(
            delta, self.retention_currency_id, self.current_currency_id, on_date,
        )
        new_retention = self.retention_balance + delta
        rounding = self.retention_currency_id.rounding or 0.01
        if float_compare(new_retention, 0.0, precision_rounding=rounding) < 0:
            raise UserError(_(
                "Letter of credit amount exceeds the dollar retention balance of %s.",
                self.display_name,
            ))
        self.write({
            'retention_balance': new_retention,
            'current_balance': self.current_balance + current_delta,
        })

    @api.model
    def _ie_load_demo_ahadu(self):
        """One Ahadu Bank balance: retention in USD, current balance in ETB."""
        company = self.env.ref('base.main_company', raise_if_not_found=False)
        if not company:
            return
        if self.search_count([('name', '=', 'Ahadu Bank'), ('company_id', '=', company.id)]):
            return
        bank = self.env['res.bank'].search([('name', '=', 'Ahadu Bank')], limit=1)
        if not bank:
            bank = self.env['res.bank'].sudo().create({
                'name': 'Ahadu Bank',
                'bic': 'AHDUETAA',
                'city': 'Addis Ababa',
                'country': self.env.ref('base.et').id,
            })
        reference = self.env['ie.bank.reference'].search([
            ('name', '=', 'AHD/2026/001'), ('company_id', '=', company.id),
        ], limit=1)
        if not reference:
            reference = self.env['ie.bank.reference'].sudo().create({
                'name': 'AHD/2026/001',
                'company_id': company.id,
            })
        balance = self.with_company(company).sudo().create({
            'name': 'Ahadu Bank',
            'bank_id': bank.id,
            'retention_currency_id': self.env.ref('base.USD').id,
            'current_currency_id': self.env.ref('base.ETB').id,
            'company_id': company.id,
        })
        self.env['ie.dollar.balance.line'].sudo().create({
            'balance_id': balance.id,
            'permit_number': 'NBE-AHD-2026-001',
            'contract': 'AHD-CT-1001',
            'lc_number': 'LC-AHD-1500',
            'bank_reference_id': reference.id,
            'currency_id': self.env.ref('base.USD').id,
            'paid_date': fields.Date.context_today(self),
            'amount': 10000,
        })


class IeBankReference(models.Model):
    _name = 'ie.bank.reference'
    _description = 'Bank Reference'
    _order = 'name'

    name = fields.Char(string='Bank Reference', required=True, index=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ('name_company_uniq', 'unique(name, company_id)', 'This bank reference already exists for the company.'),
    ]


class IeDollarBalanceLine(models.Model):
    _name = 'ie.dollar.balance.line'
    _description = 'Dollar Balance Receipt'
    _order = 'paid_date desc, id desc'

    balance_id = fields.Many2one('ie.dollar.balance', required=True, ondelete='cascade', index=True)
    permit_number = fields.Char(string='Permit Number')
    contract = fields.Char(string='Contract')
    lc_number = fields.Char(string='LC Number')
    bank_reference_id = fields.Many2one(
        'ie.bank.reference', string='Bank Reference', check_company=True,
    )
    currency_id = fields.Many2one(
        'res.currency', string='Currency', required=True,
        default=lambda self: self.env.ref('base.USD', raise_if_not_found=False),
    )
    paid_date = fields.Date(string='Paid Date', default=fields.Date.context_today)
    amount = fields.Monetary(required=True)
    company_id = fields.Many2one(related='balance_id.company_id', store=True)

    def _ie_retention_value(self):
        self.ensure_one()
        balance = self.balance_id
        return balance._ie_to_currency(
            self.amount, self.currency_id, balance.retention_currency_id, self.paid_date,
        )

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        for line in lines:
            line.balance_id._ie_add_retention(line._ie_retention_value(), line.paid_date)
        return lines

    def write(self, vals):
        watched = {'amount', 'currency_id', 'paid_date', 'balance_id'} & set(vals)
        if watched:
            for line in self:
                line.balance_id._ie_add_retention(-line._ie_retention_value(), line.paid_date)
        result = super().write(vals)
        if watched:
            for line in self:
                line.balance_id._ie_add_retention(line._ie_retention_value(), line.paid_date)
        return result

    def unlink(self):
        for line in self:
            line.balance_id._ie_add_retention(-line._ie_retention_value(), line.paid_date)
        return super().unlink()


class IeShipment(models.Model):
    _name = 'ie.shipment'
    _description = 'Shipment'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(required=True, copy=False, default=lambda self: _('New'), readonly=True)
    transport_mode = fields.Selection([
        ('sea', 'Sea'),
        ('air', 'Air'),
        ('road', 'Road'),
        ('rail', 'Rail'),
    ], required=True, default='sea', tracking=True)
    carrier = fields.Char(tracking=True)
    bl_number = fields.Char(string='B/L or AWB No.', tracking=True)
    container_numbers = fields.Char(string='Container Numbers')
    container_available = fields.Boolean(string='Container Available', tracking=True)
    container_booked = fields.Boolean(string='Container Booked', tracking=True)
    booking_reference = fields.Char(string='Booking Reference', tracking=True)
    shipping_line = fields.Char(string='Shipping Line', tracking=True)
    container_type = fields.Selection([
        ('standard', 'Standard'),
        ('reefer', 'Reefer'),
    ], string='Container Type', tracking=True)
    factory_arrival = fields.Date(string='Arrival at Factory', tracking=True)
    port_loading = fields.Char(string='Port of Loading')
    port_destination = fields.Char(string='Destination Port')
    etd = fields.Date(string='ETD', tracking=True)
    eta = fields.Date(string='ETA', tracking=True)
    atd = fields.Date(string='Actual Departure')
    ata = fields.Date(string='Actual Arrival')
    state = fields.Selection([
        ('booked', 'Booked'),
        ('in_transit', 'In Transit'),
        ('arrived', 'Arrived'),
        ('delayed', 'Delayed'),
    ], default='booked', tracking=True)
    is_partial = fields.Boolean(string='Partial Shipment')
    quantity = fields.Float()
    delay_days = fields.Integer(compute='_compute_delay', store=True)
    delay_reason = fields.Text()
    import_order_id = fields.Many2one('ie.import.order', index=True)
    export_order_id = fields.Many2one('ie.export.order', index=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)

    @api.depends('eta', 'ata', 'state')
    def _compute_delay(self):
        today = fields.Date.context_today(self)
        for shipment in self:
            reference = shipment.ata or today
            if shipment.eta and reference > shipment.eta and shipment.state != 'arrived':
                shipment.delay_days = (reference - shipment.eta).days
            else:
                shipment.delay_days = 0

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('ie.shipment') or _('New')
        return super().create(vals_list)

    def _ie_container_ready(self):
        """Sea shipments need the container checklist before an export can ship."""
        self.ensure_one()
        if self.transport_mode != 'sea':
            return True
        return bool(
            self.container_available
            and self.container_booked
            and self.booking_reference
            and self.shipping_line
            and self.container_type
            and self.factory_arrival
        )

    @api.model
    def _cron_delay_alert(self):
        today = fields.Date.context_today(self)
        late = self.search([
            ('state', 'in', ('booked', 'in_transit')),
            ('eta', '!=', False),
            ('eta', '<', today),
        ])
        template = self.env.ref('import_export_control.mail_template_shipment_delay', raise_if_not_found=False)
        for shipment in late:
            shipment.state = 'delayed'
            order = shipment.import_order_id or shipment.export_order_id
            if order:
                shipment.activity_schedule('mail.mail_activity_data_todo', user_id=order.user_id.id, summary=_('Shipment delayed'))
                self.env['ie.audit.log'].ie_log(order, 'exception', detail=_('Shipment %s is delayed.', shipment.name))
            if template:
                template.send_mail(shipment.id, force_send=False)


class IeCustomsClearance(models.Model):
    _name = 'ie.customs.clearance'
    _description = 'Customs Clearance'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(string='Declaration No.', required=True, tracking=True)
    direction = fields.Selection([('import', 'Import'), ('export', 'Export')], required=True, default='import')
    hs_code_id = fields.Many2one('ie.hs.code', string='HS Verification')
    duty_amount = fields.Monetary(string='Duties')
    tax_amount = fields.Monetary(string='Taxes')
    currency_id = fields.Many2one('res.currency', required=True, default=lambda self: self.env.company.currency_id)
    state = fields.Selection([
        ('pending', 'Pending'),
        ('cleared', 'Cleared'),
        ('held', 'Held'),
    ], default='pending', tracking=True)
    clearance_date = fields.Date()
    hold_reason = fields.Text()
    import_order_id = fields.Many2one('ie.import.order', index=True)
    export_order_id = fields.Many2one('ie.export.order', index=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    attachment_ids = fields.Many2many('ir.attachment', string='Documents')

    exemption_applicable = fields.Boolean(string='Exemption or Incentive')
    exemption_type = fields.Selection([
        ('duty', 'Customs Duty'),
        ('tax', 'Tax'),
        ('investment', 'Investment Incentive'),
    ], string='Exemption Type')
    exemption_reference = fields.Char(string='Exemption Reference')
    exemption_status = fields.Selection([
        ('na', 'Not Applicable'),
        ('pending', 'Pending'),
        ('granted', 'Granted'),
        ('refused', 'Refused'),
    ], string='Exemption Status', default='na', tracking=True)

    def action_clear(self):
        for customs in self:
            if customs.exemption_applicable and customs.exemption_status in ('pending', 'refused'):
                raise UserError(_("Customs cannot be cleared while the duty exemption or investment incentive is pending or refused."))
        self.write({'state': 'cleared', 'clearance_date': fields.Date.context_today(self)})

    def action_hold(self):
        for customs in self:
            if not customs.hold_reason:
                raise UserError(_("A hold reason is required."))
            customs.state = 'held'


class IeExportProceeds(models.Model):
    _name = 'ie.export.proceeds'
    _description = 'Export Proceeds'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(required=True, copy=False, default=lambda self: _('New'), readonly=True)
    export_order_id = fields.Many2one('ie.export.order', required=True, ondelete='cascade', index=True)
    currency_id = fields.Many2one(related='export_order_id.currency_id', store=True)
    amount_expected = fields.Monetary(required=True)
    amount_received = fields.Monetary()
    amount_outstanding = fields.Monetary(compute='_compute_outstanding', store=True)
    bank_id = fields.Many2one('res.bank')
    receipt_date = fields.Date()
    expected_date = fields.Date()
    fx_rate = fields.Float(digits=(12, 6), default=1.0)
    payment_id = fields.Many2one('account.payment')
    statement_line_id = fields.Many2one('account.bank.statement.line', string='Bank Statement Line')
    bank_reconciled = fields.Boolean(related='statement_line_id.is_reconciled')
    delay_days = fields.Integer(compute='_compute_delay')
    company_id = fields.Many2one(related='export_order_id.company_id', store=True)
    invoice_id = fields.Many2one('account.move', domain="[('move_type', '=', 'out_invoice')]")

    @api.depends('amount_expected', 'amount_received')
    def _compute_outstanding(self):
        for proceeds in self:
            proceeds.amount_outstanding = proceeds.amount_expected - proceeds.amount_received

    def _compute_delay(self):
        today = fields.Date.context_today(self)
        for proceeds in self:
            if proceeds.expected_date and proceeds.amount_outstanding > 0 and today > proceeds.expected_date:
                proceeds.delay_days = (today - proceeds.expected_date).days
            else:
                proceeds.delay_days = 0

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('ie.export.proceeds') or _('New')
        return super().create(vals_list)

    @api.model
    def _cron_outstanding_alert(self):
        today = fields.Date.context_today(self)
        template = self.env.ref('import_export_control.mail_template_proceeds_delay', raise_if_not_found=False)
        for company in self.env['res.company'].search([]):
            limit = today - timedelta(days=company.ie_proceeds_alert_days or 0)
            records = self.search([
                ('company_id', '=', company.id),
                ('amount_outstanding', '>', 0),
                ('expected_date', '!=', False),
                ('expected_date', '<=', limit),
            ])
            for proceeds in records:
                proceeds.activity_schedule(
                    'mail.mail_activity_data_todo',
                    user_id=proceeds.export_order_id.user_id.id,
                    summary=_('Outstanding export proceeds'),
                )
                if template:
                    template.send_mail(proceeds.id, force_send=False)


class IeTransactionException(models.Model):
    _name = 'ie.transaction.exception'
    _description = 'No-Order Transaction Exception'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(required=True, copy=False, default=lambda self: _('New'), readonly=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    order_type = fields.Selection([('import', 'Import'), ('export', 'Export')], required=True)
    reason = fields.Text(required=True)
    res_model = fields.Char(required=True, index=True)
    res_id = fields.Integer(required=True, index=True)
    requester_id = fields.Many2one('res.users', default=lambda self: self.env.user, required=True)
    approver_id = fields.Many2one('res.users', readonly=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('approved', 'Approved'),
        ('back_registered', 'Back-registered'),
        ('expired', 'Expired'),
        ('refused', 'Refused'),
    ], default='draft', tracking=True)
    deadline = fields.Date()
    back_registration_date = fields.Date()
    import_order_id = fields.Many2one('ie.import.order')
    export_order_id = fields.Many2one('ie.export.order')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('ie.transaction.exception') or _('New')
        records = super().create(vals_list)
        for record in records:
            self.env['ie.audit.log'].ie_log(record, 'exception', detail=record.reason)
        return records

    def action_approve(self):
        for item in self:
            if not self.env.su and not self.env.user.has_group('import_export_control.group_ie_management'):
                raise UserError(_("Only management can approve a transaction exception."))
            if item.requester_id == self.env.user and not self.env.su:
                raise UserError(_("The requester cannot approve the exception."))
            days = item.company_id.ie_backreg_days or 0
            item.write({
                'state': 'approved',
                'approver_id': self.env.user.id,
                'deadline': fields.Date.context_today(self) + timedelta(days=days),
            })
            self.env['ie.audit.log'].ie_log(item, 'override', detail=_('Transaction exception approved.'), reason=item.reason)
        return True

    def action_back_register(self):
        for item in self:
            order = item.import_order_id or item.export_order_id
            if not order or order.state in ('draft', 'cancelled'):
                raise UserError(_("Link an approved import or export order before back-registration."))
            unlocked = ('approved', 'bank_lc', 'shipment', 'received', 'closed') if item.order_type == 'import' else (
                'approved', 'customs_loading', 'shipped', 'proceeds', 'reconciled', 'closed'
            )
            if order.state not in unlocked:
                raise UserError(_("The linked order is not approved yet."))
            item.write({'state': 'back_registered', 'back_registration_date': fields.Date.context_today(self)})
        return True

    @api.model
    def _ie_covers(self, record):
        if not record or not record.id:
            return False
        today = fields.Date.context_today(record)
        return bool(self.search_count([
            ('res_model', '=', record._name),
            ('res_id', '=', record.id),
            ('state', '=', 'approved'),
            ('company_id', '=', record.company_id.id),
            '|', ('deadline', '=', False), ('deadline', '>=', today),
        ]))

    @api.model
    def _cron_back_registration(self):
        today = fields.Date.context_today(self)
        late = self.search([
            ('state', '=', 'approved'),
            ('deadline', '!=', False),
            ('deadline', '<', today),
        ])
        for item in late:
            item.state = 'expired'
            item.activity_schedule(
                'mail.mail_activity_data_todo',
                user_id=item.approver_id.id or item.requester_id.id,
                summary=_('Back-registration overdue'),
            )


class IeOrderRevision(models.Model):
    _name = 'ie.order.revision'
    _description = 'Order Amendment Revision'
    _order = 'id desc'

    import_order_id = fields.Many2one('ie.import.order', ondelete='cascade')
    export_order_id = fields.Many2one('ie.export.order', ondelete='cascade')
    version = fields.Integer(required=True)
    reason = fields.Text(required=True)
    snapshot = fields.Json()
    user_id = fields.Many2one('res.users', required=True)
    date = fields.Datetime(default=fields.Datetime.now, required=True)
    company_id = fields.Many2one('res.company', compute='_compute_company', store=True)

    @api.depends('import_order_id.company_id', 'export_order_id.company_id')
    def _compute_company(self):
        for revision in self:
            order = revision.import_order_id or revision.export_order_id
            revision.company_id = order.company_id

    def _compute_display_name(self):
        for revision in self:
            order = revision.import_order_id or revision.export_order_id
            revision.display_name = '%s v%s' % (order.name if order else '', revision.version)
