# -*- coding: utf-8 -*-
# Copyright 2017 Ignacio Ibeas <ignacio@acysos.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from openerp import models, fields, api
from requests import Session
from zeep import Client
from zeep.transports import Transport
from zeep.plugins import HistoryPlugin
from datetime import datetime


class AccountInvoice(models.Model):
    _inherit = 'account.invoice'
    
    sii_sent = fields.Boolean('SII Sent')
    sii_return = fields.Text('SII Return')
    
    @api.multi
    def _change_date_format(self, date):
        datetimeobject = datetime.strptime(date,'%Y-%m-%d')
        new_date = datetimeobject.strftime('%d-%m-%Y')
        return new_date
    
    @api.multi
    def _get_header(self, company, TipoComunicacion):
        header = {
            "IDVersionSii": company.sii_version,
            "Titular": {
                "NombreRazon": company.name,
                "NIF": company.vat[2:]},
            "TipoComunicacion": TipoComunicacion
        }
        return header
    
    @api.multi
    def _get_tax_line_req(self, tax_type, line, line_taxes):
        taxes = False
        if len(line_taxes) > 1:
            req_ids = line_taxes.search([('description', 'in', [
                'P_REQ5.2', 'S_REQ52', 'P_REQ14', 'S_REQ14', 'P_REQ05',
                'S_REQ05'])])
            for tax in line_taxes:
                if tax in req_ids:
                    price = line.price_unit * (1 - (
                        line.discount or 0.0) / 100.0)
                    taxes = tax.compute_all(
                        price, line.quantity, line.product_id,
                        line.invoice_id.partner_id)
                    taxes['percentage'] = tax.amount
                    return taxes
        return taxes
    
    @api.multi
    def _get_sii_tax_line(self, tax_line, line, line_taxes):
        tax_type = tax_line.amount * 100
        tax_line_req = self._get_tax_line_req(tax_type, line, line_taxes)
        taxes = tax_line.compute_all(
            (line.price_unit * (1 - (line.discount or 0.0) / 100.0)),
            line.quantity, line.product_id, line.invoice_id.partner_id)
        if tax_line_req:
            TipoRecargo = tax_line_req['percentage'] * 100
            CuotaRecargo = tax_line_req['taxes'][0]['amount']
        else:
            TipoRecargo = 0
            CuotaRecargo = 0
        tax_sii = {
            "TipoImpositivo":tax_type,
            "BaseImponible":taxes['total'],
            "CuotaRepercutida":taxes['taxes'][0]['amount'],
            "TipoRecargoEquivalencia":TipoRecargo,
            "CuotaRecargoEquivalencia":CuotaRecargo
        }
        return tax_sii
    
    @api.multi
    def _update_sii_tax_line(self, taxes, tax_line, line, line_taxes):
        tax_type = tax_type = tax_line.amount * 100
        tax_line_req = self._get_tax_line_req(tax_type, line, line_taxes)
        taxes = tax_line.compute_all(
            (line.price_unit * (1 - (line.discount or 0.0) / 100.0)),
            line.quantity, line.product_id, line.invoice_id.partner_id)
        if tax_line_req:
            TipoRecargo = tax_line_req['percentage'] * 100
            CuotaRecargo = tax_line_req['taxes'][0]['amount']
        else:
            TipoRecargoEquivalencia = 0
            CuotaRecargoEquivalencia = 0
        taxes[str(tax_type)]['BaseImponible'] += taxes['total']
        taxes[str(tax_type)]['CuotaRepercutida'] += taxes['taxes'][0]['amount']
        taxes[str(tax_type)]['TipoRecargoEquivalencia'] += TipoRecargo
        taxes[str(tax_type)]['CuotaRecargoEquivalencia'] += CuotaRecargo
        return taxes

    @api.multi
    def _get_sii_out_taxes(self, invoice):
        taxes_sii = {}
        taxes_f = {}
        taxes_to = {}
        for line in invoice.invoice_line:
            for tax_line in line.invoice_line_tax_id:
                if tax_line.description in [
                    'S_IVA21B', 'S_IVA10B', 'S_IVA4B', 'S_IVA0', 'S_IVA0_ISP',
                    'S_IVA_NS']:
                    if 'DesgloseFactura' not in taxes_sii:
                        taxes_sii['DesgloseFactura'] = {}
                    if tax_line.description in [
                        'S_IVA21B', 'S_IVA10B', 'S_IVA4B', 'S_IVA0']:
                        if 'Sujeta' not in taxes_sii['DesgloseFactura']:
                            taxes_sii['DesgloseFactura']['Sujeta'] = {}
                        if tax_line.description in ['S_IVA0']:
                            if 'Exenta' not in taxes_sii['DesgloseFactura'][
                                'Sujeta']:
                                taxes_sii['DesgloseFactura']['Sujeta'][
                                    'Exenta'] = {'BaseImponible':taxes['total']}
                            else:
                                taxes_sii['DesgloseFactura']['Sujeta'][
                                    'Exenta']['BaeImponible'] += taxes['total']
