# -*- coding: utf-8 -*-
"""Shared workflow helpers for import and export orders.

State changes go through ``_ie_set_state``. A direct write of ``state`` is
refused so the statusbar cannot be edited from the UI or the RPC.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .ie_const import (
    EXPORT_APPROVAL_STATES,
    EXPORT_NEXT,
    EXPORT_PREV,
    EXPORT_STATE_GROUPS,
    IMPORT_APPROVAL_STATES,
    IMPORT_NEXT,
    IMPORT_PREV,
    IMPORT_STATE_GROUPS,
)

SKIPPED_AUDIT_TYPES = ('one2many', 'many2many', 'binary', 'html', 'json')


class IeWorkflowMixin(models.AbstractModel):
    _name = 'ie.workflow.mixin'
    _description = 'Import and Export Workflow'

    analytic_business_unit_plan_id = fields.Many2one(
        'account.analytic.plan', compute='_compute_ie_analytic_plans',
    )
    analytic_department_plan_id = fields.Many2one(
        'account.analytic.plan', compute='_compute_ie_analytic_plans',
    )
    analytic_warehouse_plan_id = fields.Many2one(
        'account.analytic.plan', compute='_compute_ie_analytic_plans',
    )
    analytic_business_unit_id = fields.Many2one(
        'account.analytic.account', string='Business Unit', check_company=True,
        domain="[('plan_id', '=', analytic_business_unit_plan_id)]",
    )
    analytic_department_id = fields.Many2one(
        'account.analytic.account', string='Department', check_company=True,
        domain="[('plan_id', '=', analytic_department_plan_id)]",
    )
    analytic_warehouse_id = fields.Many2one(
        'account.analytic.account', string='Warehouse', check_company=True,
        domain="[('plan_id', '=', analytic_warehouse_plan_id)]",
    )

    def _ie_analytic_plan(self, xmlid, names):
        plan = self.env.ref(xmlid, raise_if_not_found=False) if xmlid else self.env['account.analytic.plan']
        if plan:
            return plan
        return self.env['account.analytic.plan'].search([
            ('name', 'in', names),
            ('parent_id', '=', False),
        ], order='id', limit=1)

    @api.depends_context('uid')
    def _compute_ie_analytic_plans(self):
        resolved = {
            'analytic_business_unit_plan_id': self._ie_analytic_plan(False, ['Business Unit']),
            'analytic_department_plan_id': self._ie_analytic_plan(
                'import_export_control.analytic_plan_department', ['Department'],
            ),
            'analytic_warehouse_plan_id': self._ie_analytic_plan(False, ['Warehouse']),
        }
        for order in self:
            for field_name, plan in resolved.items():
                order[field_name] = plan

    def _ie_analytic_distribution(self):
        """One distribution covering every selected analytic account."""
        self.ensure_one()
        accounts = (
            self.analytic_business_unit_id
            | self.analytic_department_id
            | self.analytic_warehouse_id
        )
        if 'analytic_export_id' in self._fields:
            accounts |= self.analytic_export_id
        if not accounts:
            return {}
        key = ','.join(str(account_id) for account_id in sorted(accounts.ids))
        return {key: 100}

    def _ie_push_analytic_distribution(self):
        """Copy the order accounts onto related sales, purchases, and draft invoices."""
        for order in self:
            distribution = order._ie_analytic_distribution()
            if not distribution:
                continue
            sales = getattr(order, 'sale_ids', self.env['sale.order'])
            purchases = getattr(order, 'purchase_ids', self.env['purchase.order'])
            invoices = getattr(order, 'invoice_ids', self.env['account.move'])
            for sale in sales.filtered(lambda document: document.state != 'cancel'):
                sale.order_line.filtered(lambda line: not line.display_type).write({
                    'analytic_distribution': distribution,
                })
            for purchase in purchases.filtered(lambda document: document.state != 'cancel'):
                purchase.order_line.filtered(lambda line: not line.display_type).write({
                    'analytic_distribution': distribution,
                })
            for move in invoices.filtered(lambda document: document.state == 'draft'):
                move.invoice_line_ids.filtered(lambda line: line.display_type == 'product').write({
                    'analytic_distribution': distribution,
                })

    def _ie_order_kind(self):
        self.ensure_one()
        return 'import' if self._name == 'ie.import.order' else 'export'

    def _ie_flow_next(self):
        return IMPORT_NEXT if self._ie_order_kind() == 'import' else EXPORT_NEXT

    def _ie_flow_prev(self):
        return IMPORT_PREV if self._ie_order_kind() == 'import' else EXPORT_PREV

    def _ie_approval_states(self):
        return IMPORT_APPROVAL_STATES if self._ie_order_kind() == 'import' else EXPORT_APPROVAL_STATES

    def _ie_state_group(self, state):
        mapping = IMPORT_STATE_GROUPS if self._ie_order_kind() == 'import' else EXPORT_STATE_GROUPS
        return mapping.get(state)

    def _ie_check_group(self, xmlid):
        if self.env.su:
            return
        if xmlid and not self.env.user.has_group(xmlid):
            raise UserError(_("You are not allowed to perform this step."))

    def _ie_check_compliance_user(self):
        """Control & Compliance and the module administrator may complete the gate.

        The user who created the order is allowed.
        """
        if self.env.su:
            return
        user = self.env.user
        if user.has_group('import_export_control.group_ie_compliance') or user.has_group('import_export_control.group_ie_admin'):
            return
        raise UserError(_("Only Control & Compliance or an administrator can complete this step."))

    def _ie_check_finance_user(self):
        """Finance and the module administrator may approve foreign currency.

        The user who created the order is allowed.
        """
        if self.env.su:
            return
        user = self.env.user
        if user.has_group('import_export_control.group_ie_finance') or user.has_group('import_export_control.group_ie_admin'):
            return
        raise UserError(_("Only Finance or an administrator can approve foreign currency."))

    def _ie_check_not_maker(self):
        self.ensure_one()
        if self.env.su:
            return
        if self.create_uid == self.env.user:
            raise UserError(_("The user who created the order cannot review, check or approve it."))

    def _ie_log(self, action, detail=None, reason=None, old_state=None, new_state=None, changes=None):
        for record in self:
            self.env['ie.audit.log'].ie_log(
                record, action, detail=detail, reason=reason,
                old_state=old_state, new_state=new_state, changes=changes,
            )

    def _ie_set_state(self, new_state, reason=None):
        for order in self:
            old = order.state
            if old == new_state:
                continue
            order.with_context(ie_state_bypass=True, ie_skip_audit=True).write({'state': new_state})
            order._ie_log('transition', detail='%s -> %s' % (old, new_state), reason=reason, old_state=old, new_state=new_state)
            order.message_post(body=_("Status changed from %(old)s to %(new)s. %(reason)s", old=old, new=new_state, reason=reason or ''))

    def _ie_require_documents(self, target_state):
        """Document lines stay on the order. They do not block a state change."""
        return True

    def _ie_prepare_document_lines(self):
        kind = 'import' if self._name == 'ie.import.order' else 'export'
        types = self.env['ie.document.type'].search([
            ('active', '=', True),
            ('order_type', 'in', (kind, 'both')),
            '|', ('company_id', '=', False), ('company_id', '=', self.company_id.id),
        ])
        inverse = 'import_order_id' if kind == 'import' else 'export_order_id'
        return [(0, 0, {
            'document_type_id': doc_type.id,
            'sequence': doc_type.sequence,
            'blocking_state': doc_type.blocking_state,
            'required': doc_type.required,
            inverse: self.id,
        }) for doc_type in types]

    def _ie_open_approval(self):
        """Approval stages are not required. The order moves without an Approvals request."""
        return True

    def _ie_on_stage_approved(self, request):
        self.ensure_one()
        if self.state != request.ie_stage_code:
            return
        self._ie_log('approval', detail=_('Approval request %s was approved.', request.name), old_state=self.state)
        nxt = self._ie_flow_next().get(self.state)
        if not nxt:
            return
        self._ie_enter_state(nxt, reason=_('Approved in Approvals.'))

    def _ie_on_stage_refused(self, request):
        self.ensure_one()
        reason = request.ie_refuse_reason or _('Refused in Approvals.')
        self._ie_log('rejection', detail=_('Approval request %s was refused.', request.name), reason=reason, old_state=self.state)
        self.with_context(ie_state_bypass=True, ie_skip_audit=True).write({'hold_state': self.state})
        self._ie_set_state('hold', reason=reason)
        self.activity_schedule(
            'mail.mail_activity_data_todo',
            user_id=self.create_uid.id,
            summary=_('Approval refused'),
            note=reason,
        )

    def _ie_enter_state(self, new_state, reason=None):
        """Validate the entry conditions of ``new_state`` and move there."""
        self.ensure_one()
        self._ie_require_documents(new_state)
        self._ie_before_enter(new_state)
        self._ie_set_state(new_state, reason=reason)
        self._ie_after_enter(new_state)

    def _ie_before_enter(self, new_state):
        """Hook for stock, quality and LC checks. Override on the concrete order."""
        return True

    def _ie_after_enter(self, new_state):
        if new_state in self._ie_approval_states():
            self._ie_open_approval()
        elif new_state == 'compliance':
            self._ie_open_compliance_gate()
            group = self.env.ref('import_export_control.group_ie_compliance', raise_if_not_found=False)
            users = group.users.filtered(lambda user: user.company_ids & self.company_id) if group else self.env['res.users']
            for user in users[:5]:
                self.activity_schedule(
                    'mail.mail_activity_data_todo',
                    user_id=user.id,
                    summary=_('Compliance gate'),
                )

    def _ie_open_compliance_gate(self):
        return True

    def _ie_cancel_open_approvals(self):
        requests = self.env['approval.request'].search([
            ('request_status', 'in', ('new', 'pending')),
            '|',
            ('ie_import_order_id', 'in', self.ids),
            ('ie_export_order_id', 'in', self.ids),
        ])
        if requests:
            requests.action_cancel()

    def action_next_stage(self):
        """Move the order one step forward. Draft still uses the submit checks."""
        for order in self:
            if order.state == 'draft':
                order.action_submit()
                continue
            nxt = order._ie_flow_next().get(order.state)
            if not nxt:
                raise UserError(_("This order has no next stage."))
            order._ie_enter_state(nxt, reason=_('Moved to the next stage.'))
        return True

    def action_return_previous(self, reason):
        if not reason:
            raise UserError(_("A reason is required to return the order."))
        for order in self:
            order._ie_check_not_maker()
            prev = order._ie_flow_prev().get(order.state)
            if not prev:
                raise UserError(_("This order cannot be returned from %s.", order.state))
            group = order._ie_state_group(order.state)
            order._ie_check_group(group)
            order._ie_cancel_open_approvals()
            order._ie_release_side_effects(prev)
            order._ie_set_state(prev, reason=reason)
            if prev in order._ie_approval_states():
                order._ie_open_approval()
        return True

    def action_hold(self, reason):
        if not reason:
            raise UserError(_("A reason is required to put the order on hold."))
        for order in self:
            if order.state in ('closed', 'cancelled', 'hold'):
                raise UserError(_("This order cannot be put on hold."))
            order._ie_check_compliance_user()
            order.with_context(ie_state_bypass=True, ie_skip_audit=True).write({'hold_state': order.state})
            order._ie_cancel_open_approvals()
            order._ie_set_state('hold', reason=reason)
        return True

    def action_resume(self, reason=None):
        for order in self:
            if order.state != 'hold' or not order.hold_state:
                raise UserError(_("Only orders on hold can be resumed."))
            order._ie_check_group('import_export_control.group_ie_compliance')
            target = order.hold_state
            order._ie_set_state(target, reason=reason or _('Resumed from hold.'))
            if target in order._ie_approval_states():
                order._ie_open_approval()
        return True

    def _ie_release_side_effects(self, target_state):
        return True

    def write(self, vals):
        if 'state' in vals and not self.env.context.get('ie_state_bypass'):
            raise UserError(_("Status is controlled by the workflow and cannot be edited directly."))
        tracked = [name for name in vals if name in self._fields and self._fields[name].type not in SKIPPED_AUDIT_TYPES and name != 'state']
        before = {}
        if tracked and not self.env.context.get('ie_skip_audit'):
            for record in self:
                before[record.id] = {name: record[name] for name in tracked}
        res = super().write(vals)
        if before:
            for record in self:
                changes = {}
                for name, old in before.get(record.id, {}).items():
                    new = record[name]
                    old_value = old.id if getattr(old, '_name', None) else old
                    new_value = new.id if getattr(new, '_name', None) else new
                    if old_value != new_value:
                        changes[name] = {'old': str(old), 'new': str(new)}
                if changes:
                    record._ie_log('write', detail=', '.join(changes), changes=changes)
        analytic_fields = {
            'analytic_business_unit_id', 'analytic_department_id',
            'analytic_warehouse_id', 'analytic_export_id',
        }
        if analytic_fields.intersection(vals):
            self._ie_push_analytic_distribution()
        return res

    def unlink(self):
        for record in self:
            if 'state' in record._fields and record.state != 'draft':
                raise UserError(_("Processed orders cannot be deleted. Cancel or archive them."))
            self.env['ie.audit.log'].ie_log(record, 'unlink', detail=record.display_name)
        return super().unlink()

    @api.depends('document_line_ids.required', 'document_line_ids.attached')
    def _compute_doc_completeness(self):
        for order in self:
            required = order.document_line_ids.filtered('required')
            if not required:
                order.doc_completeness = 100.0
            else:
                done = required.filtered('attached')
                order.doc_completeness = 100.0 * len(done) / len(required)
