from odoo import models, fields,api
from collections import defaultdict
class RiskReport(models.TransientModel):
    _name = 'account.report.risk.client'
    _description = 'Reporte riesgo de clientes'

    date = fields.Date('Fecha')
    partner_ids = fields.Many2many('res.partner','Cliente')
    # Campos para almacenar los resultados del reporte
    line_ids = fields.One2many('account.report.risk.client.line', 'wizard_id', string='Líneas del reporte')

    def create_report(self):
        self.ensure_one()
        lines = []
        # Si no hay partners seleccionados, tomar todos los que tengan facturas o deudas
        partners = self.partner_ids
        if not partners:
            partners = self.env['res.partner'].search([('customer_rank', '>', 0)])

        # Convertir fecha a datetime para comparaciones
        report_date = self.date

        for partner in partners:
            # 1. Pendiente ($) = Valor de órdenes de venta pendientes (confirmadas, no facturadas completamente)
            pending_amount = 0.0
            sale_orders = self.env['sale.order'].search([
                ('partner_id', '=', partner.id),
                ('state', 'in', ['sale', 'done']),  # Confirmadas
                ('date_order', '<=', report_date),
                ('invoice_status', '!=', 'invoiced'),  # No completamente facturadas
            ])
            for so in sale_orders:
                # Tomar solo las líneas pendientes de facturación
                for line in so.order_line:
                    qty_to_invoice = line.product_uom_qty - line.qty_invoiced
                    if qty_to_invoice > 0:
                        pending_amount += line.price_unit * qty_to_invoice

            # 2. Saldo del cliente = Saldo contable en cuentas por cobrar (a la fecha)
            account_type = 'asset_receivable'
            account_ids = partner.property_account_receivable_id.ids
            domain = [
                ('partner_id', '=', partner.id),
                ('account_id', 'in', account_ids),
                ('date', '<=', report_date),
                ('parent_state', '=', 'posted'),  # Solo asientos contables publicados
            ]
            mov_lines = self.env['account.move.line'].search(domain)
            balance = sum(mov_lines.mapped('balance')) * -1  # En moneda de la compañía

            # 3. Subtotal = Pendiente + Saldo
            subtotal = pending_amount + balance

            # 4. Cheques en cartera = Pagos con cheques no depositados aún
            # Suponemos que los cheques se registran como pagos con método de pago "Cheque"
            # y están en estado "draft" o "sent" (no depositados)
            cheque_payments = self.env['account.payment'].search([
                ('partner_id', '=', partner.id),
                ("state", "=", "posted"), 
                ("l10n_latam_check_current_journal_id.inbound_payment_method_line_ids.payment_method_id.code", "in", ["new_third_party_checks", "in_third_party_checks"]),  # Cheques no depositados
            ])
            cheques = sum(cheque_payments.mapped('amount'))

            # 5. Saldo + Cheques
            saldo_cheques = balance + cheques

            # Crear línea del reporte
            lines.append((0, 0, {
                'partner_id': partner.id,
                'pending_amount': pending_amount,
                'balance': balance,
                'subtotal': subtotal,
                'cheques': cheques,
                'saldo_cheques': saldo_cheques,
            }))

        # Limpiar líneas anteriores
        self.line_ids.unlink()
        self.write({'line_ids': lines})

        # Retornar acción para ver el reporte
        return {
            'type': 'ir.actions.act_window',
            'name': 'Reporte de Riesgo de Clientes',
            'res_model': 'account.report.risk.client',
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'current',
            'context': {'form_view_initial_mode': 'readonly'},
        }


class RiskReportLine(models.TransientModel):
    _name = 'account.report.risk.client.line'
    _description = 'Línea del reporte de riesgo de clientes'

    wizard_id = fields.Many2one('account.report.risk.client', required=True, ondelete='cascade')
    partner_id = fields.Many2one('res.partner', string='Cliente', required=True)
    
    # Montos
    pending_amount = fields.Monetary('Pendiente ($)', currency_field='currency_id')
    balance = fields.Monetary('Saldo del Cliente', currency_field='currency_id')
    subtotal = fields.Monetary('Subtotal', currency_field='currency_id')
    cheques = fields.Monetary('Cheques', currency_field='currency_id')
    saldo_cheques = fields.Monetary('Saldo + Cheques', currency_field='currency_id')
    currency_id = fields.Many2one('res.currency', default=lambda self: self.env.company.currency_id)

    # Campos para almacenar IDs de movimientos (no se muestran, solo para acción)
    sale_order_ids = fields.Many2many('sale.order', string='Órdenes pendientes', compute='_compute_sale_orders')
    move_line_ids = fields.Many2many('account.move.line', string='Movimientos de saldo', compute='_compute_move_lines')
    payment_ids = fields.Many2many('account.payment', string='Pagos (cheques)', compute='_compute_payments')

    def _compute_sale_orders(self):
        report_date = self.wizard_id.date
        for line in self:
            so_domain = [
                ('partner_id', '=', line.partner_id.id),
                ('state', 'in', ['sale', 'done']),
                ('date_order', '<=', report_date),
                ('invoice_status', '!=', 'invoiced'),
            ]
            sale_orders = self.env['sale.order'].search(so_domain)
            line.write({
                'sale_order_ids': [(6, 0, sale_orders.ids)]
            })

    def _compute_move_lines(self):
        # Recalcular movimientos contables (saldo)
        report_date = self.wizard_id.date
        for line in self:
            account_ids = line.partner_id.property_account_receivable_id.ids
            domain = [
                ('partner_id', '=', line.partner_id.id),
                ('account_id', 'in', account_ids),
                ('date', '<=', report_date),
                ('parent_state', '=', 'posted'),
            ]
            line.move_line_ids = self.env['account.move.line'].search(domain)

    def _compute_payments(self):
        # Recalcular pagos (cheques no depositados)
        report_date = self.wizard_id.date
        for line in self:
            payment_domain = [
                ('partner_id', '=', line.partner_id.id),
                ("state", "=", "posted"), 
                ("l10n_latam_check_current_journal_id.inbound_payment_method_line_ids.payment_method_id.code", "in", ["new_third_party_checks", "in_third_party_checks"])
            ]
            line.payment_ids = self.env['account.payment'].search(payment_domain)


    def action_view_pending_orders(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Órdenes pendientes - {self.partner_id.name}',
            'res_model': 'sale.order',
            'view_mode': 'tree,form',
            'domain': [('id', 'in', self.sale_order_ids.ids)],
            'context': {'create': False, 'edit': False},
            'target': 'current',
        }

    def action_view_balance_moves(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Movimientos de saldo - {self.partner_id.name}',
            'res_model': 'account.move.line',
            'view_mode': 'tree,form',
            'domain': [('id', 'in', self.move_line_ids.ids)],
            'context': {'create': False, 'edit': False},
            'target': 'current',
        }

    def action_view_cheques(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Cheques no depositados - {self.partner_id.name}',
            'res_model': 'account.payment',
            'view_mode': 'tree,form',
            'domain': [('id', 'in', self.payment_ids.ids)],
            'context': {'create': False, 'edit': False},
            'target': 'current',
        }