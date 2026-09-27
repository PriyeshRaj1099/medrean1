"""Localized one-line progress messages for the report processing pipeline.

These are the staged loading messages shown while a report is processed
(spec: "During analysis display"). They are published through
``Report.status_message`` and surfaced by ``GET /api/reports/{id}/status``.

Honesty rule: a message is only ever set while the system is genuinely doing
that work — "Finding laboratory values..." is set just before the parser runs,
"Checking the reference ranges..." just before values are compared against
their printed ranges, and so on. See the callers in
``backend/jobs/pipeline.py`` and ``backend/services/analysis_service.py``.
"""

from __future__ import annotations

# Stage keys in the order the work happens (and the order users see them).
STAGES: tuple[str, ...] = (
    "reading",
    "finding_values",
    "checking_ranges",
    "preparing_explanation",
    "finalizing",
)

STAGE_MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "reading": "Reading your report...",
        "finding_values": "Finding laboratory values...",
        "checking_ranges": "Checking the reference ranges...",
        "preparing_explanation": "Preparing your explanation...",
        "finalizing": "Finalizing your report...",
    },
    "hi": {
        "reading": "आपकी रिपोर्ट पढ़ी जा रही है...",
        "finding_values": "रिपोर्ट में दिए मान खोजे जा रहे हैं...",
        "checking_ranges": "संदर्भ सीमाओं से तुलना की जा रही है...",
        "preparing_explanation": "आपके लिए आसान भाषा में समझ तैयार की जा रही है...",
        "finalizing": "आपकी रिपोर्ट को अंतिम रूप दिया जा रहा है...",
    },
    "hinglish": {
        "reading": "Aapki report padhi ja rahi hai...",
        "finding_values": "Report mein di gayi values dhoondhi ja rahi hain...",
        "checking_ranges": "Reference ranges se tulna ki ja rahi hai...",
        "preparing_explanation": "Aapke liye aasan bhasha mein samajh taiyaar ki ja rahi hai...",
        "finalizing": "Aapki report ko final kiya ja raha hai...",
    },
    "bn": {
        "reading": "আপনার রিপোর্ট পড়া হচ্ছে...",
        "finding_values": "রিপোর্টে থাকা মান খোঁজা হচ্ছে...",
        "checking_ranges": "রেফারেন্স সীমার সঙ্গে মিলিয়ে দেখা হচ্ছে...",
        "preparing_explanation": "আপনার জন্য সহজ ভাষায় ব্যাখ্যা তৈরি করা হচ্ছে...",
        "finalizing": "আপনার রিপোর্ট চূড়ান্ত করা হচ্ছে...",
    },
    "ta": {
        "reading": "உங்கள் அறிக்கை படிக்கப்படுகிறது...",
        "finding_values": "அறிக்கையில் உள்ள மதிப்புகள் தேடப்படுகின்றன...",
        "checking_ranges": "குறிப்பு வரம்புகளுடன் ஒப்பிடப்படுகிறது...",
        "preparing_explanation": "உங்களுக்காக எளிய மொழியில் விளக்கம் தயாராகிறது...",
        "finalizing": "உங்கள் அறிக்கை இறுதி செய்யப்படுகிறது...",
    },
    "te": {
        "reading": "మీ నివేదిక చదవబడుతోంది...",
        "finding_values": "నివేదికలోని విలువలు వెతకబడుతున్నాయి...",
        "checking_ranges": "సూచన పరిధులతో పోల్చబడుతోంది...",
        "preparing_explanation": "మీ కోసం సరళమైన భాషలో వివరణ సిద్ధమవుతోంది...",
        "finalizing": "మీ నివేదిక ఖరారు చేయబడుతోంది...",
    },
    "mr": {
        "reading": "तुमचा अहवाल वाचला जात आहे...",
        "finding_values": "अहवालातील मूल्ये शोधली जात आहेत...",
        "checking_ranges": "संदर्भ मर्यादांशी तुलना केली जात आहे...",
        "preparing_explanation": "तुमच्यासाठी सोप्या भाषेत समज तयार केली जात आहे...",
        "finalizing": "तुमचा अहवाल अंतिम केला जात आहे...",
    },
    "gu": {
        "reading": "તમારો રિપોર્ટ વાંચવામાં આવી રહ્યો છે...",
        "finding_values": "રિપોર્ટમાંનાં મૂલ્યો શોધવામાં આવી રહ્યાં છે...",
        "checking_ranges": "સંદર્ભ મર્યાદાઓ સાથે સરખામણી કરવામાં આવી રહી છે...",
        "preparing_explanation": "તમારા માટે સરળ ભાષામાં સમજણ તૈયાર કરવામાં આવી રહી છે...",
        "finalizing": "તમારો રિપોર્ટ અંતિમ કરવામાં આવી રહ્યો છે...",
    },
    "kn": {
        "reading": "ನಿಮ್ಮ ವರದಿಯನ್ನು ಓದಲಾಗುತ್ತಿದೆ...",
        "finding_values": "ವರದಿಯಲ್ಲಿನ ಮೌಲ್ಯಗಳನ್ನು ಹುಡುಕಲಾಗುತ್ತಿದೆ...",
        "checking_ranges": "ಉಲ್ಲೇಖ ಶ್ರೇಣಿಗಳೊಂದಿಗೆ ಹೋಲಿಸಲಾಗುತ್ತಿದೆ...",
        "preparing_explanation": "ನಿಮಗಾಗಿ ಸರಳ ಭಾಷೆಯಲ್ಲಿ ವಿವರಣೆ ಸಿದ್ಧಪಡಿಸಲಾಗುತ್ತಿದೆ...",
        "finalizing": "ನಿಮ್ಮ ವರದಿಯನ್ನು ಅಂತಿಮಗೊಳಿಸಲಾಗುತ್ತಿದೆ...",
    },
    "ml": {
        "reading": "നിങ്ങളുടെ റിപ്പോർട്ട് വായിക്കുന്നു...",
        "finding_values": "റിപ്പോർട്ടിലെ മൂല്യങ്ങൾ കണ്ടെത്തുന്നു...",
        "checking_ranges": "റഫറൻസ് പരിധികളുമായി താരതമ്യം ചെയ്യുന്നു...",
        "preparing_explanation": "നിങ്ങൾക്കായി ലളിതമായ ഭാഷയിൽ വിശദീകരണം തയ്യാറാക്കുന്നു...",
        "finalizing": "നിങ്ങളുടെ റിപ്പോർട്ട് അന്തിമമാക്കുന്നു...",
    },
    "pa": {
        "reading": "ਤੁਹਾਡੀ ਰਿਪੋਰਟ ਪੜ੍ਹੀ ਜਾ ਰਹੀ ਹੈ...",
        "finding_values": "ਰਿਪੋਰਟ ਵਿੱਚ ਦਿੱਤੇ ਮੁੱਲ ਲੱਭੇ ਜਾ ਰਹੇ ਹਨ...",
        "checking_ranges": "ਹਵਾਲਾ ਸੀਮਾਵਾਂ ਨਾਲ ਤੁਲਨਾ ਕੀਤੀ ਜਾ ਰਹੀ ਹੈ...",
        "preparing_explanation": "ਤੁਹਾਡੇ ਲਈ ਸੌਖੀ ਭਾਸ਼ਾ ਵਿੱਚ ਸਮਝ ਤਿਆਰ ਕੀਤੀ ਜਾ ਰਹੀ ਹੈ...",
        "finalizing": "ਤੁਹਾਡੀ ਰਿਪੋਰਟ ਨੂੰ ਅੰਤਿਮ ਰੂਪ ਦਿੱਤਾ ਜਾ ਰਿਹਾ ਹੈ...",
    },
    "or": {
        "reading": "ଆପଣଙ୍କ ରିପୋର୍ଟ ପଢ଼ାଯାଉଛି...",
        "finding_values": "ରିପୋର୍ଟରେ ଥିବା ମୂଲ୍ୟଗୁଡ଼ିକ ଖୋଜାଯାଉଛି...",
        "checking_ranges": "ସନ୍ଦର୍ଭ ସୀମା ସହିତ ତୁଳନା କରାଯାଉଛି...",
        "preparing_explanation": "ଆପଣଙ୍କ ପାଇଁ ସରଳ ଭାଷାରେ ବ୍ୟାଖ୍ୟା ପ୍ରସ୍ତୁତ କରାଯାଉଛି...",
        "finalizing": "ଆପଣଙ୍କ ରିପୋର୍ଟ ଅନ୍ତିମ କରାଯାଉଛି...",
    },
    "as": {
        "reading": "আপোনাৰ ৰিপৰ্ট পঢ়া হৈছে...",
        "finding_values": "ৰিপৰ্টত থকা মানবোৰ বিচাৰি উলিওৱা হৈছে...",
        "checking_ranges": "প্ৰসংগ পৰিসীমাৰ সৈতে তুলনা কৰা হৈছে...",
        "preparing_explanation": "আপোনাৰ বাবে সৰল ভাষাত ব্যাখ্যা প্ৰস্তুত কৰা হৈছে...",
        "finalizing": "আপোনাৰ ৰিপৰ্ট চূড়ান্ত কৰা হৈছে...",
    },
    "ur": {
        "reading": "آپ کی رپورٹ پڑھی جا رہی ہے...",
        "finding_values": "رپورٹ میں دی گئی قدریں تلاش کی جا رہی ہیں...",
        "checking_ranges": "حوالہ حدود سے موازنہ کیا جا رہا ہے...",
        "preparing_explanation": "آپ کے لیے آسان زبان میں وضاحت تیار کی جا رہی ہے...",
        "finalizing": "آپ کی رپورٹ حتمی شکل دی جا رہی ہے...",
    },
}


def stage_message(language: str, stage: str) -> str:
    """The localized progress message for one pipeline stage.

    Unknown languages fall back to English; unknown stages are a programming
    error and raise ``KeyError``.
    """
    catalog = STAGE_MESSAGES.get(language, STAGE_MESSAGES["en"])
    return catalog[stage]
