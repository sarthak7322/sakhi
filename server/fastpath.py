"""Local fast path: understand routine dictation on the server, with no LLM call.

Most of a home visit is the same handful of sentences: "ID 1007", "वज़न 50 किलो", "BP 120 by 80", "Hb 10.5",
"बस save करो", "हाँ सही है". For these the LLM costs ~1.5k tokens and ~0.5 s each and burns the free-tier
per-minute budget. This module parses them with patterns and answers with fixed templates.

It is deliberately conservative: an utterance is handled locally only if EVERY word is explained by a known
pattern or a filler word. Anything else (questions, negations, danger words, unusual phrasing, other
languages) returns None and goes to the LLM as before. The result goes through the same _apply() path as an LLM
update, so validation, danger thresholds, the read-back rule and the save state machine are unchanged.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import danger, numwords, record

LANGS = ("hi-IN", "en-IN")  # templates exist for these; other languages always use the LLM

NUM = r"(\d{1,3}(?:\.\d{1,2})?)"
GLUE = (r"(?:\s*(?:है|hai|था|tha|का|की|ka|ki|मेरा|मेरी|mera|meri|उसका|उसकी|आपका|aapka|उनका|का मान|value|is|was|वो|wo|जो|jo|"
        r"around|लगभग|lagbhag|करीब|kareeb|approx|नापा|मापा|लिया|liya|naapa|"
        r"गिनी|गिना|जाँच|जांच|check|आज|aaj|reading|:|,))*\s*")
_UNIT_KG = r"(?:kgs|kg|किलोग्राम|किलो|kilograms|kilogram|kilos|kilo)"

# field patterns: (field, regex). Each regex must consume its whole phrase.
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("gestation", re.compile(r"(\d{1,2}(?:\.\d)?)\s*(?:महीने|महीना|mahine|mahina|months?)\s*(?:की|का|ki|ka|of)?\s*"
                             r"(?:pregnant|pregnancy|गर्भवती|गर्भ|garbh|garbhvati|प्रेग्नेंट)", re.I)),
    ("gestation", re.compile(r"(?:pregnant|गर्भवती|प्रेग्नेंट)\s*(?:है|hai|is|since|from)?\s*(\d{1,2}(?:\.\d)?)\s*(?:महीने|महीना|mahine|months?)(?!\s*(?:बाद|baad|later|after))", re.I)),
    ("age", re.compile(r"(?:baby|बच्चा|बच्ची|शिशु|नवजात|bachcha|bachchi)?\s*(?:is|है|hai)?\s*(\d{1,3})\s*(दिन|din|days?|हफ़्ते|हफ्ते|weeks?|महीने|mahine|months?)\s*"
                       r"(?:का|की|ka|ki|old|पुराना)(?!\s*(?:बाद|baad|later|after|में|गर्भवती|गर्भ|pregnant|प्रेग्नेंट))", re.I)),
    ("bp", re.compile(r"(?:bp|बीपी|blood pressure|ब्लड प्रेशर)" + GLUE + r"(\d{2,3})\s*"
                      r"(?:/|by|बटा|बाई|over|पर|और|aur|and)?\s*(\d{2,3})(?!\d)(?!\.\d)", re.I)),
    ("bp_top_only", re.compile(r"(?:bp|बीपी|blood pressure|ब्लड प्रेशर)" + GLUE + r"(\d{2,3})(?!\s*(?:/|by|बटा|बाई|over|पर|और|aur|and)?\s*\d)(?!\d)", re.I)),
    ("weight_kg", re.compile(r"(?:weight|wajan|vajan|वज़न|वजन)" + GLUE + NUM + r"\s*" + _UNIT_KG + r"?", re.I)),
    ("weight_kg", re.compile(NUM + r"\s*" + _UNIT_KG + r"\s*(?:weight|वज़न|वजन)?", re.I)),
    ("hb_g_dl", re.compile(r"(?:hb|एचबी|hemoglobin|haemoglobin|हीमोग्लोबिन|हिमोग्लोबिन)" + GLUE + NUM
                           + r"\s*(?:g/dl|gram|ग्राम)?", re.I)),
    ("temperature", re.compile(r"(?:temperature|temp|तापमान|टेम्परेचर|बुखार)" + GLUE + NUM +
                               r"\s*(?:degree|degrees|डिग्री|°)?\s*(fahrenheit|फ़ारेनहाइट|फारेनहाइट|celsius|सेल्सियस|f|c)?(?![a-z])", re.I)),
    ("respiratory_rate", re.compile(r"(?:saans|sans|सांस|साँस|breathing|respiratory rate|rr)" + GLUE + r"(\d{1,3})\s*"
                                    r"(?:saans|सांस|साँस)?\s*(?:per minute|per min|प्रति मिनट|पर मिनट|/min|पर minute|प्रति minute)", re.I)),
    ("ifa_tablets_given", re.compile(r"(?:ifa|आईएफए|आयरन|iron)\s*(?:की|ki|के|ke)?\s*(?:गोली|गोलियाँ|गोलियां|goli|goliyan|tablets?)?\s*"
                                     r"(\d{1,3})(?!\d)(?!\.\d)\s*(?:गोली|गोलियाँ|गोलियां|goli|goliyan|tablets?)?\s*(?:दे\s*)?(?:दीं|दी|di|diye|given|दिया|दिए|de di)?", re.I)),
    ("ifa_tablets_given", re.compile(r"(\d{1,3})\s*(?:गोली|गोलियाँ|गोलियां|goli|goliyan|tablets?)?\s*(?:ifa|आईएफए|आयरन|iron)\s*(?:की|ki)?\s*"
                                     r"(?:गोली|गोलियाँ|गोलियां|goli|goliyan|tablets?)?\s*(?:दीं|दी|di|given|दिया|दिए)?", re.I)),
    ("beneficiary_id", re.compile(r"(?:registration|beneficiary|लाभार्थी)?\s*(?:id|आईडी|आई डी|नंबर|number|no\.?)\s*(?:number|नंबर)?\s*"
                                  r"(?:है|hai|:)?\s*(\d{3,6})(?!\d)", re.I)),
    ("beneficiary_id", re.compile(r"(?<!\d)(\d{4})\s*(?:नंबर|number|वाली|wali|id)(?![a-z])", re.I)),
    ("reg_id", re.compile(r"(?<![\d.])(\d{4})(?![\d.])")),
    ("vaccine", re.compile(r"\b(td|tt|टीटी)[\s-]?([12])\b(?:\s*(?:का|ka)?\s*(?:टीका|injection|इंजेक्शन|vaccine))?", re.I)),
    ("vaccine", re.compile(r"\b(bcg|opv[\s-]?0|hep[\s-]?b[\s-]?0|penta[\s-]?[123]|opv[\s-]?[123]|rvv[\s-]?[123]|rota[\s-]?[123]|fipv[\s-]?[123]|"
                           r"pcv[\s-]?[12]|mr[\s-]?[12])\b", re.I)),
    ("temperature", re.compile(NUM + r"\s*(?:degree|degrees|डिग्री|°)\s*(fahrenheit|फ़ारेनहाइट|फारेनहाइट|celsius|सेल्सियस|f|c)?(?![a-z])", re.I)),
    ("respiratory_rate", re.compile(r"(?:respiratory rate|breathing rate|सांस की गिनती|साँस की गिनती|saans|sans|सांसें|साँसें|सांस|साँस|breathing|rr)"
                                    + GLUE + r"(\d{2,3})(?!\d)(?!\.\d)", re.I)),
    ("respiratory_rate", re.compile(r"(?:(?:एक|ek|1)\s*(?:मिनट|minute|min)\s*(?:में|mein|me)|in one minute|per minute)\s*(\d{1,3})\s*(?:saans|सांस|साँस|breaths?)", re.I)),
    ("respiratory_rate", re.compile(r"(\d{1,3})\s*(?:saans|सांस|साँस|breaths?)?\s*(?:per minute|per min|प्रति मिनट|पर मिनट|/min|पर minute|"
                                    r"प्रति minute|एक मिनट में|ek minute mein)", re.I)),
    ("next_rel", re.compile(r"(?:अगली|अगला|next|agli|agla)\s*(?:विज़िट|विजिट|visit|बार|baar)?\s*(?:after|on)?\s*(\d{1,2})\s*(दिन|din|days?|हफ़्ते|हफ्ते|हफ़्ता|हफ्ता|hafte|hafta|weeks?|सप्ताह|महीने|महीना|mahine|mahina|months?)\s*(?:बाद|baad|में|mein|later|after)?", re.I)),
    ("next_rel", re.compile(r"(?:फिर|phir)?\s*(\d{1,2})\s*(दिन|days?|हफ़्ते|हफ्ते|weeks?|सप्ताह|महीने|months?)\s*(?:बाद|later|after)\s*"
                            r"(?:अगली विज़िट|अगली विजिट|अगला visit|next visit|आऊँगी|आऊंगी|aaungi|आएँगे|फिर आऊँगी)", re.I)),
    ("next_abs", re.compile(r"(?:अगली|अगला|next)\s*(?:विज़िट|विजिट|visit)\s*(?:on)?\s*(\d{1,2})\s*(?:तारीख़?|तारीख|th|st|nd|rd)?\s*"
                            r"(जनवरी|फ़रवरी|फरवरी|मार्च|अप्रैल|मई|जून|जुलाई|अगस्त|सितंबर|अक्टूबर|नवंबर|दिसंबर|"
                            r"january|february|march|april|may|june|july|august|september|october|november|december)?(?:\s*को)?", re.I)),
    ("name", re.compile(r"(?:नाम|name|naam)\s*(?:है|hai|is|:)?\s*((?!(?:है|hai)(?:[\s,.।]|$))[A-Za-z\u0900-\u097F]{2,}"
                        r"(?:\s+(?!(?:है|hai)(?:[\s,.।]|$))[A-Za-z\u0900-\u097F]{2,})?)\s*(?:है|hai)?(?=\s*(?:[,.।]|$))", re.I)),
    ("btype", re.compile(r"(?<![A-Za-z])(pregnant|गर्भवती|garbhvati|newborn|नवजात|postpartum|प्रसव के बाद|delivery ke baad|डिलीवरी के बाद)(?![A-Za-z])"
                         r"\s*(?:महिला|mahila|woman|baby|बच्चा|शिशु)?", re.I)),
    ("td_any", re.compile(r"(?<![a-z])(?:tt|td|टीटी)\s*(?:का|ka|ki)?\s*(?:टीका|tika|injection|इंजेक्शन|vaccine|shot)", re.I)),
    ("name", re.compile(r"^(?:this is|ये|यह|ye)\s+([A-Za-z\u0900-\u097F]{2,}(?:\s+[A-Za-z\u0900-\u097F]{2,})?)(?=\s*,)", re.I)),
    ("visit_type", re.compile(r"\b(anc|pnc|hbnc|immuni[sz]ation|टीकाकरण)\b\s*(?:visit|विज़िट|विजिट|checkup|चेकअप|का visit)?", re.I)),
]

_FINISH = re.compile(r"^(?:(?:बस|bas|bus|ok|ठीक है|theek hai|thik hai|चलिए|chaliye|तो|अब|that'?s all|that is all|done|"
                     r"हो गया|ho gaya|हो गई|बस हो गया|report|रिपोर्ट|मेरा|सब|इतना ही|itna hi|आप|aap|उसको|usko|इसको|isko|इसे|उसे|ise|"
                     r"record|रिकॉर्ड|visit|विज़िट|अभी|abhi|please|प्लीज़|पूरी|poori|हुई|hui|हो|गई|complete|completed|है|दीदी|didi|सब|बता|दिया|सारी|sari|जानकारी|jankari|दे|दी|de|di|यही|yahi|था|tha|छोड़िए|छोड़ो|chhodiye|मेरे|ख्याल|से|अब|ab|कुछ|kuch|सब कुछ)[\s,.।!]*)*"
                     r"(?:read it back|read back|पढ़कर सुनाओ|पढ़कर सुनाइए|padhkar sunao|सुनाओ|सुनाइए|save|safe|सेफ|सेव|सहेज|दर्ज|register|रजिस्टर)?\s*(?:कर सकते हैं|kar sakte hain|कर सकती हैं|कर|kar|करो|karo|कर दो|kar do|कीजिए|कर दीजिए|kar dijiye|dijiye|kijiye|kar lijiye|कर दीजिए|कर लीजिए|it|it now)?"
                     r"[\s,.।!]*(?:सब कुछ|sab kuch|सब|sab|everything|all)?[\s,.।!]*(?:हो गया|ho gaya|done)?[\s,.।!]*$", re.I)
_FINISH_WORD = re.compile(r"read it back|read back|पढ़कर सुनाओ|पढ़कर सुनाइए|padhkar sunao|सुनाओ|सुनाइए|save|safe|सेफ|सेव|सहेज|दर्ज|register|रजिस्टर|पूरी|complete|बता दिया|हो गया|ho gaya|done|बस|\bbas\b|that'?s all|that is all|इतना ही", re.I)
_YES = re.compile(r"^(?:(?:हाँ|हां|हा|haan|han|ha|yes|yeah|जी|ji|हाँ जी|सही|sahi|सही है|sahi hai|correct|ठीक|theek|ठीक है|"
                  r"बिल्कुल|bilkul|सब सही|sab sahi|है|hai|is|all|right|it's|it is|it|that's|thats|that is|that|save|सेव|कर दो|karo|करो|दर्ज|कर|लीजिए|दीजिए|सब|sab|ठीक है)[\s,.।!]*)+$", re.I)
_YES_CORE = re.compile(r"हाँ|हां|haan|\byes\b|सही|sahi|correct|ठीक|theek|बिल्कुल|bilkul|right", re.I)
_LEAD_NO = re.compile(r"^\s*(?:(?:नहीं|नही|nahi|nahin|no|गलत|galat)[\s,.।!]*)+", re.I)
_FILLER = set("""
है हैं था थी थे hai hain tha का की के ka ki ke को ko में mein me और aur and , . । ! मेरा मेरी mera meri उसका उसकी
uska uski इसका आज aaj today भी bhi तो to अब ab ji जी दीदी didi ok okay ठीक हाँ हां haan visit विज़िट विजिट चेकअप
checkup check-up बच्चे बच्चा bachche bachcha baby का_ लाभार्थी beneficiary she her the a is was of with given दी दिया
लगा लगी लगाया lagaya laga टीका tika injection इंजेक्शन vaccine लिख लिखो likho note नोट करो karo कर kar लीजिए दीजिए
और_ एक ek गई गया gaya gayi गयी please प्लीज़ फिर phir सुनिए suniye hello हेलो नमस्ते namaste
lag lagi lage laga लग patient मरीज़ मरीज her his this that's thats it vaccine vaccines vaccination vaccinations टीका टीके due बाकी baaki baki
next agla अगला अगली dose खुराक लगना lagna है_ हो ho done pending देना dena old पुराना so सो around लगभग lagbhag करीब kareeb approx approximately आपका aapka उनका unka labharthi लाभार्थी naam
अभी abhi use किया kiya जो jo वो wo वह woh यह yeh ये ye wala वाला wait रुको रुकिए ruko sorry सॉरी अरे arey भी bhi दिए diye दीं दे de दिये reason कारण वजह wajah type टाइप प्रकार purpose आया aaya निकला nikla निकली नापा मापा लिया liya गिनी जाँच जांच is body rate after वाली वाला wali घर ghar
लिए आई aayi हूँ हूं registration number नंबर रिपोर्ट report reading साँस सांस saans breathing चल chal रही rahi रहा raha रहे rahe हूँ हूं hoon type टाइप प्रकार purpose for के_लिए लिए liye ये यह ye yeh है_ हुआ hua हुई hui
""".split())
_QUESTION = re.compile(r"\?|क्या|kya|kaise|कैसे|kaun|कौन|kitna|कितना|kitni|कितनी|kab|कब|kyon|क्यों|why|what|how|which|matlab|मतलब", re.I)
_NEGATION = re.compile(r"नहीं|नही|\bnahi\b|\bnahin\b|\bnot\b|\bno\b|\bna\b|मत|गलत|galat|बिना", re.I)


_ACK = re.compile(r"^(?:(?:सब सही है|सब ठीक है|sab sahi hai|sab theek hai|all correct|कुछ नहीं|kuch nahi|और कुछ नहीं|जी सही है|सही है|जी हाँ सही है|हाँ|हां|हा|जी|हाँ जी|हां जी|ji|haan|haan ji|ok|okay|ओके|ठीक है|ठीक|theek hai|thik hai|अच्छा|achha|accha|"
                  r"hmm|हम्म|हम|right|alright|हाँ ठीक है|जी हाँ|जी हां|तो|to|so|अच्छा तो)[\s,.।!]*)+$", re.I)
_HELLO = re.compile(r"^(?:नमस्ते|namaste|नमस्कार|hello|हेलो|hi|हाय)(?:\s*(?:सखी|sakhi|दीदी|didi|जी|ji))?[\s,.।!]*$", re.I)
_BARE_NUM = re.compile(r"^\s*(?:(?:हाँ|हां|जी|नहीं|नही|ji|haan|nahi|no|yes|ok|okay|तो|वो)[\s,.।!]+)*(?:है|hai|वो|wo)?\s*(\d{1,4}(?:\.\d{1,2})?)\s*(kg|किलो|kilo|g/dl|ग्राम|degree|डिग्री|f|c|fahrenheit|"
                       r"फ़ारेनहाइट|celsius|सेल्सियस|per minute|प्रति मिनट|पर मिनट|गोली|गोलियाँ|tablets?)?\s*(?:है|hai|था|tha)?[\s,.।!]*$", re.I)
_BARE_BP = re.compile(r"^\s*(?:है|hai)?\s*(\d{2,3})\s*(?:/|by|बटा|बाई|over|पर)\s*(\d{2,3})\s*(?:है|hai)?[\s,.।!]*$", re.I)
_LABELS = [
    ("weight_kg", r"weight|wajan|vajan|वज़न|वजन"), ("bp", r"bp|बीपी|blood pressure|ब्लड प्रेशर"),
    ("hb_g_dl", r"hb|एचबी|hemoglobin|haemoglobin|हीमोग्लोबिन|हिमोग्लोबिन"), ("temperature", r"temperature|temp|तापमान|टेम्परेचर"),
    ("respiratory_rate", r"saans|sans|सांस|साँस|breathing"), ("ifa_tablets_given", r"ifa|आईएफए"),
    ("beneficiary_id", r"id|आईडी|आई डी"),
]
_LABEL_ONLY = [(f, re.compile(r"^(?:(?:उसका|उसकी|मेरा|मेरी|इसका|बच्चे का|बच्चे की|का|की|अब|और|aur|तो|to)\s+)*(?:" + rx + r")"
                              r"(?:\s+(?:है|hai|हैं|मेरा|मेरी|का|की|value|मान|बताती हूँ|बताती हूं|लिखो))*[\s,.।!]*$", re.I)) for f, rx in _LABELS]
_ASK = {
    "weight_kg": ("वज़न कितना है, दीदी?", "What is the weight, didi?"), "bp": ("BP कितना है?", "What is the BP?"),
    "hb_g_dl": ("Hb कितना है?", "What is the Hb?"), "temperature": ("तापमान कितना है?", "What is the temperature?"),
    "respiratory_rate": ("सांस एक मिनट में कितनी है?", "How many breaths per minute?"),
    "ifa_tablets_given": ("IFA की कितनी गोलियाँ दीं?", "How many IFA tablets were given?"),
    "beneficiary_id": ("लाभार्थी का ID बताइए.", "Please tell me the beneficiary ID."),
}
_IFA_NONE = re.compile(r"^(?:मैंने|maine|मैं|main|वो|वह|उसने|उन्होंने|आज|aaj|उसको|उसे|इसको)?\s*(?:आज|aaj)?\s*(?:कोई भी|कोई|koi)?\s*(?:ifa|आईएफए|iron|आयरन)\s*(?:की|ki|के)?\s*(?:गोली|गोलियाँ|गोलियां|goli|tablets?)?"
                       r"\s*(?:भी|bhi)?\s*(?:नहीं|नही|nahi|nahin|not)\s*(?:दीं|दी|लीं|ली|di|li|लेती|लेतीं|leti|खाती|खाई|given|taken|दिया|दिए)?\s*(?:हूँ|हूं|hoon|है|हैं|hai|थी|thi|गई|gayi)?\s*(?:आज|aaj|today)?[\s,.।!]*$", re.I)
_IFA_NO_COUNT = re.compile(r"^(?:आज|aaj|उसने|मैंने)?\s*(?:ifa|आईएफए|आयरन|iron)\s*(?:की|ki)?\s*(?:गोली|goli|गोलियाँ|tablets?)?\s*"
                           r"(?:भी)?\s*(?:ले ली|ले लिया|ली|ली है|ले ली है|दी|दी है|दे दी|di|li|le li|given|taken|खा ली|kha li)\s*(?:है|hai)?[\s,.।!]*$", re.I)
_IFA_NONE2 = re.compile(r"^(?:no|कोई|koi|zero|0)\s*(?:ifa|आईएफए|iron|आयरन)\s*(?:की|ki)?\s*(?:गोली|गोलियाँ|tablets?)?\s*(?:given|दी|नहीं दी)?[\s,.।!]*$", re.I)
_VAX_NONE = re.compile(r"^(?:आज|aaj|today)?\s*(?:कोई भी|कोई|koi|no)?\s*(?:टीका|टीके|tika|teeka|vaccine|vaccines|injection|इंजेक्शन)\s*(?:भी|bhi)?\s*"
                       r"(?:नहीं|नही|nahi|not)?\s*(?:लगा|लगे|लगी|लगाए|लगाया|laga|lage|lagaye|दिया|दिए|given)?\s*(?:नहीं|नही|nahi)?\s*(?:गया|गए|gaya)?\s*(?:है|हैं|hai)?\s*(?:आज|aaj)?[\s,.।!]*$", re.I)
_MONTH_IDX = {m: i + 1 for i, m in enumerate(["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर",
                                             "नवंबर", "दिसंबर"])}
_MONTH_IDX.update({m: i + 1 for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                                   "september", "october", "november", "december"])})
_MONTH_IDX["फरवरी"] = 2


_NONE_OK = set("""अभी abhi use किया kiya और aur and आज aaj today मैंने maine मैं main मेरी meri मेरा उसने usne उसको usko उसे वो वह भी bhi कोई koi भी की ki के ke का
ka ली li लिया liya लिए liye लीं लगा laga लगे lage लगी लगाया lagaya लगाए लगवाया lagwaya दी di दिया diya दिए diye दीं है hai हैं
था tha थी thi गई gayi गया gaya गए लेना lena खाना khana खाई khayi खाया बोल bol रही rahi रहा हूँ हूं hoon कि ki हमने
not taken given no any did didn't i she we today was were has have had been the a any of""".split())
_NONE_NEG = re.compile(r"नहीं|नही|\bnahi\b|\bnahin\b|\bnot\b|\bno\b|didn't|भूल|bhool|bhul|forgot", re.I)
_VAX_WORDS = {"टीका", "टीके", "tika", "teeka", "vaccine", "vaccines", "vaccination", "injection", "इंजेक्शन", "टीकाकरण"}
_IFA_WORDS = {"ifa", "आईएफए", "iron", "आयरन", "गोली", "गोलियाँ", "गोलियां", "goli", "tablet", "tablets"}


