# -*- coding: utf-8 -*-
"""Immutable audit log.

Entries are created with sudo from business methods. write() and unlink()
refuse every user, including administrators, unless the context key
``ie_audit_archive`` is set by an explicit system archival call.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class IeAuditLog(models.Model):
    _name = 'ie.audit.log'
    _description = 'Import & Export Audit Log'
    _order = 'date desc, id desc'
    _rec_name = 'res_name'

    model_name = fields.Char(required=True, index=True)
    res_id = fields.Integer(required=True, index=True)
    res_name = fields.Char(index=True)
    user_id = fields.Many2one('res.users', required=True, default=lambda self: self.env.user, index=True)
    date = fields.Datetime(required=True, default=fields.Datetime.now, index=True)
    action = fields.Selection([
        ('create', 'Create'),
        ('write', 'Update'),
        ('unlink', 'Delete'),
        ('transition', 'State Transition'),
        ('approval', 'Approval'),
        ('rejection', 'Rejection'),
        ('attachment', 'Attachment'),
        ('override', 'Override'),
        ('exception', 'Exception'),
    ], required=True, index=True)
    detail = fields.Text()
    reason = fields.Text()
    old_state = fields.Char()
    new_state = fields.Char()
    change_json = fields.Json()
    company_id = fields.Many2one('res.company', index=True)
    active = fields.Boolean(default=True)

    def write(self, vals):
        if not self.env.context.get('ie_audit_archive'):
            raise UserError(_("Audit log entries cannot be modified."))
        return super().write(vals)

    def unlink(self):
        if not self.env.context.get('ie_audit_archive'):
            raise UserError(_("Audit log entries cannot be deleted."))
        return super().unlink()

    @api.model
    def ie_log(self, record, action, detail=None, reason=None, old_state=None, new_state=None, changes=None):
        """Append one immutable row for ``record``."""
        if self.env.context.get('ie_skip_audit') or not record or not record.id:
            return self.browse()
        company = record.company_id if 'company_id' in record._fields else self.env.company
        name = record.display_name if 'display_name' in record._fields else record.name if 'name' in record._fields else str(record.id)
        return self.sudo().create({
            'model_name': record._name,
            'res_id': record.id,
            'res_name': name,
            'user_id': self.env.uid,
            'action': action,
            'detail': detail,
            'reason': reason,
            'old_state': old_state,
            'new_state': new_state,
            'change_json': changes or {},
            'company_id': company.id if company else False,
        })

    def action_system_archive(self):
        """Explicit archival. Refused for every user who is not the superuser."""
        if self.env.uid != 1 and not self.env.su:
            raise UserError(_("Only the system user can archive audit log entries."))
        return self.with_context(ie_audit_archive=True).write({'active': False})
