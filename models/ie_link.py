# -*- coding: utf-8 -*-
"""No-order-no-transaction guards and links onto standard Odoo documents.

Enforcement runs in Python before the standard confirm, validate or post
method. A company can switch it off. An approved ie.transaction.exception
covers one document until its back-registration deadline.
"""
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .ie_const import EXPORT_UNLOCKED, IMPORT_UNLOCKED

IE_ATTACHMENT_MODELS = {
    'ie.import.order',
    'ie.export.order',
    'ie.compliance.gate',
    'ie.lc',
    'ie.shipment',
    'ie.customs.clearance',
    'ie.export.proceeds',
}


def _ie_guard(record, kind):
    """Block a flagged document when its order is missing or not approved."""
    if record.env.context.get('ie_skip_enforce'):
        return
    company = record.company_id
    if not company or not company.ie_enforcement:
        return
    if record.env['ie.transaction.exception']._ie_covers(record):
        return
    if kind == 'import':
        if not record.ie_is_import and not record.ie_import_order_id:
            return
        order = record.ie_import_order_id
        if not order or order.state not in IMPORT_UNLOCKED:
            raise UserError(_("No approved import order: this import transaction is blocked."))
        if order.payment_method == 'lc' and record._name in ('account.move', 'account.payment'):
            usable = order.lc_ids.filtered(lambda lc: lc.state in ('issued', 'amended', 'utilised', 'closed'))
            if not usable:
                raise UserError(_("Payment method is LC. A valid letter of credit is required before posting."))
        if record._name == 'account.payment' and order.create_uid == record.env.user and not record.env.su:
            raise UserError(_("The import order creator cannot release the payment."))
        return
    if not record.ie_is_export and not record.ie_export_order_id:
        return
    order = record.ie_export_order_id
    if not order or order.state not in EXPORT_UNLOCKED:
        raise UserError(_("No approved export order: this export transaction is blocked."))
    if record._name == 'stock.picking':
        failed = record.env['quality.check'].search_count([
            ('ie_export_order_id', '=', order.id),
            ('quality_state', '=', 'fail'),
        ])
        if failed:
            raise UserError(_("Quality was rejected. The export shipment cannot be validated."))
        if not order.reservation_picking_id or order.reservation_picking_id.state == 'cancel':
            raise UserError(_("Reserved stock is required before the export delivery is validated."))
    if record._name == 'account.payment' and order.create_uid == record.env.user and not record.env.su:
        raise UserError(_("The export order creator cannot release the payment."))


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    ie_import_order_id = fields.Many2one('ie.import.order', string='Import Order', index=True, copy=False)
    ie_is_import = fields.Boolean(string='Import Transaction', copy=False)
    ie_analytic_business_unit_id = fields.Many2one(
        'account.analytic.account', string='Business Unit',
        related='ie_import_order_id.analytic_business_unit_id',
    )
    ie_analytic_department_id = fields.Many2one(
        'account.analytic.account', string='Department',
        related='ie_import_order_id.analytic_department_id',
    )
    ie_analytic_warehouse_id = fields.Many2one(
        'account.analytic.account', string='Warehouse',
        related='ie_import_order_id.analytic_warehouse_id',
    )

    def button_confirm(self):
        for order in self:
            _ie_guard(order, 'import')
        return super().button_confirm()

    def button_approve(self, force=False):
        for order in self:
            _ie_guard(order, 'import')
        return super().button_approve(force=force)

    def _prepare_invoice(self):
        vals = super()._prepare_invoice()
        if self.ie_import_order_id:
            vals['ie_import_order_id'] = self.ie_import_order_id.id
            vals['ie_is_import'] = True
            vals['ref'] = self.ie_import_order_id.name
        return vals

    def _prepare_picking(self):
        vals = super()._prepare_picking()
        if self.ie_import_order_id:
            vals['ie_import_order_id'] = self.ie_import_order_id.id
            vals['ie_is_import'] = True
            vals['origin'] = '%s / %s' % (vals.get('origin') or self.name, self.ie_import_order_id.name)
        return vals


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    ie_export_order_id = fields.Many2one('ie.export.order', string='Export Order', index=True, copy=False)
    ie_is_export = fields.Boolean(string='Export Transaction', copy=False)
    ie_analytic_business_unit_id = fields.Many2one(
        'account.analytic.account', string='Business Unit',
        related='ie_export_order_id.analytic_business_unit_id',
    )
    ie_analytic_department_id = fields.Many2one(
        'account.analytic.account', string='Department',
        related='ie_export_order_id.analytic_department_id',
    )
    ie_analytic_warehouse_id = fields.Many2one(
        'account.analytic.account', string='Warehouse',
        related='ie_export_order_id.analytic_warehouse_id',
    )
    ie_analytic_export_id = fields.Many2one(
        'account.analytic.account', string='Export Reference',
        related='ie_export_order_id.analytic_export_id',
    )

    def action_confirm(self):
        for order in self:
            _ie_guard(order, 'export')
        linked = self.filtered('ie_export_order_id')
        plain = self - linked
        res = True
        if plain:
            res = super(SaleOrder, plain).action_confirm()
        if linked:
            # The export order already reserved the quantity. Do not launch a second delivery.
            res = super(SaleOrder, linked.with_context(ie_skip_stock_rule=True)).action_confirm()
            for order in linked:
                picking = order.ie_export_order_id.reservation_picking_id
                if picking and picking.state != 'cancel':
                    picking.write({
                        'sale_id': order.id,
                        'ie_export_order_id': order.ie_export_order_id.id,
                        'ie_is_export': True,
                    })
        return res

    def _prepare_invoice(self):
        vals = super()._prepare_invoice()
        if self.ie_export_order_id:
            vals['ie_export_order_id'] = self.ie_export_order_id.id
            vals['ie_is_export'] = True
            vals['ref'] = self.ie_export_order_id.name
        return vals


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    def _action_launch_stock_rule(self, previous_product_uom_qty=False):
        if self.env.context.get('ie_skip_stock_rule'):
            return True
        return super()._action_launch_stock_rule(previous_product_uom_qty=previous_product_uom_qty)


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    ie_import_order_id = fields.Many2one('ie.import.order', string='Import Order', index=True, copy=False)
    ie_export_order_id = fields.Many2one('ie.export.order', string='Export Order', index=True, copy=False)
    ie_is_import = fields.Boolean(string='Import Receipt', copy=False)
    ie_is_export = fields.Boolean(string='Export Delivery', copy=False)

    def button_validate(self):
        for picking in self:
            if picking.picking_type_id.code == 'incoming':
                _ie_guard(picking, 'import')
                if picking.ie_import_order_id:
                    failed = self.env['quality.check'].search_count([
                        ('ie_import_order_id', '=', picking.ie_import_order_id.id),
                        ('quality_state', '=', 'fail'),
                    ])
                    if failed:
                        raise UserError(_("A failed quality check blocks acceptance of this receipt."))
            elif picking.picking_type_id.code == 'outgoing':
                _ie_guard(picking, 'export')
        return super().button_validate()