def _none_statement(low: str) -> str | None:
    """'vaccine भी आज मैंने नहीं लिया', 'आयरन की गोली आज लेना भूल गई' -> which field is a plain 'none'."""
    if not _NONE_NEG.search(low):
        return None
    toks = [t.strip(".,।!?'\"") for t in low.split()]
    toks = [t for t in toks if t]
    vax = any(t in _VAX_WORDS for t in toks)
    ifa = any(t in _IFA_WORDS for t in toks) and any(t in ("ifa", "आईएफए", "iron", "आयरन") or t.startswith("गोल") for t in toks)
    if not vax and not ifa:
        return None
    allowed = _NONE_OK | _VAX_WORDS | _IFA_WORDS | {"नहीं", "नही", "nahi", "nahin", "भूल", "bhool", "bhul", "forgot"}
    if any(t not in allowed for t in toks):
        return None
    return "both" if vax and ifa else "vax" if vax else "ifa"


_WHAT_LEFT = re.compile(r"(और|aur|अब|ab)\s*(क्या|kya)\s*(बताना|batana|बताऊँ|बताऊं|bataun|bolna|बोलना|बोलूँ|बचा|bacha)|what (else|is left|more)", re.I)


def _what_left(rec: dict, lang: str) -> str:
    hi = lang != "en-IN"
    names = [("beneficiary_id", "ID", "ID"), ("visit_type", "विज़िट का प्रकार", "visit type"), ("weight_kg", "वज़न", "weight"),
             ("bp_systolic", "BP", "BP"), ("hb_g_dl", "Hb", "Hb"), ("temperature_f", "तापमान", "temperature"),
             ("vaccinations_given_today", "आज के टीके", "vaccines today"), ("next_visit_date", "अगली विज़िट", "next visit")]
    miss = []
    for f, h, e in names:
        v = rec.get(f)
        if f == "temperature_f":
            v = v if v is not None else rec.get("temperature_c")
        if v in (None, "", []):
            miss.append(h if hi else e)
    if not miss:
        return "सब ज़रूरी बातें आ गईं. सेव करना हो तो 'बस, save करो' बोलिए." if hi else "Everything important is in. Say 'that's all, save it' to save."
    return ("अभी बाकी है: " + ", ".join(miss[:5]) + ". जो पता है वो बताइए, या 'बस, save करो' बोलिए.") if hi else \
           ("Still missing: " + ", ".join(miss[:5]) + ". Tell me what you know, or say 'save it'.")


