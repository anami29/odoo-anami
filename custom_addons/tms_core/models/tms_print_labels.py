"""BR-PRN-08: the LR and POD form carry a second language set per branch."""
from odoo import models

# label -> (Marathi, Hindi)
LABELS = {
    "CONSIGNMENT NOTE": ("माल वाहतूक पावती", "माल रसीद (बिल्टी)"),
    "CONSIGNOR COPY": ("प्रेषक प्रत", "प्रेषक प्रति"),
    "CONSIGNEE COPY": ("प्रेषिती प्रत", "प्रेषिती प्रति"),
    "DRIVER COPY": ("चालक प्रत", "चालक प्रति"),
    "OFFICE COPY": ("कार्यालय प्रत", "कार्यालय प्रति"),
    "LR No.": ("पावती क्र.", "बिल्टी सं."),
    "Consignor": ("प्रेषक", "प्रेषक"),
    "Consignee": ("प्रेषिती", "प्रेषिती"),
    "From": ("पासून", "से"),
    "To": ("पर्यंत", "तक"),
    "Vehicle": ("वाहन", "वाहन"),
    "Freight": ("भाडे", "भाड़ा"),
    "GST payable by": ("जीएसटी देय", "जीएसटी देय"),
    "Goods": ("माल", "माल"),
    "Packing": ("पॅकिंग", "पैकिंग"),
    "Pkgs": ("नग", "नग"),
    "Actual kg": ("प्रत्यक्ष वजन (किलो)", "वास्तविक वजन (कि.ग्रा.)"),
    "Charged kg": ("आकारलेले वजन (किलो)", "प्रभारित वजन (कि.ग्रा.)"),
    "Value": ("मूल्य", "मूल्य"),
    "E-way bill": ("ई-वे बिल", "ई-वे बिल"),
    "Consignor's signature": ("प्रेषकाची सही", "प्रेषक के हस्ताक्षर"),
    "Driver's signature": ("चालकाची सही", "चालक के हस्ताक्षर"),
    "For": ("करिता", "की ओर से"),
    "Proof of delivery": ("माल पोहोच पावती", "सुपुर्दगी प्रमाण"),
    "LR": ("पावती", "बिल्टी"),
    "Trip": ("फेरी", "यात्रा"),
    "Packages sent": ("पाठवलेले नग", "भेजे गए नग"),
    "Packages received": ("मिळालेले नग", "प्राप्त नग"),
    "Received by (name)": ("स्वीकारणाऱ्याचे नाव", "प्राप्तकर्ता (नाम)"),
    "Shortage (packages)": ("कमी नग", "कमी (नग)"),
    "Damage (packages and remarks)": ("नुकसान (नग व शेरा)", "क्षति (नग व टिप्पणी)"),
    "Date and time": ("दिनांक व वेळ", "दिनांक व समय"),
    "Consignee's stamp": ("प्रेषितीचा शिक्का", "प्रेषिती की मुहर"),
    "Signature": ("सही", "हस्ताक्षर"),
}
COLUMN = {"mr": 0, "hi": 1}


class ResCompany(models.Model):
    _inherit = "res.company"

    def tms_print_label(self, text):
        """The English label, followed by the branch's second print language where one is set and known."""
        self.ensure_one()
        lang = (self.tms_second_language_id.code or "")[:2]
        if lang not in COLUMN or text not in LABELS:
            return text
        return "%s / %s" % (text, LABELS[text][COLUMN[lang]])
