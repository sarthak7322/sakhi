# Danger-sign list and sources

The agent raises a danger flag **only** for the signs below. For every flag it says one fixed sentence in the
worker's language: *"this is a danger sign, refer to the PHC now, the record is flagged"*. It gives no diagnosis
and no treatment advice. The list lives in code at `server/danger.py` (`SIGNS`), and the live app serves it at
`GET /api/danger-signs`.

## Sources (public guidelines)

1. **Mother and Child Protection (MCP) Card**, Ministry of Health & Family Welfare and Ministry of Women & Child
   Development, Government of India (joint MCP card, 2018 edition). It covers danger signs during pregnancy and
   after delivery, and newborn danger signs.
2. **Home Based Newborn Care (HBNC) Operational Guidelines**, MoHFW, Government of India (revised 2014),
   together with the ASHA training modules 6 & 7 that implement HBNC. These cover newborn danger signs, the
   temperature limits and the HBNC home-visit schedule (days 1, 3, 7, 14, 21, 28 and 42).
3. **Integrated Management of Neonatal and Childhood Illness (IMNCI)**, WHO/UNICEF, adapted by MoHFW. Its
   *general danger signs* in young children are used for the child group.
4. **Universal Immunization Programme – National Immunization Schedule**, MoHFW. This is used for the
   "vaccinations due" list. It is not a danger sign but is part of the follow-up list.

> Before submitting, check each threshold against the PDF edition you cite. The numbers we used are BP ≥ 140/90,
> Hb < 7 g/dL, newborn temperature ≥ 37.5 °C or < 35.5 °C, newborn breathing ≥ 60/min and newborn weight
> < 1.8 kg. The team should then lock the edition and page numbers here.

## The list

| id | group | sign | how it is detected | source |
|---|---|---|---|---|
| MAT_BLEEDING | pregnancy / after delivery | bleeding in pregnancy, heavy bleeding after delivery | keywords + LLM | MCP |
| MAT_CONVULSION | maternal | fits / convulsions | keywords + LLM | MCP |
| MAT_HEADACHE_VISION | maternal | severe headache, blurred vision | keywords + LLM | MCP |
| MAT_FEVER | maternal | high fever (≥ 38 °C if measured) | keywords + threshold + LLM | MCP |
| MAT_ABDOMINAL_PAIN | maternal | severe abdominal pain | keywords + LLM | MCP |
| MAT_SWELLING | maternal | swelling of face / hands | keywords + LLM | MCP |
| MAT_BREATHING | maternal | difficulty in breathing; also breathing rate ≥ 30 or < 10 per minute | keywords + threshold + LLM | MCP card; rate cut-offs from WHO IMPAC (Managing Complications in Pregnancy and Childbirth) |
| MAT_FETAL_MOVEMENT | pregnancy | reduced / absent fetal movement | keywords + LLM | MCP |
| MAT_WATER_LEAK | pregnancy | leaking of water before labour | keywords + LLM | MCP |
| MAT_FOUL_DISCHARGE | after delivery | foul-smelling discharge | keywords + LLM | MCP |
| MAT_HIGH_BP | maternal | BP ≥ 140/90 | threshold on the record | MCP / ANC |
| MAT_SEVERE_ANAEMIA | maternal | Hb < 7 g/dL | threshold on the record | MCP / Anemia Mukt Bharat |
| NB_NOT_FEEDING | newborn | not feeding / poor suck | keywords + LLM | HBNC |
| NB_CONVULSION | newborn | fits | keywords + LLM | HBNC |
| NB_FAST_BREATHING | newborn | breathing ≥ 60/min or difficult breathing | keywords + threshold + LLM | HBNC |
| NB_CHEST_INDRAWING | newborn | chest indrawing ("pasli chalna") | keywords + LLM | HBNC |
| NB_FEVER | newborn | temperature ≥ 37.5 °C | keywords + threshold + LLM | HBNC |
| NB_COLD | newborn | temperature < 35.5 °C, cold to touch | keywords + threshold + LLM | HBNC |
| NB_LETHARGY | newborn | lethargic / unconscious | keywords + LLM | HBNC |
| NB_JAUNDICE | newborn | yellow palms/soles, jaundice on day 1 | keywords + LLM | HBNC |
| NB_UMBILICUS | newborn | red or pus-filled umbilicus | keywords + LLM | HBNC |
| NB_PUSTULES | newborn | many skin pustules / boils | keywords + LLM | HBNC |
| NB_BLEEDING | newborn | bleeding from any site | keywords + LLM | HBNC |
| NB_LOW_WEIGHT | newborn | weight < 1.8 kg | threshold on the record | HBNC |
| CH_CONVULSION | child | fits | keywords + LLM | IMNCI |
| CH_UNABLE_TO_DRINK | child | unable to drink / breastfeed | keywords + LLM | IMNCI |
| CH_VOMITS_EVERYTHING | child | vomits everything | keywords + LLM | IMNCI |
| CH_LETHARGY | child | lethargic / unconscious | keywords + LLM | IMNCI |
| CH_BREATHING | child | difficult breathing / chest indrawing | keywords + LLM | IMNCI |

## Why three detectors

A missed danger sign costs double, so detection is a **union** of three detectors. None of them can remove a
flag that another one raised.

1. **Keyword detector** (`detect_text`). Regexes cover Hindi, Hinglish and English, plus the key words in Marathi,
   Bengali, Gujarati, Punjabi, Tamil, Telugu, Kannada and Malayalam. It runs on the transcript **before the LLM**,
   so the referral message is the first audio she hears. It handles negation ("bleeding nahi hai", "no fever",
   "saans normal hai") within a short window. A phrase such as "bleeding hai, bukhar nahi" still flags bleeding.
   It ignores phrases that only look like danger words ("blood pressure", "khoon ki kami ki jaanch", "तापमान").
2. **Threshold detector** (`detect_thresholds`). It checks the numbers in the structured record (BP, Hb,
   temperature in °C or °F, breathing rate, newborn weight).
3. **LLM flags.** The model may add ids from this list only. It is told that an extra referral is better than a
   missed one. This catches phrasings and languages the regexes miss.