class AccountMove(models.Model):
    _inherit = 'account.move'

    ie_import_order_id = fields.Many2one('ie.import.order', string='Import Order', index=True, copy=False)
    ie_export_order_id = fields.Many2one('ie.export.order', string='Export Order', index=True, copy=False)
    ie_is_import = fields.Boolean(copy=False)
    ie_is_export = fields.Boolean(copy=False)
    ie_analytic_business_unit_id = fields.Many2one(
        'account.analytic.account', string='Business Unit', compute='_compute_ie_analytic_accounts',
    )
    ie_analytic_department_id = fields.Many2one(
        'account.analytic.account', string='Department', compute='_compute_ie_analytic_accounts',
    )
    ie_analytic_warehouse_id = fields.Many2one(
        'account.analytic.account', string='Warehouse', compute='_compute_ie_analytic_accounts',
    )
    ie_analytic_export_id = fields.Many2one(
        'account.analytic.account', string='Export Reference', compute='_compute_ie_analytic_accounts',
    )

    @api.depends(
        'ie_import_order_id.analytic_business_unit_id',
        'ie_import_order_id.analytic_department_id',
        'ie_import_order_id.analytic_warehouse_id',
        'ie_export_order_id.analytic_business_unit_id',
        'ie_export_order_id.analytic_department_id',
        'ie_export_order_id.analytic_warehouse_id',
        'ie_export_order_id.analytic_export_id',
    )
    def _compute_ie_analytic_accounts(self):
        for move in self:
            order = move.ie_import_order_id or move.ie_export_order_id
            move.ie_analytic_business_unit_id = order.analytic_business_unit_id if order else False
            move.ie_analytic_department_id = order.analytic_department_id if order else False
            move.ie_analytic_warehouse_id = order.analytic_warehouse_id if order else False
            move.ie_analytic_export_id = move.ie_export_order_id.analytic_export_id

    @api.model_create_multi
    def create(self, vals_list):
        moves = super().create(vals_list)
        moves._ie_apply_order_analytic()
        return moves

    def write(self, vals):
        result = super().write(vals)
        if not self.env.context.get('ie_skip_analytic') and {'ie_import_order_id', 'ie_export_order_id'}.intersection(vals):
            self._ie_apply_order_analytic()
        return result

    def _ie_apply_order_analytic(self):
        for move in self.filtered(lambda document: document.state == 'draft'):
            order = move.ie_import_order_id or move.ie_export_order_id
            if not order:
                continue
            distribution = order._ie_analytic_distribution()
            if not distribution:
                continue
            lines = move.invoice_line_ids.filtered(lambda line: line.display_type == 'product')
            if lines:
                lines.with_context(ie_skip_analytic=True).write({'analytic_distribution': distribution})

    def action_post(self):
        for move in self:
            if move.move_type in ('in_invoice', 'in_refund'):
                _ie_guard(move, 'import')
            elif move.move_type in ('out_invoice', 'out_refund'):
                _ie_guard(move, 'export')
        return super().action_post()


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    ie_import_order_id = fields.Many2one('ie.import.order', string='Import Order', index=True, copy=False)
    ie_export_order_id = fields.Many2one('ie.export.order', string='Export Order', index=True, copy=False)
    ie_is_import = fields.Boolean(copy=False)
    ie_is_export = fields.Boolean(copy=False)
    ie_fx_rate = fields.Float(string='IE FX Rate', digits=(12, 6))
    ie_statement_line_id = fields.Many2one('account.bank.statement.line', string='Bank Statement Line')

    def action_post(self):
        for payment in self:
            if payment.ie_is_import or payment.ie_import_order_id:
                _ie_guard(payment, 'import')
            if payment.ie_is_export or payment.ie_export_order_id:
                _ie_guard(payment, 'export')
        return super().action_post()


