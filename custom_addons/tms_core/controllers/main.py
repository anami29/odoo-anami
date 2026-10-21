from odoo import http
from odoo.http import request

from odoo.addons.web.controllers.dataset import DataSet

from ..models.tms_guard import tms_guard_rpc


class TmsDataSet(DataSet):
    """Web client entry points run the TMS remote call guard (BR-AUD-06)."""

    @http.route()
    def call_kw(self, model, method, args, kwargs, path=None):
        tms_guard_rpc(request.env, model, method, args, kwargs)
        return super().call_kw(model, method, args, kwargs, path=path)

    @http.route()
    def call_button(self, model, method, args, kwargs, path=None):
        tms_guard_rpc(request.env, model, method, args, kwargs)
        return super().call_button(model, method, args, kwargs, path=path)
