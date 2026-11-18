# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class IeDocumentLine(models.Model):
    _name = 'ie.document.line'
    _description = 'Document Checklist Line'
    _order = 'sequence, id'

    sequence = fields.Integer(default=10)
    document_type_id = fields.Many2one('ie.document.type', required=True, ondelete='restrict')
    name = fields.Char(related='document_type_id.name')
    code = fields.Char(related='document_type_id.code')
    blocking_state = fields.Char()
    required = fields.Boolean(default=True)
    import_order_id = fields.Many2one('ie.import.order', ondelete='cascade', index=True)
    export_order_id = fields.Many2one('ie.export.order', ondelete='cascade', index=True)
    attachment_ids = fields.Many2many(
        'ir.attachment',
        'ie_document_line_attachment_rel',
        'line_id',
        'attachment_id',
        string='Attachments',
    )
    attached = fields.Boolean(compute='_compute_attached', store=True)
    uploaded_by_id = fields.Many2one('res.users', readonly=True)
    uploaded_on = fields.Datetime(readonly=True)
    verified_by_id = fields.Many2one('res.users', readonly=True)
    verified_on = fields.Datetime(readonly=True)
    company_id = fields.Many2one('res.company', compute='_compute_company', store=True)

    @api.onchange('document_type_id')
    def _onchange_document_type_id(self):
        if self.document_type_id:
            self.sequence = self.document_type_id.sequence
            self.required = self.document_type_id.required
            self.blocking_state = self.document_type_id.blocking_state

    @api.model_create_multi
    def create(self, vals_list):
        types = self.env['ie.document.type'].browse([
            vals['document_type_id'] for vals in vals_list if vals.get('document_type_id')
        ])
        by_id = {doc_type.id: doc_type for doc_type in types}
        for vals in vals_list:
            doc_type = by_id.get(vals.get('document_type_id'))
            if not doc_type:
                continue
            vals.setdefault('sequence', doc_type.sequence)
            vals.setdefault('required', doc_type.required)
            vals.setdefault('blocking_state', doc_type.blocking_state)
        return super().create(vals_list)

    @api.depends('attachment_ids')
    def _compute_attached(self):
        for line in self:
            line.attached = bool(line.attachment_ids)

    @api.depends('import_order_id.company_id', 'export_order_id.company_id')
    def _compute_company(self):
        for line in self:
            line.company_id = line.import_order_id.company_id or line.export_order_id.company_id

    def write(self, vals):
        res = super().write(vals)
        if 'attachment_ids' in vals:
            now = fields.Datetime.now()
            for line in self:
                order = line.import_order_id or line.export_order_id
                if line.attachment_ids and not line.uploaded_by_id:
                    super(IeDocumentLine, line).write({
                        'uploaded_by_id': self.env.user.id,
                        'uploaded_on': now,
                    })
                if order:
                    self.env['ie.audit.log'].ie_log(
                        order,
                        'attachment',
                        detail=_('Checklist "%(doc)s" now has %(count)s attachment(s).',
                                 doc=line.name, count=len(line.attachment_ids)),
                    )
        return res

    def action_verify(self):
        for line in self:
            if not line.attachment_ids:
                raise UserError(_("Attach the document before verifying it."))
            if line.uploaded_by_id == self.env.user:
                raise UserError(_("The user who uploaded the document cannot verify it."))
            line.write({
                'verified_by_id': self.env.user.id,
                'verified_on': fields.Datetime.now(),
            })
        return True