class AccountPaymentRegister(models.TransientModel):
    _inherit = 'account.payment.register'

    def _create_payment_vals_from_wizard(self, batch_result):
        vals = super()._create_payment_vals_from_wizard(batch_result)
        moves = self.line_ids.move_id
        imports = moves.mapped('ie_import_order_id')
        exports = moves.mapped('ie_export_order_id')
        if len(imports) == 1:
            vals['ie_import_order_id'] = imports.id
            vals['ie_is_import'] = True
        if len(exports) == 1:
            vals['ie_export_order_id'] = exports.id
            vals['ie_is_export'] = True
        return vals


class StockLandedCost(models.Model):
    _inherit = 'stock.landed.cost'

    ie_import_order_id = fields.Many2one('ie.import.order', string='Import Order', index=True, copy=False)


class QualityPoint(models.Model):
    _inherit = 'quality.point'

    ie_import_order_id = fields.Many2one('ie.import.order', string='Import Order')
    ie_export_order_id = fields.Many2one('ie.export.order', string='Export Order')


class QualityCheck(models.Model):
    _inherit = 'quality.check'

    ie_import_order_id = fields.Many2one('ie.import.order', string='Import Order', index=True)
    ie_export_order_id = fields.Many2one('ie.export.order', string='Export Order', index=True)
    ie_moisture = fields.Float(string='Moisture %')
    ie_purity = fields.Float(string='Purity %')
    ie_foreign_matter = fields.Float(string='Foreign Matter %')
    ie_certificate_no = fields.Char(string='Certificate No.')
    ie_result = fields.Selection([
        ('pass', 'Pass'),
        ('fail', 'Fail'),
        ('conditional', 'Conditional'),
    ], string='Import/Export Result')

    def do_pass(self):
        res = super().do_pass()
        self.filtered(lambda check: not check.ie_result).write({'ie_result': 'pass'})
        return res

    def do_fail(self):
        res = super().do_fail()
        for check in self:
            check.ie_result = 'fail'
            if (check.ie_import_order_id or check.ie_export_order_id) and not check.alert_ids:
                self.env['quality.alert'].create({
                    'name': _('Quality rejection %s', check.name or check.title or ''),
                    'product_tmpl_id': check.product_id.product_tmpl_id.id,
                    'product_id': check.product_id.id,
                    'lot_id': check.lot_id.id,
                    'picking_id': check.picking_id.id,
                    'check_id': check.id,
                    'team_id': check.team_id.id,
                    'company_id': check.company_id.id,
                    'ie_import_order_id': check.ie_import_order_id.id,
                    'ie_export_order_id': check.ie_export_order_id.id,
                    'description': _('Quality check failed for the linked import or export order.'),
                })
            order = check.ie_import_order_id or check.ie_export_order_id
            if order:
                self.env['ie.audit.log'].ie_log(order, 'exception', detail=_('Quality check %s failed.', check.name))
        return res