#                         TODO Facturas No sujetas
                        if tax_line.description in [
                            'S_IVA21B', 'S_IVA10B', 'S_IVA4B', 'S_IVA0_ISP']:
                            if 'NoExenta' not in taxes_sii['DesgloseFactura'][
                                'Sujeta']:
                                taxes_sii['DesgloseFactura']['Sujeta'][
                                    'NoExenta'] = {}
                            if tax_line.description in ['S_IVA0_ISP']:
                                TipoNoExenta = 'S2'
                            else:
                                TipoNoExenta = 'S1'  
                            taxes_sii['DesgloseFactura']['Sujeta']['NoExenta'][
                                'TipoNoExenta'] = TipoNoExenta
                            if 'DesgloseIVA' not in taxes_sii[
                                'DesgloseFactura']['Sujeta']['NoExenta']:
                                taxes_sii['DesgloseFactura']['Sujeta'][
                                    'NoExenta']['DesgloseIVA'] = {}
                                taxes_sii['DesgloseFactura']['Sujeta'][
                                    'NoExenta']['DesgloseIVA'][
                                        'DetalleIVA'] = []
                            tax_type = tax_line.amount * 100
                            if tax_type not in taxes_f:
                                taxes_f[str(tax_type)] = self._get_sii_tax_line(
                                    tax_line, line, line.invoice_line_tax_id)
                            else:
                                taxes_f = self._update_sii_tax_line(
                                    taxes_f, tax_line, line,
                                    line.invoice_line_tax_id)
                if tax_line.description in [
                    'S_IVA21S', 'S_IVA10S', 'S_IVA4S']:
                    if 'DesgloseTipoOperacion' not in taxes_sii:
                        taxes_sii['DesgloseTipoOperacion'] = {}
                    if 'PrestacionServicios' not in taxes_sii[
                        'DesgloseTipoOperacion']:
                        taxes_sii['DesgloseTipoOperacion'][
                            'PrestacionServicios'] = {}
                    if 'Sujeta' not in taxes_sii['DesgloseTipoOperacion'][
                            'PrestacionServicios']:
                        taxes_sii['DesgloseTipoOperacion'][
                            'PrestacionServicios']['Sujeta'] = {}
#                     TODO l10n_es no tiene impuesto exento de servicios
#                     if tax_line.description in ['']:
#                         if 'Exenta' not in taxes_sii['DesgloseFactura'][
#                             'Sujeta']:
#                             taxes_sii['DesgloseFactura']['Sujeta'][
#                                 'Exenta'] = {'BaseImponible':taxes['total']}
#                         else:
#                             taxes_sii['DesgloseFactura']['Sujeta'][
#                                 'Exenta']['BaeImponible'] += taxes['total']
#                     TODO Facturas no sujetas
                    if tax_line.description in [
                        'S_IVA21S', 'S_IVA10S', 'S_IVA4S']:
                        if 'NoExenta' not in taxes_sii['DesgloseTipoOperacion'][
                            'PrestacionServicios']['Sujeta']:
                            taxes_sii['DesgloseTipoOperacion'][
                            'PrestacionServicios']['Sujeta']['NoExenta'] = {}
