# -*- coding: utf-8 -*-
"""Compliance KPI snapshots.

Each formula is documented on the calculator. The daily cron stores one row
per company and code so the 90-day review can chart the trend. Green means the
value meets the target, amber means it is within five points, red means it misses.
For target-zero KPIs, only zero is green.
"""
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class IeComplianceKpi(models.Model):
    _name = 'ie.compliance.kpi'
    _description = 'Compliance KPI Snapshot'
    _order = 'date desc, code'

    date = fields.Date(required=True, index=True, default=fields.Date.context_today)
    company_id = fields.Many2one('res.company', required=True, index=True)
    code = fields.Char(required=True, index=True)
    name = fields.Char(required=True, translate=True)
    value = fields.Float()
    target = fields.Float()
    status = fields.Selection([
        ('green', 'Green'),
        ('amber', 'Amber'),
        ('red', 'Red'),
    ], required=True)
    zero_is_good = fields.Boolean()

    _sql_constraints = [
        ('day_code_company', 'unique(date, code, company_id)', 'Only one KPI snapshot per day, code and company.'),
    ]

    @api.model
    def _status(self, value, target, zero_is_good=False):
        if zero_is_good:
            return 'green' if not value else 'red'
        if value >= target:
            return 'green'
        if value >= target - 5:
            return 'amber'
        return 'red'

    @api.model
    def _ratios(self, company):
        """Return code -> (label, value, target, zero_is_good).

        transactions: 100 * operational orders / (operational orders + open bypass exceptions). Target 100.
        approved_import: 100 * approved import orders / import orders that left draft. Target 100.
        approved_export: same formula for export orders. Target 100.
        documentation: average Documentation Completeness % of open orders. Target company setting (default 98).
        unauthorized: count of approved or expired no-order exceptions. Target 0.
        duplicates: count of orders flagged by duplicate detection. Target 0.
        stock_accuracy: 100 * (1 - inventory-adjustment qty / on-hand qty). Target company setting (default 99).
        bank_recon: 100 * reconciled linked bank statement lines / linked lines. Target company setting (default 100).
        proceeds_recon: 100 * proceeds lines with no outstanding balance / proceeds lines. Target 100.
        lc_closure: 100 * closed letters of credit / letters of credit past Applied. Target 100.
        audit: 100 * processed orders that have an audit row / processed orders. Target 100.
        critical: count of open critical compliance exceptions. Target 0.
        """
        Import = self.env['ie.import.order']
        Export = self.env['ie.export.order']
        imports = Import.search([('company_id', '=', company.id), ('state', '!=', 'cancelled')])
        exports = Export.search([('company_id', '=', company.id), ('state', '!=', 'cancelled')])
        import_left = imports.filtered(lambda order: order.state != 'draft')
        export_left = exports.filtered(lambda order: order.state != 'draft')
        import_ok = import_left.filtered(lambda order: order.state in (
            'approved', 'bank_lc', 'shipment', 'received', 'closed',
        ))
        export_ok = export_left.filtered(lambda order: order.state in (
            'approved', 'customs_loading', 'shipped', 'proceeds', 'reconciled', 'closed',
        ))
        exceptions = self.env['ie.transaction.exception'].search([
            ('company_id', '=', company.id),
            ('state', 'in', ('approved', 'expired')),
        ])
        operational = len(import_ok) + len(export_ok)
        transactions = 100.0 * operational / (operational + len(exceptions)) if (operational or exceptions) else 100.0
        approved_import = 100.0 * len(import_ok) / len(import_left) if import_left else 100.0
        approved_export = 100.0 * len(export_ok) / len(export_left) if export_left else 100.0
        docs = list(imports.mapped('doc_completeness')) + list(exports.mapped('doc_completeness'))
        documentation = sum(docs) / len(docs) if docs else 100.0
        duplicates = len(imports.filtered('duplicate_flag')) + len(exports.filtered('duplicate_flag'))
        stock_accuracy = self._stock_accuracy(company)
        bank_recon = self._bank_recon(company)
        proceeds = self.env['ie.export.proceeds'].search([('company_id', '=', company.id)])
        settled = proceeds.filtered(lambda item: abs(item.amount_outstanding) < 0.01)
        proceeds_recon = 100.0 * len(settled) / len(proceeds) if proceeds else 100.0
        lcs = self.env['ie.lc'].search([('company_id', '=', company.id), ('state', '!=', 'applied')])
        closed = lcs.filtered(lambda lc: lc.state == 'closed')
        lc_closure = 100.0 * len(closed) / len(lcs) if lcs else 100.0
        with_audit = 0
        Log = self.env['ie.audit.log']
        for order in list(import_left) + list(export_left):
            if Log.search_count([('model_name', '=', order._name), ('res_id', '=', order.id)], limit=1):
                with_audit += 1
        processed_count = len(import_left) + len(export_left)
        audit = 100.0 * with_audit / processed_count if processed_count else 100.0
        critical = self.env['ie.compliance.exception'].search_count([
            ('company_id', '=', company.id),
            ('severity', '=', 'critical'),
            ('state', '=', 'open'),
        ])
        target_doc = company.ie_kpi_doc_target or 98.0
        target_stock = company.ie_kpi_stock_target or 99.0
        target_recon = company.ie_kpi_reconcile_target or 100.0
        return {
            'transactions': (_('Transactions through Odoo'), transactions, 100.0, False),
            'approved_import': (_('Approved Import Orders'), approved_import, 100.0, False),
            'approved_export': (_('Approved Export Orders'), approved_export, 100.0, False),
            'documentation': (_('Complete Documentation'), documentation, target_doc, False),
            'unauthorized': (_('Unauthorized Transactions'), float(len(exceptions)), 0.0, True),
            'duplicates': (_('Duplicate Orders'), float(duplicates), 0.0, True),
            'stock_accuracy': (_('ERP-Physical Stock Accuracy'), stock_accuracy, target_stock, False),
            'bank_recon': (_('ERP-Bank Reconciliation'), bank_recon, target_recon, False),
            'proceeds_recon': (_('Export Proceeds Reconciliation'), proceeds_recon, target_recon, False),
            'lc_closure': (_('LC Closure'), lc_closure, 100.0, False),
            'audit': (_('Audit Trail Completeness'), audit, 100.0, False),
            'critical': (_('Critical Compliance Exceptions'), float(critical), 0.0, True),
        }

    def _stock_accuracy(self, company):
        """100 * (1 - absolute inventory-adjustment quantity / internal on-hand quantity)."""
        quants = self.env['stock.quant']._read_group(
            [('company_id', '=', company.id), ('location_id.usage', '=', 'internal')],
            [],
            ['quantity:sum'],
        )
        on_hand = abs(quants[0][0] or 0.0) if quants else 0.0
        moves = self.env['stock.move']._read_group(
            [('company_id', '=', company.id), ('is_inventory', '=', True), ('state', '=', 'done')],
            [],
            ['quantity:sum'],
        )
        adjusted = abs(moves[0][0] or 0.0) if moves else 0.0
        if not on_hand:
            return 100.0
        return max(0.0, 100.0 * (1.0 - (adjusted / on_hand)))

    def _bank_recon(self, company):
        """100 * reconciled statement lines linked to IE payments or proceeds / those lines.

        Uses account.bank.statement.line.is_reconciled, maintained by Enterprise
        bank reconciliation. When nothing is linked yet the KPI stays at 100.
        """
        payments = self.env['account.payment'].search([
            ('company_id', '=', company.id),
            ('ie_statement_line_id', '!=', False),
        ])
        proceeds = self.env['ie.export.proceeds'].search([
            ('company_id', '=', company.id),
            ('statement_line_id', '!=', False),
        ])
        lines = payments.mapped('ie_statement_line_id') | proceeds.mapped('statement_line_id')
        if not lines:
            return 100.0
        done = lines.filtered('is_reconciled')
        return 100.0 * len(done) / len(lines)

    @api.model
    def _cron_snapshot(self):
        self = self.sudo()
        today = fields.Date.context_today(self)
        for company in self.env['res.company'].search([]):
            for code, (label, value, target, zero_is_good) in self._ratios(company).items():
                existing = self.search([
                    ('date', '=', today),
                    ('code', '=', code),
                    ('company_id', '=', company.id),
                ], limit=1)
                vals = {
                    'name': label,
                    'value': value,
                    'target': target,
                    'status': self._status(value, target, zero_is_good),
                    'zero_is_good': zero_is_good,
                }
                if existing:
                    existing.write(vals)
                else:
                    self.create(dict(vals, date=today, code=code, company_id=company.id))

    @api.model
    def _export_stock_available(self, company, product_id=False):
        """Free internal quantity for products sitting on export orders.

        Free means on-hand minus reserved. With no product filter this is every
        product on a non-cancelled export order, so the dashboard card is not
        stuck at zero. Quants are read with sudo because dashboard groups do
        not all have stock access; only the total is returned.
        """
        if product_id:
            products = self.env['product.product'].browse(int(product_id)).exists()
        else:
            grouped = self.env['ie.export.order.line']._read_group(
                [
                    ('order_id.company_id', '=', company.id),
                    ('order_id.state', '!=', 'cancelled'),
                    ('product_id', '!=', False),
                ],
                ['product_id'],
                [],
            )
            products = self.env['product.product']
            for product, in grouped:
                if product:
                    products |= product
        if not products:
            return 0.0
        totals = self.env['stock.quant'].sudo()._read_group(
            [
                ('company_id', '=', company.id),
                ('location_id.usage', '=', 'internal'),
                ('product_id', 'in', products.ids),
            ],
            [],
            ['available_quantity:sum'],
        )
        return totals[0][0] or 0.0 if totals else 0.0

    @api.model
    def get_compliance_dashboard(self):
        self.sudo()._cron_snapshot()
        rows = self.search([
            ('company_id', '=', self.env.company.id),
            ('date', '=', fields.Date.context_today(self)),
        ])
        return [{
            'code': row.code,
            'name': row.name,
            'value': row.value,
            'target': row.target,
            'status': row.status,
            'zero_is_good': row.zero_is_good,
        } for row in rows]

    @api.model
    def get_master_dashboard(self, date_from=False, date_to=False, business_unit_id=False, product_id=False):
        """Cards for the management dashboard. Counts use _read_group."""
        company = self.env.company
        import_domain = [('company_id', '=', company.id), ('state', 'not in', ('cancelled',))]
        export_domain = [('company_id', '=', company.id), ('state', 'not in', ('cancelled',))]
        if date_from:
            import_domain.append(('date', '>=', date_from))
            export_domain.append(('date', '>=', date_from))
        if date_to:
            import_domain.append(('date', '<=', date_to))
            export_domain.append(('date', '<=', date_to))
        if business_unit_id:
            import_domain.append(('business_unit_id', '=', int(business_unit_id)))
            export_domain.append(('business_unit_id', '=', int(business_unit_id)))
        Import = self.env['ie.import.order']
        Export = self.env['ie.export.order']
        import_states = Import._read_group(import_domain, ['state'], ['__count', 'amount_total:sum'])
        export_states = Export._read_group(export_domain, ['state'], ['__count', 'amount_total:sum'])
        import_by_state = {state: (count, amount or 0.0) for state, count, amount in import_states}
        export_by_state = {state: (count, amount or 0.0) for state, count, amount in export_states}

        def _sum_count(mapping, states):
            return sum(mapping.get(state, (0, 0.0))[0] for state in states)

        def _sum_amount(mapping):
            return sum(amount for _count, amount in mapping.values())

        open_import_states = [key for key, _label in Import._fields['state'].selection if key not in ('closed', 'cancelled', 'draft')]
        open_export_states = [key for key, _label in Export._fields['state'].selection if key not in ('closed', 'cancelled', 'draft')]
        lc_domain = [('company_id', '=', company.id), ('state', 'in', ('applied', 'issued', 'amended'))]
        lc_value = self.env['ie.lc']._read_group(lc_domain, [], ['amount:sum', '__count'])
        shipments = self.env['ie.shipment'].search_count([('company_id', '=', company.id), ('state', '=', 'in_transit')])
        customs_pending = self.env['ie.customs.clearance'].search_count([('company_id', '=', company.id), ('state', '=', 'pending')])
        product_domain = [('order_id.company_id', '=', company.id)]
        if product_id:
            product_domain.append(('product_id', '=', int(product_id)))
        qty_by_product = self.env['ie.export.order.line']._read_group(
            product_domain, ['product_id'], ['quantity:sum'],
        )
        soybean = sesame = 0.0
        for product, qty in qty_by_product:
            if not product:
                continue
            code = (product.default_code or '') + ' ' + (product.display_name or '')
            lowered = code.lower()
            if 'soy' in lowered:
                soybean += qty or 0.0
            if 'sesame' in lowered:
                sesame += qty or 0.0
        proceeds = self.env['ie.export.proceeds']._read_group(
            [('company_id', '=', company.id)], [], ['amount_received:sum', 'amount_outstanding:sum'],
        )
        received = outstanding = 0.0
        if proceeds:
            received = proceeds[0][0] or 0.0
            outstanding = proceeds[0][1] or 0.0
        stock_free = self._export_stock_available(company, product_id)
        variance = Import._read_group(import_domain, [], ['cost_variance:sum'])
        bills = self.env['account.move'].sudo().search([
            ('company_id', '=', company.id),
            ('ie_import_order_id', '!=', False),
            ('move_type', 'in', ('in_invoice', 'in_refund')),
            ('state', '=', 'posted'),
            ('payment_state', 'not in', ('paid', 'reversed', 'in_payment')),
        ])
        supplier_balance = sum(bills.mapped('amount_residual_signed'))
        imported_products = self.env['ie.import.order.line'].search([
            ('order_id.company_id', '=', company.id),
            ('order_id.state', '!=', 'cancelled'),
        ]).product_id
        resale_revenue = 0.0
        if imported_products:
            invoice_lines = self.env['account.move.line'].sudo().search([
                ('company_id', '=', company.id),
                ('parent_state', '=', 'posted'),
                ('move_id.move_type', 'in', ('out_invoice', 'out_refund')),
                ('display_type', '=', 'product'),
                ('product_id', 'in', imported_products.ids),
            ])
            resale_revenue = -sum(invoice_lines.mapped('balance'))
        landed_rows = Import._read_group(
            [('company_id', '=', company.id), ('state', '!=', 'cancelled')],
            [], ['actual_landed_cost:sum'],
        )
        landed_cost = landed_rows[0][0] or 0.0 if landed_rows else 0.0

        def _pipeline(model, mapping):
            selection = model._fields['state']._description_selection(self.env)
            return [{
                'key': key,
                'label': label,
                'count': mapping.get(key, (0, 0.0))[0],
                'amount': mapping.get(key, (0, 0.0))[1],
            } for key, label in selection if key != 'cancelled']

        return {
            'import_open': _sum_count(import_by_state, open_import_states),
            'import_value': _sum_amount(import_by_state),
            'import_received': import_by_state.get('received', (0, 0.0))[0] + import_by_state.get('closed', (0, 0.0))[0],
            'import_outstanding': _sum_count(import_by_state, ('approved', 'bank_lc', 'shipment', 'received')),
            'pending_lc': lc_value[0][1] if lc_value else 0,
            'lc_value': lc_value[0][0] or 0.0 if lc_value else 0.0,
            'shipment_transit': shipments,
            'customs_pending': customs_pending,
            'pending_payment': self.env['account.move'].search_count([
                ('company_id', '=', company.id),
                ('ie_import_order_id', '!=', False),
                ('state', '=', 'posted'),
                ('payment_state', 'not in', ('paid', 'reversed', 'in_payment')),
            ]),
            'cost_variance': variance[0][0] or 0.0 if variance else 0.0,
            'export_open': _sum_count(export_by_state, open_export_states),
            'export_count': sum(count for count, _amount in export_by_state.values()),
            'export_value': _sum_amount(export_by_state),
            'soybean_qty': soybean,
            'sesame_qty': sesame,
            'export_shipped': export_by_state.get('shipped', (0, 0.0))[0] + export_by_state.get('proceeds', (0, 0.0))[0] + export_by_state.get('reconciled', (0, 0.0))[0] + export_by_state.get('closed', (0, 0.0))[0],
            'export_pending_shipment': _sum_count(export_by_state, ('approved', 'customs_loading')),
            'export_receivable': self.env['account.move'].search_count([
                ('company_id', '=', company.id),
                ('ie_export_order_id', '!=', False),
                ('state', '=', 'posted'),
                ('payment_state', 'not in', ('paid', 'reversed', 'in_payment')),
            ]),
            'proceeds_received': received,
            'proceeds_outstanding': outstanding,
            'export_stock': stock_free,
            'supplier_balance': supplier_balance,
            'resale_revenue': resale_revenue,
            'trading_profit': resale_revenue - landed_cost,
            'currency_id': company.currency_id.id,
            'company_name': company.display_name,
            'import_pipeline': _pipeline(Import, import_by_state),
            'export_pipeline': _pipeline(Export, export_by_state),
            'compliance': self.get_compliance_dashboard(),
        }

    @api.model
    def _ie_load_demo_figures(self):
        """Post unpaid bills and invoices, and leave part of the export proceeds outstanding.

        Trading profit, supplier balance, resale revenue, pending payment and
        export receivable are all read from posted accounting documents.
        """
        company = self.env.ref('base.main_company', raise_if_not_found=False)
        if not company:
            return
        self = self.with_company(company).sudo()
        self._ie_demo_ensure_chart(company)
        sale_journal = self.env['account.journal'].search([
            ('company_id', '=', company.id), ('type', '=', 'sale'),
        ], limit=1)
        purchase_journal = self.env['account.journal'].search([
            ('company_id', '=', company.id), ('type', '=', 'purchase'),
        ], limit=1)
        if not sale_journal or not purchase_journal:
            _logger.warning('Demo dashboard figures skipped: %s has no sales or purchase journal.', company.display_name)
            return
        if not self.env['account.move'].search_count([
            ('company_id', '=', company.id), ('ref', '=', 'IE-DEMO-KPI'),
        ]):
            received = self.env['ie.import.order'].search([
                ('proforma_number', '=', 'PI-STAGE-RECEIVED'), ('company_id', '=', company.id),
            ], limit=1)
            closed = self.env['ie.import.order'].search([
                ('proforma_number', '=', 'PI-STAGE-CLOSED'), ('company_id', '=', company.id),
            ], limit=1)
            shipped = self.env['ie.export.order'].search([
                ('contract_no', '=', 'CT-STAGE-SHIPPED'), ('company_id', '=', company.id),
            ], limit=1)
            proceeds_order = self.env['ie.export.order'].search([
                ('contract_no', '=', 'CT-STAGE-PROCEEDS'), ('company_id', '=', company.id),
            ], limit=1)
            buyer = self.env.ref('import_export_control.demo_buyer', raise_if_not_found=False)
            import_product = received.line_ids[:1].product_id or closed.line_ids[:1].product_id
            if received:
                self._ie_demo_invoice(purchase_journal, 'in_invoice', received.partner_id, received, received.line_ids[:1].product_id, 100, 18, 'ie_import_order_id')
            if closed:
                self._ie_demo_invoice(purchase_journal, 'in_invoice', closed.partner_id, closed, closed.line_ids[:1].product_id, 80, 22, 'ie_import_order_id')
            if shipped:
                self._ie_demo_invoice(sale_journal, 'out_invoice', shipped.partner_id, shipped, shipped.product_id, 200, 4, 'ie_export_order_id')
            if proceeds_order:
                self._ie_demo_invoice(sale_journal, 'out_invoice', proceeds_order.partner_id, proceeds_order, proceeds_order.product_id, 150, 4.5, 'ie_export_order_id')
            if buyer and import_product:
                self._ie_demo_invoice(sale_journal, 'out_invoice', buyer, self.env['ie.export.order'], import_product, 40, 35, False)
        self._ie_demo_outstanding_proceeds(company)
        self._ie_demo_landed_cost(company)

    def _ie_demo_ensure_chart(self, company):
        """Add the journals this company needs without replacing an existing chart."""
        self._ie_demo_bank_journal(company)
        if self.env['account.journal'].search_count([
            ('company_id', '=', company.id), ('type', '=', 'sale'),
        ]):
            return
        Account = self.env['account.account'].with_company(company).sudo()

        def _account(name, code, account_type, reconcile=False):
            existing = Account.search([
                ('code', '=', code), ('company_ids', 'in', company.id),
            ], limit=1)
            if existing:
                return existing
            return Account.create({
                'name': name,
                'code': code,
                'account_type': account_type,
                'reconcile': reconcile,
                'company_ids': [(6, 0, [company.id])],
            })

        receivable = _account('Demo Receivable', '121000', 'asset_receivable', True)
        payable = _account('Demo Payable', '211000', 'liability_payable', True)
        income = _account('Demo Income', '411000', 'income')
        expense = _account('Demo Expense', '611000', 'expense')
        Journal = self.env['account.journal'].with_company(company).sudo()
        if not Journal.search_count([('company_id', '=', company.id), ('type', '=', 'sale')]):
            Journal.create({
                'name': 'Customer Invoices',
                'code': 'INV',
                'type': 'sale',
                'company_id': company.id,
                'default_account_id': income.id,
            })
        if not Journal.search_count([('company_id', '=', company.id), ('type', '=', 'purchase')]):
            Journal.create({
                'name': 'Vendor Bills',
                'code': 'BILL',
                'type': 'purchase',
                'company_id': company.id,
                'default_account_id': expense.id,
            })
        partners = self.env['res.partner'].browse([
            self.env.ref('import_export_control.demo_supplier', raise_if_not_found=False).id,
            self.env.ref('import_export_control.demo_buyer', raise_if_not_found=False).id,
        ]).exists()
        for partner in partners:
            partner.with_company(company).sudo().write({
                'property_account_receivable_id': receivable.id,
                'property_account_payable_id': payable.id,
            })
        products = self.env['product.product'].browse([
            self.env.ref('import_export_control.demo_product_soy', raise_if_not_found=False).id,
            self.env.ref('import_export_control.demo_product_import', raise_if_not_found=False).id,
        ]).exists()
        for category in products.categ_id:
            values = {}
            if not category.with_company(company).property_account_income_categ_id:
                values['property_account_income_categ_id'] = income.id
            if not category.with_company(company).property_account_expense_categ_id:
                values['property_account_expense_categ_id'] = expense.id
            if values:
                category.with_company(company).sudo().write(values)

    def _ie_demo_bank_journal(self, company):
        """A bank journal so customer and supplier payments can be posted."""
        journal = self.env['account.journal'].search([
            ('company_id', '=', company.id), ('type', '=', 'bank'),
        ], limit=1)
        if journal:
            return journal
        Account = self.env['account.account'].with_company(company).sudo()
        account = Account.search([
            ('account_type', '=', 'asset_cash'), ('company_ids', 'in', company.id),
        ], limit=1)
        if not account:
            account = Account.create({
                'name': 'Demo Bank',
                'code': '101000',
                'account_type': 'asset_cash',
                'company_ids': [(6, 0, [company.id])],
            })
        return self.env['account.journal'].with_company(company).sudo().create({
            'name': 'Demo Bank',
            'code': 'BNK',
            'type': 'bank',
            'company_id': company.id,
            'default_account_id': account.id,
        })

    def _ie_demo_invoice(self, journal, move_type, partner, order, product, quantity, price, link_field):
        if not partner or not product:
            return
        company = journal.company_id
        vals = {
            'move_type': move_type,
            'partner_id': partner.id,
            'journal_id': journal.id,
            'invoice_date': fields.Date.context_today(self),
            'currency_id': company.currency_id.id,
            'company_id': company.id,
            'ref': 'IE-DEMO-KPI',
            'invoice_line_ids': [(0, 0, {
                'product_id': product.id,
                'name': product.display_name,
                'quantity': quantity,
                'price_unit': price,
                'tax_ids': [(6, 0, [])],
            })],
        }
        if link_field and order:
            vals[link_field] = order.id
            vals['ie_is_import'] = link_field == 'ie_import_order_id'
            vals['ie_is_export'] = link_field == 'ie_export_order_id'
        move = self.env['account.move'].with_company(company).sudo().create(vals)
        move.with_context(ie_skip_enforce=True).action_post()

    def _ie_demo_outstanding_proceeds(self, company):
        order = self.env['ie.export.order'].search([
            ('contract_no', '=', 'CT-STAGE-SHIPPED'),
            ('company_id', '=', company.id),
        ], limit=1)
        if not order or order.proceeds_ids.filtered(lambda item: item.amount_outstanding > 0.01):
            return
        expected = order.amount_total or 3500.0
        self.env['ie.export.proceeds'].sudo().create({
            'export_order_id': order.id,
            'amount_expected': expected,
            'amount_received': expected * 0.4,
            'receipt_date': fields.Date.context_today(self),
            'expected_date': fields.Date.add(fields.Date.context_today(self), days=-10),
            'fx_rate': 1.0,
        })

    def _ie_demo_landed_cost(self, company):
        """Validate one landed cost so trading profit is revenue minus a real cost."""
        order = self.env['ie.import.order'].search([
            ('proforma_number', '=', 'PI-STAGE-RECEIVED'),
            ('company_id', '=', company.id),
        ], limit=1)
        cost = order.landed_cost_ids.filtered(lambda item: item.state == 'draft')[:1]
        if not cost or cost.cost_lines:
            return
        account = self.env['account.account'].with_company(company).search([
            ('account_type', '=', 'expense'),
        ], limit=1)
        freight = self.env['product.product'].sudo().search([
            ('landed_cost_ok', '=', True),
            '|', ('company_id', '=', False), ('company_id', '=', company.id),
        ], limit=1)
        if not freight:
            freight = self.env['product.product'].sudo().create({
                'name': 'Demo Freight',
                'type': 'service',
                'landed_cost_ok': True,
                'company_id': company.id,
            })
        if not account:
            return
        try:
            with self.env.cr.savepoint():
                self.env['stock.landed.cost.lines'].sudo().create({
                    'cost_id': cost.id,
                    'product_id': freight.id,
                    'name': 'Demo freight',
                    'split_method': 'equal',
                    'price_unit': 650.0,
                    'account_id': account.id,
                })
                cost.button_validate()
        except Exception:
            _logger.warning('Demo landed cost was left in draft.', exc_info=True)