class QualityAlert(models.Model):
    _inherit = 'quality.alert'

    ie_import_order_id = fields.Many2one('ie.import.order', string='Import Order', index=True)
    ie_export_order_id = fields.Many2one('ie.export.order', string='Export Order', index=True)


class IrAttachment(models.Model):
    _inherit = 'ir.attachment'

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._ie_audit_linked('upload')
        records._ie_mirror_documents()
        return records

    def unlink(self):
        self._ie_audit_linked('delete')
        return super().unlink()

    def _ie_audit_linked(self, verb):
        for attachment in self:
            if attachment.res_model in IE_ATTACHMENT_MODELS and attachment.res_id:
                record = self.env[attachment.res_model].browse(attachment.res_id).exists()
                if record:
                    self.env['ie.audit.log'].ie_log(
                        record, 'attachment', detail=_('Attachment %(verb)s: %(name)s', verb=verb, name=attachment.name),
                    )

    def _ie_mirror_documents(self):
        """Copy the file into Documents when that app is installed. Never required."""
        if 'documents.document' not in self.env:
            return
        document_model = self.env['documents.document'].sudo()
        folder = document_model.search([('name', '=', 'Import & Export'), ('type', '=', 'folder')], limit=1)
        if not folder:
            try:
                folder = document_model.create({
                    'name': 'Import & Export',
                    'type': 'folder',
                    'owner_id': self.env.ref('base.user_root').id,
                })
            except (UserError, ValidationError, AccessError):
                return
        for attachment in self.filtered(lambda item: item.res_model in IE_ATTACHMENT_MODELS and item.res_id):
            if document_model.search_count([('attachment_id', '=', attachment.id)]):
                continue
            try:
                document_model.create({
                    'name': attachment.name or _('Document'),
                    'type': 'binary',
                    'folder_id': folder.id,
                    'attachment_id': attachment.id,
                    'res_model': attachment.res_model,
                    'res_id': attachment.res_id,
                })
            except (UserError, ValidationError, AccessError):
                continue


class ApprovalRequestCron(models.Model):
    _inherit = 'approval.request'

    @api.model
    def _cron_ie_escalation(self):
        from datetime import timedelta
        now = fields.Datetime.now()
        template = self.env.ref('import_export_control.mail_template_stage_approval', raise_if_not_found=False)
        pending = self.search([
            ('request_status', '=', 'pending'),
            ('ie_stage_code', '!=', False),
            ('date_confirmed', '!=', False),
        ])
        for request in pending:
            delay = request.company_id.ie_approval_delay_days or 2
            if request.date_confirmed <= now - timedelta(days=delay):
                request.activity_schedule(
                    'mail.mail_activity_data_todo',
                    summary=_('Overdue approval'),
                    user_id=request.approver_ids.filtered(lambda line: line.status == 'pending')[:1].user_id.id or request.request_owner_id.id,
                )
                if template:
                    template.send_mail(request.id, force_send=False)


class IeExportOrderCron(models.Model):
    _inherit = 'ie.export.order'

    @api.model
    def _cron_contract_expiry(self):
        today = fields.Date.context_today(self)
        orders = self.search([
            ('contract_expiry', '!=', False),
            ('contract_expiry', '<', today),
            ('state', 'not in', ('cancelled', 'closed')),
        ])
        template = self.env.ref('import_export_control.mail_template_contract_expiry', raise_if_not_found=False)
        for order in orders:
            order.activity_schedule('mail.mail_activity_data_todo', user_id=order.user_id.id, summary=_('Export contract expired'))
            if template:
                template.send_mail(order.id, force_send=False)