def _is_finish(low: str) -> bool:
    """'बस save करो', or several save phrases in a row ('safe कीजिए, register कर लीजिए')."""
    if not _FINISH_WORD.search(low):
        return False
    parts = [p.strip() for p in re.split(r"[,।;]|\s(?:और|aur|and)\s", low) if p.strip(" .")]
    return bool(parts) and all(_FINISH.match(p) for p in parts)


_INSIST = re.compile(r"ही है|वही|सही है|सही ही|बिल्कुल|correct|that is right|it is|yes it|पक्का", re.I)
_USUAL = {"hb_g_dl": ("Hb", "5 से 16", "5 to 16"), "temperature": ("तापमान", "95 से 106 फ़ारेनहाइट", "95 to 106 F"),
          "weight_kg": ("वज़न", "बड़ों में 35 से 120 किलो, नवजात में 1.5 से 5 किलो", "35-120 kg for adults, 1.5-5 kg for newborns"),
          "bp": ("BP", "ऊपर 80 से 200, नीचे 40 से 130", "top 80-200, bottom 40-130"), "respiratory_rate": ("सांस", "10 से 60", "10 to 60")}


def _insist_line(rejected: tuple, lang: str) -> str:
    f, v = rejected
    name, hi_rng, en_rng = _USUAL.get(f, (f, "", ""))
    if lang != "en-IN":
        return (f"दीदी, {name} {_g(v)} लिखा नहीं जा सकता — यह आम तौर पर {hi_rng} के बीच होता है. "
                f"मशीन या कार्ड पर जो लिखा है, वो फिर से पढ़कर बताइए.")
    return f"Didi, {name} {_g(v)} cannot be recorded — it is usually {en_rng}. Please read the reading again and tell me."


