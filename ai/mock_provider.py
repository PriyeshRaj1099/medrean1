"""Deterministic offline AI provider.

This provider requires no API key and no network access. It is a real,
rule-based analysis engine (not a fake placeholder):
* structured analysis is generated from the extracted values + retrieved
  knowledge-base chunks, with citations,
* chat answers are composed from report data and retrieved chunks,
* embeddings are deterministic signed-hash vectors (dimension from settings).

It exists so the whole product is demonstrable and testable offline. Set
AI_PROVIDER=openai to use a real LLM through the OpenAI-compatible provider.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any

from ai.base import (
    AIProvider,
    AnalysisRequest,
    ChatAnswer,
    ChatRequest,
    RetrievedChunk,
    ValueContext,
)
from ai.safety import (
    EMERGENCY_ADDENDUM,
    detect_blocked_intent,
    detect_urgent,
    refusal_answer,
)
from backend.schemas.analysis import (
    AbnormalResultItem,
    EducationalExplanation,
    NormalResultItem,
    SourceReference,
    StructuredAnalysis,
    TestExplanationItem,
)
from backend.schemas.report import ReferenceRangeOut
from document_processing.lab_tests import ALIAS_INDEX, TESTS_BY_KEY

# ----------------------------------------------------------------------
# Multilingual message templates (education-only wording)
# ----------------------------------------------------------------------
TEMPLATES: dict[str, dict[str, str]] = {
    "en": {
        "summary_intro": "This report contains {n} measurements.",
        "summary_counts": (
            "{within} are within the reference range shown on the report, {below} are below it, "
            "{above} are above it, and {unknown} could not be classified because no usable "
            "reference range was found."
        ),
        "summary_abnormal": "Values outside the range shown on your report: {list}.",
        "summary_caveat": (
            "This is an educational explanation, not a diagnosis. Please discuss these results "
            "with a qualified healthcare professional."
        ),
        "range_below": "The value reported on your report is below the reference range printed by the laboratory.",
        "range_above": "The value reported on your report is above the reference range printed by the laboratory.",
        "factors": (
            "Many factors — including the testing method used, how the sample was collected, "
            "and normal biological variation — can influence a single laboratory result."
        ),
        "low_conf_note": (
            "Some information could not be extracted confidently. Please verify the value "
            "against your original report."
        ),
        "question": (
            "Ask your healthcare professional what your {test} result means in the context of "
            "your symptoms, history and examination."
        ),
        "no_evidence": (
            "Sufficient supporting information was not found in the knowledge base for some "
            "topics, so those explanations are general only."
        ),
        "limitations": [
            "A single laboratory result on its own cannot confirm or exclude any medical condition.",
            "Reference ranges are specific to the laboratory and method that produced them; the range printed on your report takes precedence.",
            "Laboratory results should always be interpreted together with your symptoms, medical history and examination.",
            "This explanation was generated for educational purposes from the values on your report and general health information.",
            "It is not a diagnosis, and it does not replace advice from a qualified healthcare professional.",
            "Values flagged here reflect the numbers on your report; if a value looks different from your original copy, please re-check it.",
        ],
        "chat_no_report": (
            "I don't have a report attached to this conversation, so I can only share general "
            "educational information. Open a report and ask again for answers grounded in your values."
        ),
        "chat_out_of_range_none": (
            "Based on the report attached to this conversation, none of the values are outside "
            "the reference ranges shown on the report."
        ),
        "chat_out_of_range_header": "Based on your report, these values are outside the range printed on the report:",
        "chat_out_of_range_footer": (
            "Please review these with your healthcare professional, who can interpret them with "
            "your symptoms and history."
        ),
        "chat_meaning_header": "In simple language:",
        "chat_no_kb": (
            "I could not find sufficiently relevant information in the knowledge base to answer "
            "that confidently. Please consider asking your healthcare professional."
        ),
        "chat_footer": (
            "This is general educational information, not a medical diagnosis. "
            "For personal medical advice, consult a qualified healthcare professional."
        ),
        "status_within": (
            "On your report, {name} is {value} {unit} (reference range: {range}). "
            "This is within the range printed by the laboratory."
        ),
        "status_below": (
            "On your report, {name} is {value} {unit} (reference range: {range}). "
            "This is below the range printed by the laboratory."
        ),
        "status_above": (
            "On your report, {name} is {value} {unit} (reference range: {range}). "
            "This is above the range printed by the laboratory."
        ),
        "status_unknown": (
            "On your report, {name} is {value} {unit}. The report did not include a "
            "usable reference range, so this value could not be classified."
        ),
        "generic_test_description": "This measurement is reported in laboratory testing.",
        "unknown_limitation": (
            "{n} value(s) could not be classified because the report did not provide a "
            "usable reference range. No range has been assumed for them."
        ),
        "value_line": "On your report, {name} is {value} {unit} (reference range: {range}).",
        "question_generic_1": (
            "Ask your healthcare professional what these results mean together with your "
            "symptoms, history and examination."
        ),
        "question_generic_2": (
            "Ask whether any of these values should be repeated or monitored over time."
        ),
        "question_generic_3": (
            "Ask what day-to-day factors could be relevant for these results."
        ),
    },
    "hi": {
        "summary_intro": "इस रिपोर्ट में {n} माप शामिल हैं।",
        "summary_counts": (
            "{within} मान रिपोर्ट में दी गई संदर्भ सीमा के भीतर हैं, {below} उससे कम हैं, {above} "
            "उससे अधिक हैं, और {unknown} को वर्गीकृत नहीं किया जा सका क्योंकि कोई उपयोगी संदर्भ "
            "सीमा नहीं मिली।"
        ),
        "summary_abnormal": "आपकी रिपोर्ट में दी गई संदर्भ सीमा से बाहर के मान: {list}।",
        "summary_caveat": (
            "यह एक शैक्षिक व्याख्या है, निदान नहीं। कृपया इन परिणामों पर योग्य चिकित्सक से चर्चा करें।"
        ),
        "range_below": "आपकी रिपोर्ट में दिया गया मान प्रयोगशाला द्वारा छपी संदर्भ सीमा से कम है।",
        "range_above": "आपकी रिपोर्ट में दिया गया मान प्रयोगशाला द्वारा छपी संदर्भ सीमा से अधिक है।",
        "factors": (
            "कई कारक — जांच की विधि, नमूना लेने का तरीका और सामान्य जैविक भिन्नता — किसी एक "
            "प्रयोगशाला परिणाम को प्रभावित कर सकते हैं।"
        ),
        "low_conf_note": (
            "कुछ जानकारी विश्वास के साथ नहीं निकाली जा सकी। कृपया मान को अपनी मूल रिपोर्ट से मिलाकर जांचें।"
        ),
        "question": (
            "अपने चिकित्सक से पूछें कि आपके लक्षणों, इतिहास और जांच के संदर्भ में आपके {test} "
            "परिणाम का क्या अर्थ है।"
        ),
        "no_evidence": (
            "कुछ विषयों के लिए ज्ञानकोश में पर्याप्त सहायक जानकारी नहीं मिली, इसलिए वे व्याख्याएं "
            "केवल सामान्य हैं।"
        ),
        "limitations": [
            "कोई भी एक प्रयोगशाला परिणाम अकेले किसी चिकित्सीय स्थिति की पुष्टि या बहिष्कार नहीं कर सकता।",
            "संदर्भ सीमाएं उस प्रयोगशाला और विधि के अनुसार होती हैं जिनसे वे बनीं; आपकी रिपोर्ट में छपी सीमा सर्वोपरि है।",
            "प्रयोगशाला परिणामों की व्याख्या सदैव आपके लक्षणों, इतिहास और जांच के साथ मिलाकर की जानी चाहिए।",
            "यह व्याख्या शैक्षिक उद्देश्य के लिए आपकी रिपोर्ट के मानों और सामान्य स्वास्थ्य जानकारी से बनाई गई है।",
            "यह निदान नहीं है और किसी योग्य चिकित्सक की सलाह का विकल्प नहीं है।",
            "यहां दिखाए गए मान आपकी रिपोर्ट के अनुसार हैं; यदि कोई मान भिन्न लगे तो कृपया मूल प्रति से मिलाएं।",
        ],
        "chat_no_report": (
            "इस बातचीत से कोई रिपोर्ट जुड़ी नहीं है, इसलिए मैं केवल सामान्य शैक्षिक जानकारी दे सकता हूं। "
            "रिपोर्ट खोलकर दोबारा पूछें ताकि उत्तर आपके मानों पर आधारित हों।"
        ),
        "chat_out_of_range_none": (
            "इस बातचीत से जुड़ी रिपोर्ट के अनुसार, कोई भी मान रिपोर्ट में दी गई संदर्भ सीमा से बाहर नहीं है।"
        ),
        "chat_out_of_range_header": "आपकी रिपोर्ट के अनुसार, ये मान रिपोर्ट में छपी संदर्भ सीमा से बाहर हैं:",
        "chat_out_of_range_footer": (
            "कृपया इन पर अपने चिकित्सक से चर्चा करें, जो आपके लक्षणों और इतिहास के साथ इनकी व्याख्या कर सकते हैं।"
        ),
        "chat_meaning_header": "सरल भाषा में:",
        "chat_no_kb": (
            "इस प्रश्न का विश्वसनीय उत्तर देने के लिए ज्ञानकोश में पर्याप्त प्रासंगिक जानकारी नहीं मिली। "
            "कृपया अपने चिकित्सक से पूछने पर विचार करें।"
        ),
        "chat_footer": (
            "यह सामान्य शैक्षिक जानकारी है, चिकित्सीय निदान नहीं। व्यक्तिगत सलाह के लिए योग्य चिकित्सक से मिलें।"
        ),
        "status_within": (
            "आपकी रिपोर्ट में {name} {value} {unit} है (संदर्भ सीमा: {range})। "
            "यह प्रयोगशाला द्वारा छपी सीमा के भीतर है।"
        ),
        "status_below": (
            "आपकी रिपोर्ट में {name} {value} {unit} है (संदर्भ सीमा: {range})। "
            "यह प्रयोगशाला द्वारा छपी सीमा से कम है।"
        ),
        "status_above": (
            "आपकी रिपोर्ट में {name} {value} {unit} है (संदर्भ सीमा: {range})। "
            "यह प्रयोगशाला द्वारा छपी सीमा से अधिक है।"
        ),
        "status_unknown": (
            "आपकी रिपोर्ट में {name} {value} {unit} है। रिपोर्ट में उपयोगी संदर्भ सीमा नहीं थी, "
            "इसलिए इस मान को वर्गीकृत नहीं किया जा सका।"
        ),
        "generic_test_description": "यह माप प्रयोगशाला जांच में बताया जाता है।",
        "unknown_limitation": (
            "{n} मानों को वर्गीकृत नहीं किया जा सका क्योंकि रिपोर्ट में उपयोगी संदर्भ सीमा नहीं दी गई थी। "
            "उनके लिए कोई सीमा मान नहीं ली गई है।"
        ),
        "value_line": "आपकी रिपोर्ट में {name} {value} {unit} है (संदर्भ सीमा: {range})।",
        "question_generic_1": (
            "अपने चिकित्सक से पूछें कि आपके लक्षणों, इतिहास और जांच के साथ इन परिणामों का क्या अर्थ है।"
        ),
        "question_generic_2": (
            "पूछें कि क्या इनमें से कोई मान दोबारा जांचना या समय-समय पर निगरानी करना जरूरी है।"
        ),
        "question_generic_3": (
            "पूछें कि इन परिणामों के लिए रोजमर्रा के कौन से कारक मायने रख सकते हैं।"
        ),
    },
    "hinglish": {
        "summary_intro": "Is report mein {n} measurements hain.",
        "summary_counts": (
            "{within} values report par di gayi reference range ke andar hain, {below} usse kam "
            "hain, {above} usse zyada hain, aur {unknown} ko classify nahi kiya ja saka kyunki "
            "koi usable reference range nahi mili."
        ),
        "summary_abnormal": "Aapki report mein di gayi reference range se bahar ki values: {list}.",
        "summary_caveat": (
            "Yeh ek educational explanation hai, diagnosis nahi. In results par qualified "
            "healthcare professional se baat karein."
        ),
        "range_below": "Aapki report mein di gayi value laboratory ki printed reference range se kam hai.",
        "range_above": "Aapki report mein di gayi value laboratory ki printed reference range se zyada hai.",
        "factors": (
            "Kaafi factors — testing method, sample kaise liya gaya, aur normal biological "
            "variation — ek single lab result ko affect kar sakte hain."
        ),
        "low_conf_note": (
            "Kuch information confidently extract nahi ho payi. Value ko apni original report "
            "se verify karein."
        ),
        "question": (
            "Apne doctor se poochein ki aapke symptoms, history aur examination ke context mein "
            "aapke {test} result ka kya matlab hai."
        ),
        "no_evidence": (
            "Kuch topics ke liye knowledge base mein kaafi supporting information nahi mili, "
            "isliye wo explanations sirf general hain."
        ),
        "limitations": [
            "Sirf ek lab result apne aap kisi medical condition ko confirm ya exclude nahi kar sakta.",
            "Reference ranges us laboratory aur method ke hisaab se hoti hain; aapki report par printed range hi maaney hai.",
            "Lab results ko hamesha aapke symptoms, history aur examination ke saath mila kar samjha jata hai.",
            "Yeh explanation educational purpose ke liye aapki report ki values aur general health information se banayi gayi hai.",
            "Yeh diagnosis nahi hai aur kisi qualified doctor ki advice ka replacement nahi hai.",
            "Yahan dikhayi gayi values aapki report ke hisaab se hain; koi value alag lage to original copy se check karein.",
        ],
        "chat_no_report": (
            "Is conversation mein koi report attached nahi hai, isliye main sirf general "
            "educational information de sakta hoon. Report khol kar dobara poochein taaki "
            "jawab aapki values par based ho."
        ),
        "chat_out_of_range_none": (
            "Is conversation se judi report ke hisaab se, koi bhi value report par di gayi "
            "reference range se bahar nahi hai."
        ),
        "chat_out_of_range_header": "Aapki report ke hisaab se, ye values report par printed range se bahar hain:",
        "chat_out_of_range_footer": (
            "In par apne doctor se baat karein, jo aapke symptoms aur history ke context mein "
            "inhe samjha sakte hain."
        ),
        "chat_meaning_header": "Simple language mein:",
        "chat_no_kb": (
            "Is sawaal ka bharosemand jawab dene ke liye knowledge base mein kaafi relevant "
            "information nahi mili. Please apne doctor se poochne par vichaar karein."
        ),
        "chat_footer": (
            "Yeh general educational information hai, medical diagnosis nahi. Personal advice "
            "ke liye qualified doctor se milein."
        ),
        "status_within": (
            "Aapki report mein {name} {value} {unit} hai (reference range: {range}). "
            "Yeh laboratory ki printed range ke andar hai."
        ),
        "status_below": (
            "Aapki report mein {name} {value} {unit} hai (reference range: {range}). "
            "Yeh laboratory ki printed range se kam hai."
        ),
        "status_above": (
            "Aapki report mein {name} {value} {unit} hai (reference range: {range}). "
            "Yeh laboratory ki printed range se zyada hai."
        ),
        "status_unknown": (
            "Aapki report mein {name} {value} {unit} hai. Report mein usable reference range "
            "nahi thi, isliye is value ko classify nahi kiya ja saka."
        ),
        "generic_test_description": "Yeh measurement laboratory testing mein report kiya jata hai.",
        "unknown_limitation": (
            "{n} values classify nahi ho sakin kyunki report mein usable reference range nahi thi. "
            "Unke liye koi range assume nahi ki gayi."
        ),
        "value_line": "Aapki report mein {name} {value} {unit} hai (reference range: {range}).",
        "question_generic_1": (
            "Apne doctor se poochein ki aapke symptoms, history aur examination ke saath in "
            "results ka kya matlab hai."
        ),
        "question_generic_2": (
            "Poochein ki kya in values ko dobara test karna ya time ke saath monitor karna chahiye."
        ),
        "question_generic_3": (
            "Poochein ki in results ke liye rozmarra ke kaunse factors matter kar sakte hain."
        ),
    },
    "bn": {
        "summary_intro": "এই রিপোর্টে {n}টি মাপ রয়েছে।",
        "summary_counts": (
            "{within}টি মান রিপোর্টে দেওয়া রেফারেন্স সীমার মধ্যে আছে, {below}টি তার কম, {above}টি "
            "তার বেশি, এবং {unknown}টি শ্রেণীবদ্ধ করা যায়নি কারণ ব্যবহারযোগ্য রেফারেন্স সীমা পাওয়া যায়নি।"
        ),
        "summary_abnormal": "আপনার রিপোর্টে দেওয়া রেফারেন্স সীমার বাইরের মান: {list}।",
        "summary_caveat": (
            "এটি একটি শিক্ষামূলক ব্যাখ্যা, রোগনির্ণয় নয়। অনুগ্রহ করে এই ফলাফলগুলি নিয়ে একজন "
            "যোগ্য চিকিৎসকের সঙ্গে আলোচনা করুন।"
        ),
        "range_below": "আপনার রিপোর্টে দেওয়া মানটি পরীক্ষাগারের ছাপা রেফারেন্স সীমার চেয়ে কম।",
        "range_above": "আপনার রিপোর্টে দেওয়া মানটি পরীক্ষাগারের ছাপা রেফারেন্স সীমার চেয়ে বেশি।",
        "factors": (
            "পরীক্ষার পদ্ধতি, নমুনা সংগ্রহের ধরন এবং স্বাভাবিক জৈবিক তারতম্যসহ অনেক কারণ একটি "
            "একক পরীক্ষার ফলাফলকে প্রভাবিত করতে পারে।"
        ),
        "low_conf_note": "কিছু তথ্য নিশ্চিতভাবে বের করা যায়নি। অনুগ্রহ করে মূল রিপোর্টের সঙ্গে মানটি মিলিয়ে দেখুন।",
        "question": (
            "আপনার লক্ষণ, ইতিহাস ও পরীক্ষার প্রেক্ষাপটে আপনার {test} ফলাফলের অর্থ কী, "
            "তা আপনার চিকিৎসককে জিজ্ঞাসা করুন।"
        ),
        "no_evidence": "কিছু বিষয়ের জন্য জ্ঞানভাণ্ডারে যথেষ্ট সহায়ক তথ্য পাওয়া যায়নি, তাই সেগুলির ব্যাখ্যা সাধারণ।",
        "limitations": [
            "একটি একক পরীক্ষার ফলাফল নিজে থেকে কোনো রোগ নিশ্চিত বা বাতিল করতে পারে না।",
            "রেফারেন্স সীমা নির্দিষ্ট পরীক্ষাগার ও পদ্ধতির উপর নির্ভর করে; আপনার রিপোর্টে ছাপা সীমাই প্রাধান্য পায়।",
            "পরীক্ষার ফলাফল সর্বদা আপনার লক্ষণ, ইতিহাস ও পরীক্ষার সঙ্গে মিলিয়ে বোঝা উচিত।",
            "এটি রোগনির্ণয় নয় এবং যোগ্য চিকিৎসকের পরামর্শের বিকল্প নয়।",
        ],
        "chat_no_report": (
            "এই কথোপকথনে কোনো রিপোর্ট সংযুক্ত নেই, তাই আমি শুধু সাধারণ শিক্ষামূলক তথ্য দিতে পারি। "
            "রিপোর্ট খুলে আবার জিজ্ঞাসা করুন যাতে উত্তর আপনার মানের উপর ভিত্তি করে হয়।"
        ),
        "chat_out_of_range_none": "এই কথোপকথনের সঙ্গে যুক্ত রিপোর্ট অনুযায়ী, কোনো মান রেফারেন্স সীমার বাইরে নেই।",
        "chat_out_of_range_header": "আপনার রিপোর্ট অনুযায়ী, এই মানগুলি ছাপা রেফারেন্স সীমার বাইরে:",
        "chat_out_of_range_footer": (
            "অনুগ্রহ করে এগুলি আপনার চিকিৎসকের সঙ্গে দেখুন, যিনি আপনার লক্ষণ ও ইতিহাসের সঙ্গে "
            "এগুলির ব্যাখ্যা দিতে পারবেন।"
        ),
        "chat_meaning_header": "সহজ ভাষায়:",
        "chat_no_kb": (
            "এই প্রশ্নের নির্ভরযোগ্য উত্তর দিতে জ্ঞানভাণ্ডারে যথেষ্ট প্রাসঙ্গিক তথ্য পাওয়া যায়নি। "
            "অনুগ্রহ করে আপনার চিকিৎসককে জিজ্ঞাসা করার কথা ভাবুন।"
        ),
        "chat_footer": (
            "এটি সাধারণ শিক্ষামূলক তথ্য, চিকিৎসা নির্ণয় নয়। ব্যক্তিগত পরামর্শের জন্য যোগ্য "
            "চিকিৎসকের সঙ্গে দেখা করুন।"
        ),
        "status_within": (
            "আপনার রিপোর্টে {name} {value} {unit} (রেফারেন্স সীমা: {range})। "
            "এটি পরীক্ষাগারের ছাপা সীমার মধ্যে আছে।"
        ),
        "status_below": (
            "আপনার রিপোর্টে {name} {value} {unit} (রেফারেন্স সীমা: {range})। "
            "এটি ছাপা সীমার চেয়ে কম।"
        ),
        "status_above": (
            "আপনার রিপোর্টে {name} {value} {unit} (রেফারেন্স সীমা: {range})। "
            "এটি ছাপা সীমার চেয়ে বেশি।"
        ),
        "status_unknown": (
            "আপনার রিপোর্টে {name} {value} {unit}। ব্যবহারযোগ্য রেফারেন্স সীমা না থাকায় এই মানটি "
            "শ্রেণীবদ্ধ করা যায়নি।"
        ),
        "generic_test_description": "এই মাপটি পরীক্ষাগার পরীক্ষায় রিপোর্ট করা হয়।",
        "unknown_limitation": (
            "{n}টি মান শ্রেণীবদ্ধ করা যায়নি কারণ রিপোর্টে ব্যবহারযোগ্য রেফারেন্স সীমা দেওয়া ছিল না। "
            "সেগুলির জন্য কোনো সীমা ধরে নেওয়া হয়নি।"
        ),
        "value_line": "আপনার রিপোর্টে {name} {value} {unit} (রেফারেন্স সীমা: {range})।",
        "question_generic_1": (
            "আপনার লক্ষণ, ইতিহাস ও পরীক্ষার সঙ্গে এই ফলাফলগুলির অর্থ কী, চিকিৎসককে জিজ্ঞাসা করুন।"
        ),
        "question_generic_2": (
            "জিজ্ঞাসা করুন এই মানগুলির কোনওটি আবার পরীক্ষা করা বা সময়ের সঙ্গে মনিটর করা দরকার কি না।"
        ),
        "question_generic_3": (
            "জিজ্ঞাসা করুন এই ফলাফলগুলির জন্য দৈনন্দিন কোন কারণগুলি প্রাসঙ্গিক হতে পারে।"
        ),
    },
    "ta": {
        "summary_intro": "இந்த அறிக்கையில் {n} அளவீடுகள் உள்ளன.",
        "summary_counts": (
            "{within} மதிப்புகள் அறிக்கையில் கொடுக்கப்பட்டுள்ள குறிப்பு வரம்பிற்குள் உள்ளன, {below} "
            "அதைவிட குறைவு, {above} அதைவிட அதிகம், {unknown} பயன்படும் குறிப்பு வரம்பு இல்லாததால் "
            "வகைப்படுத்தப்படவில்லை."
        ),
        "summary_abnormal": "உங்கள் அறிக்கையில் கொடுக்கப்பட்ட குறிப்பு வரம்பிற்கு வெளியே உள்ள மதிப்புகள்: {list}.",
        "summary_caveat": (
            "இது ஒரு கல்வி நோக்கிலான விளக்கம், நோய் கண்டறிதல் அல்ல. இந்த முடிவுகளை தகுதியான "
            "மருத்துவரிடம் கலந்தாலோசிக்கவும்."
        ),
        "range_below": "உங்கள் அறிக்கையில் உள்ள மதிப்பு ஆய்வகம் அச்சிட்ட குறிப்பு வரம்பை விட குறைவாக உள்ளது.",
        "range_above": "உங்கள் அறிக்கையில் உள்ள மதிப்பு ஆய்வகம் அச்சிட்ட குறிப்பு வரம்பை விட அதிகமாக உள்ளது.",
        "factors": (
            "சோதனை முறை, மாதிரி சேகரிக்கப்பட்ட விதம், இயற்கையான உயிரியல் மாறுபாடு போன்ற பல "
            "காரணிகள் ஒரு தனி ஆய்வக முடிவை பாதிக்கலாம்."
        ),
        "low_conf_note": "சில தகவல்களை உறுதியாக எடுக்க முடியவில்லை. மதிப்பை உங்கள் அசல் அறிக்கையுடன் சரிபார்க்கவும்.",
        "question": (
            "உங்கள் அறிகுறிகள், வரலாறு மற்றும் பரிசோதனையின் பின்னணியில் உங்கள் {test} முடிவின் "
            "பொருள் என்ன என்று மருத்துவரிடம் கேளுங்கள்."
        ),
        "no_evidence": "சில தலைப்புகளுக்கு அறிவுத் தளத்தில் போதுமான துணை தகவல் கிடைக்கவில்லை, எனவே அந்த விளக்கங்கள் பொதுவானவை.",
        "limitations": [
            "ஒரு தனி ஆய்வக முடிவு எந்த மருத்துவ நிலையையும் தானாக உறுதிப்படுத்தவோ நீக்கவோ முடியாது.",
            "குறிப்பு வரம்புகள் அவற்றை உருவாக்கிய ஆய்வகம் மற்றும் முறையைப் பொறுத்தவை; அறிக்கையில் அச்சிட்ட வரம்பே முதன்மையானது.",
            "ஆய்வக முடிவுகளை எப்போதும் அறிகுறிகள், வரலாறு மற்றும் பரிசோதனையுடன் சேர்த்து புரிந்து கொள்ள வேண்டும்.",
            "இது நோய் கண்டறிதல் அல்ல; தகுதியான மருத்துவரின் ஆலோசனைக்கு மாற்றல்ல.",
        ],
        "chat_no_report": (
            "இந்த உரையாடலுடன் அறிக்கை இணைக்கப்படவில்லை, எனவே பொதுவான கல்வித் தகவலை மட்டுமே தர முடியும். "
            "அறிக்கையைத் திறந்து மீண்டும் கேளுங்கள்."
        ),
        "chat_out_of_range_none": "இந்த உரையாடலுடன் இணைந்த அறிக்கையின்படி, எந்த மதிப்பும் குறிப்பு வரம்பிற்கு வெளியே இல்லை.",
        "chat_out_of_range_header": "உங்கள் அறிக்கையின்படி, இந்த மதிப்புகள் அச்சிட்ட குறிப்பு வரம்பிற்கு வெளியே உள்ளன:",
        "chat_out_of_range_footer": (
            "இவற்றை உங்கள் மருத்துவரிடம் காட்டவும்; அறிகுறிகள் மற்றும் வரலாற்றுடன் அவற்றை விளக்க முடியும்."
        ),
        "chat_meaning_header": "எளிய மொழியில்:",
        "chat_no_kb": (
            "இந்தக் கேள்விக்கு நம்பகமான பதில் அளிக்க அறிவுத் தளத்தில் போதுமான தகவல் கிடைக்கவில்லை. "
            "உங்கள் மருத்துவரிடம் கேட்கவும்."
        ),
        "chat_footer": (
            "இது பொதுவான கல்வித் தகவல், மருத்துவ நோய் கண்டறிதல் அல்ல. தனிப்பட்ட ஆலோசனைக்கு "
            "தகுதியான மருத்துவரை அணுகவும்."
        ),
        "status_within": (
            "உங்கள் அறிக்கையில் {name} {value} {unit} (குறிப்பு வரம்பு: {range}). "
            "இது ஆய்வகம் அச்சிட்ட வரம்பிற்குள் உள்ளது."
        ),
        "status_below": (
            "உங்கள் அறிக்கையில் {name} {value} {unit} (குறிப்பு வரம்பு: {range}). "
            "இது அச்சிட்ட வரம்பை விட குறைவு."
        ),
        "status_above": (
            "உங்கள் அறிக்கையில் {name} {value} {unit} (குறிப்பு வரம்பு: {range}). "
            "இது அச்சிட்ட வரம்பை விட அதிகம்."
        ),
        "status_unknown": (
            "உங்கள் அறிக்கையில் {name} {value} {unit}. பயன்படும் குறிப்பு வரம்பு இல்லாததால் "
            "இந்த மதிப்பு வகைப்படுத்தப்படவில்லை."
        ),
        "generic_test_description": "இந்த அளவீடு ஆய்வக பரிசோதனையில் தெரிவிக்கப்படுகிறது.",
        "unknown_limitation": (
            "{n} மதிப்புகள் வகைப்படுத்தப்படவில்லை, ஏனெனில் பயன்படும் குறிப்பு வரம்பு அறிக்கையில் "
            "இல்லை. அவற்றுக்கு எந்த வரம்பும் ஊகிக்கப்படவில்லை."
        ),
        "value_line": "உங்கள் அறிக்கையில் {name} {value} {unit} (குறிப்பு வரம்பு: {range}).",
        "question_generic_1": (
            "அறிகுறிகள், வரலாறு மற்றும் பரிசோதனையுடன் இந்த முடிவுகளின் பொருள் என்ன என்று "
            "மருத்துவரிடம் கேளுங்கள்."
        ),
        "question_generic_2": (
            "இந்த மதிப்புகளில் ஏதேனும் மீண்டும் சோதிக்க வேண்டுமா அல்லது காலப்போக்கில் கண்காணிக்க "
            "வேண்டுமா என்று கேளுங்கள்."
        ),
        "question_generic_3": (
            "இந்த முடிவுகளுக்கு அன்றாட வாழ்வின் எந்தக் காரணிகள் பொருத்தமானவை என்று கேளுங்கள்."
        ),
    },
    "te": {
        "summary_intro": "ఈ నివేదికలో {n} కొలతలు ఉన్నాయి.",
        "summary_counts": (
            "{within} విలువలు నివేదికలో ఇచ్చిన సూచన పరిధిలో ఉన్నాయి, {below} దానికంటే తక్కువ, "
            "{above} దానికంటే ఎక్కువ, {unknown} ఉపయోగపడే సూచన పరిధి లేనందున వర్గీకరించబడలేదు."
        ),
        "summary_abnormal": "మీ నివేదికలో ఇచ్చిన సూచన పరిధి వెలుపల ఉన్న విలువలు: {list}.",
        "summary_caveat": (
            "ఇది విద్యా వివరణ మాత్రమే, నిర్ధారణ కాదు. ఈ ఫలితాలను అర్హత కలిగిన వైద్యుడితో చర్చించండి."
        ),
        "range_below": "మీ నివేదికలోని విలువ ప్రయోగశాల ముద్రించిన సూచన పరిధి కంటే తక్కువగా ఉంది.",
        "range_above": "మీ నివేదికలోని విలువ ప్రయోగశాల ముద్రించిన సూచన పరిధి కంటే ఎక్కువగా ఉంది.",
        "factors": (
            "పరీక్ష పద్ధతి, నమూనా సేకరణ విధానం, సాధారణ జీవ సహజ మార్పులు వంటి అనేక అంశాలు "
            "ఒకే ఒక ప్రయోగశాల ఫలితాన్ని ప్రభావితం చేయవచ్చు."
        ),
        "low_conf_note": "కొంత సమాచారం నమ్మకంగా సేకరించలేకపోయాము. విలువను మీ అసలు నివేదికతో సరిపోల్చండి.",
        "question": (
            "మీ లక్షణాలు, చరిత్ర, పరీక్షల నేపథ్యంలో మీ {test} ఫలితం అర్థం ఏమిటో "
            "మీ వైద్యుడిని అడగండి."
        ),
        "no_evidence": "కొన్ని అంశాలకు జ్ఞాన నిధిలో సరిపడా సహాయక సమాచారం దొరకలేదు, కాబట్టి ఆ వివరణలు సాధారణమైనవి మాత్రమే.",
        "limitations": [
            "ఒకే ప్రయోగశాల ఫలితం ఏ వైద్య పరిస్థితిని స్వయంగా నిర్ధారించలేదు లేదా తోసిపుచ్చలేదు.",
            "సూచన పరిధులు వాటిని రూపొందించిన ప్రయోగశాల, పద్ధతిని బట్టి ఉంటాయి; మీ నివేదికలో ముద్రించిన పరిధికే ప్రాధాన్యం.",
            "ప్రయోగశాల ఫలితాలను ఎప్పుడూ మీ లక్షణాలు, చరిత్ర, పరీక్షలతో కలిపి అర్థం చేసుకోవాలి.",
            "ఇది నిర్ధారణ కాదు; అర్హత కలిగిన వైద్యుని సలహాకు ప్రత్యామ్నాయం కాదు.",
        ],
        "chat_no_report": (
            "ఈ సంభాషణకు నివేదిక జోడించబడలేదు, కాబట్టి సాధారణ విద్యా సమాచారం మాత్రమే ఇవ్వగలను. "
            "నివేదిక తెరిచి మళ్లీ అడగండి."
        ),
        "chat_out_of_range_none": "ఈ సంభాషణకు జోడించిన నివేదిక ప్రకారం, ఏ విలువా సూచన పరిధి వెలుపల లేదు.",
        "chat_out_of_range_header": "మీ నివేదిక ప్రకారం, ఈ విలువలు ముద్రించిన సూచన పరిధి వెలుపల ఉన్నాయి:",
        "chat_out_of_range_footer": (
            "వీటిని మీ వైద్యుడికి చూపండి; మీ లక్షణాలు, చరిత్రతో వాటిని వివరించగలరు."
        ),
        "chat_meaning_header": "సరళ భాషలో:",
        "chat_no_kb": (
            "ఈ ప్రశ్నకు నమ్మదగిన సమాధానం ఇవ్వడానికి జ్ఞాన నిధిలో సరిపడా సమాచారం దొరకలేదు. "
            "దయచేసి మీ వైద్యుడిని అడగండి."
        ),
        "chat_footer": (
            "ఇది సాధారణ విద్యా సమాచారం, వైద్య నిర్ధారణ కాదు. వ్యక్తిగత సలహా కోసం అర్హత కలిగిన "
            "వైద్యుడిని సంప్రదించండి."
        ),
        "status_within": (
            "మీ నివేదికలో {name} {value} {unit} (సూచన పరిధి: {range}). "
            "ఇది ప్రయోగశాల ముద్రించిన పరిధిలో ఉంది."
        ),
        "status_below": (
            "మీ నివేదికలో {name} {value} {unit} (సూచన పరిధి: {range}). "
            "ఇది ముద్రించిన పరిధి కంటే తక్కువ."
        ),
        "status_above": (
            "మీ నివేదికలో {name} {value} {unit} (సూచన పరిధి: {range}). "
            "ఇది ముద్రించిన పరిధి కంటే ఎక్కువ."
        ),
        "status_unknown": (
            "మీ నివేదికలో {name} {value} {unit}. ఉపయోగపడే సూచన పరిధి లేనందున ఈ విలువ "
            "వర్గీకరించబడలేదు."
        ),
        "generic_test_description": "ఈ కొలత ప్రయోగశాల పరీక్షలో నివేదించబడుతుంది.",
        "unknown_limitation": (
            "{n} విలువలు వర్గీకరించబడలేదు, ఎందుకంటే నివేదికలో ఉపయోగపడే సూచన పరిధి లేదు. "
            "వాటికి ఏ పరిధి ఊహించబడలేదు."
        ),
        "value_line": "మీ నివేదికలో {name} {value} {unit} (సూచన పరిధి: {range}).",
        "question_generic_1": (
            "మీ లక్షణాలు, చరిత్ర, పరీక్షలతో ఈ ఫలితాల అర్థం ఏమిటో వైద్యుడిని అడగండి."
        ),
        "question_generic_2": (
            "ఈ విలువల్లో ఏవైనా మళ్లీ పరీక్షించాలా లేదా కాలక్రమేణా పర్యవేక్షించాలా అడగండి."
        ),
        "question_generic_3": (
            "ఈ ఫలితాలకు రోజువారీ జీవితంలోని ఏ అంశాలు సంబంధం కలిగి ఉండవచ్చో అడగండి."
        ),
    },
    "mr": {
        "summary_intro": "या अहवालात {n} मोजमापे आहेत.",
        "summary_counts": (
            "{within} मूल्ये अहवालात दिलेल्या संदर्भ श्रेणीत आहेत, {below} तिच्यापेक्षा कमी आहेत, "
            "{above} तिच्यापेक्षा जास्त आहेत, आणि {unknown} यांना वर्गीकृत करता आले नाही कारण "
            "उपयुक्त संदर्भ श्रेणी मिळाली नाही."
        ),
        "summary_abnormal": "तुमच्या अहवालात दिलेल्या संदर्भ श्रेणीबाहेरची मूल्ये: {list}.",
        "summary_caveat": (
            "ही एक शैक्षणिक व्याख्या आहे, निदान नाही. कृपया या निकालांबद्दल पात्र आरोग्य "
            "व्यावसायिकांशी चर्चा करा."
        ),
        "range_below": "तुमच्या अहवालातील मूल्य प्रयोगशाळेने छापलेल्या संदर्भ श्रेणीपेक्षा कमी आहे.",
        "range_above": "तुमच्या अहवालातील मूल्य प्रयोगशाळेने छापलेल्या संदर्भ श्रेणीपेक्षा जास्त आहे.",
        "factors": (
            "अनेक घटक — वापरलेली चाचणी पद्धत, नमुना कसा घेतला गेला आणि सामान्य जैविक फरक — "
            "एका प्रयोगशाळा निकालावर परिणाम करू शकतात."
        ),
        "low_conf_note": (
            "काही माहिती आत्मविश्वासाने काढता आली नाही. कृपया मूल्य तुमच्या मूळ अहवालाशी तपासा."
        ),
        "question": (
            "तुमची लक्षणे, इतिहास आणि तपासणी यांच्या संदर्भात तुमच्या {test} निकालाचा अर्थ काय "
            "आहे, हे तुमच्या डॉक्टरांना विचारा."
        ),
        "no_evidence": (
            "काही विषयांसाठी ज्ञानकोशात पुरेशी आधारभूत माहिती मिळाली नाही, त्यामुळे त्या व्याख्या "
            "केवळ सामान्य आहेत."
        ),
        "limitations": [
            "कोणताही एकच प्रयोगशाळा निकाल स्वतःहून कोणत्याही वैद्यकीय स्थितीची पुष्टी किंवा नकार करू शकत नाही.",
            "संदर्भ श्रेणी ज्या प्रयोगशाळेने आणि पद्धतीने तयार केल्या त्यांना विशिष्ट असतात; तुमच्या अहवालावर छापलेली श्रेणी सर्वोच्च आहे.",
            "प्रयोगशाळा निकालांची व्याख्या नेहमी तुमची लक्षणे, वैद्यकीय इतिहास आणि तपासणी यांसह करावी.",
            "ही व्याख्या शैक्षणिक उद्देशासाठी तुमच्या अहवालातील मूल्यांवरून आणि सामान्य आरोग्य माहितीवरून तयार केली आहे.",
            "हे निदान नाही आणि पात्र आरोग्य व्यावसायिकांच्या सल्ल्याचा पर्याय नाही.",
            "येथे दर्शविलेली मूल्ये तुमच्या अहवालानुसार आहेत; एखादे मूल्य वेगळे वाटल्यास मूळ प्रतीशी तपासा.",
        ],
        "chat_no_report": (
            "या संभाषणाशी कोणताही अहवाल जोडलेला नाही, त्यामुळे मी केवळ सामान्य शैक्षणिक माहिती "
            "देऊ शकतो. अहवाल उघडून पुन्हा विचारा जेणेकरून उत्तरे तुमच्या मूल्यांवर आधारित असतील."
        ),
        "chat_out_of_range_none": (
            "या संभाषणाशी जोडलेल्या अहवालानुसार, कोणतेही मूल्य अहवालात दर्शविलेल्या संदर्भ "
            "श्रेणींबाहेर नाही."
        ),
        "chat_out_of_range_header": "तुमच्या अहवालानुसार, ही मूल्ये अहवालावर छापलेल्या श्रेणीबाहेर आहेत:",
        "chat_out_of_range_footer": (
            "कृपया ही मूल्ये तुमच्या आरोग्य व्यावसायिकांसोबत तपासा, जे तुमची लक्षणे आणि इतिहास "
            "यांच्या आधारे त्यांचा अर्थ सांगू शकतात."
        ),
        "chat_meaning_header": "सोप्या भाषेत:",
        "chat_no_kb": (
            "त्या उत्तरासाठी ज्ञानकोशात पुरेशी संबंधित माहिती मला मिळाली नाही. कृपया तुमच्या "
            "आरोग्य व्यावसायिकांना विचारण्याचा विचार करा."
        ),
        "chat_footer": (
            "ही सामान्य शैक्षणिक माहिती आहे, वैद्यकीय निदान नाही. वैयक्तिक वैद्यकीय सल्ल्यासाठी "
            "पात्र आरोग्य व्यावसायिकांचा सल्ला घ्या."
        ),
        "status_within": (
            "तुमच्या अहवालात {name} {value} {unit} आहे (संदर्भ श्रेणी: {range}). "
            "हे प्रयोगशाळेने छापलेल्या श्रेणीत आहे."
        ),
        "status_below": (
            "तुमच्या अहवालात {name} {value} {unit} आहे (संदर्भ श्रेणी: {range}). "
            "हे छापलेल्या श्रेणीपेक्षा कमी आहे."
        ),
        "status_above": (
            "तुमच्या अहवालात {name} {value} {unit} आहे (संदर्भ श्रेणी: {range}). "
            "हे छापलेल्या श्रेणीपेक्षा जास्त आहे."
        ),
        "status_unknown": (
            "तुमच्या अहवालात {name} {value} {unit} आहे. अहवालात उपयुक्त संदर्भ श्रेणी नव्हती, "
            "त्यामुळे हे मूल्य वर्गीकृत करता आले नाही."
        ),
        "generic_test_description": "हे मोजमाप प्रयोगशाळा चाचणीत नोंदवले जाते.",
        "unknown_limitation": (
            "{n} मूल्ये वर्गीकृत करता आली नाहीत कारण अहवालात उपयुक्त संदर्भ श्रेणी नव्हती. "
            "त्यांच्यासाठी कोणतीही श्रेणी गृहीत धरलेली नाही."
        ),
        "value_line": "तुमच्या अहवालात {name} {value} {unit} आहे (संदर्भ श्रेणी: {range}).",
        "question_generic_1": (
            "तुमची लक्षणे, इतिहास आणि तपासणी यांच्यासोबत या निकालांचा अर्थ काय आहे, हे तुमच्या "
            "डॉक्टरांना विचारा."
        ),
        "question_generic_2": (
            "यापैकी कोणतीही मूल्ये पुन्हा तपासावी लागतील का किंवा कालांतराने निरीक्षण करावे लागेल "
            "का, हे विचारा."
        ),
        "question_generic_3": "या निकालांसाठी दैनंदिन जीवनातील कोणते घटक संबंधित असू शकतात, हे विचारा.",
    },
    "gu": {
        "summary_intro": "આ રિપોર્ટમાં {n} માપનો છે.",
        "summary_counts": (
            "{within} મૂલ્યો રિપોર્ટમાં દર્શાવેલ સંદર્ભ શ્રેણીમાં છે, {below} તેનાથી નીચે છે, "
            "{above} તેનાથી ઉપર છે, અને {unknown} ને વર્ગીકૃત કરી શકાયા નહીં કારણ કે કોઈ "
            "ઉપયોગી સંદર્ભ શ્રેણી મળી નહીં."
        ),
        "summary_abnormal": "તમારા રિપોર્ટમાં દર્શાવેલ સંદર્ભ શ્રેણીની બહારના મૂલ્યો: {list}.",
        "summary_caveat": (
            "આ શૈક્ષણિક સમજૂતી છે, નિદાન નહીં. કૃપા કરીને આ પરિણામો વિશે લાયક આરોગ્ય "
            "સંભાળ વ્યાવસાયિક સાથે ચર્ચા કરો."
        ),
        "range_below": "તમારા રિપોર્ટમાં દર્શાવેલ મૂલ્ય પ્રયોગશાળા દ્વારા છપાયેલી સંદર્ભ શ્રેણીથી નીચે છે.",
        "range_above": "તમારા રિપોર્ટમાં દર્શાવેલ મૂલ્ય પ્રયોગશાળા દ્વારા છપાયેલી સંદર્ભ શ્રેણીથી ઉપર છે.",
        "factors": (
            "ઘણા પરિબળો — વપરાયેલ પરીક્ષણ પદ્ધતિ, નમૂનો કેવી રીતે લેવાયો અને સામાન્ય જૈવિક "
            "ભિન્નતા — એક પ્રયોગશાળા પરિણામને પ્રભાવિત કરી શકે છે."
        ),
        "low_conf_note": (
            "કેટલીક માહિતી વિશ્વાસપૂર્વક કાઢી શકાઈ નહીં. કૃપા કરીને મૂલ્યને તમારા મૂળ "
            "રિપોર્ટ સાથે ચકાસો."
        ),
        "question": (
            "તમારા લક્ષણો, ઇતિહાસ અને તપાસના સંદર્ભમાં તમારા {test} પરિણામનો અર્થ શું છે તે "
            "તમારા ડૉક્ટરને પૂછો."
        ),
        "no_evidence": (
            "કેટલાક વિષયો માટે જ્ઞાનકોશમાં પૂરતી સહાયક માહિતી મળી નહીં, તેથી તે સમજૂતીઓ "
            "ફક્ત સામાન્ય છે."
        ),
        "limitations": [
            "કોઈપણ એક પ્રયોગશાળા પરિણામ એકલું કોઈપણ તબીબી સ્થિતિની પુષ્ટિ કે નકાર કરી શકતું નથી.",
            "સંદર્ભ શ્રેણીઓ જે પ્રયોગશાળા અને પદ્ધતિથી બની તે મુજબ વિશિષ્ટ હોય છે; તમારા રિપોર્ટ પર છપાયેલી શ્રેણી સર્વોપરી છે.",
            "પ્રયોગશાળા પરિણામોની સમજૂતી હંમેશા તમારા લક્ષણો, તબીબી ઇતિહાસ અને તપાસ સાથે મળીને કરવી જોઈએ.",
            "આ સમજૂતી શૈક્ષણિક હેતુ માટે તમારા રિપોર્ટના મૂલ્યો અને સામાન્ય આરોગ્ય માહિતી પરથી બનાવવામાં આવી છે.",
            "આ નિદાન નથી અને લાયક આરોગ્ય સંભાળ વ્યાવસાયિકની સલાહનો વિકલ્પ નથી.",
            "અહીં દર્શાવેલા મૂલ્યો તમારા રિપોર્ટ મુજબ છે; જો કોઈ મૂલ્ય અલગ લાગે તો કૃપા કરીને મૂળ નકલ સાથે ચકાસો.",
        ],
        "chat_no_report": (
            "આ વાતચીત સાથે કોઈ રિપોર્ટ જોડાયેલો નથી, તેથી હું ફક્ત સામાન્ય શૈક્ષણિક માહિતી "
            "આપી શકું છું. રિપોર્ટ ખોલો અને ફરીથી પૂછો જેથી જવાબો તમારા મૂલ્યો પર આધારિત હોય."
        ),
        "chat_out_of_range_none": (
            "આ વાતચીત સાથે જોડાયેલા રિપોર્ટ મુજબ, કોઈપણ મૂલ્ય રિપોર્ટમાં દર્શાવેલ સંદર્ભ "
            "શ્રેણીની બહાર નથી."
        ),
        "chat_out_of_range_header": "તમારા રિપોર્ટ મુજબ, આ મૂલ્યો રિપોર્ટ પર છપાયેલી શ્રેણીની બહાર છે:",
        "chat_out_of_range_footer": (
            "કૃપા કરીને આ મૂલ્યો તમારા આરોગ્ય સંભાળ વ્યાવસાયિક સાથે સમીક્ષા કરો, જે તમારા "
            "લક્ષણો અને ઇતિહાસ સાથે તેમનું અર્થઘટન કરી શકે."
        ),
        "chat_meaning_header": "સહેલી ભાષામાં:",
        "chat_no_kb": (
            "તે જવાબ માટે જ્ઞાનકોશમાં પૂરતી સંબંધિત માહિતી મળી નહીં. કૃપા કરીને તમારા આરોગ્ય "
            "સંભાળ વ્યાવસાયિકને પૂછવાનું વિચારો."
        ),
        "chat_footer": (
            "આ સામાન્ય શૈક્ષણિક માહિતી છે, તબીબી નિદાન નથી. વ્યક્તિગત તબીબી સલાહ માટે લાયક "
            "આરોગ્ય સંભાળ વ્યાવસાયિકની સલાહ લો."
        ),
        "status_within": (
            "તમારા રિપોર્ટમાં {name} {value} {unit} છે (સંદર્ભ શ્રેણી: {range}). "
            "આ પ્રયોગશાળા દ્વારા છપાયેલી શ્રેણીમાં છે."
        ),
        "status_below": (
            "તમારા રિપોર્ટમાં {name} {value} {unit} છે (સંદર્ભ શ્રેણી: {range}). "
            "આ છપાયેલી શ્રેણીથી નીચે છે."
        ),
        "status_above": (
            "તમારા રિપોર્ટમાં {name} {value} {unit} છે (સંદર્ભ શ્રેણી: {range}). "
            "આ છપાયેલી શ્રેણીથી ઉપર છે."
        ),
        "status_unknown": (
            "તમારા રિપોર્ટમાં {name} {value} {unit} છે. રિપોર્ટમાં ઉપયોગી સંદર્ભ શ્રેણી ન હતી, "
            "તેથી આ મૂલ્યને વર્ગીકૃત કરી શકાયું નહીં."
        ),
        "generic_test_description": "આ માપન પ્રયોગશાળા પરીક્ષણમાં નોંધાય છે.",
        "unknown_limitation": (
            "{n} મૂલ્યોને વર્ગીકૃત કરી શકાયા નહીં કારણ કે રિપોર્ટમાં ઉપયોગી સંદર્ભ શ્રેણી ન હતી. "
            "તેમના માટે કોઈ શ્રેણી ધારવામાં આવી નથી."
        ),
        "value_line": "તમારા રિપોર્ટમાં {name} {value} {unit} છે (સંદર્ભ શ્રેણી: {range}).",
        "question_generic_1": (
            "તમારા લક્ષણો, ઇતિહાસ અને તપાસ સાથે આ પરિણામોનો અર્થ શું છે તે તમારા ડૉક્ટરને પૂછો."
        ),
        "question_generic_2": (
            "આમાંથી કોઈ મૂલ્યો ફરીથી પરીક્ષણ કરવાની કે સમય જતાં દેખરેખ રાખવાની જરૂર છે કે નહીં તે પૂછો."
        ),
        "question_generic_3": "આ પરિણામો માટે રોજિંદા જીવનના કયા પરિબળો સંબંધિત હોઈ શકે તે પૂછો.",
    },
    "kn": {
        "summary_intro": "ಈ ವರದಿಯಲ್ಲಿ {n} ಅಳತೆಗಳಿವೆ.",
        "summary_counts": (
            "{within} ಮೌಲ್ಯಗಳು ವರದಿಯಲ್ಲಿ ತೋರಿಸಿದ ಉಲ್ಲೇಖ ಶ್ರೇಣಿಯೊಳಗಿವೆ, {below} ಅದಕ್ಕಿಂತ ಕಡಿಮೆ ಇವೆ, "
            "{above} ಅದಕ್ಕಿಂತ ಹೆಚ್ಚು ಇವೆ, ಮತ್ತು ಉಪಯುಕ್ತ ಉಲ್ಲೇಖ ಶ್ರೇಣಿ ಸಿಗದ ಕಾರಣ {unknown} ಅನ್ನು "
            "ವರ್ಗೀಕರಿಸಲಾಗಲಿಲ್ಲ."
        ),
        "summary_abnormal": "ನಿಮ್ಮ ವರದಿಯಲ್ಲಿ ತೋರಿಸಿದ ಉಲ್ಲೇಖ ಶ್ರೇಣಿಯ ಹೊರಗಿನ ಮೌಲ್ಯಗಳು: {list}.",
        "summary_caveat": (
            "ಇದು ಶೈಕ್ಷಣಿಕ ವಿವರಣೆ, ರೋಗನಿರ್ಣಯವಲ್ಲ. ದಯವಿಟ್ಟು ಈ ಫಲಿತಾಂಶಗಳನ್ನು ಅರ್ಹ ಆರೋಗ್ಯ "
            "ವೃತ್ತಿಪರರೊಂದಿಗೆ ಚರ್ಚಿಸಿ."
        ),
        "range_below": "ನಿಮ್ಮ ವರದಿಯಲ್ಲಿ ನೀಡಿದ ಮೌಲ್ಯವು ಪ್ರಯೋಗಾಲಯ ಮುದ್ರಿಸಿದ ಉಲ್ಲೇಖ ಶ್ರೇಣಿಗಿಂತ ಕಡಿಮೆ ಇದೆ.",
        "range_above": "ನಿಮ್ಮ ವರದಿಯಲ್ಲಿ ನೀಡಿದ ಮೌಲ್ಯವು ಪ್ರಯೋಗಾಲಯ ಮುದ್ರಿಸಿದ ಉಲ್ಲೇಖ ಶ್ರೇಣಿಗಿಂತ ಹೆಚ್ಚು ಇದೆ.",
        "factors": (
            "ಹಲವು ಅಂಶಗಳು — ಬಳಸಿದ ಪರೀಕ್ಷಾ ವಿಧಾನ, ಮಾದರಿ ಸಂಗ್ರಹಿಸಿದ ರೀತಿ ಮತ್ತು ಸಾಮಾನ್ಯ ಜೈವಿಕ "
            "ವ್ಯತ್ಯಾಸ — ಒಂದೇ ಪ್ರಯೋಗಾಲಯ ಫಲಿತಾಂಶದ ಮೇಲೆ ಪರಿಣಾಮ ಬೀರಬಹುದು."
        ),
        "low_conf_note": (
            "ಕೆಲವು ಮಾಹಿತಿಯನ್ನು ವಿಶ್ವಾಸದಿಂದ ಹೊರತೆಗೆಯಲಾಗಲಿಲ್ಲ. ದಯವಿಟ್ಟು ಮೌಲ್ಯವನ್ನು ನಿಮ್ಮ ಮೂಲ "
            "ವರದಿಯೊಂದಿಗೆ ಪರಿಶೀಲಿಸಿ."
        ),
        "question": (
            "ನಿಮ್ಮ ಲಕ್ಷಣಗಳು, ಇತಿಹಾಸ ಮತ್ತು ಪರೀಕ್ಷೆಯ ಸಂದರ್ಭದಲ್ಲಿ ನಿಮ್ಮ {test} ಫಲಿತಾಂಶದ ಅರ್ಥವೇನು "
            "ಎಂದು ನಿಮ್ಮ ವೈದ್ಯರನ್ನು ಕೇಳಿ."
        ),
        "no_evidence": (
            "ಕೆಲವು ವಿಷಯಗಳಿಗೆ ಜ್ಞಾನಕೋಶದಲ್ಲಿ ಸಾಕಷ್ಟು ಬೆಂಬಲ ಮಾಹಿತಿ ಸಿಗಲಿಲ್ಲ, ಆದ್ದರಿಂದ ಆ ವಿವರಣೆಗಳು "
            "ಸಾಮಾನ್ಯವಾದವು ಮಾತ್ರ."
        ),
        "limitations": [
            "ಯಾವುದೇ ಒಂದೇ ಪ್ರಯೋಗಾಲಯ ಫಲಿತಾಂಶವು ತಾನೇ ಯಾವುದೇ ವೈದ್ಯಕೀಯ ಸ್ಥಿತಿಯನ್ನು ದೃಢೀಕರಿಸಲು ಅಥವಾ ನಿರಾಕರಿಸಲು ಸಾಧ್ಯವಿಲ್ಲ.",
            "ಉಲ್ಲೇಖ ಶ್ರೇಣಿಗಳು ಅವನ್ನು ತಯಾರಿಸಿದ ಪ್ರಯೋಗಾಲಯ ಮತ್ತು ವಿಧಾನಕ್ಕೆ ನಿರ್ದಿಷ್ಟವಾಗಿರುತ್ತವೆ; ನಿಮ್ಮ ವರದಿಯಲ್ಲಿ ಮುದ್ರಿತ ಶ್ರೇಣಿಯೇ ಪ್ರಮುಖ.",
            "ಪ್ರಯೋಗಾಲಯ ಫಲಿತಾಂಶಗಳ ವ್ಯಾಖ್ಯಾನವನ್ನು ಯಾವಾಗಲೂ ನಿಮ್ಮ ಲಕ್ಷಣಗಳು, ವೈದ್ಯಕೀಯ ಇತಿಹಾಸ ಮತ್ತು ಪರೀಕ್ಷೆಯೊಂದಿಗೆ ಮಾಡಬೇಕು.",
            "ಈ ವಿವರಣೆಯನ್ನು ಶೈಕ್ಷಣಿಕ ಉದ್ದೇಶಕ್ಕಾಗಿ ನಿಮ್ಮ ವರದಿಯ ಮೌಲ್ಯಗಳು ಮತ್ತು ಸಾಮಾನ್ಯ ಆರೋಗ್ಯ ಮಾಹಿತಿಯಿಂದ ಸಿದ್ಧಪಡಿಸಲಾಗಿದೆ.",
            "ಇದು ರೋಗನಿರ್ಣಯವಲ್ಲ ಮತ್ತು ಅರ್ಹ ಆರೋಗ್ಯ ವೃತ್ತಿಪರರ ಸಲಹೆಗೆ ಪರ್ಯಾಯವಲ್ಲ.",
            "ಇಲ್ಲಿ ತೋರಿಸಿದ ಮೌಲ್ಯಗಳು ನಿಮ್ಮ ವರದಿಗೆ ಅನುಗುಣವಾಗಿವೆ; ಯಾವುದೇ ಮೌಲ್ಯ ಭಿನ್ನವಾಗಿ ಕಂಡರೆ ಮೂಲ ಪ್ರತಿಯೊಂದಿಗೆ ಪರಿಶೀಲಿಸಿ.",
        ],
        "chat_no_report": (
            "ಈ ಸಂಭಾಷಣೆಗೆ ಯಾವುದೇ ವರದಿ ಲಗತ್ತಾಗಿಲ್ಲ, ಆದ್ದರಿಂದ ನಾನು ಸಾಮಾನ್ಯ ಶೈಕ್ಷಣಿಕ ಮಾಹಿತಿ ಮಾತ್ರ "
            "ನೀಡಬಲ್ಲೆ. ವರದಿ ತೆರೆದು ಮತ್ತೆ ಕೇಳಿ, ಆಗ ಉತ್ತರಗಳು ನಿಮ್ಮ ಮೌಲ್ಯಗಳ ಆಧಾರಿತವಾಗಿರುತ್ತವೆ."
        ),
        "chat_out_of_range_none": (
            "ಈ ಸಂಭಾಷಣೆಗೆ ಲಗತ್ತಿಸಿದ ವರದಿಯ ಪ್ರಕಾರ, ಯಾವುದೇ ಮೌಲ್ಯವು ವರದಿಯಲ್ಲಿ ತೋರಿಸಿದ ಉಲ್ಲೇಖ "
            "ಶ್ರೇಣಿಗಳ ಹೊರಗಿಲ್ಲ."
        ),
        "chat_out_of_range_header": "ನಿಮ್ಮ ವರದಿಯ ಪ್ರಕಾರ, ಈ ಮೌಲ್ಯಗಳು ವರದಿಯಲ್ಲಿ ಮುದ್ರಿತ ಶ್ರೇಣಿಯ ಹೊರಗಿವೆ:",
        "chat_out_of_range_footer": (
            "ದಯವಿಟ್ಟು ಈ ಮೌಲ್ಯಗಳನ್ನು ನಿಮ್ಮ ಆರೋಗ್ಯ ವೃತ್ತಿಪರರೊಂದಿಗೆ ಪರಿಶೀಲಿಸಿ; ಅವರು ನಿಮ್ಮ ಲಕ್ಷಣಗಳು "
            "ಮತ್ತು ಇತಿಹಾಸದೊಂದಿಗೆ ಅರ್ಥೈಸಬಲ್ಲರು."
        ),
        "chat_meaning_header": "ಸರಳ ಭಾಷೆಯಲ್ಲಿ:",
        "chat_no_kb": (
            "ಆ ಉತ್ತರಕ್ಕಾಗಿ ಜ್ಞಾನಕೋಶದಲ್ಲಿ ಸಾಕಷ್ಟು ಸಂಬಂಧಿತ ಮಾಹಿತಿ ಸಿಗಲಿಲ್ಲ. ದಯವಿಟ್ಟು ನಿಮ್ಮ ಆರೋಗ್ಯ "
            "ವೃತ್ತಿಪರರನ್ನು ಕೇಳುವುದನ್ನು ಪರಿಗಣಿಸಿ."
        ),
        "chat_footer": (
            "ಇದು ಸಾಮಾನ್ಯ ಶೈಕ್ಷಣಿಕ ಮಾಹಿತಿ, ವೈದ್ಯಕೀಯ ರೋಗನಿರ್ಣಯವಲ್ಲ. ವೈಯಕ್ತಿಕ ವೈದ್ಯಕೀಯ ಸಲಹೆಗಾಗಿ "
            "ಅರ್ಹ ಆರೋಗ್ಯ ವೃತ್ತಿಪರರನ್ನು ಸಂಪರ್ಕಿಸಿ."
        ),
        "status_within": (
            "ನಿಮ್ಮ ವರದಿಯಲ್ಲಿ {name} {value} {unit} ಇದೆ (ಉಲ್ಲೇಖ ಶ್ರೇಣಿ: {range}). "
            "ಇದು ಪ್ರಯೋಗಾಲಯ ಮುದ್ರಿಸಿದ ಶ್ರೇಣಿಯೊಳಗಿದೆ."
        ),
        "status_below": (
            "ನಿಮ್ಮ ವರದಿಯಲ್ಲಿ {name} {value} {unit} ಇದೆ (ಉಲ್ಲೇಖ ಶ್ರೇಣಿ: {range}). "
            "ಇದು ಮುದ್ರಿತ ಶ್ರೇಣಿಗಿಂತ ಕಡಿಮೆ ಇದೆ."
        ),
        "status_above": (
            "ನಿಮ್ಮ ವರದಿಯಲ್ಲಿ {name} {value} {unit} ಇದೆ (ಉಲ್ಲೇಖ ಶ್ರೇಣಿ: {range}). "
            "ಇದು ಮುದ್ರಿತ ಶ್ರೇಣಿಗಿಂತ ಹೆಚ್ಚು ಇದೆ."
        ),
        "status_unknown": (
            "ನಿಮ್ಮ ವರದಿಯಲ್ಲಿ {name} {value} {unit} ಇದೆ. ವರದಿಯಲ್ಲಿ ಉಪಯುಕ್ತ ಉಲ್ಲೇಖ ಶ್ರೇಣಿ ಇರಲಿಲ್ಲ, "
            "ಆದ್ದರಿಂದ ಈ ಮೌಲ್ಯವನ್ನು ವರ್ಗೀಕರಿಸಲಾಗಲಿಲ್ಲ."
        ),
        "generic_test_description": "ಈ ಅಳತೆಯನ್ನು ಪ್ರಯೋಗಾಲಯ ಪರೀಕ್ಷೆಯಲ್ಲಿ ವರದಿ ಮಾಡಲಾಗುತ್ತದೆ.",
        "unknown_limitation": (
            "{n} ಮೌಲ್ಯಗಳನ್ನು ವರ್ಗೀಕರಿಸಲಾಗಲಿಲ್ಲ ಏಕೆಂದರೆ ವರದಿಯಲ್ಲಿ ಉಪಯುಕ್ತ ಉಲ್ಲೇಖ ಶ್ರೇಣಿ ಇರಲಿಲ್ಲ. "
            "ಅವುಗಳಿಗೆ ಯಾವುದೇ ಶ್ರೇಣಿಯನ್ನು ಊಹಿಸಲಾಗಿಲ್ಲ."
        ),
        "value_line": "ನಿಮ್ಮ ವರದಿಯಲ್ಲಿ {name} {value} {unit} ಇದೆ (ಉಲ್ಲೇಖ ಶ್ರೇಣಿ: {range}).",
        "question_generic_1": (
            "ನಿಮ್ಮ ಲಕ್ಷಣಗಳು, ಇತಿಹಾಸ ಮತ್ತು ಪರೀಕ್ಷೆಯೊಂದಿಗೆ ಈ ಫಲಿತಾಂಶಗಳ ಅರ್ಥವೇನು ಎಂದು ನಿಮ್ಮ "
            "ವೈದ್ಯರನ್ನು ಕೇಳಿ."
        ),
        "question_generic_2": (
            "ಈ ಮೌಲ್ಯಗಳಲ್ಲಿ ಯಾವುದನ್ನಾದರೂ ಮತ್ತೆ ಪರೀಕ್ಷಿಸಬೇಕೇ ಅಥವಾ ಕಾಲಾನಂತರದಲ್ಲಿ ಮೇಲ್ವಿಚಾರಣೆ "
            "ಮಾಡಬೇಕೇ ಎಂದು ಕೇಳಿ."
        ),
        "question_generic_3": "ಈ ಫಲಿತಾಂಶಗಳಿಗೆ ದೈನಂದಿನ ಜೀವನದ ಯಾವ ಅಂಶಗಳು ಸಂಬಂಧಿಸಿರಬಹುದು ಎಂದು ಕೇಳಿ.",
    },
    "ml": {
        "summary_intro": "ഈ റിപ്പോർട്ടിൽ {n} അളവുകൾ ഉണ്ട്.",
        "summary_counts": (
            "{within} മൂല്യങ്ങൾ റിപ്പോർട്ടിൽ കാണിച്ചിരിക്കുന്ന റഫറൻസ് പരിധിക്കുള്ളിലാണ്, {below} "
            "അതിനേക്കാൾ കുറവാണ്, {above} അതിനേക്കാൾ കൂടുതലാണ്, ഉപയോഗപ്രദമായ റഫറൻസ് പരിധി "
            "ലഭിക്കാത്തതിനാൽ {unknown} വർഗ്ഗീകരിക്കാൻ കഴിഞ്ഞില്ല."
        ),
        "summary_abnormal": "നിങ്ങളുടെ റിപ്പോർട്ടിൽ കാണിച്ചിരിക്കുന്ന റഫറൻസ് പരിധിക്ക് പുറത്തുള്ള മൂല്യങ്ങൾ: {list}.",
        "summary_caveat": (
            "ഇത് വിദ്യാഭ്യാസപരമായ വിശദീകരണമാണ്, രോഗനിർണയമല്ല. ദയവായി ഈ ഫലങ്ങൾ യോഗ്യതയുള്ള "
            "ആരോഗ്യ വിദഗ്ധരുമായി ചർച്ച ചെയ്യുക."
        ),
        "range_below": "നിങ്ങളുടെ റിപ്പോർട്ടിൽ നൽകിയിരിക്കുന്ന മൂല്യം ലബോറട്ടറി അച്ചടിച്ച റഫറൻസ് പരിധിയേക്കാൾ കുറവാണ്.",
        "range_above": "നിങ്ങളുടെ റിപ്പോർട്ടിൽ നൽകിയിരിക്കുന്ന മൂല്യം ലബോറട്ടറി അച്ചടിച്ച റഫറൻസ് പരിധിയേക്കാൾ കൂടുതലാണ്.",
        "factors": (
            "പല ഘടകങ്ങളും — ഉപയോഗിച്ച പരിശോധനാ രീതി, സാമ്പിൾ ശേഖരിച്ച വിധം, സാധാരണ ജൈവ "
            "വ്യതിയാനം — ഒരു ലബോറട്ടറി ഫലത്തെ സ്വാധീനിക്കാം."
        ),
        "low_conf_note": (
            "ചില വിവരങ്ങൾ ആത്മവിശ്വാസത്തോടെ വേർതിരിച്ചെടുക്കാൻ കഴിഞ്ഞില്ല. ദയവായി മൂല്യം നിങ്ങളുടെ "
            "യഥാർത്ഥ റിപ്പോർട്ടുമായി ഒത്തുനോക്കുക."
        ),
        "question": (
            "നിങ്ങളുടെ ലക്ഷണങ്ങൾ, ചരിത്രം, പരിശോധന എന്നിവയുടെ പശ്ചാത്തലത്തിൽ നിങ്ങളുടെ {test} "
            "ഫലത്തിന്റെ അർത്ഥമെന്തെന്ന് ഡോക്ടറോട് ചോദിക്കുക."
        ),
        "no_evidence": (
            "ചില വിഷയങ്ങൾക്ക് വിജ്ഞാനകോശത്തിൽ മതിയായ പിന്തുണാ വിവരങ്ങൾ ലഭിച്ചില്ല, അതിനാൽ ആ "
            "വിശദീകരണങ്ങൾ പൊതുവായവ മാത്രമാണ്."
        ),
        "limitations": [
            "ഒരു ലബോറട്ടറി ഫലത്തിന് മാത്രം ഏതെങ്കിലും രോഗാവസ്ഥ സ്ഥിരീകരിക്കാനോ നിഷേധിക്കാനോ കഴിയില്ല.",
            "റഫറൻസ് പരിധികൾ അവ തയ്യാറാക്കിയ ലബോറട്ടറിക്കും രീതിക്കും പ്രത്യേകമാണ്; നിങ്ങളുടെ റിപ്പോർട്ടിൽ അച്ചടിച്ച പരിധിക്കാണ് മുൻഗണന.",
            "ലബോറട്ടറി ഫലങ്ങളുടെ വ്യാഖ്യാനം എപ്പോഴും നിങ്ങളുടെ ലക്ഷണങ്ങൾ, രോഗചരിത്രം, പരിശോധന എന്നിവയോടൊപ്പം ചെയ്യണം.",
            "ഈ വിശദീകരണം വിദ്യാഭ്യാസ ആവശ്യത്തിനായി നിങ്ങളുടെ റിപ്പോർട്ടിലെ മൂല്യങ്ങളിൽ നിന്നും പൊതുവായ ആരോഗ്യ വിവരങ്ങളിൽ നിന്നും തയ്യാറാക്കിയതാണ്.",
            "ഇത് രോഗനിർണയമല്ല, യോഗ്യതയുള്ള ആരോഗ്യ വിദഗ്ധരുടെ ഉപദേശത്തിന് പകരമല്ല.",
            "ഇവിടെ കാണിച്ചിരിക്കുന്ന മൂല്യങ്ങൾ നിങ്ങളുടെ റിപ്പോർട്ട് അനുസരിച്ചുള്ളതാണ്; ഏതെങ്കിലും മൂല്യം വ്യത്യസ്തമായി തോന്നിയാൽ യഥാർത്ഥ പകർപ്പുമായി ഒത്തുനോക്കുക.",
        ],
        "chat_no_report": (
            "ഈ സംഭാഷണത്തോടൊപ്പം ഒരു റിപ്പോർട്ടും ഉൾപ്പെടുത്തിയിട്ടില്ല, അതിനാൽ എനിക്ക് പൊതുവായ "
            "വിദ്യാഭ്യാസ വിവരങ്ങൾ മാത്രം നൽകാനാകും. റിപ്പോർട്ട് തുറന്ന് വീണ്ടും ചോദിക്കുക, അപ്പോൾ "
            "ഉത്തരങ്ങൾ നിങ്ങളുടെ മൂല്യങ്ങളെ അടിസ്ഥാനമാക്കിയായിരിക്കും."
        ),
        "chat_out_of_range_none": (
            "ഈ സംഭാഷണത്തോടൊപ്പം ഉള്ള റിപ്പോർട്ട് അനുസരിച്ച്, ഒരു മൂല്യവും റിപ്പോർട്ടിൽ "
            "കാണിച്ചിരിക്കുന്ന റഫറൻസ് പരിധിക്ക് പുറത്തല്ല."
        ),
        "chat_out_of_range_header": "നിങ്ങളുടെ റിപ്പോർട്ട് അനുസരിച്ച്, ഈ മൂല്യങ്ങൾ റിപ്പോർട്ടിൽ അച്ചടിച്ച പരിധിക്ക് പുറത്താണ്:",
        "chat_out_of_range_footer": (
            "ദയവായി ഈ മൂല്യങ്ങൾ നിങ്ങളുടെ ആരോഗ്യ വിദഗ്ധരുമായി അവലോകനം ചെയ്യുക; നിങ്ങളുടെ "
            "ലക്ഷണങ്ങളും ചരിത്രവും കൂടി കണക്കിലെടുത്ത് അവയെ വ്യാഖ്യാനിക്കാൻ അവർക്ക് കഴിയും."
        ),
        "chat_meaning_header": "ലളിതമായ ഭാഷയിൽ:",
        "chat_no_kb": (
            "ആ ചോദ്യത്തിന് ഉത്തരം നൽകാൻ ആവശ്യമായ വിവരങ്ങൾ വിജ്ഞാനകോശത്തിൽ കണ്ടെത്താനായില്ല. "
            "ദയവായി നിങ്ങളുടെ ആരോഗ്യ വിദഗ്ധരോട് ചോദിക്കുന്നത് പരിഗണിക്കുക."
        ),
        "chat_footer": (
            "ഇത് പൊതുവായ വിദ്യാഭ്യാസ വിവരമാണ്, വൈദ്യ രോഗനിർണയമല്ല. വ്യക്തിപരമായ വൈദ്യ "
            "ഉപദേശത്തിനായി യോഗ്യതയുള്ള ആരോഗ്യ വിദഗ്ധരെ സമീപിക്കുക."
        ),
        "status_within": (
            "നിങ്ങളുടെ റിപ്പോർട്ടിൽ {name} {value} {unit} ആണ് (റഫറൻസ് പരിധി: {range}). "
            "ഇത് ലബോറട്ടറി അച്ചടിച്ച പരിധിക്കുള്ളിലാണ്."
        ),
        "status_below": (
            "നിങ്ങളുടെ റിപ്പോർട്ടിൽ {name} {value} {unit} ആണ് (റഫറൻസ് പരിധി: {range}). "
            "ഇത് അച്ചടിച്ച പരിധിയേക്കാൾ കുറവാണ്."
        ),
        "status_above": (
            "നിങ്ങളുടെ റിപ്പോർട്ടിൽ {name} {value} {unit} ആണ് (റഫറൻസ് പരിധി: {range}). "
            "ഇത് അച്ചടിച്ച പരിധിയേക്കാൾ കൂടുതലാണ്."
        ),
        "status_unknown": (
            "നിങ്ങളുടെ റിപ്പോർട്ടിൽ {name} {value} {unit} ആണ്. റിപ്പോർട്ടിൽ ഉപയോഗപ്രദമായ റഫറൻസ് "
            "പരിധി ഉണ്ടായിരുന്നില്ല, അതിനാൽ ഈ മൂല്യം വർഗ്ഗീകരിക്കാൻ കഴിഞ്ഞില്ല."
        ),
        "generic_test_description": "ഈ അളവ് ലബോറട്ടറി പരിശോധനയിൽ റിപ്പോർട്ട് ചെയ്യപ്പെടുന്നു.",
        "unknown_limitation": (
            "{n} മൂല്യങ്ങൾ വർഗ്ഗീകരിക്കാൻ കഴിഞ്ഞില്ല, കാരണം റിപ്പോർട്ടിൽ ഉപയോഗപ്രദമായ റഫറൻസ് "
            "പരിധി ഉണ്ടായിരുന്നില്ല. അവയ്ക്ക് ഒരു പരിധിയും അനുമാനിച്ചിട്ടില്ല."
        ),
        "value_line": "നിങ്ങളുടെ റിപ്പോർട്ടിൽ {name} {value} {unit} ആണ് (റഫറൻസ് പരിധി: {range}).",
        "question_generic_1": (
            "നിങ്ങളുടെ ലക്ഷണങ്ങൾ, ചരിത്രം, പരിശോധന എന്നിവയോടൊപ്പം ഈ ഫലങ്ങളുടെ അർത്ഥമെന്തെന്ന് "
            "ഡോക്ടറോട് ചോദിക്കുക."
        ),
        "question_generic_2": (
            "ഈ മൂല്യങ്ങളിൽ ഏതെങ്കിലും വീണ്ടും പരിശോധിക്കേണ്ടതുണ്ടോ അല്ലെങ്കിൽ കാലക്രമേണ "
            "നിരീക്ഷിക്കേണ്ടതുണ്ടോ എന്ന് ചോദിക്കുക."
        ),
        "question_generic_3": "ഈ ഫലങ്ങൾക്ക് ദൈനംദിന ജീവിതത്തിലെ ഏത് ഘടകങ്ങൾ പ്രസക്തമായിരിക്കാം എന്ന് ചോദിക്കുക.",
    },
    "pa": {
        "summary_intro": "ਇਸ ਰਿਪੋਰਟ ਵਿੱਚ {n} ਮਾਪ ਹਨ।",
        "summary_counts": (
            "{within} ਮੁੱਲ ਰਿਪੋਰਟ ਵਿੱਚ ਦਿੱਤੀ ਸੰਦਰਭ ਸੀਮਾ ਦੇ ਅੰਦਰ ਹਨ, {below} ਉਸ ਤੋਂ ਘੱਟ ਹਨ, "
            "{above} ਉਸ ਤੋਂ ਵੱਧ ਹਨ, ਅਤੇ {unknown} ਨੂੰ ਸ਼੍ਰੇਣੀਬੱਧ ਨਹੀਂ ਕੀਤਾ ਜਾ ਸਕਿਆ ਕਿਉਂਕਿ ਕੋਈ "
            "ਵਰਤੋਂਯੋਗ ਸੰਦਰਭ ਸੀਮਾ ਨਹੀਂ ਮਿਲੀ।"
        ),
        "summary_abnormal": "ਤੁਹਾਡੀ ਰਿਪੋਰਟ ਵਿੱਚ ਦਿੱਤੀ ਸੰਦਰਭ ਸੀਮਾ ਤੋਂ ਬਾਹਰ ਦੇ ਮੁੱਲ: {list}।",
        "summary_caveat": (
            "ਇਹ ਇੱਕ ਸਿੱਖਿਆਤਮਕ ਵਿਆਖਿਆ ਹੈ, ਨਿਦਾਨ ਨਹੀਂ। ਕਿਰਪਾ ਕਰਕੇ ਇਹਨਾਂ ਨਤੀਜਿਆਂ ਬਾਰੇ ਯੋਗ ਸਿਹਤ "
            "ਸੰਭਾਲ ਪੇਸ਼ੇਵਰ ਨਾਲ ਚਰਚਾ ਕਰੋ।"
        ),
        "range_below": "ਤੁਹਾਡੀ ਰਿਪੋਰਟ ਵਿੱਚ ਦਿੱਤਾ ਮੁੱਲ ਪ੍ਰਯੋਗਸ਼ਾਲਾ ਵੱਲੋਂ ਛਾਪੀ ਸੰਦਰਭ ਸੀਮਾ ਤੋਂ ਘੱਟ ਹੈ।",
        "range_above": "ਤੁਹਾਡੀ ਰਿਪੋਰਟ ਵਿੱਚ ਦਿੱਤਾ ਮੁੱਲ ਪ੍ਰਯੋਗਸ਼ਾਲਾ ਵੱਲੋਂ ਛਾਪੀ ਸੰਦਰਭ ਸੀਮਾ ਤੋਂ ਵੱਧ ਹੈ।",
        "factors": (
            "ਕਈ ਕਾਰਕ — ਵਰਤੀ ਗਈ ਟੈਸਟ ਵਿਧੀ, ਨਮੂਨਾ ਕਿਵੇਂ ਲਿਆ ਗਿਆ ਅਤੇ ਆਮ ਜੈਵਿਕ ਭਿੰਨਤਾ — ਇੱਕ "
            "ਪ੍ਰਯੋਗਸ਼ਾਲਾ ਨਤੀਜੇ ਨੂੰ ਪ੍ਰਭਾਵਿਤ ਕਰ ਸਕਦੇ ਹਨ।"
        ),
        "low_conf_note": (
            "ਕੁਝ ਜਾਣਕਾਰੀ ਭਰੋਸੇ ਨਾਲ ਕੱਢੀ ਨਹੀਂ ਜਾ ਸਕੀ। ਕਿਰਪਾ ਕਰਕੇ ਮੁੱਲ ਨੂੰ ਆਪਣੀ ਮੂਲ ਰਿਪੋਰਟ ਨਾਲ ਜਾਂਚੋ।"
        ),
        "question": (
            "ਆਪਣੇ ਡਾਕਟਰ ਨੂੰ ਪੁੱਛੋ ਕਿ ਤੁਹਾਡੇ ਲੱਛਣਾਂ, ਇਤਿਹਾਸ ਅਤੇ ਜਾਂਚ ਦੇ ਸੰਦਰਭ ਵਿੱਚ ਤੁਹਾਡੇ {test} "
            "ਨਤੀਜੇ ਦਾ ਕੀ ਅਰਥ ਹੈ।"
        ),
        "no_evidence": (
            "ਕੁਝ ਵਿਸ਼ਿਆਂ ਲਈ ਗਿਆਨਕੋਸ਼ ਵਿੱਚ ਲੋੜੀਂਦੀ ਸਹਾਇਕ ਜਾਣਕਾਰੀ ਨਹੀਂ ਮਿਲੀ, ਇਸ ਲਈ ਉਹ ਵਿਆਖਿਆਵਾਂ "
            "ਕੇਵਲ ਆਮ ਹਨ।"
        ),
        "limitations": [
            "ਕੋਈ ਵੀ ਇਕੱਲਾ ਪ੍ਰਯੋਗਸ਼ਾਲਾ ਨਤੀਜਾ ਆਪਣੇ ਆਪ ਵਿੱਚ ਕਿਸੇ ਵੀ ਡਾਕਟਰੀ ਸਥਿਤੀ ਦੀ ਪੁਸ਼ਟੀ ਜਾਂ ਇਨਕਾਰ ਨਹੀਂ ਕਰ ਸਕਦਾ।",
            "ਸੰਦਰਭ ਸੀਮਾਵਾਂ ਉਸ ਪ੍ਰਯੋਗਸ਼ਾਲਾ ਅਤੇ ਵਿਧੀ ਲਈ ਵਿਸ਼ੇਸ਼ ਹੁੰਦੀਆਂ ਹਨ ਜਿਨ੍ਹਾਂ ਨੇ ਉਹ ਬਣਾਈਆਂ; ਤੁਹਾਡੀ ਰਿਪੋਰਟ 'ਤੇ ਛਾਪੀ ਸੀਮਾ ਸਭ ਤੋਂ ਮਹੱਤਵਪੂਰਨ ਹੈ।",
            "ਪ੍ਰਯੋਗਸ਼ਾਲਾ ਨਤੀਜਿਆਂ ਦੀ ਵਿਆਖਿਆ ਹਮੇਸ਼ਾ ਤੁਹਾਡੇ ਲੱਛਣਾਂ, ਡਾਕਟਰੀ ਇਤਿਹਾਸ ਅਤੇ ਜਾਂਚ ਨਾਲ ਮਿਲਾ ਕੇ ਕੀਤੀ ਜਾਣੀ ਚਾਹੀਦੀ ਹੈ।",
            "ਇਹ ਵਿਆਖਿਆ ਸਿੱਖਿਆਤਮਕ ਉਦੇਸ਼ ਲਈ ਤੁਹਾਡੀ ਰਿਪੋਰਟ ਦੇ ਮੁੱਲਾਂ ਅਤੇ ਆਮ ਸਿਹਤ ਜਾਣਕਾਰੀ ਤੋਂ ਤਿਆਰ ਕੀਤੀ ਗਈ ਹੈ।",
            "ਇਹ ਨਿਦਾਨ ਨਹੀਂ ਹੈ ਅਤੇ ਯੋਗ ਸਿਹਤ ਸੰਭਾਲ ਪੇਸ਼ੇਵਰ ਦੀ ਸਲਾਹ ਦਾ ਬਦਲ ਨਹੀਂ ਹੈ।",
            "ਇੱਥੇ ਦਿਖਾਏ ਮੁੱਲ ਤੁਹਾਡੀ ਰਿਪੋਰਟ ਅਨੁਸਾਰ ਹਨ; ਜੇ ਕੋਈ ਮੁੱਲ ਵੱਖਰਾ ਲੱਗੇ ਤਾਂ ਕਿਰਪਾ ਕਰਕੇ ਮੂਲ ਕਾਪੀ ਨਾਲ ਜਾਂਚੋ।",
        ],
        "chat_no_report": (
            "ਇਸ ਗੱਲਬਾਤ ਨਾਲ ਕੋਈ ਰਿਪੋਰਟ ਨਹੀਂ ਜੁੜੀ, ਇਸ ਲਈ ਮੈਂ ਕੇਵਲ ਆਮ ਸਿੱਖਿਆਤਮਕ ਜਾਣਕਾਰੀ ਦੇ ਸਕਦਾ ਹਾਂ। "
            "ਰਿਪੋਰਟ ਖੋਲ੍ਹ ਕੇ ਦੁਬਾਰਾ ਪੁੱਛੋ ਤਾਂ ਜੋ ਜਵਾਬ ਤੁਹਾਡੇ ਮੁੱਲਾਂ 'ਤੇ ਆਧਾਰਿਤ ਹੋਣ।"
        ),
        "chat_out_of_range_none": (
            "ਇਸ ਗੱਲਬਾਤ ਨਾਲ ਜੁੜੀ ਰਿਪੋਰਟ ਅਨੁਸਾਰ, ਕੋਈ ਵੀ ਮੁੱਲ ਰਿਪੋਰਟ ਵਿੱਚ ਦਿਖਾਈ ਸੰਦਰਭ ਸੀਮਾ "
            "ਤੋਂ ਬਾਹਰ ਨਹੀਂ ਹੈ।"
        ),
        "chat_out_of_range_header": "ਤੁਹਾਡੀ ਰਿਪੋਰਟ ਅਨੁਸਾਰ, ਇਹ ਮੁੱਲ ਰਿਪੋਰਟ 'ਤੇ ਛਾਪੀ ਸੀਮਾ ਤੋਂ ਬਾਹਰ ਹਨ:",
        "chat_out_of_range_footer": (
            "ਕਿਰਪਾ ਕਰਕੇ ਇਹਨਾਂ ਮੁੱਲਾਂ ਦੀ ਸਮੀਖਿਆ ਆਪਣੇ ਸਿਹਤ ਸੰਭਾਲ ਪੇਸ਼ੇਵਰ ਨਾਲ ਕਰੋ, ਜੋ ਤੁਹਾਡੇ ਲੱਛਣਾਂ "
            "ਅਤੇ ਇਤਿਹਾਸ ਨਾਲ ਇਹਨਾਂ ਦੀ ਵਿਆਖਿਆ ਕਰ ਸਕਦੇ ਹਨ।"
        ),
        "chat_meaning_header": "ਸੌਖੀ ਭਾਸ਼ਾ ਵਿੱਚ:",
        "chat_no_kb": (
            "ਉਸ ਜਵਾਬ ਲਈ ਗਿਆਨਕੋਸ਼ ਵਿੱਚ ਲੋੜੀਂਦੀ ਸੰਬੰਧਿਤ ਜਾਣਕਾਰੀ ਨਹੀਂ ਮਿਲੀ। ਕਿਰਪਾ ਕਰਕੇ ਆਪਣੇ ਸਿਹਤ "
            "ਸੰਭਾਲ ਪੇਸ਼ੇਵਰ ਨੂੰ ਪੁੱਛਣ ਬਾਰੇ ਵਿਚਾਰ ਕਰੋ।"
        ),
        "chat_footer": (
            "ਇਹ ਆਮ ਸਿੱਖਿਆਤਮਕ ਜਾਣਕਾਰੀ ਹੈ, ਡਾਕਟਰੀ ਨਿਦਾਨ ਨਹੀਂ। ਨਿੱਜੀ ਡਾਕਟਰੀ ਸਲਾਹ ਲਈ ਯੋਗ ਸਿਹਤ "
            "ਸੰਭਾਲ ਪੇਸ਼ੇਵਰ ਦੀ ਸਲਾਹ ਲਓ।"
        ),
        "status_within": (
            "ਤੁਹਾਡੀ ਰਿਪੋਰਟ ਵਿੱਚ {name} {value} {unit} ਹੈ (ਸੰਦਰਭ ਸੀਮਾ: {range})। "
            "ਇਹ ਪ੍ਰਯੋਗਸ਼ਾਲਾ ਵੱਲੋਂ ਛਾਪੀ ਸੀਮਾ ਦੇ ਅੰਦਰ ਹੈ।"
        ),
        "status_below": (
            "ਤੁਹਾਡੀ ਰਿਪੋਰਟ ਵਿੱਚ {name} {value} {unit} ਹੈ (ਸੰਦਰਭ ਸੀਮਾ: {range})। "
            "ਇਹ ਛਾਪੀ ਸੀਮਾ ਤੋਂ ਘੱਟ ਹੈ।"
        ),
        "status_above": (
            "ਤੁਹਾਡੀ ਰਿਪੋਰਟ ਵਿੱਚ {name} {value} {unit} ਹੈ (ਸੰਦਰਭ ਸੀਮਾ: {range})। "
            "ਇਹ ਛਾਪੀ ਸੀਮਾ ਤੋਂ ਵੱਧ ਹੈ।"
        ),
        "status_unknown": (
            "ਤੁਹਾਡੀ ਰਿਪੋਰਟ ਵਿੱਚ {name} {value} {unit} ਹੈ। ਰਿਪੋਰਟ ਵਿੱਚ ਵਰਤੋਂਯੋਗ ਸੰਦਰਭ ਸੀਮਾ ਨਹੀਂ ਸੀ, "
            "ਇਸ ਲਈ ਇਸ ਮੁੱਲ ਨੂੰ ਸ਼੍ਰੇਣੀਬੱਧ ਨਹੀਂ ਕੀਤਾ ਜਾ ਸਕਿਆ।"
        ),
        "generic_test_description": "ਇਹ ਮਾਪ ਪ੍ਰਯੋਗਸ਼ਾਲਾ ਟੈਸਟ ਵਿੱਚ ਦਰਜ ਕੀਤਾ ਜਾਂਦਾ ਹੈ।",
        "unknown_limitation": (
            "{n} ਮੁੱਲ ਸ਼੍ਰੇਣੀਬੱਧ ਨਹੀਂ ਕੀਤੇ ਜਾ ਸਕੇ ਕਿਉਂਕਿ ਰਿਪੋਰਟ ਵਿੱਚ ਵਰਤੋਂਯੋਗ ਸੰਦਰਭ ਸੀਮਾ ਨਹੀਂ ਸੀ। "
            "ਉਹਨਾਂ ਲਈ ਕੋਈ ਸੀਮਾ ਨਹੀਂ ਮੰਨੀ ਗਈ।"
        ),
        "value_line": "ਤੁਹਾਡੀ ਰਿਪੋਰਟ ਵਿੱਚ {name} {value} {unit} ਹੈ (ਸੰਦਰਭ ਸੀਮਾ: {range})।",
        "question_generic_1": (
            "ਤੁਹਾਡੇ ਲੱਛਣਾਂ, ਇਤਿਹਾਸ ਅਤੇ ਜਾਂਚ ਨਾਲ ਇਹਨਾਂ ਨਤੀਜਿਆਂ ਦਾ ਕੀ ਅਰਥ ਹੈ, ਆਪਣੇ ਡਾਕਟਰ ਨੂੰ ਪੁੱਛੋ।"
        ),
        "question_generic_2": (
            "ਪੁੱਛੋ ਕਿ ਇਹਨਾਂ ਵਿੱਚੋਂ ਕੋਈ ਮੁੱਲ ਦੁਬਾਰਾ ਟੈਸਟ ਕਰਨ ਜਾਂ ਸਮੇਂ ਦੇ ਨਾਲ ਨਿਗਰਾਨੀ ਕਰਨ ਦੀ ਲੋੜ ਹੈ ਜਾਂ ਨਹੀਂ।"
        ),
        "question_generic_3": "ਪੁੱਛੋ ਕਿ ਇਹਨਾਂ ਨਤੀਜਿਆਂ ਲਈ ਰੋਜ਼ਾਨਾ ਜੀਵਨ ਦੇ ਕਿਹੜੇ ਕਾਰਕ ਸੰਬੰਧਿਤ ਹੋ ਸਕਦੇ ਹਨ।",
    },
    "or": {
        "summary_intro": "ଏହି ରିପୋର୍ଟରେ {n} ମାପ ଅଛି।",
        "summary_counts": (
            "{within} ମୂଲ୍ୟ ରିପୋର୍ଟରେ ଦିଆଯାଇଥିବା ରେଫରେନ୍ସ ସୀମା ଭିତରେ ଅଛି, {below} ତାହାଠାରୁ କମ୍ "
            "ଅଛି, {above} ତାହାଠାରୁ ଅଧିକ ଅଛି, ଏବଂ ଉପଯୋଗୀ ରେଫରେନ୍ସ ସୀମା ନ ମିଳିବାରୁ {unknown} "
            "କୁ ଶ୍ରେଣୀଭୁକ୍ତ କରାଯାଇପାରିଲା ନାହିଁ।"
        ),
        "summary_abnormal": "ଆପଣଙ୍କ ରିପୋର୍ଟରେ ଦିଆଯାଇଥିବା ରେଫରେନ୍ସ ସୀମା ବାହାରେ ଥିବା ମୂଲ୍ୟ: {list}।",
        "summary_caveat": (
            "ଏହା ଏକ ଶିକ୍ଷାପରକ ବ୍ୟାଖ୍ୟା, ନିଦାନ ନୁହେଁ। ଦୟାକରି ଏହି ଫଳାଫଳ ବିଷୟରେ ଯୋଗ୍ୟ "
            "ସ୍ୱାସ୍ଥ୍ୟସେବା ବୃତ୍ତିଧାରୀଙ୍କ ସହ ଆଲୋଚନା କରନ୍ତୁ।"
        ),
        "range_below": "ଆପଣଙ୍କ ରିପୋର୍ଟରେ ଦିଆଯାଇଥିବା ମୂଲ୍ୟ ପରୀକ୍ଷାଗାର ଛାପିଥିବା ରେଫରେନ୍ସ ସୀମାଠାରୁ କମ୍ ଅଛି।",
        "range_above": "ଆପଣଙ୍କ ରିପୋର୍ଟରେ ଦିଆଯାଇଥିବା ମୂଲ୍ୟ ପରୀକ୍ଷାଗାର ଛାପିଥିବା ରେଫରେନ୍ସ ସୀମାଠାରୁ ଅଧିକ ଅଛି।",
        "factors": (
            "ଅନେକ କାରଣ — ବ୍ୟବହୃତ ପରୀକ୍ଷା ପଦ୍ଧତି, ନମୁନା ସଂଗ୍ରହ କରାଯିବାର ଢଙ୍ଗ ଏବଂ ସାଧାରଣ ଜୈବିକ "
            "ଭିନ୍ନତା — ଏକ ପରୀକ୍ଷାଗାର ଫଳାଫଳକୁ ପ୍ରଭାବିତ କରିପାରେ।"
        ),
        "low_conf_note": (
            "କିଛି ସୂଚନା ଆତ୍ମବିଶ୍ୱାସର ସହ ବାହାର କରାଯାଇପାରିଲା ନାହିଁ। ଦୟାକରି ମୂଲ୍ୟକୁ ଆପଣଙ୍କ ମୂଳ "
            "ରିପୋର୍ଟ ସହ ମିଳାଇ ଯାଞ୍ଚ କରନ୍ତୁ।"
        ),
        "question": (
            "ଆପଣଙ୍କ ଲକ୍ଷଣ, ଇତିହାସ ଏବଂ ପରୀକ୍ଷାର ପ୍ରସଙ୍ଗରେ ଆପଣଙ୍କ {test} ଫଳାଫଳର ଅର୍ଥ କଣ, "
            "ଡାକ୍ତରଙ୍କୁ ପଚାରନ୍ତୁ।"
        ),
        "no_evidence": (
            "କିଛି ବିଷୟ ପାଇଁ ଜ୍ଞାନକୋଷରେ ପର୍ଯ୍ୟାପ୍ତ ସହାୟକ ସୂଚନା ମିଳିଲା ନାହିଁ, ତେଣୁ ସେହି "
            "ବ୍ୟାଖ୍ୟାଗୁଡ଼ିକ କେବଳ ସାଧାରଣ।"
        ),
        "limitations": [
            "ଗୋଟିଏ ପରୀକ୍ଷାଗାର ଫଳାଫଳ ଏକାକୀ କୌଣସି ଚିକିତ୍ସା ସ୍ଥିତିର ପୁଷ୍ଟି କିମ୍ବା ଅସ୍ୱୀକୃତି କରିପାରିବ ନାହିଁ।",
            "ରେଫରେନ୍ସ ସୀମାଗୁଡ଼ିକ ସେଗୁଡ଼ିକୁ ପ୍ରସ୍ତୁତ କରିଥିବା ପରୀକ୍ଷାଗାର ଏବଂ ପଦ୍ଧତି ପାଇଁ ନିର୍ଦ୍ଦିଷ୍ଟ; ଆପଣଙ୍କ ରିପୋର୍ଟରେ ଛାପାଯାଇଥିବା ସୀମା ସର୍ବାଧିକ ଗୁରୁତ୍ୱପୂର୍ଣ୍ଣ।",
            "ପରୀକ୍ଷାଗାର ଫଳାଫଳର ବ୍ୟାଖ୍ୟା ସର୍ବଦା ଆପଣଙ୍କ ଲକ୍ଷଣ, ଚିକିତ୍ସା ଇତିହାସ ଏବଂ ପରୀକ୍ଷା ସହ ମିଶାଇ କରାଯିବା ଉଚିତ।",
            "ଏହି ବ୍ୟାଖ୍ୟା ଶିକ୍ଷାପରକ ଉଦ୍ଦେଶ୍ୟରେ ଆପଣଙ୍କ ରିପୋର୍ଟର ମୂଲ୍ୟ ଏବଂ ସାଧାରଣ ସ୍ୱାସ୍ଥ୍ୟ ସୂଚନାରୁ ପ୍ରସ୍ତୁତ କରାଯାଇଛି।",
            "ଏହା ନିଦାନ ନୁହେଁ ଏବଂ ଯୋଗ୍ୟ ସ୍ୱାସ୍ଥ୍ୟସେବା ବୃତ୍ତିଧାରୀଙ୍କ ପରାମର୍ଶର ବିକଳ୍ପ ନୁହେଁ।",
            "ଏଠାରେ ଦେଖାଯାଇଥିବା ମୂଲ୍ୟଗୁଡ଼ିକ ଆପଣଙ୍କ ରିପୋର୍ଟ ଅନୁସାରେ; କୌଣସି ମୂଲ୍ୟ ଭିନ୍ନ ଲାଗିଲେ ଦୟାକରି ମୂଳ ପ୍ରତି ସହ ଯାଞ୍ଚ କରନ୍ତୁ।",
        ],
        "chat_no_report": (
            "ଏହି ଆଲୋଚନା ସହ କୌଣସି ରିପୋର୍ଟ ଯୋଡ଼ା ଯାଇନାହିଁ, ତେଣୁ ମୁଁ କେବଳ ସାଧାରଣ ଶିକ୍ଷାପରକ "
            "ସୂଚନା ଦେଇପାରିବି। ରିପୋର୍ଟ ଖୋଲି ପୁଣି ପଚାରନ୍ତୁ, ତେବେ ଉତ୍ତର ଆପଣଙ୍କ ମୂଲ୍ୟ ଉପରେ ଆଧାରିତ ହେବ।"
        ),
        "chat_out_of_range_none": (
            "ଏହି ଆଲୋଚନା ସହ ଯୋଡ଼ାଯାଇଥିବା ରିପୋର୍ଟ ଅନୁସାରେ, କୌଣସି ମୂଲ୍ୟ ରିପୋର୍ଟରେ ଦେଖାଯାଇଥିବା "
            "ରେଫରେନ୍ସ ସୀମା ବାହାରେ ନାହିଁ।"
        ),
        "chat_out_of_range_header": "ଆପଣଙ୍କ ରିପୋର୍ଟ ଅନୁସାରେ, ଏହି ମୂଲ୍ୟଗୁଡ଼ିକ ରିପୋର୍ଟରେ ଛାପାଯାଇଥିବା ସୀମା ବାହାରେ ଅଛି:",
        "chat_out_of_range_footer": (
            "ଦୟାକରି ଏହି ମୂଲ୍ୟଗୁଡ଼ିକୁ ଆପଣଙ୍କ ସ୍ୱାସ୍ଥ୍ୟସେବା ବୃତ୍ତିଧାରୀଙ୍କ ସହ ସମୀକ୍ଷା କରନ୍ତୁ, ସେମାନେ "
            "ଆପଣଙ୍କ ଲକ୍ଷଣ ଏବଂ ଇତିହାସ ସହ ଏଗୁଡ଼ିକର ବ୍ୟାଖ୍ୟା କରିପାରିବେ।"
        ),
        "chat_meaning_header": "ସରଳ ଭାଷାରେ:",
        "chat_no_kb": (
            "ସେହି ଉତ୍ତର ପାଇଁ ଜ୍ଞାନକୋଷରେ ପର୍ଯ୍ୟାପ୍ତ ସମ୍ପୃକ୍ତ ସୂଚନା ମିଳିଲା ନାହିଁ। ଦୟାକରି ଆପଣଙ୍କ "
            "ସ୍ୱାସ୍ଥ୍ୟସେବା ବୃତ୍ତିଧାରୀଙ୍କୁ ପଚାରିବା ବିଷୟରେ ବିଚାର କରନ୍ତୁ।"
        ),
        "chat_footer": (
            "ଏହା ସାଧାରଣ ଶିକ୍ଷାପରକ ସୂଚନା, ଚିକିତ୍ସା ନିଦାନ ନୁହେଁ। ବ୍ୟକ୍ତିଗତ ଚିକିତ୍ସା ପରାମର୍ଶ ପାଇଁ "
            "ଯୋଗ୍ୟ ସ୍ୱାସ୍ଥ୍ୟସେବା ବୃତ୍ତିଧାରୀଙ୍କ ପରାମର୍ଶ ନିଅନ୍ତୁ।"
        ),
        "status_within": (
            "ଆପଣଙ୍କ ରିପୋର୍ଟରେ {name} {value} {unit} ଅଛି (ରେଫରେନ୍ସ ସୀମା: {range})। "
            "ଏହା ପରୀକ୍ଷାଗାର ଛାପିଥିବା ସୀମା ଭିତରେ ଅଛି।"
        ),
        "status_below": (
            "ଆପଣଙ୍କ ରିପୋର୍ଟରେ {name} {value} {unit} ଅଛି (ରେଫରେନ୍ସ ସୀମା: {range})। "
            "ଏହା ଛାପିଥିବା ସୀମାଠାରୁ କମ୍ ଅଛି।"
        ),
        "status_above": (
            "ଆପଣଙ୍କ ରିପୋର୍ଟରେ {name} {value} {unit} ଅଛି (ରେଫରେନ୍ସ ସୀମା: {range})। "
            "ଏହା ଛାପିଥିବା ସୀମାଠାରୁ ଅଧିକ ଅଛି।"
        ),
        "status_unknown": (
            "ଆପଣଙ୍କ ରିପୋର୍ଟରେ {name} {value} {unit} ଅଛି। ରିପୋର୍ଟରେ ଉପଯୋଗୀ ରେଫରେନ୍ସ ସୀମା "
            "ନଥିଲା, ତେଣୁ ଏହି ମୂଲ୍ୟକୁ ଶ୍ରେଣୀଭୁକ୍ତ କରାଯାଇପାରିଲା ନାହିଁ।"
        ),
        "generic_test_description": "ଏହି ମାପ ପରୀକ୍ଷାଗାର ପରୀକ୍ଷାରେ ରିପୋର୍ଟ କରାଯାଏ।",
        "unknown_limitation": (
            "{n} ଟି ମୂଲ୍ୟ ଶ୍ରେଣୀଭୁକ୍ତ କରାଯାଇପାରିଲା ନାହିଁ କାରଣ ରିପୋର୍ଟରେ ଉପଯୋଗୀ ରେଫରେନ୍ସ ସୀମା "
            "ନଥିଲା। ସେଗୁଡ଼ିକ ପାଇଁ କୌଣସି ସୀମା ଅନୁମାନ କରାଯାଇନାହିଁ।"
        ),
        "value_line": "ଆପଣଙ୍କ ରିପୋର୍ଟରେ {name} {value} {unit} ଅଛି (ରେଫରେନ୍ସ ସୀମା: {range})।",
        "question_generic_1": (
            "ଆପଣଙ୍କ ଲକ୍ଷଣ, ଇତିହାସ ଏବଂ ପରୀକ୍ଷା ସହ ଏହି ଫଳାଫଳର ଅର୍ଥ କଣ, ଡାକ୍ତରଙ୍କୁ ପଚାରନ୍ତୁ।"
        ),
        "question_generic_2": (
            "ଏହି ମୂଲ୍ୟଗୁଡ଼ିକରୁ କୌଣସିଟି ପୁଣି ପରୀକ୍ଷା କରିବା କିମ୍ବା ସମୟକ୍ରମେ ମନିଟର କରିବା ଆବଶ୍ୟକ କି ନାହିଁ ପଚାରନ୍ତୁ।"
        ),
        "question_generic_3": "ଏହି ଫଳାଫଳ ପାଇଁ ଦୈନନ୍ଦିନ ଜୀବନର କେଉଁ କାରଣ ପ୍ରାସଙ୍ଗିକ ହୋଇପାରେ ପଚାରନ୍ତୁ।",
    },
    "as": {
        "summary_intro": "এই ৰিপৰ্টত {n}টা জোখ আছে।",
        "summary_counts": (
            "{within}টা মান ৰিপৰ্টত দিয়া সংদৰ্ভ সীমাৰ ভিতৰত আছে, {below}টা তাৰ তলত আছে, {above}টা "
            "তাৰ ওপৰত আছে, আৰু ব্যৱহাৰযোগ্য সংদৰ্ভ সীমা নোপোৱাৰ বাবে {unknown}টা শ্ৰেণীবদ্ধ কৰিব "
            "পৰা নগ'ল।"
        ),
        "summary_abnormal": "আপোনাৰ ৰিপৰ্টত দিয়া সংদৰ্ভ সীমাৰ বাহিৰৰ মানসমূহ: {list}।",
        "summary_caveat": (
            "এইটো এটা শৈক্ষিক ব্যাখ্যা, ৰোগনিৰ্ণয় নহয়। অনুগ্ৰহ কৰি এই ফলাফলসমূহৰ বিষয়ে যোগ্য "
            "স্বাস্থ্যসেৱা পেছাদাৰীৰ সৈতে আলোচনা কৰক।"
        ),
        "range_below": "আপোনাৰ ৰিপৰ্টত দিয়া মান প্ৰয়োগশালাই ছপা কৰা সংদৰ্ভ সীমাতকৈ কম।",
        "range_above": "আপোনাৰ ৰিপৰ্টত দিয়া মান প্ৰয়োগশালাই ছপা কৰা সংদৰ্ভ সীমাতকৈ বেছি।",
        "factors": (
            "বহু কাৰকে — ব্যৱহৃত পৰীক্ষা পদ্ধতি, নমুনা সংগ্ৰহ কৰাৰ ধৰণ আৰু স্বাভাৱিক জৈৱিক "
            "ভিন্নতা — এটা প্ৰয়োগশালাৰ ফলাফলত প্ৰভাৱ পেলাব পাৰে।"
        ),
        "low_conf_note": (
            "কিছুমান তথ্য আত্মবিশ্বাসেৰে উলিয়াব পৰা নগ'ল। অনুগ্ৰহ কৰি মানটো আপোনাৰ মূল ৰিপৰ্টৰ "
            "সৈতে মিলাই পৰীক্ষা কৰক।"
        ),
        "question": (
            "আপোনাৰ লক্ষণ, ইতিহাস আৰু পৰীক্ষাৰ প্ৰসংগত আপোনাৰ {test} ফলাফলৰ অৰ্থ কি, চিকিৎসকক সোধক।"
        ),
        "no_evidence": (
            "কিছুমান বিষয়ৰ বাবে জ্ঞানকোষত পৰ্যাপ্ত সহায়ক তথ্য পোৱা নগ'ল, সেয়েহে সেই ব্যাখ্যাসমূহ "
            "কেৱল সাধাৰণ।"
        ),
        "limitations": [
            "কোনো এটা অকলশৰীয়া প্ৰয়োগশালাৰ ফলাফলে নিজে কোনো চিকিৎসা অৱস্থাৰ নিশ্চিতি বা অস্বীকাৰ কৰিব নোৱাৰে।",
            "সংদৰ্ভ সীমাসমূহ সেই প্ৰয়োগশালা আৰু পদ্ধতিৰ বাবে নিৰ্দিষ্ট যিয়ে সেইবোৰ প্ৰস্তুত কৰিছে; আপোনাৰ ৰিপৰ্টত ছপা কৰা সীমাই সৰ্বোচ্চ।",
            "প্ৰয়োগশালাৰ ফলাফলৰ ব্যাখ্যা সদায় আপোনাৰ লক্ষণ, চিকিৎসা ইতিহাস আৰু পৰীক্ষাৰ সৈতে মিলাই কৰিব লাগে।",
            "এই ব্যাখ্যাটো শৈক্ষিক উদ্দেশ্যৰ বাবে আপোনাৰ ৰিপৰ্টৰ মান আৰু সাধাৰণ স্বাস্থ্য তথ্যৰ পৰা প্ৰস্তুত কৰা হৈছে।",
            "এইটো ৰোগনিৰ্ণয় নহয় আৰু যোগ্য স্বাস্থ্যসেৱা পেছাদাৰীৰ পৰামৰ্শৰ বিকল্প নহয়।",
            "ইয়াত দেখুওৱা মানসমূহ আপোনাৰ ৰিপৰ্ট অনুসৰি; কোনো মান বেলেগ যেন লাগিলে অনুগ্ৰহ কৰি মূল প্ৰতিলিপিৰ সৈতে পৰীক্ষা কৰক।",
        ],
        "chat_no_report": (
            "এই আলোচনাৰ সৈতে কোনো ৰিপৰ্ট সংযুক্ত নাই, সেয়েহে মই কেৱল সাধাৰণ শৈক্ষিক তথ্য দিব "
            "পাৰোঁ। ৰিপৰ্ট খুলি পুনৰ সোধক, তেতিয়া উত্তৰসমূহ আপোনাৰ মানৰ ওপৰত আধাৰিত হ'ব।"
        ),
        "chat_out_of_range_none": (
            "এই আলোচনাৰ সৈতে সংযুক্ত ৰিপৰ্ট অনুসৰি, কোনো মান ৰিপৰ্টত দেখুওৱা সংদৰ্ভ সীমাৰ বাহিৰত নাই।"
        ),
        "chat_out_of_range_header": "আপোনাৰ ৰিপৰ্ট অনুসৰি, এই মানসমূহ ৰিপৰ্টত ছপা কৰা সীমাৰ বাহিৰত আছে:",
        "chat_out_of_range_footer": (
            "অনুগ্ৰহ কৰি এই মানসমূহ আপোনাৰ স্বাস্থ্যসেৱা পেছাদাৰীৰ সৈতে পুনৰীক্ষণ কৰক, যিয়ে আপোনাৰ "
            "লক্ষণ আৰু ইতিহাসৰ সৈতে এইবোৰৰ ব্যাখ্যা কৰিব পাৰে।"
        ),
        "chat_meaning_header": "সহজ ভাষাত:",
        "chat_no_kb": (
            "সেই প্ৰশ্নৰ উত্তৰৰ বাবে জ্ঞানকোষত পৰ্যাপ্ত প্ৰাসংগিক তথ্য পোৱা নগ'ল। অনুগ্ৰহ কৰি আপোনাৰ "
            "স্বাস্থ্যসেৱা পেছাদাৰীক সোধাৰ কথা বিবেচনা কৰক।"
        ),
        "chat_footer": (
            "এইটো সাধাৰণ শৈক্ষিক তথ্য, চিকিৎসা ৰোগনিৰ্ণয় নহয়। ব্যক্তিগত চিকিৎসা পৰামৰ্শৰ বাবে "
            "যোগ্য স্বাস্থ্যসেৱা পেছাদাৰীৰ পৰামৰ্শ লওক।"
        ),
        "status_within": (
            "আপোনাৰ ৰিপৰ্টত {name} {value} {unit} আছে (সংদৰ্ভ সীমা: {range})। "
            "এইটো প্ৰয়োগশালাই ছপা কৰা সীমাৰ ভিতৰত আছে।"
        ),
        "status_below": (
            "আপোনাৰ ৰিপৰ্টত {name} {value} {unit} আছে (সংদৰ্ভ সীমা: {range})। "
            "এইটো ছপা কৰা সীমাতকৈ কম।"
        ),
        "status_above": (
            "আপোনাৰ ৰিপৰ্টত {name} {value} {unit} আছে (সংদৰ্ভ সীমা: {range})। "
            "এইটো ছপা কৰা সীমাতকৈ বেছি।"
        ),
        "status_unknown": (
            "আপোনাৰ ৰিপৰ্টত {name} {value} {unit} আছে। ৰিপৰ্টত ব্যৱহাৰযোগ্য সংদৰ্ভ সীমা নাছিল, "
            "সেয়েহে এই মানটো শ্ৰেণীবদ্ধ কৰিব পৰা নগ'ল।"
        ),
        "generic_test_description": "এই জোখটো প্ৰয়োগশালা পৰীক্ষাত লিপিবদ্ধ কৰা হয়।",
        "unknown_limitation": (
            "{n}টা মান শ্ৰেণীবদ্ধ কৰিব পৰা নগ'ল কাৰণ ৰিপৰ্টত ব্যৱহাৰযোগ্য সংদৰ্ভ সীমা নাছিল। "
            "সেইবোৰৰ বাবে কোনো সীমা ধৰি লোৱা হোৱা নাই।"
        ),
        "value_line": "আপোনাৰ ৰিপৰ্টত {name} {value} {unit} আছে (সংদৰ্ভ সীমা: {range})।",
        "question_generic_1": (
            "আপোনাৰ লক্ষণ, ইতিহাস আৰু পৰীক্ষাৰ সৈতে এই ফলাফলসমূহৰ অৰ্থ কি, চিকিৎসকক সোধক।"
        ),
        "question_generic_2": (
            "এই মানসমূহৰ কোনোবা আকৌ পৰীক্ষা কৰিব লাগিব নে সময়ৰ লগে লগে নিৰীক্ষণ কৰিব লাগিব বুলি সোধক।"
        ),
        "question_generic_3": "এই ফলাফলসমূহৰ বাবে দৈনন্দিন জীৱনৰ কোনবোৰ কাৰক প্ৰাসংগিক হ'ব পাৰে বুলি সোধক।",
    },
    "ur": {
        "summary_intro": "اس رپورٹ میں {n} پیمائشیں ہیں۔",
        "summary_counts": (
            "{within} اقدار رپورٹ میں دی گئی حوالہ حد کے اندر ہیں، {below} اس سے کم ہیں، {above} "
            "اس سے زیادہ ہیں، اور {unknown} کو درجہ بند نہیں کیا جا سکا کیونکہ کوئی قابل استعمال "
            "حوالہ حد نہیں ملی۔"
        ),
        "summary_abnormal": "آپ کی رپورٹ میں دی گئی حوالہ حد سے باہر اقدار: {list}۔",
        "summary_caveat": (
            "یہ ایک تعلیمی وضاحت ہے، تشخیص نہیں۔ براہ کرم ان نتائج پر کسی مستند طبی پیشہ ور سے "
            "بات کریں۔"
        ),
        "range_below": "آپ کی رپورٹ میں دی گئی قدر لیبارٹری کی چھپی ہوئی حوالہ حد سے کم ہے۔",
        "range_above": "آپ کی رپورٹ میں دی گئی قدر لیبارٹری کی چھپی ہوئی حوالہ حد سے زیادہ ہے۔",
        "factors": (
            "بہت سے عوامل — استعمال شدہ جانچ کا طریقہ، نمونہ کیسے لیا گیا اور عام حیاتیاتی فرق — "
            "ایک لیبارٹری نتیجے کو متاثر کر سکتے ہیں۔"
        ),
        "low_conf_note": (
            "کچھ معلومات اعتماد کے ساتھ حاصل نہیں کی جا سکیں۔ براہ کرم قدر کو اپنی اصل رپورٹ سے "
            "ملا کر جانچیں۔"
        ),
        "question": (
            "اپنے ڈاکٹر سے پوچھیں کہ آپ کی علامات، تاریخ اور جانچ کے تناظر میں آپ کے {test} "
            "نتیجے کا کیا مطلب ہے۔"
        ),
        "no_evidence": (
            "کچھ موضوعات کے لیے معلوماتی ذخیرے میں کافی معاون معلومات نہیں ملیں، اس لیے وہ "
            "وضاحتیں صرف عمومی ہیں۔"
        ),
        "limitations": [
            "کوئی بھی واحد لیبارٹری نتیجہ بذات خود کسی طبی حالت کی تصدیق یا تردید نہیں کر سکتا۔",
            "حوالہ حدود اُس لیبارٹری اور طریقے کے لیے مخصوص ہوتی ہیں جنھوں نے وہ تیار کیں؛ آپ کی رپورٹ پر چھپی حد کو ترجیح حاصل ہے۔",
            "لیبارٹری نتائج کی تشریح ہمیشہ آپ کی علامات، طبی تاریخ اور جانچ کے ساتھ مل کر کی جانی چاہیے۔",
            "یہ وضاحت تعلیمی مقصد کے لیے آپ کی رپورٹ کی اقدار اور عام صحت کی معلومات سے تیار کی گئی ہے۔",
            "یہ تشخیص نہیں ہے اور کسی مستند طبی پیشہ ور کے مشورے کا متبادل نہیں۔",
            "یہاں دکھائی گئی اقدار آپ کی رپورٹ کے مطابق ہیں؛ اگر کوئی قدر مختلف لگے تو براہ کرم اصل نقل سے جانچیں۔",
        ],
        "chat_no_report": (
            "اس گفتگو کے ساتھ کوئی رپورٹ منسلک نہیں، اس لیے میں صرف عمومی تعلیمی معلومات دے "
            "سکتا ہوں۔ رپورٹ کھول کر دوبارہ پوچھیں تاکہ جوابات آپ کی اقدار پر مبنی ہوں۔"
        ),
        "chat_out_of_range_none": (
            "اس گفتگو کے ساتھ منسلک رپورٹ کے مطابق، کوئی بھی قدر رپورٹ میں دکھائی گئی حوالہ "
            "حدود سے باہر نہیں ہے۔"
        ),
        "chat_out_of_range_header": "آپ کی رپورٹ کے مطابق، یہ اقدار رپورٹ پر چھپی حد سے باہر ہیں:",
        "chat_out_of_range_footer": (
            "براہ کرم ان اقدار کا جائزہ اپنے طبی پیشہ ور کے ساتھ لیں، جو آپ کی علامات اور تاریخ "
            "کے ساتھ ان کی تشریح کر سکتے ہیں۔"
        ),
        "chat_meaning_header": "آسان زبان میں:",
        "chat_no_kb": (
            "اس جواب کے لیے معلوماتی ذخیرے میں کافی متعلقہ معلومات نہیں ملیں۔ براہ کرم اپنے طبی "
            "پیشہ ور سے پوچھنے پر غور کریں۔"
        ),
        "chat_footer": (
            "یہ عمومی تعلیمی معلومات ہے، طبی تشخیص نہیں۔ ذاتی طبی مشورے کے لیے مستند طبی پیشہ ور "
            "سے مشورہ کریں۔"
        ),
        "status_within": (
            "آپ کی رپورٹ میں {name} {value} {unit} ہے (حوالہ حد: {range})۔ "
            "یہ لیبارٹری کی چھپی ہوئی حد کے اندر ہے۔"
        ),
        "status_below": (
            "آپ کی رپورٹ میں {name} {value} {unit} ہے (حوالہ حد: {range})۔ "
            "یہ چھپی ہوئی حد سے کم ہے۔"
        ),
        "status_above": (
            "آپ کی رپورٹ میں {name} {value} {unit} ہے (حوالہ حد: {range})۔ "
            "یہ چھپی ہوئی حد سے زیادہ ہے۔"
        ),
        "status_unknown": (
            "آپ کی رپورٹ میں {name} {value} {unit} ہے۔ رپورٹ میں قابل استعمال حوالہ حد نہیں تھی، "
            "اس لیے اس قدر کو درجہ بند نہیں کیا جا سکا۔"
        ),
        "generic_test_description": "یہ پیمائش لیبارٹری جانچ میں درج کی جاتی ہے۔",
        "unknown_limitation": (
            "{n} اقدار درجہ بند نہیں کی جا سکیں کیونکہ رپورٹ میں قابل استعمال حوالہ حد نہیں تھی۔ "
            "ان کے لیے کوئی حد فرض نہیں کی گئی۔"
        ),
        "value_line": "آپ کی رپورٹ میں {name} {value} {unit} ہے (حوالہ حد: {range})۔",
        "question_generic_1": (
            "اپنے ڈاکٹر سے پوچھیں کہ آپ کی علامات، تاریخ اور جانچ کے ساتھ ان نتائج کا کیا مطلب ہے۔"
        ),
        "question_generic_2": (
            "پوچھیں کہ ان اقدار میں سے کسی کو دوبارہ جانچنے یا وقت کے ساتھ نگرانی کرنے کی ضرورت ہے یا نہیں۔"
        ),
        "question_generic_3": "پوچھیں کہ ان نتائج کے لیے روزمرہ زندگی کے کون سے عوامل متعلقہ ہو سکتے ہیں۔",
    },
}

OUT_OF_RANGE_QUERY = re.compile(
    r"(outside|out of range|abnormal|high|low|clinic|bahar|kam|zyada|असामान्य|बाहर|कम|अधिक)",
    re.I,
)
MEANING_QUERY = re.compile(
    r"(what (does|is)|mean|matlab|kya hai|kya matlab|explain|samjha|simple|asaan|बताओ|मतलब|समझाओ)",
    re.I,
)


def _first_sentences(text: str, count: int = 2, max_chars: int = 420) -> str:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    snippet = " ".join(parts[:count]).strip()
    if len(snippet) > max_chars:
        snippet = snippet[: max_chars - 1].rstrip() + "…"
    return snippet


def _collapse(text: str) -> str:
    """Collapse runs of spaces (from empty placeholders) into single spaces."""
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def _chunk_for_test_key(
    chunk_by_topic: dict[str, RetrievedChunk], test_key: str | None
) -> RetrievedChunk | None:
    """Best retrieved chunk for a test key.

    An exact topic match (one knowledge document per test) wins; otherwise the
    test's knowledge-base topic is used (LabTest.topic), so e.g. every CBC test
    can be grounded in the shared "cbc" document.
    """
    if not test_key:
        return None
    chunk = chunk_by_topic.get(test_key)
    if chunk is not None:
        return chunk
    test = TESTS_BY_KEY.get(test_key)
    if test is not None:
        return chunk_by_topic.get(test.topic)
    return None


def _topic_serves_key(chunk_topic: str | None, test_key: str | None) -> bool:
    """True when a chunk topic grounds a test key (exact or via LabTest.topic)."""
    if not chunk_topic or not test_key:
        return False
    if chunk_topic == test_key:
        return True
    test = TESTS_BY_KEY.get(test_key)
    return test is not None and chunk_topic == test.topic


class MockAIProvider(AIProvider):
    """Offline deterministic provider (default in development)."""

    name = "mock"
    model = "medilens-rule-engine"

    def __init__(self, embedding_dimensions: int = 512) -> None:
        self.embedding_dimensions = embedding_dimensions

    # ------------------------------------------------------------------
    # Embeddings: deterministic signed-hash vectors
    # ------------------------------------------------------------------
    async def create_embedding(self, texts: list[str]) -> list[list[float]]:
        return [self._hash_embedding(text) for text in texts]

    def _hash_embedding(self, text: str) -> list[float]:
        dim = self.embedding_dimensions
        vector = [0.0] * dim
        normalized = re.sub(r"[^a-z0-9\u0900-\u097f\s]", " ", text.lower())
        words = [w for w in normalized.split() if w]
        tokens: list[str] = list(words)
        for word in words:
            padded = f"^{word}$"
            for i in range(len(padded) - 2):
                tokens.append(padded[i : i + 3])
        for token in tokens:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm > 0:
            vector = [value / norm for value in vector]
        return vector

    # ------------------------------------------------------------------
    # Structured analysis
    # ------------------------------------------------------------------
    async def generate_structured_analysis(self, request: AnalysisRequest) -> StructuredAnalysis:
        language = request.language if request.language in TEMPLATES else "en"
        strings = TEMPLATES[language]
        detailed = request.explanation_level == "detailed"

        within = [v for v in request.values if v.status == "within"]
        below = [v for v in request.values if v.status == "below"]
        above = [v for v in request.values if v.status == "above"]
        unknown = [v for v in request.values if v.status == "unknown"]

        topics = self._topics_for([*below, *above])
        chunk_by_topic = self._best_chunks_by_topic(request.retrieved)
        sources, source_id_by_chunk = self._build_source_index(request.retrieved)

        abnormal_results: list[AbnormalResultItem] = []
        for value in [*below, *above]:
            used_ids = self._source_ids_for_topic(value.test_key, chunk_by_topic, source_id_by_chunk)
            abnormal_results.append(
                AbnormalResultItem(
                    test_name=value.test_name,
                    value=f"{value.value_display} {value.unit}".strip(),
                    unit=value.unit,
                    reference_range=ReferenceRangeOut(
                        low=value.ref_low, high=value.ref_high, text=value.range_display
                    ),
                    status="below" if value.status == "below" else "above",
                    explanation=self._explanation_for(value, strings, chunk_by_topic),
                    source_ids=used_ids,
                    confidence=value.confidence,
                    low_confidence=value.confidence < 0.7,
                )
            )

        normal_results = [
            NormalResultItem(
                test_name=value.test_name,
                value=f"{value.value_display} {value.unit}".strip(),
                unit=value.unit,
                reference_range=ReferenceRangeOut(
                    low=value.ref_low, high=value.ref_high, text=value.range_display
                ),
            )
            for value in within
        ]

        test_explanations = [
            self._test_explanation(
                value,
                strings,
                detailed=detailed,
                chunk_by_topic=chunk_by_topic,
                source_id_by_chunk=source_id_by_chunk,
            )
            for value in request.values
        ]

        educational: list[EducationalExplanation] = []
        for topic in topics:
            chunk = _chunk_for_test_key(chunk_by_topic, topic)
            test_key = topic  # topics use the primary test key
            label = TESTS_BY_KEY[test_key].name if test_key in TESTS_BY_KEY else topic
            if chunk is not None:
                text = f"{label}: {_first_sentences(chunk.content)}"
                source_ids = [source_id_by_chunk[chunk.chunk_id]] if chunk.chunk_id in source_id_by_chunk else []
            else:
                text = self._generic_explanation_for_key(test_key, strings)
                source_ids = []
            educational.append(
                EducationalExplanation(topic=label, text=text, source_ids=source_ids)
            )

        # 3–7 questions for the doctor: per-abnormal-value questions first,
        # padded with generic ones so at least 3 are always present.
        questions = [
            strings["question"].format(test=value.test_name)
            for value in [*below, *above][:5]
        ]
        for key in ("question_generic_1", "question_generic_2", "question_generic_3"):
            if len(questions) >= 3:
                break
            generic = strings[key]
            if generic not in questions:
                questions.append(generic)
        questions = questions[:7]

        limitations = list(strings["limitations"])
        if unknown:
            limitations.append(strings["unknown_limitation"].format(n=len(unknown)))
        if request.low_confidence_count:
            limitations.append(strings["low_conf_note"])

        retrieval_note = None
        if not request.retrieved:
            retrieval_note = strings["no_evidence"]

        summary = self._build_summary(
            strings,
            total=len(request.values),
            within=len(within),
            below=len(below),
            above=len(above),
            unknown=len(unknown),
            abnormal_names=[value.test_name for value in [*below, *above]],
        )

        return StructuredAnalysis(
            summary=summary,
            report_type=request.report_type,
            explanation_level=request.explanation_level,
            abnormal_results=abnormal_results,
            normal_results=normal_results,
            test_explanations=test_explanations,
            educational_explanations=educational,
            questions_for_doctor=questions,
            limitations=limitations,
            sources=sources,
            retrieval_note=retrieval_note,
        )

    def _build_summary(
        self,
        strings: dict[str, str],
        *,
        total: int,
        within: int,
        below: int,
        above: int,
        unknown: int,
        abnormal_names: list[str],
    ) -> str:
        parts = [strings["summary_intro"].format(n=total)]
        parts.append(
            strings["summary_counts"].format(
                within=within, below=below, above=above, unknown=unknown
            )
        )
        if abnormal_names:
            parts.append(strings["summary_abnormal"].format(list=", ".join(abnormal_names)))
        parts.append(strings["summary_caveat"])
        return " ".join(parts)

    def _topics_for(self, values: list[ValueContext]) -> list[str]:
        topics: list[str] = []
        for value in values:
            key = value.test_key
            if key and key not in topics:
                topics.append(key)
        return topics

    def _best_chunks_by_topic(
        self, retrieved: list[RetrievedChunk]
    ) -> dict[str, RetrievedChunk]:
        best: dict[str, RetrievedChunk] = {}
        for chunk in retrieved:
            topic = chunk.topic or ""
            if topic and (topic not in best or chunk.score > best[topic].score):
                best[topic] = chunk
        return best

    def _build_source_index(
        self, retrieved: list[RetrievedChunk]
    ) -> tuple[list[SourceReference], dict[str, int]]:
        sources: list[SourceReference] = []
        index: dict[str, int] = {}
        for chunk in retrieved:
            key = chunk.url or chunk.title
            if key in {s.url or s.title for s in sources}:
                continue
            source_id = len(sources) + 1
            sources.append(
                SourceReference(
                    id=source_id,
                    title=chunk.title,
                    source_org=chunk.source_org,
                    url=chunk.url,
                    publication_date=chunk.publication_date,
                )
            )
            index[chunk.chunk_id] = source_id
        return sources, index

    def _source_ids_for_topic(
        self,
        test_key: str | None,
        chunk_by_topic: dict[str, RetrievedChunk],
        source_id_by_chunk: dict[str, int],
    ) -> list[int]:
        if not test_key:
            return []
        chunk = _chunk_for_test_key(chunk_by_topic, test_key)
        if chunk and chunk.chunk_id in source_id_by_chunk:
            return [source_id_by_chunk[chunk.chunk_id]]
        return []

    def _generic_explanation_for_key(self, test_key: str, strings: dict[str, str]) -> str:
        test = TESTS_BY_KEY.get(test_key)
        description = test.description if test else strings["generic_test_description"]
        return f"{description} {strings['factors']}"

    def _test_explanation(
        self,
        value: ValueContext,
        strings: dict[str, str],
        *,
        detailed: bool,
        chunk_by_topic: dict[str, RetrievedChunk],
        source_id_by_chunk: dict[str, int],
    ) -> TestExplanationItem:
        """One explanation entry for a single detected value (every value gets one).

        Simple level: a short measured-value line. Detailed level: the same line
        plus background factors, and the knowledge-base wording when available.
        """
        test = TESTS_BY_KEY.get(value.test_key or "")
        chunk = _chunk_for_test_key(chunk_by_topic, value.test_key)
        if chunk is not None and (detailed or test is None):
            what_it_measures = _first_sentences(chunk.content, count=1, max_chars=280)
        elif test is not None:
            what_it_measures = test.description
        elif chunk is not None:
            what_it_measures = _first_sentences(chunk.content, count=1, max_chars=280)
        else:
            what_it_measures = strings["generic_test_description"]

        status_key = (
            f"status_{value.status}"
            if value.status in {"within", "below", "above"}
            else "status_unknown"
        )
        meaning = _collapse(
            strings[status_key].format(
                name=value.test_name,
                value=value.value_display,
                unit=value.unit or "",
                range=value.range_display,
            )
        )
        if detailed:
            meaning = f"{meaning} {strings['factors']}"

        return TestExplanationItem(
            test_name=value.test_name,
            test_key=value.test_key,
            status=value.status,
            what_it_measures=what_it_measures,
            meaning=meaning,
            general_factors=strings["factors"] if detailed else None,
            discuss=strings["question_generic_1"],
            source_ids=self._source_ids_for_topic(
                value.test_key, chunk_by_topic, source_id_by_chunk
            ),
        )

    def _explanation_for(
        self, value: ValueContext, strings: dict[str, str], chunk_by_topic: dict[str, RetrievedChunk]
    ) -> str:
        parts: list[str] = []
        chunk = _chunk_for_test_key(chunk_by_topic, value.test_key)
        if chunk is not None:
            parts.append(_first_sentences(chunk.content, count=2))
        else:
            test = TESTS_BY_KEY.get(value.test_key or "")
            if test:
                parts.append(test.description)
        parts.append(strings["range_below" if value.status == "below" else "range_above"])
        parts.append(strings["factors"])
        return " ".join(parts)

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------
    async def generate_chat_response(self, request: ChatRequest) -> ChatAnswer:
        language = request.language if request.language in TEMPLATES else "en"
        strings = TEMPLATES[language]

        if detect_blocked_intent(request.message):
            content = refusal_answer(language)
            if detect_urgent(request.message):
                content = f"{content}\n\n{EMERGENCY_ADDENDUM.get(language, EMERGENCY_ADDENDUM['en'])}"
            return ChatAnswer(content=content, sources=[], safety_blocked=True)

        report = request.report_context or {}

        if OUT_OF_RANGE_QUERY.search(request.message) and report:
            return ChatAnswer(content=self._answer_out_of_range(report, strings))

        if report and report.get("available"):
            meaning = self._answer_test_meaning(request, strings)
            if meaning is not None:
                return meaning

        if not report.get("available") and not request.retrieved:
            return ChatAnswer(
                content=f"{strings['chat_no_report']}\n\n{strings['chat_footer']}"
            )

        return self._answer_from_retrieval(request, strings)

    def _answer_out_of_range(self, report: dict[str, Any], strings: dict[str, str]) -> str:
        outside = report.get("values_outside", [])
        if not outside:
            body = strings["chat_out_of_range_none"]
        else:
            lines = [
                f"• {item['test_name']}: {item['value']} {item.get('unit') or ''} "
                f"({strings['range_above' if item['status'] == 'above' else 'range_below']})"
                for item in outside[:10]
            ]
            body = "\n".join([strings["chat_out_of_range_header"], *lines, strings["chat_out_of_range_footer"]])
        return f"{body}\n\n{strings['chat_footer']}"

    def _answer_test_meaning(
        self, request: ChatRequest, strings: dict[str, str]
    ) -> ChatAnswer | None:
        message = request.message.lower()
        matched_key: str | None = None
        for alias, test in ALIAS_INDEX:
            if re.search(rf"\b{re.escape(alias)}\b", message):
                matched_key = test.key
                break
        if matched_key is None:
            for chunk in request.retrieved:
                if chunk.topic and chunk.topic in message:
                    matched_key = chunk.topic
                    break
        if matched_key is None:
            return None

        test = TESTS_BY_KEY.get(matched_key)
        report = request.report_context or {}
        value_line = ""
        for item in report.get("values", []):
            if item.get("test_key") == matched_key:
                value_line = _collapse(
                    strings["value_line"].format(
                        name=item["test_name"],
                        value=item["value"],
                        unit=item.get("unit") or "",
                        range=item.get("reference_range") or "—",
                    )
                )
                break

        chunk = next(
            (
                c
                for c in request.retrieved
                if _topic_serves_key(c.topic, matched_key)
            ),
            None,
        )
        if chunk is not None:
            body_text = _first_sentences(chunk.content)
            sources = [
                {
                    "title": chunk.title,
                    "source_org": chunk.source_org,
                    "url": chunk.url,
                    "topic": chunk.topic,
                    "snippet": _first_sentences(chunk.content, count=1, max_chars=220),
                }
            ]
        else:
            description = test.description if test else ""
            body_text = f"{description} {strings['factors']}".strip()
            if not body_text:
                return ChatAnswer(content=f"{strings['chat_no_kb']}\n\n{strings['chat_footer']}")
            sources = []

        parts = [strings["chat_meaning_header"], body_text]
        if value_line:
            parts.append(value_line)
        parts.append(strings["chat_footer"])
        return ChatAnswer(content="\n\n".join(parts), sources=sources)

    def _answer_from_retrieval(self, request: ChatRequest, strings: dict[str, str]) -> ChatAnswer:
        if not request.retrieved:
            return ChatAnswer(content=f"{strings['chat_no_kb']}\n\n{strings['chat_footer']}")
        chunk = request.retrieved[0]
        body = _first_sentences(chunk.content)
        sources = [
            {
                "title": chunk.title,
                "source_org": chunk.source_org,
                "url": chunk.url,
                "topic": chunk.topic,
                "snippet": _first_sentences(chunk.content, count=1, max_chars=220),
            }
        ]
        # Attach the relevant user values when the chunk topic matches them.
        report = request.report_context or {}
        related = [
            item
            for item in report.get("values", [])
            if _topic_serves_key(chunk.topic, item.get("test_key"))
        ]
        extra = ""
        if related:
            item = related[0]
            extra = "\n\n" + _collapse(
                strings["value_line"].format(
                    name=item["test_name"],
                    value=item["value"],
                    unit=item.get("unit") or "",
                    range=item.get("reference_range") or "—",
                )
            )
        return ChatAnswer(content=f"{body}{extra}\n\n{strings['chat_footer']}", sources=sources)