#                             TODO l10n_es_ no tiene impuesto ISP de servicios
#                             if tax_line.description in ['S_IVA0S_ISP']:
#                                 TipoNoExenta = 'S2'
#                             else:
                            TipoNoExenta = 'S1'
                            taxes_sii['DesgloseTipoOperacion'][
                                'PrestacionServicios']['Sujeta']['NoExenta'][
                                    'TipoNoExenta'] = TipoNoExenta
                        if 'DesgloseIVA' not in taxes_sii[
                            'DesgloseTipoOperacion']['PrestacionServicios'][
                                'Sujeta']['NoExenta']:
                            taxes_sii['DesgloseTipoOperacion'][
                                'PrestacionServicios']['Sujeta']['NoExenta'][
                                    'DesgloseIVA'] = {}
                            taxes_sii['DesgloseTipoOperacion'][
                                'PrestacionServicios']['Sujeta']['NoExenta'][
                                    'DesgloseIVA']['DetalleIVA'] = []
                            tax_type = tax_line.amount * 100
                            if tax_type not in taxes_to:
                                taxes_to[str(tax_type)] = self._get_sii_tax_line(
                                    tax_line, line, line.invoice_line_tax_id)
                            else:
                                taxes_to = self._update_sii_tax_line(
                                    taxes_to, tax_line, line,
                                    line.invoice_line_tax_id)
        
        if len(taxes_f) > 0:
            for key, line in taxes_f.iteritems():
                taxes_sii['DesgloseFactura']['Sujeta']['NoExenta'][
                    'DesgloseIVA']['DetalleIVA'].append(line)
        if len(taxes_to) > 0:
            for key, line in taxes_to.iteritems():
                taxes_sii['DesgloseTipoOperacion']['PrestacionServicios'][
                    'Sujeta']['NoExenta']['DesgloseIVA'][
                        'DetalleIVA'].append(line)

        return taxes_sii

    @api.multi
    def _get_sii_in_taxes(self, invoice): 
        taxes_sii = {}
        taxes_f = {}
        for line in invoice.invoice_line:
            for tax_line in line.invoice_line_tax_id:
                if tax_line.description in [
                    'P_IVA21_BC', 'P_IVA21_SC', 'P_IVA21_ISP', 'P_IVA10_BC',
                    'P_IVA10_SC', 'P_IVA10_ISP', 'P_IVA4_BC', 'P_IVA4_SC',
                    'P_IVA4_ISP', 'P_IVA0_BC', 'P_IVA0_NS']:
                    if 'NoExenta' not in taxes_sii:
                        taxes_sii['NoExenta'] = {}
                    if tax_line.description in [
                        'P_IVA21_ISP', 'P_IVA10_ISP', 'P_IVA4_ISP']:
                        TipoNoExenta = 'S2'
                    else:
                        TipoNoExenta = 'S1'
                    taxes_sii['NoExenta']['TipoNoExenta'] = TipoNoExenta
                    if 'DesgloseIVA' not in taxes_sii['NoExenta']:
                        taxes_sii['NoExenta']['DesgloseIVA'] = {}
                        taxes_sii['NoExenta']['DesgloseIVA']['DetalleIVA'] = []
                    tax_type = tax_line.amount * 100
                    if tax_type not in taxes_f:
                        taxes_f[str(tax_type)] = self._get_sii_tax_line(
                            tax_line, line, line.invoice_line_tax_id)
                    else:
                        taxes_f = self._update_sii_tax_line(
                            taxes_f, tax_line, line,
                            line.invoice_line_tax_id)

        for key, line in taxes_f.iteritems():
            taxes_sii['NoExenta']['DesgloseIVA']['DetalleIVA'].append(line)
                    
    
    @api.multi
    def _get_invoices(self, company, invoice):
        invoice_date = self._change_date_format(invoice.date_invoice)
        Ejercicio = fields.Date.from_string(
            invoice.period_id.fiscalyear_id.date_start).year
        Periodo = '%02d' % fields.Date.from_string(
            invoice.period_id.date_start).month
        
        if invoice.type in ['out_invoice']:
            TipoDesglose = self._get_sii_out_taxes(invoice)
            invoices = {
                "IDFactura":{
                    "IDEmisorFactura": {
                        "NIF": company.vat[2:]
                        },
                    "NumSerieFacturaEmisor": invoice.number,
                    "FechaExpedicionFacturaEmisor": invoice_date},
                "PeriodoImpositivo": {
                    "Ejercicio": Ejercicio,
                    "Periodo": Periodo
                    },
                "FacturaExpedida": {
                    "TipoFactura": "F1",
                    "ClaveRegimenEspecialOTrascendencia": "01",
                    "DescripcionOperacion": invoice.name,
                    "Contraparte": {
                        "NombreRazon": invoice.partner_id.name,
                        "NIF": invoice.partner_id.vat[2:]
                        },
                    "TipoDesglose": TipoDesglose
                }
            }

        if invoice.type in ['in_invoice']:
            TipoDesglose = self._get_sii_in_taxes(invoice)
            invoices = {
                "IDFactura":{
                    "IDEmisorFactura": {
                        "NIF": invoice.partner_id.vat[2:]
                        },
                    "NumSerieFacturaEmisor": invoice.supplier_invoice_number,
                    "FechaExpedicionFacturaEmisor": invoice_date},
                "PeriodoImpositivo": {
                    "Ejercicio": Ejercicio,
                    "Periodo": Periodo
                    },
                "FacturaRecibida": {
                    "TipoFactura": "F1",
                    "ClaveRegimenEspecialOTrascendencia": "01",
                    "DescripcionOperacion": invoice.name,
                    "TipoDesglose": TipoDesglose,
                    "Contraparte": {
                        "NombreRazon": invoice.partner_id.name,
                        "NIF": invoice.partner_id.vat[2:]
                        },
                    "FechaRegContable": invoice_date,
                    "CuotaDeducible": invoice.amount_tax
                }
            }
            
            
        return invoices
    
    @api.multi
    def _connect_sii(self, wsdl):
        publicCrt = self.env['ir.config_parameter'].get_param(
            'l10n_es_aeat_sii.publicCrt', False)
        privateKey = self.env['ir.config_parameter'].get_param(
            'l10n_es_aeat_sii.privateKey', False)

        session = Session()
        session.cert = (publicCrt, privateKey)
        transport = Transport(session=session)

        history = HistoryPlugin()
        client = Client(wsdl=wsdl,transport=transport,plugins=[history])
        return client

    @api.multi
    def _send_invoice_to_sii(self):
        for invoice in self:
            company = invoice.company_id
            port_name = ''
            if invoice.type == 'out_invoice':
                wsdl = company.wsdl_out
                client = self._connect_sii(wsdl)
                port_name = 'SuministroFactEmitidas'
                if company.sii_test:
                    port_name += 'Pruebas'
            elif invoice.type == 'in_invoice':
                wsdl = company.wsdl_in
                client = self._connect_sii(wsdl)
                port_name = 'SuministroFactRecibidas'
                if company.sii_test:
                    port_name += 'Pruebas'