def _visit_day(rec: dict) -> date:
    try:
        return date.fromisoformat(rec.get("visit_date") or "")
    except ValueError:
        return date.today()


def field_from_hint(hint) -> str | None:
    """The LLM's "unclear" note ("hb_g_dl", "weight") -> the field it asked for, so a bare number can fill it."""
    h = str(hint or "").lower()
    if not h:
        return None
    for f, rx in _LABELS + [("temperature", r"temperature_c|temperature_f"), ("bp", r"bp_systolic|bp_diastolic"),
                            ("weight_kg", r"weight_kg"), ("hb_g_dl", r"hb_g_dl"), ("respiratory_rate", r"respiratory_rate")]:
        if re.search(rx, h, re.I):
            return f
    return None


def _label_only(t: str) -> str | None:
    if re.search(r"\d", t):
        return None
    for f, rx in _LABEL_ONLY:
        if rx.match(t.strip()):
            return f
    return None


@dataclass
class Parsed:
    intent: str
    set: dict = field(default_factory=dict)
    say: list[str] = field(default_factory=list)
    ask: str | None = None  # field Sakhi just asked for: a bare number next turn fills it
    finish_after: bool = False  # "... बस save करो" at the end of a dictation sentence
    extra_say: list = field(default_factory=list)


_VAX_NAMES = {"penta": "Penta", "opv": "OPV", "rvv": "RVV", "fipv": "fIPV", "pcv": "PCV", "mr": "MR"}


