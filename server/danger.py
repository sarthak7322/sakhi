"""Danger-sign list, deterministic detector and escalation messages.

The list is built from public Government of India / WHO guidance (see docs/DANGER_SIGNS.md):
  - MoHFW "Mother and Child Protection (MCP) Card" – danger signs in pregnancy, after delivery, newborn
  - MoHFW "Home Based Newborn Care (HBNC) Operational Guidelines" (rev. 2014) – newborn danger signs
  - WHO/MoHFW IMNCI – general danger signs in young children

Design rule: a missed danger sign is the worst failure, so detection is a UNION of
  (1) this keyword + numeric-threshold detector (instant, runs before the LLM), and
  (2) the LLM's own flags, restricted to the ids below.
The agent never diagnoses or advises treatment: it only says "refer to PHC now" from a fixed template.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Sign:
    id: str
    group: str  # maternal | newborn | child
    en: str
    hi: str
    source: str


SIGNS: dict[str, Sign] = {s.id: s for s in [
    # Pregnancy / after delivery (MCP card)
    Sign("MAT_BLEEDING", "maternal", "bleeding in pregnancy or heavy bleeding after delivery", "गर्भावस्था में या प्रसव के बाद ज़्यादा खून आना", "MCP Card"),
    Sign("MAT_CONVULSION", "maternal", "fits or convulsions", "दौरे या झटके आना", "MCP Card"),
    Sign("MAT_HEADACHE_VISION", "maternal", "severe headache or blurred vision", "तेज़ सिरदर्द या धुंधला दिखना", "MCP Card"),
    Sign("MAT_FEVER", "maternal", "high fever", "तेज़ बुखार", "MCP Card"),
    Sign("MAT_ABDOMINAL_PAIN", "maternal", "severe abdominal pain", "पेट में तेज़ दर्द", "MCP Card"),
    Sign("MAT_SWELLING", "maternal", "swelling of face or hands", "चेहरे या हाथों पर सूजन", "MCP Card"),
    Sign("MAT_BREATHING", "maternal", "difficulty in breathing", "सांस लेने में तकलीफ़", "MCP Card"),
    Sign("MAT_FETAL_MOVEMENT", "maternal", "reduced or no fetal movement", "बच्चे की हलचल कम या बंद", "MCP Card"),
    Sign("MAT_WATER_LEAK", "maternal", "leaking of water before labour", "समय से पहले पानी आना", "MCP Card"),
    Sign("MAT_FOUL_DISCHARGE", "maternal", "foul-smelling discharge after delivery", "प्रसव के बाद बदबूदार पानी", "MCP Card"),
    Sign("MAT_HIGH_BP", "maternal", "high blood pressure, 140 by 90 or more", "बीपी एक सौ चालीस बटा नब्बे या ज़्यादा", "MCP Card / ANC guidelines"),
    Sign("MAT_SEVERE_ANAEMIA", "maternal", "severe anaemia, haemoglobin below 7", "गंभीर खून की कमी, हीमोग्लोबिन सात से कम", "MCP Card / Anemia Mukt Bharat"),
    # Newborn (HBNC)
    Sign("NB_NOT_FEEDING", "newborn", "baby not feeding or sucking poorly", "बच्चा दूध नहीं पी रहा", "HBNC"),
    Sign("NB_CONVULSION", "newborn", "baby having fits", "बच्चे को झटके आना", "HBNC"),
    Sign("NB_FAST_BREATHING", "newborn", "fast or difficult breathing, 60 or more per minute", "तेज़ सांस, एक मिनट में साठ या ज़्यादा", "HBNC"),
    Sign("NB_CHEST_INDRAWING", "newborn", "chest indrawing", "छाती का अंदर धँसना", "HBNC"),
    Sign("NB_FEVER", "newborn", "fever in newborn, 37.5 degree or more", "नवजात को बुखार", "HBNC"),
    Sign("NB_COLD", "newborn", "baby cold to touch, below 35.5 degree", "बच्चे का शरीर ठंडा", "HBNC"),
    Sign("NB_LETHARGY", "newborn", "baby lethargic or unconscious", "बच्चा सुस्त या बेहोश", "HBNC"),
    Sign("NB_JAUNDICE", "newborn", "yellow palms or soles, or jaundice in first day", "हथेली-तलवे पीले, पीलिया", "HBNC"),
    Sign("NB_UMBILICUS", "newborn", "red or pus-filled umbilicus", "नाभि लाल या मवाद", "HBNC"),
    Sign("NB_PUSTULES", "newborn", "many skin pustules or boils", "त्वचा पर बहुत फुंसियाँ", "HBNC"),
    Sign("NB_BLEEDING", "newborn", "bleeding from any site", "बच्चे को कहीं से खून आना", "HBNC"),
    Sign("NB_LOW_WEIGHT", "newborn", "very low weight, below 1.8 kilo", "बहुत कम वज़न, एक दशमलव आठ किलो से कम", "HBNC"),
    # Young child (IMNCI general danger signs)
    Sign("CH_CONVULSION", "child", "child having fits", "बच्चे को झटके आना", "IMNCI"),
    Sign("CH_UNABLE_TO_DRINK", "child", "child unable to drink or breastfeed", "बच्चा कुछ पी नहीं पा रहा", "IMNCI"),
    Sign("CH_VOMITS_EVERYTHING", "child", "child vomiting everything", "बच्चा सब कुछ उल्टी कर रहा", "IMNCI"),
    Sign("CH_LETHARGY", "child", "child lethargic or unconscious", "बच्चा सुस्त या बेहोश", "IMNCI"),
    Sign("CH_BREATHING", "child", "difficult breathing or chest indrawing", "सांस में तकलीफ़ या पसली चलना", "IMNCI"),
]}

# ---------------------------------------------------------------------------
# Keyword concepts -> sign id per beneficiary type
# ---------------------------------------------------------------------------
# Each concept: list of regex patterns (lower-cased text), whether a nearby negator cancels it,
# and the sign id to raise per beneficiary type (None key = type unknown).

M, N, C = "maternal", "newborn", "child"


@dataclass
class Concept:
    name: str
    patterns: list[str]
    by_type: dict  # beneficiary group (or None) -> sign id
    negatable: bool = True
    _re: list = field(default_factory=list)


CONCEPTS: list[Concept] = [
    Concept("bleeding", [
        r"bleed", r"\bblood\b", r"spotting", r"\bkh(oo|u)n\b", r"खून", r"रक्तस्त्?राव", r"ब्लीडिंग", r"ब्लड",
        r"स्पॉटिंग", r"রক্ত", r"લોહી", r"રક્તસ્રાવ", r"ਖੂਨ", r"ਖ਼ੂਨ", r"இரத்த", r"ரத்த", r"రక్త", r"ರಕ್ತ", r"രക്ത",
    ], {M: "MAT_BLEEDING", N: "NB_BLEEDING", C: None, None: "MAT_BLEEDING"}),
    Concept("convulsion", [
        r"\bhad an? fit\b", r"\ba fit\b", r"\bfitting\b",
        r"convuls", r"seizure", r"\bfits\b", r"\bfit (aa|aya|aaya|aate|padte|pad)", r"jh?atk[ea]", r"\bdaur[ea]\b",
        r"दौर[ाे]", r"झटक[ेा]", r"ऐंठन", r"मिर्गी", r"फिट्स", r"फिट आ", r"आकडी", r"खिंचाव",
        r"খিঁচুনি", r"આંચકી", r"ਦੌਰ[ਾੇ]", r"வலிப்பு", r"ఫిట్స్", r"మూర్ఛ", r"ಸೆಳವು", r"ಫಿಟ್ಸ್", r"അപസ്മാര", r"ജന്നി",
    ], {M: "MAT_CONVULSION", N: "NB_CONVULSION", C: "CH_CONVULSION", None: "MAT_CONVULSION"}),
    Concept("headache_severe", [
        r"severe headache", r"(tez|bahut|zyada|jyada|bhayankar)\s+(sir|sar)\s*(me|mein|ka)?\s*dard",
        r"(तेज़?|बहुत|ज़्यादा|ज्यादा|भयंकर)\s*(सिर|सर)\s*(में|का)?\s*दर्द", r"सिरदर्द\s*(बहुत|तेज़?)",
        r"blurr?(ed|y)", r"dhundh?la", r"धुंधला", r"आँखों के आगे अंधेरा", r"ankhon ke aage andhera",
        r"डोक[ंे]\s*(खूप\s*)?दुख", r"মাথা\s*(খুব\s*)?ব্যথা", r"માથું\s*(ખૂબ\s*)?દુ[ખઃ]", r"ਸਿਰ\s*(ਵਿੱਚ\s*)?(ਬਹੁਤ\s*)?ਦਰਦ",
        r"தலைவலி", r"తలనొప్పి", r"ತಲೆನೋವು", r"തലവേദന",
    ], {M: "MAT_HEADACHE_VISION", N: None, C: None, None: "MAT_HEADACHE_VISION"}),
    Concept("fever", [
        r"fever", r"bukh?aa?r", r"बुखार", r"ज्वर", r"(badan|sharir|body)\s+(garam|hot)", r"(बदन|शरीर)\s+गरम",
        r"জ্বর", r"तापाची", r"ताप\s+(आहे|आला|येत)", r"તાવ", r"ਬੁਖਾਰ", r"ਬੁਖ਼ਾਰ", r"காய்ச்சல்", r"జ్వరం", r"ಜ್ವರ", r"പനി",
    ], {M: "MAT_FEVER", N: "NB_FEVER", C: None, None: "MAT_FEVER"}),
    Concept("abdominal_pain", [
        r"(pet|पेट)\s*(me|mein|में)?\s*(bahut|बहुत|tez|तेज़?|zyada|ज़्यादा)\s*(dard|दर्द)", r"(severe|bad)\s+(stomach|belly|abdominal)\s+pain",
        r"abdominal pain", r"stomach pain", r"pet\s*(me|mein|men)?\s*(tez\s*)?dard", r"पेट\s*(में)?\s*(तेज़?\s*)?दर्द",
        r"পেটে ব্যথা", r"पोटात दुख", r"पोटदुखी", r"પેટમાં દુખાવો", r"ਪੇਟ (ਵਿੱਚ )?ਦਰਦ", r"வயிற்று வலி", r"కడుపు నొప్పి",
        r"ಹೊಟ್ಟೆ ನೋವು", r"വയറുവേദന",
    ], {M: "MAT_ABDOMINAL_PAIN", N: None, C: None, None: "MAT_ABDOMINAL_PAIN"}),
    Concept("swelling", [
        r"(face|hands?|feet|legs?)\b[\w\s,]{0,20}\b(swollen|swelling|puffy)", r"(swollen|puffy)\s+(face|hands?|feet)", r"(chehr[ae]|munh|मुँह|मुंह|चेहर[ेा]|हाथ|haath|पैर|pair)[\w\sऀ-ॿ,]{0,20}(सूज|sooj|suj)",
        r"swell", r"puffy", r"s[ou]{1,2}ja?n", r"सूजन", r"सूज", r"ফোলা", r"સોજો", r"ਸੋਜ", r"வீக்கம்", r"వాపు", r"ಊತ", r"നീര്",
    ], {M: "MAT_SWELLING", N: None, C: None, None: "MAT_SWELLING"}),
    Concept("breathing", [
        # a breath word only counts with a difficulty word ("breath hai" alone is not a danger sign)
        r"(breath\w*|saa?ns|सा[ंँ]स)\w*\s*(lene\s*(me|mein|men)\s*|लेने\s*में\s*)?(takl[ie]e?f|dikk?at|pareshani|problem|"
        r"difficult|तकलीफ़?|दिक्कत|परेशानी)",
        r"(difficult|trouble|fast|rapid)\w*\s+breath", r"breathless", r"short(ness)? of breath",
        r"(tez|jaldi|जल्दी|तेज़?)\s*(tez\s*)?(saa?ns|सा[ंँ]स)", r"(saa?ns|सा[ंँ]स)\s*(phool|फूल|nahi le|नहीं ले|ruk|रुक)",
        r"(saa?ns|सा[ंँ]स|breath\w*)\s*(bahut\s*|बहुत\s*|jyada\s*|zyada\s*|ज़्यादा\s*)?(tez|jaldi|fast|rapid|जल्दी|तेज़?)\b",
        r"chh?ati\s*(dhans|andar)", r"pasli\s*chal", r"छाती\s*(धँस|धंस|अंदर)", r"पसली\s*चल", r"chest indrawing",
        r"শ্বাসকষ্ট", r"श्वास घेण्यास त्रास", r"दम लाग", r"શ્વાસ લેવામાં તકલીફ", r"ਸਾਹ (ਲੈਣ ਵਿੱਚ )?ਤਕਲੀਫ਼?",
        r"மூச்சு(த்)? திணறல்", r"శ్వాస (తీసుకోవడంలో )?ఇబ్బంది", r"ఊపిరి ఆడ", r"ಉಸಿರಾಟದ ತೊಂದರೆ", r"ശ്വാസം മുട്ട",
    ], {M: "MAT_BREATHING", N: "NB_FAST_BREATHING", C: "CH_BREATHING", None: "MAT_BREATHING"}),
    Concept("chest_indrawing", [
        r"chest\s+(is\s+)?(pulling|sucking|going)\s+in", r"chest\s+in-?drawing", r"(छाती|chhati|chati)\s*(अंदर|andar)\s*(जा|ja|dhans|धंस)",
        r"chest indrawing", r"छाती\s*(धँस|धंस|अंदर)", r"chh?ati\s*(dhans|andar)", r"pasli\s*chal", r"पसली\s*चल",
    ], {M: None, N: "NB_CHEST_INDRAWING", C: "CH_BREATHING", None: "NB_CHEST_INDRAWING"}),
    Concept("fetal_movement", [
        r"(baby|fetal|foetal|bachch?[ae])\s*(movement|movements)\s*(has|have|is|are)?\s*(reduced|decreased|less|stopped|low)", r"baby\s+(is\s+)?not\s+moving", r"no\s+(fetal|baby)\s+movement", r"(बच्चा|बच्चे)\s*(पेट में)?\s*(हिल|हलचल)[ऀ-ॿ\s]{0,12}(नहीं|कम)",
        r"(movement|halchal|hilna|hil|harkat)\w*\s*(kam|band|nahi|nahin|nai|less|reduced)",
        r"(not|no|less|reduced)\s+(fetal\s+|baby\s+)?movement", r"baby not moving",
        r"(हलचल|हिलना|हिल|हरकत|मूवमेंट)\w*\s*(कम|बंद|नहीं|नही)", r"নড়াচড়া কম", r"हालचाल कमी", r"હલનચલન ઓછ",
        r"ਹਿਲਜੁਲ ਘੱਟ", r"அசைவு குறை", r"కదలిక తగ్గ", r"ಚಲನೆ ಕಡಿಮೆ", r"അനക്കം കുറ",
    ], {M: "MAT_FETAL_MOVEMENT", N: "NB_LETHARGY", C: "CH_LETHARGY", None: "MAT_FETAL_MOVEMENT"}, negatable=False),
    Concept("water_leak", [
        r"(paani|पानी)\s*(ki|की)?\s*(thaili|थैली)\s*(phat|फट)", r"(thaili|थैली)\s*(phat|फट)", r"waters?\s+(has\s+|have\s+)?(broken|broke|leaking)",
        r"pa?a?ni\s*(nikal|gir|ja|aa)", r"पानी\s*(निकल|गिर|जा|आ)", r"water (broke|leak)", r"leaking", r"पानी की थैली",
    ], {M: "MAT_WATER_LEAK", N: None, C: None, None: "MAT_WATER_LEAK"}),
    Concept("foul_discharge", [
        r"foul", r"smelly discharge", r"badbu", r"बदबू", r"durgandh", r"दुर्गंध",
    ], {M: "MAT_FOUL_DISCHARGE", N: None, C: None, None: "MAT_FOUL_DISCHARGE"}),
    Concept("not_feeding", [
        r"(doo?dh|feed|feeding|stanpan)\s*(bhi\s*|bilkul\s*|theek se\s*)?(nahi|nahin|nai|band|kam)\s*(pee|pi|le|kar)?",
        r"not (feeding|sucking|drinking)", r"poor (feeding|suck)", r"(unable|can'?t) (to )?(drink|feed|suck)",
        r"ch[ou]+s\s*nahi", r"दूध\s*(भी\s*|बिलकुल\s*|ठीक से\s*)?(नहीं|नही|कम|बंद)", r"स्तनपान\s*(नहीं|नही|बंद)", r"चूस\s*(नहीं|नही)",
        r"पीना\s*बंद", r"दूध\s*पी\s*नहीं", r"দুধ খাচ্ছে না", r"दूध पीत नाही", r"દૂધ પીતું નથી", r"ਦੁੱਧ ਨਹੀਂ",
        r"பால் குடிக்க(வில்லை|ல)", r"పాలు తాగడం లేదు", r"ಹಾಲು ಕುಡಿಯು?ತ್ತಿಲ್ಲ", r"പാൽ കുടിക്കുന്നില്ല",
    ], {M: None, N: "NB_NOT_FEEDING", C: "CH_UNABLE_TO_DRINK", None: "NB_NOT_FEEDING"}, negatable=False),
    Concept("cold", [
        r"(baby|बच्चा|बच्ची|शिशु)\s*(is\s+)?(very\s+)?(cold|thanda|ठंडा)", r"cold to (the )?touch", r"(body|sharir|शरीर)\s*(is\s+)?(very\s+)?(cold|thanda|ठंडा)",
        r"(badan|sharir|body|haath\s*pair)\s*(thand[ae]|cold)", r"cold (to touch|body)", r"hypotherm",
        r"(बदन|शरीर|हाथ\s*पैर)\s*ठंड[ाे]", r"ठंडा पड़",
    ], {M: None, N: "NB_COLD", C: None, None: "NB_COLD"}),
    Concept("lethargy", [
        r"(very|too)\s+sleepy", r"not\s+waking\s+up", r"(bahut|बहुत)\s*(neend|नींद|सो)", r"drowsy",
        r"letharg", r"unconscious", r"\blimp\b", r"\bsust\b", r"सुस्त", r"behosh", r"बेहोश", r"jaag\s*nahi", r"जाग\s*नहीं",
        r"not (waking|moving)", r"হুঁশ নেই", r"নিস্তেজ", r"बेशुद्ध", r"મંદ", r"ਬੇਹੋਸ਼", r"மயக்க", r"స్పృహ", r"ಪ್ರಜ್ಞೆ", r"ബോധ",
    ], {M: None, N: "NB_LETHARGY", C: "CH_LETHARGY", None: "NB_LETHARGY"}, negatable=False),
    Concept("jaundice", [
        r"(baby|skin|eyes?|palms?|soles?|body)\s+(is\s+|are\s+|look\s+|looks\s+)?(yellow|peela|peeli|पीला|पीली)", r"(yellow|पीली|पीला)\s+(eyes|skin|palms|soles|आँखें|त्वचा)",
        r"jaundice", r"p[ie]e?liya", r"पीलिया", r"yellow (skin|palm|sole|eye)", r"(हथेली|तलवे|आँख|आंख|त्वचा)\w*\s*पील",
        r"peela pad", r"पीला पड़", r"কামলা", r"न्याबा", r"कावीळ", r"કમળો", r"ਪੀਲੀਆ", r"மஞ்சள் காமாலை", r"కామెర్లు",
        r"ಕಾಮಾಲೆ", r"മഞ്ഞപ്പിത്ത",
    ], {M: None, N: "NB_JAUNDICE", C: None, None: "NB_JAUNDICE"}),
    Concept("umbilicus", [
        r"pus\s+(from|in|on)\s+(the\s+)?(navel|umbilicus|cord|belly button)", r"(navel|cord|umbilicus|belly button)\s+(is\s+)?(red|pus|oozing|swollen|smelly)", r"(naabhi|नाभि|nabhi)\s*(se|से|में|mein)?\s*(pus|मवाद|mawad|पस|लाल|laal)",
        r"(nabhi|naal|cord|umbilic|नाभि|नाल)\w*.{0,30}(pus|red|lal|mawad|swell|badbu|मवाद|पीप|लाल|सूज|बदबू|पानी)",
        r"(pus|mawad|मवाद|पीप).{0,30}(nabhi|naal|cord|umbilic|नाभि|नाल)",
    ], {M: None, N: "NB_UMBILICUS", C: None, None: "NB_UMBILICUS"}),
    Concept("pustules", [
        r"pustule", r"\bboils?\b", r"ph[ou]nsi", r"फुंसि?य?ा?", r"फोड़े",
    ], {M: None, N: "NB_PUSTULES", C: None, None: "NB_PUSTULES"}),
    Concept("unable_to_drink", [
        r"(cannot|can't|can not|unable to|not able to)\s+(drink|breastfeed|feed|suck)", r"(peena|पीना|pi)\s*(nahi|नहीं)\s*(paa|पा)",
        r"(दूध|doodh|पानी|paani)\s*(भी)?\s*(नहीं)\s*(पी\s*पा|pi\s*pa)",
    ], {M: None, N: "NB_NOT_FEEDING", C: "CH_UNABLE_TO_DRINK", None: "CH_UNABLE_TO_DRINK"}, negatable=False),
    Concept("low_weight", [
        r"(very\s+)?low\s+birth\s*weight", r"\blbw\b", r"(बहुत\s+)?कम\s+वज़?न\s+(का|वाला)\s+(बच्चा|शिशु)",
    ], {M: None, N: "NB_LOW_WEIGHT", C: None, None: "NB_LOW_WEIGHT"}, negatable=False),
    Concept("vomits_everything", [
        r"vomit\w*\s*everything", r"(sab|har\s*cheez|baar\s*baar)\s*(kuch\s*)?ul?ti", r"(सब|हर चीज़?|बार बार)\s*(कुछ\s*)?उल्टी",
    ], {M: None, N: "NB_NOT_FEEDING", C: "CH_VOMITS_EVERYTHING", None: "CH_VOMITS_EVERYTHING"}),
]

for _c in CONCEPTS:
    _c._re = [re.compile(p, re.IGNORECASE) for p in _c.patterns]

# Phrases that contain danger words but are NOT danger signs (removed before matching).
NON_DANGER = [re.compile(p, re.IGNORECASE) for p in [
    r"blood\s*(pressure|test|sugar|group|report)", r"ब्लड\s*(प्रेशर|टेस्ट|शुगर|ग्रुप|रिपोर्ट)", r"रक्तचाप", r"रक्त\s*चाप",
    r"kh?oo?n\s*(ki|ka)\s*(kami|jaanch|janch|test)", r"खून\s*(की|का)\s*(कमी|जांच|जाँच|टेस्ट)", r"तापमान",
    r"রক্তচাপ", r"রক্ত\s*পরীক্ষা", r"રક્તચાપ", r"ਬਲੱਡ ਪ੍ਰੈਸ਼ਰ", r"இரத்த அழுத்த", r"రక్తపోటు", r"ರಕ್ತದೊತ್ತಡ", r"രക്തസമ്മർദ",
    r"\bbp\b", r"बीपी",
]]

# Words that, right after (or right before) a sign word, mean "not present / normal".
NEGATORS = {
    "nahi", "nahin", "nai", "na", "no", "not", "nil", "none", "absent", "never", "normal", "theek", "thik", "sahi",
    "fine", "ok", "okay", "koi", "nahee", "नहीं", "नही", "ना", "नॉर्मल", "सामान्य", "ठीक", "सही", "नाही", "নেই", "না",
    "নাই", "স্বাভাবিক", "નથી", "ના", "ਨਹੀਂ", "ਨਹੀ", "இல்லை", "లేదు", "ಇಲ್ಲ", "ഇല്ല",
}
NEG_BEFORE = {"no", "not", "koi", "कोई", "without", "बिना", "bina"}
_TOKEN = re.compile(r"[\wऀ-෿਀-૿]+", re.UNICODE)
_CLAUSE_SPLIT = re.compile(r"[,.;!?।|]+|\b(but|lekin|magar|par|however)\b|लेकिन|मगर|पर\s", re.IGNORECASE)


@dataclass
class Hit:
    sign_id: str
    evidence: str
    negated: bool = False
    source: str = "keyword"  # keyword | threshold | llm
    concept: str = ""


def _group(beneficiary_type: str | None) -> str | None:
    return {"pregnant": M, "postpartum": M, "newborn": N, "child": C}.get(beneficiary_type or "", None)


def detect_text(text: str, beneficiary_type: str | None) -> list[Hit]:
    """Keyword detector with simple negation handling. Returns positive AND negated hits."""
    group = _group(beneficiary_type)
    cleaned = text.lower()
    for rx in NON_DANGER:
        cleaned = rx.sub(" ", cleaned)
    hits: dict[str, Hit] = {}
    for clause in _CLAUSE_SPLIT.split(cleaned):
        if not clause or not clause.strip():
            continue
        # find all concept matches in this clause with positions
        found = []
        for concept in CONCEPTS:
            for rx in concept._re:
                for m in rx.finditer(clause):
                    found.append((m.start(), m.end(), concept, m.group(0)))
        found.sort(key=lambda f: f[0])
        for i, (start, end, concept, word) in enumerate(found):
            sign_id = concept.by_type.get(group)
            if not sign_id:
                continue
            # "breathing 40 per minute" is a measurement, handled by thresholds
            if concept.name == "breathing" and re.search(r"\d", clause[end:end + 25]):
                continue
            negated = False
            if concept.negatable:
                next_start = next((f[0] for f in found[i + 1:] if f[2] is not concept and f[0] >= end), len(clause))
                after = _TOKEN.findall(clause[end:next_start])[:3]
                before = _TOKEN.findall(clause[:start])[-2:]
                negated = any(t in NEGATORS for t in after) or any(t in NEG_BEFORE for t in before)
            prev = hits.get(sign_id)
            if prev is None or (prev.negated and not negated):
                hits[sign_id] = Hit(sign_id, word.strip(), negated, concept=concept.name)
    return list(hits.values())


# Signs that are purely a measurement threshold -> the record fields that decide them.
NUMERIC_SIGNS = {
    "MAT_HIGH_BP": ("bp_systolic", "bp_diastolic"),
    "MAT_SEVERE_ANAEMIA": ("hb_g_dl",),
    "NB_LOW_WEIGHT": ("weight_kg",),
}


def remap(concept_name: str, beneficiary_type: str | None) -> str | None:
    """Sign id for a keyword concept once the beneficiary type is known."""
    c = next((c for c in CONCEPTS if c.name == concept_name), None)
    return c.by_type.get(_group(beneficiary_type)) if c else None


def detect_thresholds(rec: dict) -> list[Hit]:
    """Numeric danger thresholds on the structured record."""
    out: list[Hit] = []
    group = _group(rec.get("beneficiary_type"))
    t_c = rec.get("temperature_c")
    if t_c is None and rec.get("temperature_f") is not None:
        t_c = (float(rec["temperature_f"]) - 32) * 5 / 9
    if group == M:
        s, d = rec.get("bp_systolic"), rec.get("bp_diastolic")
        if s and d and d > s:
            s, d = d, s  # spoken in reverse ("80/120"): judge it as 120/80 while she confirms
        if (s and s >= 140) or (d and d >= 90):
            out.append(Hit("MAT_HIGH_BP", f"BP {s}/{d}", source="threshold"))
        hb = rec.get("hb_g_dl")
        if hb is not None and hb < 7:
            out.append(Hit("MAT_SEVERE_ANAEMIA", f"Hb {hb}", source="threshold"))
        if t_c is not None and t_c >= 38.0:
            out.append(Hit("MAT_FEVER", f"temperature {round(t_c, 1)} C", source="threshold"))
        rr = rec.get("respiratory_rate")
        if rr is not None and (rr >= 30 or rr < 10):  # WHO IMPAC: RR >= 30 = respiratory distress; < 10 = depressed
            out.append(Hit("MAT_BREATHING", f"breathing {rr} per minute", source="threshold"))
    if group == N:
        if t_c is not None and t_c >= 37.5:
            out.append(Hit("NB_FEVER", f"temperature {round(t_c, 1)} C", source="threshold"))
        if t_c is not None and t_c < 35.5:
            out.append(Hit("NB_COLD", f"temperature {round(t_c, 1)} C", source="threshold"))
        rr = rec.get("respiratory_rate")
        if rr is not None and rr >= 60:
            out.append(Hit("NB_FAST_BREATHING", f"breathing {rr}/min", source="threshold"))
        w = rec.get("weight_kg")
        if w is not None and 0 < w < 1.8:
            out.append(Hit("NB_LOW_WEIGHT", f"weight {w} kg", source="threshold"))
    return out


# ---------------------------------------------------------------------------
# Escalation messages (fixed templates: no diagnosis, no treatment advice)
# ---------------------------------------------------------------------------

ESCALATION = {
    "hi-IN": "ध्यान दीजिए: {label}. यह खतरे का संकेत है. अभी PHC रेफ़र कीजिए. मैंने रिकॉर्ड पर निशान लगा दिया है.",
    "en-IN": "Attention: {label}. This is a danger sign. Please refer to the PHC now. I have flagged this record.",
    "mr-IN": "लक्ष द्या: {label}. हे धोक्याचे लक्षण आहे. आत्ताच PHC ला रेफर करा. मी रेकॉर्डवर खूण केली आहे.",
    "bn-IN": "সতর্ক হোন: {label}. এটি বিপদের লক্ষণ. এখনই PHC-তে রেফার করুন. আমি রেকর্ডে চিহ্ন দিয়েছি.",
    "gu-IN": "ધ્યાન આપો: {label}. આ જોખમનું લક્ષણ છે. હમણાં જ PHC માં રેફર કરો. મેં રેકોર્ડ પર નિશાન કર્યું છે.",
    "pa-IN": "ਧਿਆਨ ਦਿਓ: {label}. ਇਹ ਖ਼ਤਰੇ ਦਾ ਲੱਛਣ ਹੈ. ਹੁਣੇ PHC ਰੈਫ਼ਰ ਕਰੋ. ਮੈਂ ਰਿਕਾਰਡ ਉੱਤੇ ਨਿਸ਼ਾਨ ਲਗਾ ਦਿੱਤਾ ਹੈ.",
    "ta-IN": "கவனிக்கவும்: {label}. இது ஆபத்து அறிகுறி. உடனே PHC-க்கு அனுப்புங்கள். பதிவில் குறியிட்டுள்ளேன்.",
    "te-IN": "గమనించండి: {label}. ఇది ప్రమాద సంకేతం. వెంటనే PHC కి రిఫర్ చేయండి. రికార్డులో గుర్తు పెట్టాను.",
    "kn-IN": "ಗಮನಿಸಿ: {label}. ಇದು ಅಪಾಯದ ಲಕ್ಷಣ. ಈಗಲೇ PHC ಗೆ ರೆಫರ್ ಮಾಡಿ. ದಾಖಲೆಯಲ್ಲಿ ಗುರುತು ಹಾಕಿದ್ದೇನೆ.",
    "ml-IN": "ശ്രദ്ധിക്കുക: {label}. ഇത് അപകട സൂചനയാണ്. ഉടൻ PHC-ലേക്ക് റഫർ ചെയ്യുക. റെക്കോർഡിൽ അടയാളപ്പെടുത്തി.",
}


def label(sign_id: str, lang: str) -> str:
    s = SIGNS[sign_id]
    return s.hi if lang in ("hi-IN", "mr-IN") else s.en


def escalation_text(sign_ids: list[str], lang: str) -> str:
    labels = ", ".join(label(i, lang) for i in sign_ids)
    return ESCALATION.get(lang, ESCALATION["en-IN"]).format(label=labels)


def list_for_prompt() -> str:
    return "\n".join(f"- {s.id} ({s.group}): {s.en}" for s in SIGNS.values())