#             TODO Property Investiment 
#             elif invoice == 'Property Investiment':
#                 wsdl = company.wsdl_pi
#                 client = self._connect_sii(wsdl)
#                 port_name = 'SuministroBienesInversion'
#                 if company.sii_test:
#                     port_name += 'Pruebas'
            elif invoice.fiscal_position.id == self.env.ref(
                'account.fp_intra').id:
                wsdl = company.wsdl_ic
                client = self._connect_sii(wsdl)
                port_name = 'SuministroOpIntracomunitarias'
                if company.sii_test:
                    port_name += 'Pruebas'                
            serv = client.bind('siiService', port_name)
            if not invoice.sii_sent:
                TipoComunicacion = 'A0'
            else:
                TipoComunicacion = 'A1'
            
            header = self._get_header(company, TipoComunicacion)
            invoices = self._get_invoices(company, invoice)
            try:
                if invoice.type == 'out_invoice':
                    res = serv.SuministroLRFacturasEmitidas(
                        header, invoices)
                elif invoice.type == 'in_invoice':
                    res = serv.SuministroLRFacturasRecibidas(
                        header, invoices)
#                 TODO Factura Bienes de inversión
#                 elif invoice == 'Property Investiment':
#                     res = serv.SuministroLRBienesInversion(
#                         header, invoices)
#                 TODO Facturas intracomunitarias
#                 elif invoice.fiscal_position.id == self.env.ref(
#                     'account.fp_intra').id:
#                     res = serv.SuministroLRDetOperacionIntracomunitaria(
#                         header, invoices)
                if res['EstadoEnvio'] == 'Correcto':
                    self.sii_sent = True
                self.sii_return = res
            except Exception as fault:
                self.sii_return = fault

    @api.multi
    def invoice_validate(self):
        res = super(AccountInvoice, self).invoice_validate()
        
        if not self.company_id.use_connector:
            self._send_invoice_to_sii()
#         TODO 
#         else:
#             Use connector
        
        return res