def _vaccine(m: re.Match) -> str:
    if m.re.groups == 2:  # Td / TT with dose number
        return f"Td-{m.group(2)}"
    g = re.sub(r"[\s-]", "", m.group(1).lower()).replace("rota", "rvv")
    fixed = {"bcg": "BCG", "opv0": "OPV-0", "hepb0": "HepB-0"}
    if g in fixed:
        return fixed[g]
    k = re.match(r"([a-z]+)(\d)", g)
    return f"{_VAX_NAMES[k.group(1)]}-{k.group(2)}" if k and k.group(1) in _VAX_NAMES else m.group(1)


_DUE = re.compile(r"\bdue\b|बाकी|baaki|baki|लगना है|लगने हैं|लगना|lagna|अगला टीका|agla tika|अगली खुराक|next (?:vaccine|dose)|pending|देना है|dena hai", re.I)
_GIVEN = re.compile(r"लगा|लगी|लगे|लगाया|लगाए|lag\b|laga|lagi|lagaya|दिया|दिए|दी\b|diya|diye|\bdi\b|given|gave|today|आज", re.I)
_SEP = re.compile(r"[,।;]|\.(?=\s|$)|\s(?:और|aur|and)\s", re.I)


def _is_due(body: str, a: int, b: int) -> bool:
    """Is this vaccine mentioned as DUE ('TT2 due hai', 'agla tika Penta 2') rather than given today?"""
    starts = [m.end() for m in _SEP.finditer(body, 0, a)]
    lo = starts[-1] if starts else 0
    nxt = _SEP.search(body, b)
    hi = nxt.start() if nxt else len(body)
    own = body[lo:hi]
    if _GIVEN.search(own):
        return False  # "Td-1 लगा, Td-2 due है": this one was given
    if _DUE.search(own):
        return True
    return bool(_DUE.search(body)) and not _GIVEN.search(body)  # "due Penta 2 and OPV 2": the list shares one "due"


def _covered(text: str, spans: list[tuple[int, int]], extra_ok: set[str]) -> bool:
    """True if every word outside the matched spans is a filler word or a register name word."""
    keep = []
    last = 0
    for a, b in sorted(spans):
        keep.append(text[last:a])
        last = max(last, b)
    keep.append(text[last:])
    rest = " ".join(keep)
    for tok in re.findall(r"[\wऀ-෿.'/-]+", rest.lower()):
        tok = tok.strip(".'-/।॥")
        if tok and all(0x0900 <= ord(c) <= 0x0DFF and unicodedata.category(c).startswith("M") for c in tok):
            continue  # a stray vowel sign / nasal mark left by a shorter alternative
        if not tok or tok in _FILLER or tok in extra_ok:
            continue
        if extra_ok and difflib.get_close_matches(tok, list(extra_ok), n=1, cutoff=0.75):
            continue  # "नायडू" vs register "नायडु": transcription spelling of a register name
        return False
    return True


_DEVA = {"क": "k", "ख": "kh", "ग": "g", "घ": "gh", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ट": "t", "ठ": "th", "ड": "d",
         "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n", "प": "p", "फ": "ph", "ब": "b", "भ": "bh",
         "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh", "ष": "sh", "स": "s", "ह": "h", "ा": "a", "ि": "i",
         "ी": "i", "ु": "u", "ू": "u", "े": "e", "ै": "ai", "ो": "o", "ौ": "au", "ं": "n", "ँ": "n", "अ": "a", "आ": "a",
         "इ": "i", "ई": "i", "उ": "u", "ऊ": "u", "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au", "़": "", "्": "", "ड़": "d", "फ़": "f", "ज़": "z"}


def _latin(word: str) -> str:
    """Rough Devanagari -> Latin, only for matching a spoken register name ("नायडू" ~ "naidu")."""
    return "".join(_DEVA.get(c, c) for c in word).lower()


def _names_words(text: str) -> tuple[set[str], dict | None]:
    b = record.find_in_text(text)
    if not b:  # name only, spoken in Devanagari for an English / other-script register name: all its words must match
        toks = [_latin(t) for t in re.findall(r"[\u0900-\u097F]+", text)]
        for cand in record.REGISTRY:
            parts = [w.lower() for w in cand["name"].split() if w.lower() not in ("baby", "of")]
            if len(parts) >= 2 and all(difflib.get_close_matches(p_, toks, n=1, cutoff=0.7) for p_ in parts):
                b = cand
                break
    if not b:
        return set(), None
    words = set(b["name"].lower().split()) | set(b["name_local"].split())
    latin = [w.lower() for w in b["name"].split()]
    for tok in re.findall(r"[\u0900-\u097F]+", text):  # Devanagari spelling of an English / Telugu register name
        if difflib.get_close_matches(_latin(tok), latin, n=1, cutoff=0.6):
            words.add(tok)
    return words | {"of", "baby", "का", "बच्चा", "बच्चे"}, b


_CLAUSE_SPLIT = re.compile(r"[।!?;]+|\.(?=\s|$)|,\s+|\s+(?:और|aur|and|फिर|phir|then)\s+", re.I)
_WAIT = re.compile(r"^(?:(?:wait|रुको|रुकिए|रुकिये|ruko|rukiye|hold on|sorry|सॉरी|अरे|are|arey|हम्म|hmm|एक मिनट|1 मिनट|ek minute|"
                   r"एक सेकंड|one second|ok|okay|अच्छा|achha|तो|to|सुनो|suno|सुनिए|suniye|दीदी|didi)[\s,.।!]*)+$", re.I)


