# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.exceptions import UserError


class IeReasonWizard(models.TransientModel):
    _name = 'ie.reason.wizard'
    _description = 'Import / Export Reason'

    reason = fields.Text(required=True)
    action_code = fields.Selection([
        ('return', 'Return'),
        ('hold', 'Hold'),
        ('resume', 'Resume'),
        ('cancel', 'Cancel'),
        ('amend', 'Amend'),
        ('override', 'Stock Override'),
        ('refuse', 'Refuse Approval'),
    ], required=True)
    import_order_id = fields.Many2one('ie.import.order')
    export_order_id = fields.Many2one('ie.export.order')
    approval_request_id = fields.Many2one('approval.request')

    def action_apply(self):
        self.ensure_one()
        if not self.reason:
            raise UserError(_("A reason is required."))
        order = self.import_order_id or self.export_order_id
        if self.action_code == 'refuse':
            request = self.approval_request_id
            if not request:
                raise UserError(_("Select the approval request to refuse."))
            request.ie_refuse_reason = self.reason
            return request.action_refuse()
        if not order:
            raise UserError(_("Select the order."))
        if self.action_code == 'return':
            return order.action_return_previous(self.reason)
        if self.action_code == 'hold':
            return order.action_hold(self.reason)
        if self.action_code == 'resume':
            return order.action_resume(self.reason)
        if self.action_code == 'cancel':
            return order.action_cancel(self.reason)
        if self.action_code == 'amend':
            return order.action_amend(self.reason)
        if self.action_code == 'override':
            return order.action_stock_override(self.reason)
        raise UserError(_("Unknown action."))
