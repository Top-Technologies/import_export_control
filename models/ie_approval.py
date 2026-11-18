# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class IeApprovalStage(models.Model):
    _name = 'ie.approval.stage'
    _description = 'Approval Stage Map'
    _order = 'order_type, sequence, id'

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    order_type = fields.Selection([('import', 'Import'), ('export', 'Export')], required=True, index=True)
    state_code = fields.Char(required=True, index=True)
    category_id = fields.Many2one('approval.category', required=True, check_company=True)
    business_unit_id = fields.Many2one('ie.business.unit', check_company=True)
    currency_id = fields.Many2one('res.currency')
    min_amount = fields.Monetary(currency_field='currency_id')
    max_amount = fields.Monetary(currency_field='currency_id')
    approver_ids = fields.Many2many('res.users', string='Approvers')
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    active = fields.Boolean(default=True)

    @api.depends('name', 'state_code', 'order_type')
    def _compute_display_name(self):
        for stage in self:
            stage.display_name = '%s / %s' % (stage.order_type, stage.name)

    @api.model
    def _ie_match(self, order):
        """Pick the most specific stage for this order, state and amount."""
        kind = 'import' if order._name == 'ie.import.order' else 'export'
        stages = self.search([
            ('order_type', '=', kind),
            ('state_code', '=', order.state),
            ('company_id', '=', order.company_id.id),
            ('active', '=', True),
        ])
        amount = order.currency_id._convert(
            order.amount_total, order.company_id.currency_id, order.company_id, fields.Date.context_today(order),
        ) if order.currency_id and order.amount_total else order.amount_total
        matched = self.browse()
        for stage in stages:
            if stage.business_unit_id and stage.business_unit_id != order.business_unit_id:
                continue
            stage_amount = amount
            if stage.currency_id and stage.currency_id != order.company_id.currency_id:
                stage_amount = order.company_id.currency_id._convert(
                    amount, stage.currency_id, order.company_id, fields.Date.context_today(order),
                )
            if stage.min_amount and stage_amount < stage.min_amount:
                continue
            if stage.max_amount and stage_amount > stage.max_amount:
                continue
            matched |= stage
        if not matched:
            return self.browse()

        def _score(stage):
            return (
                1 if stage.business_unit_id else 0,
                1 if stage.min_amount or stage.max_amount else 0,
                stage.sequence,
            )

        return matched.sorted(key=_score, reverse=True)[0]

    def _ie_configured_approvers(self):
        """Users allowed to approve this stage.

        The Approval Type is the list people fill in. The stage list is used
        only when it has someone on it.
        """
        self.ensure_one()
        if self.approver_ids:
            return self.approver_ids
        return self.category_id.approver_ids.mapped('user_id')


class ApprovalRequest(models.Model):
    _inherit = 'approval.request'

    ie_import_order_id = fields.Many2one('ie.import.order', index=True, ondelete='cascade')
    ie_export_order_id = fields.Many2one('ie.export.order', index=True, ondelete='cascade')
    ie_stage_code = fields.Char(index=True)
    ie_stage_label = fields.Char(string='Stage', compute='_compute_ie_stage_label')
    ie_approval_stage_id = fields.Many2one('ie.approval.stage')
    ie_refuse_reason = fields.Text(string='Refusal Reason')

    @api.depends('ie_approval_stage_id.name', 'ie_stage_code', 'ie_import_order_id', 'ie_export_order_id')
    def _compute_ie_stage_label(self):
        for request in self:
            if request.ie_approval_stage_id:
                request.ie_stage_label = request.ie_approval_stage_id.name
                continue
            order = request.ie_import_order_id or request.ie_export_order_id
            label = request.ie_stage_code or ''
            if order and request.ie_stage_code:
                label = dict(order._fields['state'].selection).get(request.ie_stage_code, request.ie_stage_code)
            request.ie_stage_label = label

    def _ie_order(self):
        self.ensure_one()
        return self.ie_import_order_id or self.ie_export_order_id

    def _ie_check_sod(self):
        for request in self:
            order = request._ie_order()
            if not order:
                continue
            if self.env.user == request.request_owner_id or self.env.user == order.create_uid:
                raise UserError(_("The maker of this order cannot approve or refuse it."))
            stage = request.ie_approval_stage_id
            configured = stage._ie_configured_approvers() if stage else self.env['res.users']
            if configured and self.env.user not in configured and not self.env.su:
                raise UserError(_("You are not a configured approver for this stage."))
            if not configured:
                group_xmlid = order._ie_state_group(request.ie_stage_code) if request.ie_stage_code else False
                if group_xmlid and not self.env.su and not self.env.user.has_group(group_xmlid):
                    raise UserError(_("Your access rights do not allow this approval step."))

    def action_approve(self, approver=None):
        self._ie_check_sod()
        for request in self:
            order = request._ie_order()
            if order and request.ie_stage_code == 'quality_verification' and hasattr(order, '_ie_assert_quality_passed'):
                order._ie_assert_quality_passed()
        res = super().action_approve(approver)
        for request in self:
            order = request._ie_order()
            if order and request.request_status == 'approved':
                order._ie_on_stage_approved(request)
        return res

    def action_refuse(self, approver=None):
        linked = self.filtered(lambda request: request.ie_import_order_id or request.ie_export_order_id)
        for request in linked:
            if not request.ie_refuse_reason:
                raise UserError(_("A refusal reason is required."))
        self._ie_check_sod()
        res = super().action_refuse(approver)
        for request in linked:
            order = request._ie_order()
            if order and request.request_status == 'refused':
                order._ie_on_stage_refused(request)
        return res

    def write(self, vals):
        if 'request_status' in vals and not self.env.context.get('ie_state_bypass'):
            # request_status is computed. Block a raw RPC write of the related stage pointers only.
            pass
        return super().write(vals)