def parse(text: str, rec: dict, state: str, lang: str, strict: bool = True, pending: str | None = None,
          rejected: tuple | None = None) -> Parsed | None:
    """Return a local understanding of the utterance, or None to use the LLM.

    Tries the whole sentence first, then clause by clause ("तापमान 97 F, सांस 12 per minute। IFA नहीं ली और टीका
    भी नहीं है"): if EVERY clause is understood (values, simple negatives, "wait wait" fillers) the merged result
    is used; a single clause that is not understood sends the whole sentence to the LLM."""
    text = re.sub(r"(?i)(वज़न|वजन|weight|hb|hemoglobin|हीमोग्लोबिन|तापमान|temperature|bp|बीपी|सांस|साँस|saans|id|आईडी)"
                  r"\s*(?:है|hai)?\s*[।.,]+\s*(?=\d)", r"\1 ", text)
    if rejected and _INSIST.search(text) and not re.search(r"\d", re.sub(str(rejected[1]).split(".")[0], "", text)):
        # "नहीं, 120 ही है" after Hb 120 was rejected: explain once, keep asking for that field
        return Parsed("other", say=[_insist_line(rejected, lang)], ask=rejected[0])
    whole = _parse_one(text, rec, state, lang, strict, pending)
    if whole is not None or lang not in LANGS:
        return whole
    t = numwords.normalize(text)
    if _QUESTION.search(t) or (strict and (danger.detect_text(t, rec.get("beneficiary_type")) or danger.detect_text(t, None))):
        return None
    clauses = [c.strip() for c in _CLAUSE_SPLIT.split(t) if c and c.strip(" ,.।")]
    if len(clauses) < 2:
        return None
    merged: dict = {}
    says: list[str] = []
    intent, finish = "dictation", False
    for i, c in enumerate(clauses):
        if _WAIT.match(c.lower()):
            continue
        p = _parse_one(c, rec, "collecting", lang, strict, None)
        if p is None or p.ask:
            return None
        if p.intent == "finish":
            if i != len(clauses) - 1:
                return None
            finish = True
            continue
        if p.intent == "correction":
            intent = "correction"
        if p.intent == "other" and not p.set:
            continue  # "हाँ", "ठीक है" inside a longer sentence
        clash = set(p.set) & set(merged)
        if any(merged[k] != p.set[k] for k in clash):
            return None  # the same field twice with different values: let the LLM sort it out
        merged.update(p.set)
        says += [x for x in p.say if "नोट किया" in x or "noted" in x]
    if not merged and not says:
        return Parsed("finish") if finish else None
    if state == "confirming":
        intent = "correction"
    return Parsed(intent, merged, finish_after=finish, extra_say=says)


