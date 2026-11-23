from odoo.tests import tagged

from .test_tms_estimate import EstimateCommon

# valid GSTINs: one of another customer, one of the test customer's PAN in Karnataka
NEW_GSTIN = "27AAACZ9876Q1Z2"
OTHER_STATE_GSTIN = "29AAACK1234A1Z6"


@tagged("post_install", "-at_install")
class TestUiCrmMatch(EstimateCommon):
    """Converting a lead links the customer already registered under its GSTIN, else its PAN."""

    def _lead(self, **vals):
        values = {"name": "Coil moves to Nagpur", "type": "lead", "partner_name": "Konkan Agro Foods Pvt Ltd",
                  "contact_name": "Anil Deshmukh", "email_from": "anil.deshmukh@example.org",
                  "country_id": self.env.ref("base.in").id}
        values.update(vals)
        return self.env["crm.lead"].create(values)

    def _wizard(self, lead):
        return self.env["crm.lead2opportunity.partner"].with_context(
            active_model="crm.lead", active_id=lead.id, active_ids=lead.ids).create({"name": "convert"})

    def _partner_count(self):
        return self.env["res.partner"].with_context(active_test=False).search_count([])

    def test_gstin_match_on_convert(self):
        lead = self._lead(tms_vat=self.customer.vat.lower())
        self.assertEqual(lead._find_matching_partner(), self.customer)
        wizard = self._wizard(lead)
        self.assertEqual(wizard.action, "exist")
        self.assertEqual(wizard.partner_id, self.customer)
        before = self._partner_count()
        wizard.action_apply()
        self.assertEqual(lead.type, "opportunity")
        self.assertEqual(lead.partner_id, self.customer)
        self.assertEqual(self._partner_count(), before)

    def test_gstin_match_when_create_chosen(self):
        lead = self._lead(tms_vat=self.customer.vat)
        wizard = self._wizard(lead)
        wizard.action = "create"
        before = self._partner_count()
        wizard.action_apply()
        self.assertEqual(lead.partner_id, self.customer)
        self.assertEqual(self._partner_count(), before)

    def test_gstin_before_pan(self):
        """A second registration of the same PAN is not taken when the GSTIN itself is registered."""
        sister = self.customer.copy({"name": "Konkan Agro Foods Karnataka", "vat": OTHER_STATE_GSTIN,
                                     "l10n_in_pan": "AAACK1234A"})
        lead = self._lead(tms_vat=OTHER_STATE_GSTIN, tms_pan="AAACK1234A")
        self.assertEqual(lead._find_matching_partner(), sister)
        lead._handle_partner_assignment(create_missing=True)
        self.assertEqual(lead.partner_id, sister)

    def test_pan_match(self):
        lead = self._lead(tms_pan="aaack1234a")
        before = self._partner_count()
        lead._handle_partner_assignment(create_missing=True)
        self.assertEqual(lead.partner_id, self.customer)
        self.assertEqual(self._partner_count(), before)
        # a GSTIN of another state carries the same PAN
        other = self._lead(tms_vat=OTHER_STATE_GSTIN)
        wizard = self._wizard(other)
        self.assertEqual(wizard.partner_id, self.customer)
        wizard.action_apply()
        self.assertEqual(other.partner_id, self.customer)
        self.assertEqual(self._partner_count(), before)

    def test_no_match_creates_customer(self):
        lead = self._lead(tms_vat=NEW_GSTIN, partner_name="Zenith Coils", contact_name=False,
                          email_from="orders@zenith.example.org")
        self.assertFalse(lead._tms_match_partner_by_tax_id())
        wizard = self._wizard(lead)
        self.assertNotEqual(wizard.partner_id, self.customer)
        wizard.action = "create"
        wizard.action_apply()
        self.assertTrue(lead.partner_id)
        self.assertNotEqual(lead.partner_id, self.customer)
        self.assertEqual(lead.partner_id.vat, NEW_GSTIN)
        self.assertEqual(lead.partner_id.name, "Zenith Coils")

    def test_existing_partner_kept(self):
        other = self.env["res.partner"].create({"name": "Chosen customer", "country_id": self.env.ref("base.in").id})
        lead = self._lead(tms_vat=self.customer.vat, partner_id=other.id)
        self.assertEqual(lead._find_matching_partner(), other)
        lead._handle_partner_assignment(create_missing=True)
        self.assertEqual(lead.partner_id, other)
