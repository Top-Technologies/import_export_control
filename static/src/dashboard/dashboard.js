/** @odoo-module **/
import { registry } from "@web/core/registry";
import { Component, onWillStart, onWillUnmount, useEffect, useRef, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { Layout } from "@web/search/layout";
import { _t } from "@web/core/l10n/translation";
import { KeepLast } from "@web/core/utils/concurrency";
import { loadBundle } from "@web/core/assets";
import { cookie } from "@web/core/browser/cookie";
import { getColor, getCustomColor } from "@web/core/colors/colors";
import { formatFloat, formatInteger, formatMonetary } from "@web/views/fields/formatters";

const STATUS_COLOR = {
    green: "#198754",
    amber: "#ffc107",
    red: "#dc3545",
};

export class IeChart extends Component {
    static template = "import_export_control.DashboardChart";
    static props = {
        title: String,
        type: String,
        labels: Array,
        datasets: Array,
        colors: { type: Array, optional: true },
        monetary: { type: Boolean, optional: true },
        horizontal: { type: Boolean, optional: true },
        currencyId: { type: Number, optional: true },
        clickable: { type: Boolean, optional: true },
        onSelect: { type: Function, optional: true },
    };

    setup() {
        this.canvasRef = useRef("canvas");
        this.chart = null;
        onWillStart(() => loadBundle("web.chartjs_lib"));
        useEffect(
            () => {
                this.renderChart();
                return () => this.destroyChart();
            },
            () => [
                this.props.type,
                this.props.horizontal,
                JSON.stringify(this.props.labels),
                JSON.stringify(this.props.datasets),
                JSON.stringify(this.props.colors || []),
            ]
        );
        onWillUnmount(() => this.destroyChart());
    }

    get hasData() {
        return this.props.datasets.some((set) => set.data.some((value) => value));
    }

    destroyChart() {
        if (this.chart) {
            this.chart.destroy();
            this.chart = null;
        }
    }

    palette(count) {
        if (this.props.colors && this.props.colors.length) {
            return this.props.colors;
        }
        const scheme = cookie.get("color_scheme");
        return Array.from({ length: count }, (_, index) => getColor(index, scheme, Math.max(count, 1)));
    }

    formatPoint(value) {
        if (this.props.monetary) {
            return formatMonetary(value || 0, { currencyId: this.props.currencyId });
        }
        return formatFloat(value || 0, { digits: [16, Number.isInteger(value) ? 0 : 2] });
    }

    renderChart() {
        this.destroyChart();
        if (!this.hasData || !this.canvasRef.el) {
            return;
        }
        const scheme = cookie.get("color_scheme");
        const labelColor = getCustomColor(scheme, "#111827", "#E4E4E4");
        const gridColor = getCustomColor(scheme, "rgba(0,0,0,.08)", "rgba(255,255,255,.12)");
        const isCircular = this.props.type === "pie" || this.props.type === "doughnut";
        const datasets = this.props.datasets.map((set, index) => {
            const count = isCircular || this.props.datasets.length === 1 ? set.data.length : this.props.datasets.length;
            const colors = this.palette(count);
            const color = this.props.datasets.length > 1 && !isCircular ? colors[index % colors.length] : colors;
            return {
                label: set.label,
                data: set.data,
                backgroundColor: color,
                borderColor: isCircular ? "#ffffff" : color,
                borderWidth: isCircular ? 1 : 0,
                maxBarThickness: 28,
            };
        });
        const shorten = (label) => (label && label.length > 24 ? `${label.slice(0, 23)}…` : label);
        const options = {
            responsive: true,
            maintainAspectRatio: false,
            onClick: (_event, elements) => {
                if (this.props.clickable && elements.length && this.props.onSelect) {
                    this.props.onSelect(elements[0].index);
                }
            },
            onHover: (event, elements) => {
                const target = event.native && event.native.target;
                if (target) {
                    target.style.cursor = this.props.clickable && elements.length ? "pointer" : "default";
                }
            },
            plugins: {
                legend: {
                    display: isCircular || this.props.datasets.length > 1,
                    position: "bottom",
                    labels: { color: labelColor, boxWidth: 12, padding: 10 },
                },
                tooltip: {
                    callbacks: {
                        title: (items) => (items.length ? this.props.labels[items[0].dataIndex] : ""),
                        label: (ctx) => {
                            const raw = ctx.parsed && typeof ctx.parsed === "object"
                                ? (this.props.horizontal ? ctx.parsed.x : ctx.parsed.y)
                                : ctx.parsed;
                            const value = raw === undefined ? ctx.raw : raw;
                            const name = ctx.dataset.label ? `${ctx.dataset.label}: ` : "";
                            return `${name}${this.formatPoint(value)}`;
                        },
                    },
                },
            },
        };
        if (!isCircular) {
            options.indexAxis = this.props.horizontal ? "y" : "x";
            options.scales = {
                x: {
                    ticks: {
                        color: labelColor,
                        callback(value) {
                            return shorten(String(this.getLabelForValue(value)));
                        },
                    },
                    grid: { color: this.props.horizontal ? gridColor : "transparent" },
                    beginAtZero: true,
                },
                y: {
                    ticks: {
                        color: labelColor,
                        callback(value) {
                            return shorten(String(this.getLabelForValue(value)));
                        },
                    },
                    grid: { color: this.props.horizontal ? "transparent" : gridColor },
                    beginAtZero: true,
                },
            };
        }
        this.chart = new Chart(this.canvasRef.el, {
            type: this.props.type,
            data: {
                labels: this.props.labels.map((label) => shorten(label)),
                datasets,
            },
            options,
        });
    }
}

const STATUS_LABEL = {
    green: _t("On target"),
    amber: _t("Watch"),
    red: _t("Off target"),
};

export class IeDashboard extends Component {
    static template = "import_export_control.Dashboard";
    static components = { Layout, IeChart };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.keepLast = new KeepLast();
        this.state = useState({
            dateFrom: "",
            dateTo: "",
            data: {},
            loading: false,
        });
        onWillStart(() => this.load());
    }

    get display() {
        return { controlPanel: { layoutActions: true } };
    }

    get complianceScore() {
        const summary = this.complianceSummary;
        const total = summary.green + summary.amber + summary.red;
        if (!total) {
            return 100;
        }
        return Math.round((100 * summary.green) / total);
    }

    get summaryCards() {
        const score = this.complianceScore;
        const scoreStatus = score >= 90 ? "ok" : score >= 70 ? "watch" : "alert";
        return [
            {
                key: "trading_profit",
                icon: "fa-line-chart",
                label: _t("Trading Profit"),
                kind: "money",
                tone: "profit",
                model: "ie.import.order",
                domain: [["state", "!=", "cancelled"]],
                badge: scoreStatus === "ok" ? _t("Healthy") : _t("Review"),
                badgeTone: scoreStatus,
            },
            {
                key: "import_value",
                icon: "fa-arrow-circle-down",
                label: _t("Import Value"),
                kind: "money",
                tone: "import",
                model: "ie.import.order",
                dated: true,
                excludeStates: ["cancelled"],
            },
            {
                key: "export_value",
                icon: "fa-arrow-circle-up",
                label: _t("Export Value"),
                kind: "money",
                tone: "export",
                model: "ie.export.order",
                dated: true,
                excludeStates: ["cancelled"],
            },
            {
                key: "compliance_score",
                icon: "fa-shield",
                label: _t("Compliance Score"),
                kind: "score",
                tone: "compliance",
                value: score,
                badge: `${score}%`,
                badgeTone: scoreStatus,
            },
        ];
    }

    get importCards() {
        return [
            { key: "import_open", icon: "fa-folder-open-o", label: _t("Open import orders"), model: "ie.import.order", dated: true, excludeStates: ["draft", "closed", "cancelled"] },
            { key: "import_value", icon: "fa-money", label: _t("Total import value"), kind: "money", model: "ie.import.order", dated: true, excludeStates: ["cancelled"] },
            { key: "import_received", icon: "fa-cube", label: _t("Goods received"), model: "ie.import.order", dated: true, states: ["received", "closed"] },
            { key: "import_outstanding", icon: "fa-clock-o", label: _t("Outstanding import orders"), model: "ie.import.order", dated: true, states: ["approved", "bank_lc", "shipment", "received"] },
            { key: "cost_variance", icon: "fa-exchange", label: _t("Import cost variance"), kind: "money", model: "ie.import.order", dated: true, excludeStates: ["cancelled"] },
        ];
    }

    get exportCards() {
        return [
            { key: "export_open", icon: "fa-folder-open-o", label: _t("Open export orders"), model: "ie.export.order", dated: true, excludeStates: ["draft", "closed", "cancelled"] },
            { key: "export_count", icon: "fa-list-alt", label: _t("Total export orders"), model: "ie.export.order", dated: true, excludeStates: ["cancelled"] },
            { key: "export_value", icon: "fa-money", label: _t("Export value"), kind: "money", model: "ie.export.order", dated: true, excludeStates: ["cancelled"] },
            { key: "export_shipped", icon: "fa-ship", label: _t("Shipped"), model: "ie.export.order", dated: true, states: ["shipped", "proceeds", "reconciled", "closed"] },
            { key: "export_pending_shipment", icon: "fa-truck", label: _t("Pending shipment"), model: "ie.export.order", dated: true, states: ["approved", "customs_loading"] },
            {
                key: "export_stock",
                icon: "fa-cubes",
                label: _t("Export stock available"),
                kind: "qty",
                model: "ie.export.order.line",
                views: [[false, "list"], [false, "pivot"]],
                domain: [["order_id.state", "!=", "cancelled"]],
            },
        ];
    }

    get operationCards() {
        return [
            { key: "pending_lc", icon: "fa-bank", label: _t("Pending LC"), model: "ie.lc", states: ["applied", "issued", "amended"] },
            { key: "lc_value", icon: "fa-money", label: _t("LC value"), kind: "money", model: "ie.lc", states: ["applied", "issued", "amended"] },
            { key: "shipment_transit", icon: "fa-ship", label: _t("Shipment in transit"), model: "ie.shipment", states: ["in_transit"] },
            { key: "customs_pending", icon: "fa-gavel", label: _t("Customs pending"), model: "ie.customs.clearance", states: ["pending"] },
            {
                key: "pending_payment",
                icon: "fa-file-text-o",
                label: _t("Pending payment"),
                model: "account.move",
                domain: [
                    ["ie_import_order_id", "!=", false],
                    ["move_type", "in", ["in_invoice", "in_refund"]],
                    ["state", "=", "posted"],
                    ["payment_state", "not in", ["paid", "reversed", "in_payment"]],
                ],
            },
            {
                key: "export_receivable",
                icon: "fa-file-text-o",
                label: _t("Export receivable"),
                model: "account.move",
                domain: [
                    ["ie_export_order_id", "!=", false],
                    ["move_type", "in", ["out_invoice", "out_refund"]],
                    ["state", "=", "posted"],
                    ["payment_state", "not in", ["paid", "reversed", "in_payment"]],
                ],
            },
            { key: "proceeds_received", icon: "fa-check-circle", label: _t("Export proceeds received"), kind: "money", model: "ie.export.proceeds" },
            {
                key: "proceeds_outstanding",
                icon: "fa-hourglass-half",
                label: _t("Outstanding export proceeds"),
                kind: "money",
                model: "ie.export.proceeds",
                domain: [["amount_outstanding", ">", 0]],
            },
            {
                key: "supplier_balance",
                icon: "fa-balance-scale",
                label: _t("Supplier balances"),
                kind: "money",
                model: "account.move",
                domain: [
                    ["ie_import_order_id", "!=", false],
                    ["move_type", "in", ["in_invoice", "in_refund"]],
                    ["state", "=", "posted"],
                    ["payment_state", "not in", ["paid", "reversed", "in_payment"]],
                ],
            },
            {
                key: "resale_revenue",
                icon: "fa-line-chart",
                label: _t("Resale revenue"),
                kind: "money",
                model: "account.move",
                domain: [
                    ["move_type", "in", ["out_invoice", "out_refund"]],
                    ["state", "=", "posted"],
                ],
            },
            {
                key: "trading_profit",
                icon: "fa-pie-chart",
                label: _t("Trading profit"),
                kind: "money",
                model: "ie.import.order",
                domain: [["state", "!=", "cancelled"]],
            },
        ];
    }

    stageSlices(pipeline, field) {
        return (pipeline || []).filter((stage) => stage[field]);
    }

    get charts() {
        const data = this.state.data;
        const importCount = this.stageSlices(data.import_pipeline, "count");
        const exportCount = this.stageSlices(data.export_pipeline, "count");
        const importValue = this.stageSlices(data.import_pipeline, "amount");
        const exportValue = this.stageSlices(data.export_pipeline, "amount");
        const statusSlices = [
            { label: _t("On target"), value: this.complianceSummary.green, color: STATUS_COLOR.green },
            { label: _t("Watch"), value: this.complianceSummary.amber, color: STATUS_COLOR.amber },
            { label: _t("Off target"), value: this.complianceSummary.red, color: STATUS_COLOR.red },
        ].filter((slice) => slice.value);
        return [
            {
                key: "import_pie",
                title: _t("Import orders by stage"),
                type: "pie",
                labels: importCount.map((stage) => stage.label),
                datasets: [{ label: _t("Orders"), data: importCount.map((stage) => stage.count) }],
                clickable: true,
                onSelect: (index) => this.openStage("ie.import.order", importCount[index]),
            },
            {
                key: "export_pie",
                title: _t("Export orders by stage"),
                type: "pie",
                labels: exportCount.map((stage) => stage.label),
                datasets: [{ label: _t("Orders"), data: exportCount.map((stage) => stage.count) }],
                clickable: true,
                onSelect: (index) => this.openStage("ie.export.order", exportCount[index]),
            },
            {
                key: "compliance_doughnut",
                title: _t("Compliance status"),
                type: "doughnut",
                labels: statusSlices.map((slice) => slice.label),
                datasets: [{ label: _t("KPIs"), data: statusSlices.map((slice) => slice.value) }],
                colors: statusSlices.map((slice) => slice.color),
            },
            {
                key: "import_bar",
                title: _t("Import value by stage"),
                type: "bar",
                monetary: true,
                labels: importValue.map((stage) => stage.label),
                datasets: [{ label: _t("Value"), data: importValue.map((stage) => stage.amount) }],
                clickable: true,
                onSelect: (index) => this.openStage("ie.import.order", importValue[index]),
            },
            {
                key: "export_bar",
                title: _t("Export value by stage"),
                type: "bar",
                monetary: true,
                labels: exportValue.map((stage) => stage.label),
                datasets: [{ label: _t("Value"), data: exportValue.map((stage) => stage.amount) }],
                clickable: true,
                onSelect: (index) => this.openStage("ie.export.order", exportValue[index]),
            },
            {
                key: "operations_bar",
                title: _t("Operations"),
                type: "bar",
                horizontal: true,
                labels: [
                    _t("Letters of credit"),
                    _t("In transit"),
                    _t("Customs pending"),
                    _t("Unpaid bills"),
                    _t("Unpaid invoices"),
                ],
                datasets: [{
                    label: _t("Count"),
                    data: [
                        data.pending_lc || 0,
                        data.shipment_transit || 0,
                        data.customs_pending || 0,
                        data.pending_payment || 0,
                        data.export_receivable || 0,
                    ],
                }],
            },
        ];
    }

    get complianceRows() {
        return this.state.data.compliance || [];
    }

    get complianceSummary() {
        const rows = this.complianceRows;
        return {
            green: rows.filter((row) => row.status === "green").length,
            amber: rows.filter((row) => row.status === "amber").length,
            red: rows.filter((row) => row.status === "red").length,
        };
    }

    formatValue(card) {
        const value = this.state.data[card.key] || 0;
        if (card.kind === "money") {
            return formatMonetary(value, { currencyId: this.state.data.currency_id });
        }
        if (card.kind === "qty") {
            return formatFloat(value, { digits: [16, 2] });
        }
        return formatInteger(value);
    }

    formatSummaryValue(card) {
        if (card.kind === "score") {
            return `${formatInteger(card.value || 0)}%`;
        }
        const value = this.state.data[card.key] || 0;
        if (card.kind === "money") {
            return formatMonetary(value, { currencyId: this.state.data.currency_id });
        }
        return formatInteger(value);
    }

    summaryBadgeClass(card) {
        if (!card.badgeTone) {
            return "o_ie_badge_neutral";
        }
        return {
            ok: "o_ie_badge_ok",
            watch: "o_ie_badge_watch",
            alert: "o_ie_badge_alert",
        }[card.badgeTone] || "o_ie_badge_neutral";
    }

    formatKpi(row) {
        if (row.zero_is_good) {
            return formatInteger(row.value);
        }
        return `${formatFloat(row.value, { digits: [16, 1] })}%`;
    }

    formatTarget(row) {
        if (row.zero_is_good) {
            return _t("Target 0");
        }
        return _t("Target %s%", formatFloat(row.target, { digits: [16, 0] }));
    }

    statusLabel(status) {
        return STATUS_LABEL[status] || status;
    }

    statusClass(status) {
        return { green: "success", amber: "warning", red: "danger" }[status] || "secondary";
    }

    kpiWidth(row) {
        if (row.zero_is_good) {
            return 100;
        }
        const target = row.target || 100;
        return Math.max(0, Math.min(100, (row.value / target) * 100));
    }

    async load() {
        this.state.loading = true;
        try {
            this.state.data = await this.keepLast.add(this.orm.call(
                "ie.compliance.kpi",
                "get_master_dashboard",
                [],
                {
                    date_from: this.state.dateFrom || false,
                    date_to: this.state.dateTo || false,
                },
            ));
        } finally {
            this.state.loading = false;
        }
    }

    domainFor(card) {
        const domain = [...(card.domain || [])];
        if (card.states) {
            domain.push(["state", "in", card.states]);
        }
        if (card.excludeStates) {
            domain.push(["state", "not in", card.excludeStates]);
        }
        if (card.dated && this.state.dateFrom) {
            domain.push(["date", ">=", this.state.dateFrom]);
        }
        if (card.dated && this.state.dateTo) {
            domain.push(["date", "<=", this.state.dateTo]);
        }
        return domain;
    }

    openRecord(card) {
        if (!card.model) {
            return;
        }
        return this.action.doAction({
            type: "ir.actions.act_window",
            name: card.label,
            res_model: card.model,
            views: card.views || [[false, "list"], [false, "form"]],
            domain: this.domainFor(card),
            target: "current",
        });
    }

    openStage(model, stage) {
        return this.openRecord({
            label: stage.label,
            model,
            dated: true,
            states: [stage.key],
        });
    }

    openCompliance(row) {
        if (row.code === "unauthorized") {
            return this.openRecord({
                label: row.name,
                model: "ie.transaction.exception",
                domain: [["state", "in", ["approved", "expired"]]],
            });
        }
        if (row.code === "critical") {
            return this.openRecord({
                label: row.name,
                model: "ie.compliance.exception",
                domain: [["severity", "=", "critical"], ["state", "=", "open"]],
            });
        }
        return this.openRecord({
            label: row.name,
            model: "ie.compliance.kpi",
            domain: [["code", "=", row.code]],
            views: [[false, "graph"], [false, "list"]],
        });
    }

    openHistory() {
        return this.openRecord({
            label: _t("Compliance KPIs"),
            model: "ie.compliance.kpi",
            views: [[false, "graph"], [false, "list"]],
        });
    }
}

registry.category("actions").add("ie_master_dashboard", IeDashboard);
registry.category("actions").add("ie_compliance_dashboard", IeDashboard);