def _parse_one(text: str, rec: dict, state: str, lang: str, strict: bool = True, pending: str | None = None) -> Parsed | None:
    """One clause or a whole short sentence.

    strict=False is the fallback when the LLM is unavailable (free tier exhausted): values found anywhere
    in the sentence are taken even if other words are not understood. Questions and negations still refuse.
    """
    if lang not in LANGS:
        return None
    t = numwords.normalize(text).strip()
    t = re.sub(r"(\d+)\s*(?:point|पॉइंट|प्वाइंट|दशमलव)\s*(\d{1,2})(?!\d)", r"\1.\2", t, flags=re.I)
    if not t:
        return None
    if _WHAT_LEFT.search(t):
        return Parsed("other", say=[_what_left(rec, lang)])
    if _QUESTION.search(t):
        return None
    if strict and (danger.detect_text(t, rec.get("beneficiary_type")) or danger.detect_text(t, None)):
        return None  # anything that smells like a danger sign gets the LLM as a second detector too
    low = t.lower()
    low_f = re.sub(r"(?<![\w\u0900-\u097F])(?:तो|to|भी|bhi|ना|na|जी|ji|फिर|phir|अब|ab|चलिए|ठीक है|theek hai)(?![\w\u0900-\u097F])", " ", low)
    low_f = re.sub(r"\s+", " ", low_f).strip(" ,.।")

    # --- confirmation of the read-back
    if state == "confirming":
        if (_YES.match(low) or _YES.match(low_f)) and _YES_CORE.search(low) and not _NEGATION.search(low):
            return Parsed("confirm_yes")
        if not _LEAD_NO.match(t):
            if _is_finish(low) or _is_finish(low_f):
                return Parsed("confirm_yes")  # "हाँ save कर दो" / "तो safe तो कीजिए" after the read-back
            if _WAIT.match(low):
                return Parsed("other", say=["जी दीदी, बताइए क्या बदलना है." if lang != "en-IN" else "Okay didi, what should I change?"])
            p = _parse_one(re.sub(r"^(?:wait|रुको|रुकिए|ruko|sorry|अरे|[\s,.।!])+", "", t, flags=re.I), rec, "collecting", lang, strict, pending)
            if p is not None and p.set:
                return Parsed("correction", p.set)  # "BP है 200/190" after the read-back: change it, read back again
            return None

    # --- "बस, save करो": finish
    if (_is_finish(low) or _is_finish(low_f)) and not _NEGATION.search(low):
        return Parsed("finish")

    # --- small talk while dictating: no LLM needed
    hi = lang != "en-IN"
    if _ACK.match(low):
        return Parsed("other", say=["जी दीदी, आगे बताइए." if hi else "Okay didi, go on."])
    if _HELLO.match(low):
        return Parsed("other", say=["नमस्ते दीदी, विज़िट के बारे में बोलिए." if hi else "Hello didi, tell me about the visit."])

    # --- a bare value right after Sakhi asked for one ("वज़न कितना है?" -> "50 किलो")
    if pending in ("bp_diastolic", "bp"):
        mb = _BARE_BP.match(re.sub(r"^(?:(?:हाँ|हां|जी|नहीं|नही|ji|haan|nahi|no|yes)[\s,.।!]+)+", "", low))
        if mb:
            return Parsed("dictation", {"bp_systolic": int(mb.group(1)), "bp_diastolic": int(mb.group(2))})
    if pending == "bp_diastolic":
        m = _BARE_NUM.match(low)
        if m and m.group(1).isdigit():
            return Parsed("dictation", {"bp_diastolic": int(m.group(1))})
    if pending:
        m = _BARE_BP.match(low) if pending == "bp" else _BARE_NUM.match(low)
        if m:
            if pending == "bp":
                return Parsed("dictation", {"bp_systolic": int(m.group(1)), "bp_diastolic": int(m.group(2))})
            v = float(m.group(1)) if "." in m.group(1) else int(m.group(1))
            unit = (m.group(2) or "").lower()
            if pending == "temperature":
                is_f = unit.startswith(("f", "फ")) or (not unit.startswith(("c", "स", "से")) and v > 50)
                return Parsed("dictation", {"temperature_f" if is_f else "temperature_c": v})
            if pending == "beneficiary_id":
                return Parsed("dictation", {"beneficiary_id": m.group(1).split(".")[0]})
            return Parsed("dictation", {pending: v})

    # --- a measurement named with no value: ask for it ourselves and remember what we asked
    lab = _label_only(t)
    if lab:
        q = _ASK[lab][0 if hi else 1]
        return Parsed("other", say=[q], ask=lab)

    # --- simple negatives that only need noting
    if _IFA_NO_COUNT.match(low):
        return Parsed("dictation", {"notes": "IFA given (count not said)"},
                      say=["IFA गोली दी, नोट किया. कितनी गोलियाँ दीं?" if hi else "IFA given, noted. How many tablets?"],
                      ask="ifa_tablets_given")
    none = _none_statement(low)
    if none == "both":
        return Parsed("dictation", {"ifa_tablets_given": 0}, say=["IFA गोली नहीं दी, आज कोई टीका नहीं, नोट किया." if hi
                                                                 else "No IFA tablets and no vaccine today, noted."])
    if none == "ifa" or _IFA_NONE.match(low) or _IFA_NONE2.match(low):
        return Parsed("dictation", {"ifa_tablets_given": 0})
    if none == "vax" or _VAX_NONE.match(low):
        return Parsed("dictation", {}, say=["आज कोई टीका नहीं, नोट किया." if hi else "No vaccine today, noted."])

    # --- dictation / correction of values
    correction = bool(_LEAD_NO.match(t))
    body = _LEAD_NO.sub("", t) if correction else t
    if _NEGATION.search(body):
        return None  # "IFA नहीं ली", "bukhar nahi hai": let the LLM handle negation
    upd: dict = {}
    spans: list[tuple[int, int]] = []
    vaccines: list[str] = []
    due_v: list[str] = []
    ask_bottom = False
    for fld, rx in _PATTERNS:
        for m in rx.finditer(body):
            if any(a < m.end() and m.start() < b for a, b in spans):
                continue  # already explained by an earlier (more specific) pattern
            if fld == "bp":
                upd["bp_systolic"], upd["bp_diastolic"] = int(m.group(1)), int(m.group(2))
            elif fld == "temperature":
                v = float(m.group(1)); unit = (m.group(2) or "").lower()
                if unit.startswith(("f", "फ")) or (not unit and v > 50):
                    upd["temperature_f"] = v
                else:
                    upd["temperature_c"] = v
            elif fld == "vaccine":
                (due_v if _is_due(body, m.start(), m.end()) else vaccines).append(_vaccine(m))
            elif fld == "td_any":
                due_now = [v for v in (rec.get("vaccinations_due") or []) if v.startswith("Td")]
                dose = due_now[0] if due_now else "Td-1"  # "TT injection lag gaya": the next Td dose due
                (due_v if _is_due(body, m.start(), m.end()) else vaccines).append(dose)
            elif fld == "gestation":
                upd["gestation_months"] = float(m.group(1)); upd.setdefault("beneficiary_type", "pregnant")
            elif fld == "age":
                n = int(m.group(1)); u = m.group(2).lower()
                upd["age_days"] = n * 7 if u.startswith(("हफ", "week")) else n * 30 if u.startswith(("मही", "mahin", "month")) else n
            elif fld == "next_rel":
                n = int(m.group(1)); u = m.group(2).lower()
                days = n * 7 if u.startswith(("हफ़", "हफ", "week", "सप्त", "haft")) else n * 30 if u.startswith(("मही", "month", "mahin")) else n
                upd["next_visit_date"] = (_visit_day(rec) + timedelta(days=days)).isoformat()
            elif fld == "next_abs":
                d = int(m.group(1)); mon = (m.group(2) or "").lower()
                base = _visit_day(rec)
                month = _MONTH_IDX.get(mon, base.month if d > base.day else base.month % 12 + 1)
                year = base.year + (1 if month < base.month or (month == base.month and d <= base.day) else 0)
                try:
                    upd["next_visit_date"] = date(year, month, d).isoformat()
                except ValueError:
                    return None
            elif fld == "name":
                if rec.get("registered"):
                    return None  # the register already has her name: a different name is for the LLM to judge
                upd["beneficiary_name"] = m.group(1).strip().title() if m.group(1).isascii() else m.group(1).strip()
            elif fld == "btype":
                w = m.group(1).lower()
                upd["beneficiary_type"] = ("pregnant" if w in ("pregnant", "गर्भवती", "garbhvati") else
                                           "newborn" if w in ("newborn", "नवजात") else "postpartum")
            elif fld == "visit_type":
                v = m.group(1).lower()
                upd["visit_type"] = {"anc": "ANC", "pnc": "PNC", "hbnc": "HBNC"}.get(v, "Immunisation")
            elif fld == "beneficiary_id":
                upd["beneficiary_id"] = m.group(1)
            elif fld == "bp_top_only":
                if "bp_systolic" in upd:
                    continue
                upd["bp_systolic"] = int(m.group(1))
                ask_bottom = True
            elif fld == "reg_id":
                if m.group(1) not in record.REGISTRY_BY_ID or "beneficiary_id" in upd:
                    continue  # a bare 4-digit number counts as an ID only if it is in the register
                upd["beneficiary_id"] = m.group(1)
            else:
                upd[fld] = float(m.group(1)) if "." in m.group(1) else int(m.group(1))
            spans.append((m.start(), m.end()))
    name_words, who = _names_words(body)
    if who and "beneficiary_id" not in upd:
        if rec.get("beneficiary_id") and rec["beneficiary_id"] != who["id"]:
            return None  # a different person mid-visit: let the LLM decide what she means
        upd["beneficiary_id"] = who["id"]
    if vaccines:
        upd["vaccinations_given_today"] = sorted(set((rec.get("vaccinations_given_today") or []) + vaccines))
    if due_v:
        upd["vaccinations_due_noted"] = sorted(set((rec.get("vaccinations_due_noted") or []) + due_v))
    if not upd or (strict and not _covered(body, spans, name_words)):
        return None
    if "beneficiary_id" in upd and rec.get("beneficiary_id") and upd["beneficiary_id"] != rec["beneficiary_id"] and not correction:
        return None  # an id change without "नहीं": ambiguous, ask the LLM
    if ask_bottom:  # "BP है 180": keep the top number, ask for the bottom one (not "180/?")
        return Parsed("correction" if correction else "dictation", upd,
                      say=[("BP " + str(upd["bp_systolic"]) + " के साथ नीचे वाला नंबर क्या है?") if lang != "en-IN"
                           else f"BP {upd['bp_systolic']} over what?"], ask="bp_diastolic")
    return Parsed("correction" if correction else "dictation", upd)


# ------------------------------------------------------------------ spoken templates
_MONTHS = {"hi": ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"],
           "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
                  "November", "December"]}


def _g(v):
    return f"{v:g}" if isinstance(v, float) else str(v)


def _day(iso: str, hi: bool) -> str:
    try:
        y, m, d = (int(x) for x in iso.split("-"))
        return f"{d} {_MONTHS['hi' if hi else 'en'][m - 1]}"
    except (ValueError, AttributeError, IndexError):
        return iso


