"""Consent shown before ABHA creation (M1 CRT_ABHA_102).

Sent to ABDM as consent {code: "abha-enrollment", version: "1.4"}.

IMPORTANT: before the NHA functional test, compare this text word for word with the consent
language NHA publishes (sandbox portal > Guidelines) and replace it if anything differs.
Hindi is offered too (CRT_ABHA_103, optional).
"""

VERSION = "1.4"

TEXT = {
    "en": (
        "I, hereby declare that I am voluntarily sharing my Aadhaar Number and demographic information "
        "issued by UIDAI, with National Health Authority (NHA) for the sole purpose of creation of ABHA "
        "number. I understand that my ABHA number can be used and shared for purposes as may be notified "
        "by ABDM from time to time including provision of healthcare services. Further, I am aware that my "
        "personal identifiable information (Name, Address, Age, Date of Birth, Gender and Photograph) may be "
        "made available to the entities working in the National Digital Health Ecosystem (NDHE) which inter "
        "alia includes stakeholders and entities such as healthcare professionals (e.g. doctors), facilities "
        "(e.g. hospitals, laboratories) and data fiduciaries (e.g. health programmes), which are registered "
        "with or linked to the Ayushman Bharat Digital Mission (ABDM), and various processes there under. "
        "I authorize NHA to use my Aadhaar number for performing Aadhaar based authentication with UIDAI as "
        "per the provisions of the Aadhaar (Targeted Delivery of Financial and other Subsidies, Benefits and "
        "Services) Act, 2016 for the aforesaid purpose. I understand that UIDAI will share my e-KYC details, "
        "or response of \"Yes\" with NHA upon successful authentication. I have been duly informed about the "
        "option of using other IDs apart from Aadhaar; however, I consciously choose to use Aadhaar number "
        "for the purpose of availing benefits across the NDHE. I am aware that my personal identifiable "
        "information excluding Aadhaar number / VID number can be used and shared for purposes as mentioned "
        "above. I reserve the right to revoke the given consent at any point of time as per provisions of "
        "Aadhaar Act and Regulations."
    ),
    "hi": (
        "मैं, एतद्द्वारा घोषणा करता/करती हूँ कि मैं स्वेच्छा से UIDAI द्वारा जारी अपना आधार नंबर और जनसांख्यिकीय "
        "जानकारी राष्ट्रीय स्वास्थ्य प्राधिकरण (NHA) के साथ केवल ABHA नंबर बनाने के उद्देश्य से साझा कर रहा/रही हूँ। "
        "मैं समझता/समझती हूँ कि मेरे ABHA नंबर का उपयोग और साझाकरण ABDM द्वारा समय-समय पर अधिसूचित उद्देश्यों, "
        "जिसमें स्वास्थ्य सेवाओं का प्रावधान शामिल है, के लिए किया जा सकता है। मुझे जानकारी है कि मेरी व्यक्तिगत "
        "पहचान योग्य जानकारी (नाम, पता, आयु, जन्म तिथि, लिंग और फोटो) ABDM से पंजीकृत या जुड़ी संस्थाओं को उपलब्ध "
        "कराई जा सकती है। मैं NHA को उपरोक्त उद्देश्य के लिए आधार अधिनियम, 2016 के प्रावधानों के अनुसार UIDAI के साथ "
        "आधार आधारित प्रमाणीकरण करने के लिए अपने आधार नंबर का उपयोग करने के लिए अधिकृत करता/करती हूँ। मुझे आधार के "
        "अलावा अन्य पहचान पत्रों के विकल्प के बारे में बताया गया है; फिर भी मैं सचेत रूप से आधार का उपयोग चुनता/चुनती "
        "हूँ। मैं आधार अधिनियम और विनियमों के प्रावधानों के अनुसार किसी भी समय दी गई सहमति वापस लेने का अधिकार "
        "सुरक्षित रखता/रखती हूँ।"
    ),
}
