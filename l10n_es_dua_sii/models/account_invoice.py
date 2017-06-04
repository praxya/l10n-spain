# -*- coding: utf-8 -*-
# (c) 2017 Consultoría Informática Studio 73 S.L.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from openerp import fields, models, api

class AccountInvoice(models.Model):
    _inherit = 'account.invoice'

    @api.multi
    def _get_invoices(self):
        res = super(AccountInvoice, self)._get_invoices()
        if res.get('FacturaRecibida', False) and self.is_dua_sii_invoice():
                res['FacturaRecibida']['TipoFactura'] = 'F5'
                res['FacturaRecibida'].pop('FechaOperacion', None)
        return res

    @api.multi
    def is_dua_sii_invoice(self):
        """
        :return:
        """
        self.ensure_one()
        if self.fiscal_position.name == u'Importación con DUA':
            if self.tax_line.filtered(
                    lambda x: x.tax_code_id.code in
                    ['DIBYSCC21', 'DIBYSCC10', 'DIBYSCC04']
            ):
                return True
        return False

    @api.multi
    def is_sii_invoice(self):
        """ is_sii_invoice() -> bool
            Check if the invoice must be not sended to AEAT because is
            a supplier DUA invoice, in that case should be sended
            the freight forwarder invoice instead.
            :param
            :return: bool
        """
        self.ensure_one()
        if self.fiscal_position.name == u'Importación con DUA' and \
                not self.is_dua_sii_invoice():
            return False
        return True