def value_phrases(rec: dict, fields: list[str], hi: bool) -> list[str]:
    out = []
    seen = set()
    for f in fields:
        if f in seen:
            continue
        seen.add(f)
        v = rec.get(f)
        if f == "beneficiary_id" and v:
            name = rec.get("beneficiary_name")
            out.append(f"{name}, ID {v}" if name else f"ID {v}")
        elif f == "visit_type" and v:
            out.append(f"{v} विज़िट" if hi else f"{v} visit")
        elif f == "weight_kg" and v is not None:
            out.append(f"वज़न {_g(v)} किलो" if hi else f"weight {_g(v)} kg")
        elif f in ("bp_systolic", "bp_diastolic") and rec.get("bp_systolic"):
            seen |= {"bp_systolic", "bp_diastolic"}
            out.append(f"BP {rec['bp_systolic']}/{rec.get('bp_diastolic')}")
        elif f == "hb_g_dl" and v is not None:
            out.append(f"Hb {_g(v)}")
        elif f == "temperature_f" and v is not None:
            out.append(f"तापमान {_g(v)} फ़ारेनहाइट" if hi else f"temperature {_g(v)} F")
        elif f == "temperature_c" and v is None and rec.get("temperature_f") is not None and "temperature_f" not in seen:
            seen.add("temperature_f")  # she said Celsius but the number can only be Fahrenheit: say what we stored
            out.append(f"तापमान {_g(rec['temperature_f'])} फ़ारेनहाइट (सेल्सियस नहीं हो सकता)" if hi
                       else f"temperature {_g(rec['temperature_f'])} F (cannot be Celsius)")
        elif f == "temperature_c" and v is not None:
            out.append(f"तापमान {_g(v)} डिग्री" if hi else f"temperature {_g(v)} C")
        elif f == "respiratory_rate" and v is not None:
            out.append(f"सांस {v} प्रति मिनट" if hi else f"breathing {v} per minute")
        elif f == "ifa_tablets_given" and v is not None:
            out.append(("IFA गोली नहीं दी" if hi else "no IFA tablets") if v == 0 else (f"IFA {v} गोली" if hi else f"IFA {v} tablets"))
        elif f == "beneficiary_name" and v and "beneficiary_id" not in fields:
            out.append(f"नाम {v}" if hi else f"name {v}")
        elif f == "beneficiary_type" and v and not ({"gestation_months", "age_days"} & set(fields)):
            out.append({"pregnant": ("गर्भवती", "pregnant"), "postpartum": ("प्रसव के बाद", "postpartum"),
                        "newborn": ("नवजात", "newborn"), "child": ("बच्चा", "child")}.get(v, (v, v))[0 if hi else 1])
        elif f == "next_visit_date" and v:
            out.append(f"अगली विज़िट {_day(v, hi)}" if hi else f"next visit {_day(v, hi)}")
        elif f == "vaccinations_due_noted" and v:
            out.append(("टीके बाकी: " if hi else "vaccines due: ") + ", ".join(v))
        elif f == "gestation_months" and v:
            out.append(f"{_g(v)} महीने की गर्भवती" if hi else f"{_g(v)} months pregnant")
        elif f == "age_days" and v is not None:
            out.append(f"बच्चा {v} दिन का" if hi else f"baby {v} days old")
        elif f == "vaccinations_given_today" and v:
            out.append(("आज टीके: " if hi else "vaccines today: ") + ", ".join(v))
    return out


def ack(rec: dict, changed: list[str], lang: str) -> str:
    hi = lang != "en-IN"
    ph = value_phrases(rec, changed, hi)
    if not ph:
        return "नोट किया." if hi else "Noted."
    return (", ".join(ph) + (", नोट किया." if hi else ", noted.")).replace("  ", " ")


_FIELD_WORD = {"bp": ("BP", "BP"), "weight_kg": ("वज़न", "weight"), "bp_systolic": ("BP", "BP"), "bp_diastolic": ("BP", "BP"),
               "hb_g_dl": ("Hb", "Hb"), "temperature_c": ("तापमान", "temperature"), "temperature_f": ("तापमान", "temperature"),
               "respiratory_rate": ("सांस", "breathing rate"), "ifa_tablets_given": ("IFA गोली", "IFA tablets")}


def reject_line(rejected: list, lang: str) -> str:
    hi = lang != "en-IN"
    parts = []
    for k, v in rejected:
        w = _FIELD_WORD.get(k, (k, k))[0 if hi else 1]
        if w not in [p.split()[0] for p in parts]:
            parts.append(f"{w} {_g(v)}")
    joined = ", ".join(parts)
    return (f"दीदी, {joined} संभव नहीं है, शायद ठीक से सुनाई नहीं दिया. कृपया दोबारा बताइए." if hi
            else f"Didi, {joined} is not possible, I may have misheard. Please say it again.")


def confirm_odd(rec: dict, changed: list[str], lang: str) -> str:
    hi = lang != "en-IN"
    ph = ", ".join(value_phrases(rec, changed, hi))
    return f"दीदी, {ph} सुना. क्या यह सही है?" if hi else f"Didi, I heard {ph}. Is that right?"


def readback(rec: dict, lang: str) -> list[str]:
    """The whole record read back in fixed words (Hindi / English), ending with the yes/no question."""
    hi = lang != "en-IN"
    missing = record.missing_required(rec)
    if missing:
        ask = {"beneficiary_id": ("लाभार्थी का ID", "the beneficiary ID"), "visit_type": ("विज़िट का प्रकार", "the visit type")}
        need = " और ".join(ask[m][0] for m in missing) if hi else " and ".join(ask[m][1] for m in missing)
        return [f"सेव करने से पहले {need} बताइए." if hi else f"Before saving, please tell me {need}."]
    head = value_phrases(rec, ["beneficiary_id", "visit_type", "gestation_months", "age_days"], hi)
    vals = value_phrases(rec, ["weight_kg", "bp_systolic", "hb_g_dl", "temperature_f", "temperature_c",
                               "respiratory_rate", "ifa_tablets_given", "vaccinations_given_today"], hi)
    lines = [", ".join(head) + "."]
    if vals:
        lines.append(", ".join(vals) + ".")
    if rec.get("vaccinations_due"):
        lines.append(("टीके बाकी: " if hi else "Vaccines due: ") + ", ".join(rec["vaccinations_due"]) + ".")
    if rec.get("danger_signs"):
        labels = [d.get("label_hi") if hi else d.get("label_en") for d in rec["danger_signs"]]
        lines.append(("खतरे के संकेत: " if hi else "Danger signs: ") + ", ".join(labels) + ".")
    nxt = rec.get("next_visit_date") or rec.get("next_visit_suggested")
    if nxt:
        lines.append(f"अगली विज़िट {_day(nxt, hi)}." if hi else f"Next visit {_day(nxt, hi)}.")
    lines.append("सब सही है?" if hi else "Is everything correct?")
    return lines
