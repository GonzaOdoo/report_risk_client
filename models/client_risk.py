from odoo import models, fields,api
from collections import defaultdict
import io
import base64
from datetime import datetime
import xlsxwriter

class RiskReport(models.TransientModel):
    _name = 'account.report.risk.client'
    _description = 'Reporte riesgo de clientes'

    name = fields.Char("Nombre",default="Reporte de riesgo")
    date = fields.Date('Fecha', default=fields.Date.today)
    partner_ids = fields.Many2many('res.partner','Cliente')
    # Campos para almacenar los resultados del reporte
    line_ids = fields.One2many('account.report.risk.client.line', 'wizard_id', string='Líneas del reporte')

    def create_report(self):
        self.ensure_one()
        lines = []
        excluded_product_tmpl_ids = [34534, 34535, 6, 34185, 34497, 38838, 34498, 34206]
        # Si no hay partners seleccionados, tomar todos los que tengan facturas o deudas
        partners = self.partner_ids
        if not partners:
            partners = self.env['res.partner'].search([('customer_rank', '>', 0)])

        # Convertir fecha a datetime para comparaciones
        report_date = self.date

        for partner in partners:
            # 1. Pendiente ($) = Valor de órdenes de venta pendientes (confirmadas, no facturadas completamente)
            pending_amount = 0.0
            
            # Dominio para sale.order.line
            line_domain = [
                ('order_id.partner_id', '=', partner.id),
                ('product_id.product_tmpl_id', 'not in', excluded_product_tmpl_ids),  # Excluir productos
                ('order_id.date_order', '<=', report_date),  # Fecha del pedido <= fecha del reporte
                ('order_id.state', 'in', ['sale', 'done']),
            ]
    
            sale_order_lines = self.env['sale.order.line'].search(line_domain)
    
            for line in sale_order_lines:
                # Considerar solo la parte no facturada (podría haber sido facturada parcialmente)
                qty_to_invoice = line.qty_to_deliver  # Este campo ya considera lo facturado
                if qty_to_invoice > 0:
                    # Usar el precio unitario acordado
                    pending_amount += line.price_subtotal

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
            balance = sum(mov_lines.mapped('balance')) # En moneda de la compañía

            # 3. Subtotal = Pendiente + Saldo
            subtotal = pending_amount + balance

            # 4. Cheques en cartera = Pagos con cheques no depositados aún
            # Suponemos que los cheques se registran como pagos con método de pago "Cheque"
            # y están en estado "draft" o "sent" (no depositados)
            cheque_payments = self.env['account.payment'].search([
                ('partner_id', '=', partner.id),
                ("state", "=", "posted"),
                ('l10n_latam_check_payment_date', '>=', report_date),
                ("journal_id.inbound_payment_method_line_ids.payment_method_id.code", "in", ["new_third_party_checks", "in_third_party_checks"]),  # Cheques no depositados
            ])
            cheques = sum(cheque_payments.mapped('amount'))

            # 5. Saldo + Cheques
            saldo_cheques = balance + cheques

            # Crear línea del reporte
            if pending_amount != 0 or balance != 0 or cheques != 0:
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


    def generate_excel_report(self):
        self.ensure_one()
        # Crear archivo en memoria
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        worksheet = workbook.add_worksheet('Riesgo de Clientes')
    
        # Formatos
        header_format = workbook.add_format({
            'bold': True,
            'align': 'center',
            'valign': 'vcenter',
            'bg_color': '#2C3E50',
            'font_color': 'white',
            'border': 1,
            'text_wrap': False
        })
    
        text_format = workbook.add_format({'border': 1, 'align': 'left'})
        amount_format = workbook.add_format({'num_format': '#,##0.00', 'border': 1})
        date_format = workbook.add_format({'num_format': 'dd/mm/yyyy', 'border': 1})
        title_format = workbook.add_format({
        'bold': True,
        'font_size': 14,
        'align': 'left',
        'valign': 'vcenter',
        })
        subtitle_format = workbook.add_format({
            'align': 'left',
            'valign': 'vcenter',
            'font_size': 10,
        })

        current_row = 0
        worksheet.write(current_row, 7, 'Reporte de Riesgo de Clientes', title_format)
        current_row += 1  # Dejamos una fila de separación
        if self.date:
            date_str = self.date.strftime('%d/%m/%Y')
            worksheet.write(current_row, 7, f'A fecha: {date_str}', subtitle_format)
            current_row += 1

        if self.partner_ids:
            partner_names = ', '.join(self.partner_ids.mapped('name'))
            # Dividir en líneas si es muy largo
            max_chars = 80
            if len(partner_names) > max_chars:
                partner_names = partner_names[:max_chars] + '...'
            worksheet.write(current_row, 7, f'Clientes seleccionados: {partner_names}', subtitle_format)
            current_row += 2  # Dejar espacio antes de la tabla
        else:
            current_row += 1
        # Cabeceras
        headers = [
            'Cliente',
            'Pendiente ($)',
            'Saldo del Cliente',
            'Subtotal',
            'Cheques en Cartera',
            'Saldo + Cheques'
        ]
    
        # Escribir cabeceras
        for col, header in enumerate(headers):
            worksheet.write(0, col, header, header_format)
        current_row = 1
        # Datos del reporte
        row = current_row
        for line in self.line_ids:
            worksheet.write(row, 0, line.partner_id.name or '', text_format)
            worksheet.write(row, 1, line.pending_amount, amount_format)
            worksheet.write(row, 2, line.balance, amount_format)
            worksheet.write(row, 3, line.subtotal, amount_format)
            worksheet.write(row, 4, line.cheques, amount_format)
            worksheet.write(row, 5, line.saldo_cheques, amount_format)
            row += 1
    
        # Ajustar ancho de columnas
        worksheet.set_column('A:A', 30)  # Cliente
        worksheet.set_column('B:F', 18)  # Montos
    
        # Agregar totales al final
        if row > 1:
            total_pending = sum(self.line_ids.mapped('pending_amount'))
            total_balance = sum(self.line_ids.mapped('balance'))
            total_subtotal = sum(self.line_ids.mapped('subtotal'))
            total_cheques = sum(self.line_ids.mapped('cheques'))
            total_saldo_cheques = sum(self.line_ids.mapped('saldo_cheques'))
    
            worksheet.write(row, 0, 'TOTALES', header_format)
            worksheet.write(row, 1, total_pending, amount_format)
            worksheet.write(row, 2, total_balance, amount_format)
            worksheet.write(row, 3, total_subtotal, amount_format)
            worksheet.write(row, 4, total_cheques, amount_format)
            worksheet.write(row, 5, total_saldo_cheques, amount_format)
    
        workbook.close()
        output.seek(0)
        file_data = base64.b64encode(output.read())
        output.close()
    
        # Nombre del archivo con fecha
        date_str = self.date.strftime('%Y%m%d') if self.date else 'sin_fecha'
        filename = f"Reporte_Riesgo_Clientes_{date_str}.xlsx"
    
        # Crear adjunto
        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'datas': file_data,
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'res_model': self._name,
            'res_id': self.id,
            'public': False,
        })
    
        # Retornar acción de descarga
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'self',
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
    sale_order_line_ids = fields.Many2many(
        'sale.order.line',
        string='Líneas de pedido pendientes',
        compute='_compute_sale_order_lines'
    )
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
    def _compute_sale_order_lines(self):
        report_date = self.wizard_id.date
        excluded_product_tmpl_ids = [34534, 34535, 6, 34185, 34497, 38838, 34498, 34206]
        for line in self:
            if not report_date:
                line.write({'sale_order_line_ids': [(5, 0, 0)]})  # limpia todos
                continue
    
            # Dominio para líneas de pedido que contribuyen al "pendiente"
            so_line_domain = [
                ('order_id.partner_id', '=', line.partner_id.id),
                ('product_id.product_tmpl_id', 'not in', excluded_product_tmpl_ids),
                ('order_id.date_order', '<=', report_date),
            ]
            all_lines = self.env['sale.order.line'].search(so_line_domain)

            # Filtrar en Python las que tienen qty_to_deliver > 0
            so_lines = all_lines.filtered(lambda l: l.qty_to_deliver > 0)
    
            line.write({
                'sale_order_line_ids': [(6, 0, so_lines.ids)]
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
                ('l10n_latam_check_payment_date', '>=', report_date),
                ("journal_id.inbound_payment_method_line_ids.payment_method_id.code", "in", ["new_third_party_checks", "in_third_party_checks"]),
            ]
            line.payment_ids = self.env['account.payment'].search(payment_domain)

    def action_view_pending_lines(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Líneas pendientes - {self.partner_id.name}',
            'res_model': 'sale.order.line',
            'view_mode': 'tree,form',
            'domain': [('id', 'in', self.sale_order_line_ids.ids)],
            'context': {'create': False, 'edit': False},
            'target': 'current',
        }

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