"""Safety guardrails: block diagnosis/treatment requests and unsafe outputs.

This is a defense-in-depth layer that sits in front of (and after) any AI
provider. It implements the application's medical-safety requirement:
explain laboratory results educationally — never diagnose, prescribe, or
provide emergency treatment instructions.

It also implements prompt-injection protection: text extracted from uploaded
documents, private notes and chat messages is DATA, never instructions. Such
text is detected, logged, neutralized and fenced inside
``<untrusted_document_text>`` blocks that the system prompts forbid following.
Finally it sanitizes provider output (markup stripping, length clamps, list
caps) before anything is stored or shown.
"""

from __future__ import annotations

import re

from backend.schemas.analysis import StructuredAnalysis

# Requests that would amount to asking the tool to act as a doctor.
BLOCKED_INTENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(do i have|have i got|is it (cancer|diabetes|tb|t\.b|covid|aids|hiv|leukemia|leukaemia))\b", re.I),
    re.compile(r"\b(diagnos(e|is|e me|e my))\b", re.I),
    re.compile(r"\b(prescrib|medicine|medication|tablet|dawa|dawai|pill|drug|antibiotic)\b", re.I),
    re.compile(r"\b(what should i (take|eat)|kaun si dawa|konsi dawa|kaunsi dawa)\b", re.I),
    re.compile(r"\b(dosage|dose|kitni dawa|kitna dose)\b", re.I),
    re.compile(r"\b(stop taking|should i stop|band kar du|chhod du)\b", re.I),
    re.compile(r"\b(cure|ilaaj|ilaj|treat(ment)? for|upchar)\b", re.I),
    re.compile(r"\b(how (do|can) i (cure|treat))\b", re.I),
    re.compile(r"(क्या मुझे|मुझे कौन सी दवा|दवा|इलाज|उपचार|निदान|डॉक्टर कौन सी दवा)", re.I),
)

# Urgent phrasing: never triage — direct to emergency services.
URGENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(emergency|urgent|severe pain|chest pain|can'?t breathe|bleeding heavily|unconscious|fainted)\b", re.I),
    re.compile(r"(आपातकाल|गंभीर दर्द|सीने में दर्द|सांस नहीं|बहुत खून)", re.I),
)

# Output filters: catch a provider drifting into diagnosis/prescription language.
UNSAFE_OUTPUT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\byou (definitely |likely |probably )?have (a |an |been diagnosed with )?[a-z]", re.I),
    re.compile(r"\byou are suffering from\b", re.I),
    re.compile(r"\bthis (confirms|proves) (that )?you have\b", re.I),
    re.compile(r"\byou should (take|start taking|stop taking|use) [a-z0-9]", re.I),
    re.compile(r"\btake \d+\s*(mg|ml|mcg)\b", re.I),
    re.compile(r"\bstop taking your\b", re.I),
    re.compile(r"\byour diagnosis is\b", re.I),
    re.compile(r"\byou (must|need to) start (medication|treatment)\b", re.I),
    re.compile(r"\b(aapko|tumhe) [a-z]* ?(dawa|tablet) (lena|khana)\b", re.I),
)

# ---------------------------------------------------------------------------
# Prompt-injection protection: documents are DATA, never instructions
# ---------------------------------------------------------------------------

# Every block of untrusted text (document values, private notes, chat
# questions) is placed inside this fence; the system prompts explicitly
# forbid following anything found between the markers.
UNTRUSTED_FENCE_OPEN = "<untrusted_document_text>"
UNTRUSTED_FENCE_CLOSE = "</untrusted_document_text>"

# Text that tries to override the assistant's rules, change its role or
# extract its prompt. Applied to untrusted inputs (document values, notes and
# chat messages) so the attempt is detected, logged and answered safely.
INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(ignore|disregard|forget)\s+(all\s+|any\s+|the\s+|your\s+){0,2}"
        r"(previous|prior|above|earlier|system)?\s*(instructions?|rules?|prompts?|context)\b",
        re.I,
    ),
    re.compile(r"\bforget\s+everything\b", re.I),
    re.compile(
        r"\b(reveal|show|print|repeat|output|display|translate)\s+(me\s+)?"
        r"(your|the)\s+(system\s+prompt|hidden\s+prompt|initial\s+prompt|instructions)\b",
        re.I,
    ),
    re.compile(r"\byou\s+are\s+now\b|\bfrom\s+now\s+on\s+you\b", re.I),
    re.compile(
        r"\bact\s+as\s+(a|an|the)\s+"
        r"(doctor|admin|administrator|system|developer|unrestricted|unfiltered)",
        re.I,
    ),
    re.compile(r"\b(jailbreak|dan\s+mode|developer\s+mode|do\s+anything\s+now)\b", re.I),
    re.compile(r"(^|\n)\s*(system|assistant|developer)\s*(prompt\s*)?:", re.I),
    re.compile(r"\bnew\s+(system\s+)?instructions?\s*:", re.I),
    re.compile(
        r"</?\s*(system|assistant|instructions?|untrusted_document_text)[^>\n]{0,40}>", re.I
    ),
    re.compile(
        r"(अनदेखा|नज़रअंदाज़|नजरअंदाज).{0,24}(निर्देश|हिदायत|नियम)"
        r"|(निर्देश|हिदायत|नियम).{0,24}(अनदेखा|नज़रअंदाज़|नजरअंदाज)",
        re.I,
    ),
)

_FENCE_RE = re.compile(r"<\s*/?\s*untrusted_document_text[^>\n]{0,40}>", re.I)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)
_HTML_LIKE_RE = re.compile(r"</?[a-zA-Z][^>\n]{0,200}>")
_ACTIVE_URL_RE = re.compile(r"\b(?:javascript|vbscript|data)\s*:", re.I)


def looks_like_prompt_injection(text: str | None) -> bool:
    """True when untrusted text tries to override the assistant's rules."""
    if not text:
        return False
    return any(pattern.search(text) for pattern in INJECTION_PATTERNS)


def _strip_invisible(text: str) -> str:
    return _CONTROL_RE.sub("", text).translate(_ZERO_WIDTH)


def sanitize_untrusted_text(text: str | None, *, max_length: int = 4000) -> str:
    """Neutralize untrusted text before it is embedded in a prompt.

    Removes control and zero-width characters, defuses any attempt to close or
    reopen the untrusted-data fence, and clamps the length. Content is never
    rewritten based on suspicion — detection happens separately via
    :func:`looks_like_prompt_injection`.
    """
    if not text:
        return ""
    cleaned = _strip_invisible(str(text))
    cleaned = _FENCE_RE.sub("[fence removed]", cleaned)
    return cleaned[:max_length].rstrip()


def clean_model_text(text: str | None, *, max_length: int = 4000) -> str:
    """Defense-in-depth cleanup of model-produced text before storage/display."""
    if not text:
        return ""
    cleaned = _strip_invisible(str(text))
    cleaned = _HTML_LIKE_RE.sub("", cleaned)
    cleaned = _ACTIVE_URL_RE.sub("", cleaned)
    cleaned = cleaned.strip()
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip() + "…"
    return cleaned

SAFE_REFUSAL = {
    "en": (
        "I can't diagnose conditions, recommend or prescribe medicines, or advise you to "
        "start or stop any treatment — that belongs to a qualified healthcare professional "
        "who can consider your symptoms, history and examination. I can explain what the "
        "values on your report generally measure and suggest questions you may want to ask "
        "your doctor. If you feel unwell or this is urgent, please contact your local "
        "emergency services."
    ),
    "hi": (
        "मैं बीमारी का निदान नहीं कर सकता, दवा नहीं बता सकता और किसी इलाज को शुरू या बंद करने की "
        "सलाह नहीं दे सकता — यह किसी योग्य डॉक्टर का काम है, जो आपके लक्षणों, इतिहास और जांच को "
        "देख सकते हैं। मैं यह समझा सकता हूं कि आपकी रिपोर्ट में दिए मान सामान्यतः क्या मापते हैं "
        "और आप डॉक्टर से क्या प्रश्न पूछ सकते हैं। अगर आप अस्वस्थ महसूस कर रहे हैं या स्थिति "
        "आपातकालीन है, तो तुरंत स्थानीय आपातकालीन सेवा से संपर्क करें।"
    ),
    "hinglish": (
        "Main bimari ka diagnosis nahi kar sakta, dawa recommend nahi kar sakta, aur kisi "
        "treatment ko shuru ya band karne ki salah nahi de sakta — yeh ek qualified doctor ka "
        "kaam hai jo aapke symptoms, history aur examination ko dekh sakte hain. Main yeh "
        "samjha sakta hoon ki aapki report ki values generally kya measure karti hain aur aap "
        "doctor se kaunse sawaal pooch sakte hain. Agar aap ya is situation urgent hai, to "
        "turant apni local emergency services se sampark karein."
    ),
    "bn": (
        "আমি রোগ নির্ণয় করতে পারি না, ওষুধের সুপারিশ বা প্রেসক্রিপশন করতে পারি না, এবং কোনো "
        "চিকিৎসা শুরু বা বন্ধ করার পরামর্শ দিতে পারি না — এটি একজন যোগ্য চিকিৎসকের কাজ, যিনি "
        "আপনার লক্ষণ, ইতিহাস ও পরীক্ষা বিবেচনা করতে পারেন। আমি ব্যাখ্যা করতে পারি আপনার "
        "রিপোর্টের মানগুলি সাধারণত কী মাপে এবং আপনি চিকিৎসককে কী প্রশ্ন করতে পারেন। যদি আপনার "
        "অসুস্থ লাগে বা এটি আপাতকালীন হয়, অনুগ্রহ করে অবিলম্বে স্থানীয় আপাতকালীন সেবায় "
        "যোগাযোগ করুন।"
    ),
    "ta": (
        "என்னால் நோய் கண்டறிய முடியாது, மருந்துகளை பரிந்துரைக்கவோ எழுதவோ முடியாது, எந்த "
        "சிகிச்சையையும் தொடங்கவோ நிறுத்தவோ ஆலோசனை வழங்க முடியாது — இது தகுதியான மருத்துவ "
        "நிபுணரின் பணி, அவர்கள் உங்கள் அறிகுறிகள், வரலாறு மற்றும் பரிசோதனையை கருத்தில் "
        "கொள்ள முடியும். உங்கள் அறிக்கையில் உள்ள மதிப்புகள் பொதுவாக என்ன அளக்கின்றன என்பதை "
        "விளக்கவும், நீங்கள் மருத்துவரிடம் என்ன கேள்விகள் கேட்கலாம் என்பதையும் என்னால் சொல்ல "
        "முடியும். உங்களுக்கு உடல்நலக்குறைவு ஏற்பட்டால் அல்லது இது அவசரநிலை என்றால், "
        "உடனடியாக உங்கள் உள்ளூர் அவசர சேவைகளை தொடர்பு கொள்ளுங்கள்."
    ),
    "te": (
        "నేను వ్యాధిని నిర్ధారించలేను, మందులను సిఫార్సు చేయలేను లేదా ప్రిస్క్రైబ్ చేయలేను, "
        "ఏదైనా చికిత్సను ప్రారంభించమని లేదా ఆపమని సలహా ఇవ్వలేను — అది మీ లక్షణాలు, "
        "చరిత్ర మరియు పరీక్షను పరిగణించగల అర్హత కలిగిన వైద్య నిపుణుడి పని. మీ నివేదికలోని "
        "విలువలు సాధారణంగా ఏమి కొలుస్తాయో నేను వివరించగలను మరియు మీరు వైద్యుడిని ఏ ప్రశ్నలు "
        "అడగవచ్చో సూచించగలను. మీకు అనారోగ్యంగా అనిపిస్తే లేదా ఇది అత్యవసరమైతే, దయచేసి "
        "వెంటనే మీ స్థానిక అత్యవసర సేవలను సంప్రదించండి."
    ),
    "mr": (
        "मी आजाराचे निदान करू शकत नाही, औषधे सुचवू किंवा लिहू शकत नाही आणि कोणताही उपचार "
        "सुरू किंवा बंद करण्याचा सल्ला देऊ शकत नाही — हे पात्र डॉक्टरांचे काम आहे, जे तुमची "
        "लक्षणे, इतिहास आणि तपासणी विचारात घेऊ शकतात. तुमच्या अहवालातील मूल्ये सामान्यतः काय "
        "मोजतात हे मी समजावून सांगू शकतो आणि तुम्ही डॉक्टरांना कोणते प्रश्न विचारू शकता हे "
        "सांगू शकतो. तुम्हाला अस्वस्थ वाटत असल्यास किंवा ही आपत्कालीन स्थिती असल्यास, कृपया "
        "त्वरित तुमच्या स्थानिक आपत्कालीन सेवांशी संपर्क साधा."
    ),
    "gu": (
        "હું રોગનું નિદાન કરી શકતો નથી, દવાઓ સૂચવી કે લખી શકતો નથી, અને કોઈપણ સારવાર "
        "શરૂ કે બંધ કરવાની સલાહ આપી શકતો નથી — તે લાયક ડૉક્ટરનું કામ છે, જે તમારા લક્ષણો, "
        "ઇતિહાસ અને તપાસ ધ્યાનમાં લઈ શકે. તમારા રિપોર્ટનાં મૂલ્યો સામાન્ય રીતે શું માપે છે "
        "તે હું સમજાવી શકું છું અને તમે ડૉક્ટરને કયા પ્રશ્નો પૂછી શકો તે કહી શકું છું. જો "
        "તમને અસ્વસ્થતા લાગે અથવા આ કટોકટી હોય, તો કૃપા કરીને તરત જ તમારી સ્થાનિક "
        "ઇમરજન્સી સેવાઓનો સંપર્ક કરો."
    ),
    "kn": (
        "ನಾನು ರೋಗನಿರ್ಣಯ ಮಾಡಲಾರೆ, ಔಷಧಿಗಳನ್ನು ಶಿಫಾರಸು ಮಾಡಲಾರೆ ಅಥವಾ ಲಿಖಿಸಲಾರೆ, ಯಾವುದೇ "
        "ಚಿಕಿತ್ಸೆಯನ್ನು ಪ್ರಾರಂಭಿಸಲು ಅಥವಾ ನಿಲ್ಲಿಸಲು ಸಲಹೆ ನೀಡಲಾರೆ — ಅದು ನಿಮ್ಮ ಲಕ್ಷಣಗಳು, "
        "ಇತಿಹಾಸ ಮತ್ತು ಪರೀಕ್ಷೆಯನ್ನು ಪರಿಗಣಿಸಬಲ್ಲ ಅರ್ಹ ವೈದ್ಯರ ಕೆಲಸ. ನಿಮ್ಮ ವರದಿಯಲ್ಲಿನ ಮೌಲ್ಯಗಳು "
        "ಸಾಮಾನ್ಯವಾಗಿ ಏನನ್ನು ಅಳೆಯುತ್ತವೆ ಎಂಬುದನ್ನು ನಾನು ವಿವರಿಸಬಲ್ಲೆ ಮತ್ತು ನೀವು ವೈದ್ಯರನ್ನು ಯಾವ "
        "ಪ್ರಶ್ನೆಗಳನ್ನು ಕೇಳಬಹುದು ಎಂದು ಸೂಚಿಸಬಲ್ಲೆ. ನಿಮಗೆ ಅಸ್ವಸ್ಥವೆನಿಸಿದರೆ ಅಥವಾ ಇದು ತುರ್ತು "
        "ಪರಿಸ್ಥಿತಿಯಾದರೆ, ದಯವಿಟ್ಟು ತಕ್ಷಣ ನಿಮ್ಮ ಸ್ಥಳೀಯ ತುರ್ತು ಸೇವೆಗಳನ್ನು ಸಂಪರ್ಕಿಸಿ."
    ),
    "ml": (
        "എനിക്ക് രോഗനിർണയം നടത്താൻ കഴിയില്ല, മരുന്നുകൾ ശുപാർശ ചെയ്യാനോ നിർദ്ദേശിക്കാനോ "
        "കഴിയില്ല, ഏതെങ്കിലും ചികിത്സ ആരംഭിക്കാനോ നിർത്താനോ ഉപദേശം നൽകാൻ കഴിയില്ല — അത് "
        "നിങ്ങളുടെ ലക്ഷണങ്ങൾ, ചരിത്രം, പരിശോധന എന്നിവ പരിഗണിക്കാൻ കഴിയുന്ന യോഗ്യതയുള്ള "
        "ഡോക്ടറുടെ ജോലിയാണ്. നിങ്ങളുടെ റിപ്പോർട്ടിലെ മൂല്യങ്ങൾ സാധാരണയായി എന്താണ് "
        "അളക്കുന്നതെന്ന് എനിക്ക് വിശദീകരിക്കാനും ഡോക്ടറോട് എന്ത് ചോദ്യങ്ങൾ ചോദിക്കാമെന്ന് "
        "നിർദ്ദേശിക്കാനും കഴിയും. നിങ്ങൾക്ക് അസ്വസ്ഥത തോന്നുകയാണെങ്കിൽ അല്ലെങ്കിൽ ഇത് "
        "അടിയന്തരമാണെങ്കിൽ, ദയവായി ഉടൻ നിങ്ങളുടെ പ്രാദേശിക അടിയന്തര സേവനങ്ങളെ സമീപിക്കുക."
    ),
    "pa": (
        "ਮੈਂ ਬਿਮਾਰੀ ਦਾ ਨਿਦਾਨ ਨਹੀਂ ਕਰ ਸਕਦਾ, ਦਵਾਈਆਂ ਦੀ ਸਿਫਾਰਸ਼ ਜਾਂ ਪ੍ਰਿਸਕ੍ਰਿਪਸ਼ਨ ਨਹੀਂ ਕਰ ਸਕਦਾ, "
        "ਅਤੇ ਕਿਸੇ ਵੀ ਇਲਾਜ ਨੂੰ ਸ਼ੁਰੂ ਜਾਂ ਬੰਦ ਕਰਨ ਦੀ ਸਲਾਹ ਨਹੀਂ ਦੇ ਸਕਦਾ — ਇਹ ਇੱਕ ਯੋਗ ਡਾਕਟਰ ਦਾ "
        "ਕੰਮ ਹੈ, ਜੋ ਤੁਹਾਡੇ ਲੱਛਣਾਂ, ਇਤਿਹਾਸ ਅਤੇ ਜਾਂਚ ਨੂੰ ਵਿਚਾਰ ਸਕਦੇ ਹਨ। ਮੈਂ ਸਮਝਾ ਸਕਦਾ ਹਾਂ ਕਿ "
        "ਤੁਹਾਡੀ ਰਿਪੋਰਟ ਦੇ ਮੁੱਲ ਆਮ ਤੌਰ 'ਤੇ ਕੀ ਮਾਪਦੇ ਹਨ ਅਤੇ ਤੁਸੀਂ ਡਾਕਟਰ ਨੂੰ ਕਿਹੜੇ ਸਵਾਲ ਪੁੱਛ ਸਕਦੇ "
        "ਹੋ। ਜੇ ਤੁਸੀਂ ਅਸਹਿਜ ਮਹਿਸੂਸ ਕਰਦੇ ਹੋ ਜਾਂ ਇਹ ਐਮਰਜੈਂਸੀ ਹੈ, ਤਾਂ ਕਿਰਪਾ ਕਰਕੇ ਤੁਰੰਤ ਆਪਣੀਆਂ "
        "ਸਥਾਨਕ ਐਮਰਜੈਂਸੀ ਸੇਵਾਵਾਂ ਨਾਲ ਸੰਪਰਕ ਕਰੋ।"
    ),
    "or": (
        "ମୁଁ ରୋଗ ନିର୍ଣ୍ଣୟ କରିପାରିବି ନାହିଁ, ଔଷଧ ସୁପାରିଶ କିମ୍ବା ପ୍ରେସକ୍ରିପସନ କରିପାରିବି ନାହିଁ, "
        "ଏବଂ କୌଣସି ଚିକିତ୍ସା ଆରମ୍ଭ କିମ୍ବା ବନ୍ଦ କରିବାକୁ ପରାମର୍ଶ ଦେଇପାରିବି ନାହିଁ — ଏହା ଜଣେ "
        "ଯୋଗ୍ୟ ଡାକ୍ତରଙ୍କ କାମ, ଯିଏ ଆପଣଙ୍କ ଲକ୍ଷଣ, ଇତିହାସ ଏବଂ ପରୀକ୍ଷାକୁ ବିଚାର କରିପାରିବେ। "
        "ଆପଣଙ୍କ ରିପୋର୍ଟର ମୂଲ୍ୟଗୁଡ଼ିକ ସାଧାରଣତଃ କଣ ମାପେ ମୁଁ ବ୍ୟାଖ୍ୟା କରିପାରିବି ଏବଂ ଆପଣ "
        "ଡାକ୍ତରଙ୍କୁ କେଉଁ ପ୍ରଶ୍ନ ପଚାରିପାରିବେ ତାହା କହିପାରିବି। ଯଦି ଆପଣ ଅସୁସ୍ଥ ଅନୁଭବ କରନ୍ତି କିମ୍ବା "
        "ଏହା ଆପାତକାଳୀନ ଅଟେ, ଦୟାକରି ତୁରନ୍ତ ଆପଣଙ୍କ ସ୍ଥାନୀୟ ଆପାତକାଳୀନ ସେବାକୁ ସମ୍ପର୍କ କରନ୍ତୁ।"
    ),
    "as": (
        "মই ৰোগ নিৰ্ণয় কৰিব নোৱাৰোঁ, ঔষধৰ পৰামৰ্শ বা প্ৰেছক্ৰিপচন কৰিব নোৱাৰোঁ, আৰু কোনো "
        "চিকিৎসা আৰম্ভ বা বন্ধ কৰাৰ পৰামৰ্শ দিব নোৱাৰোঁ — এইটো এজন যোগ্য চিকিৎসকৰ কাম, যিয়ে "
        "আপোনাৰ লক্ষণ, ইতিহাস আৰু পৰীক্ষা বিবেচনা কৰিব পাৰে। আপোনাৰ ৰিপৰ্টৰ মানসমূহ সাধাৰণতে "
        "কি জুখে মই ব্যাখ্যা কৰিব পাৰোঁ আৰু আপুনি চিকিৎসকক কি প্ৰশ্ন সুধিব পাৰে তাক ক'ব পাৰোঁ। "
        "যদি আপুনি অসুস্থ অনুভৱ কৰে বা এইটো আপাতকালীন হয়, অনুগ্ৰহ কৰি তৎক্ষণাত আপোনাৰ স্থানীয় "
        "আপাতকালীন সেৱাৰ সৈতে যোগাযোগ কৰক।"
    ),
    "ur": (
        "میں بیماری کی تشخیص نہیں کر سکتا، دوائیں تجویز یا نسخہ نہیں کر سکتا، اور کسی علاج کو "
        "شروع یا بند کرنے کا مشورہ نہیں دے سکتا — یہ ایک مستند ڈاکٹر کا کام ہے، جو آپ کی "
        "علامات، تاریخ اور جانچ کو مدنظر رکھ سکتا ہے۔ میں سمجھا سکتا ہوں کہ آپ کی رپورٹ کی "
        "اقدار عام طور پر کیا پیمائش کرتی ہیں اور آپ ڈاکٹر سے کون سے سوالات پوچھ سکتے ہیں۔ "
        "اگر آپ کی طبیعت خراب محسوس ہو یا یہ ہنگامی صورتحال ہو، تو براہ کرم فوراً اپنی مقامی "
        "ہنگامی خدمات سے رابطہ کریں۔"
    ),
}

EMERGENCY_ADDENDUM = {
    "en": "If this is an emergency, contact your local emergency services immediately.",
    "hi": "यदि यह आपातकालीन स्थिति है, तो तुरंत स्थानीय आपातकालीन सेवा से संपर्क करें।",
    "hinglish": "Agar ye emergency hai, to turant apni local emergency services se sampark karein.",
    "bn": "যদি এটি আপাতকালীন পরিস্থিতি হয়, অবিলম্বে স্থানীয় আপাতকালীন সেবায় যোগাযোগ করুন।",
    "ta": "இது அவசரநிலை என்றால், உடனடியாக உள்ளூர் அவசர சேவைகளை தொடர்பு கொள்ளுங்கள்.",
    "te": "ఇది అత్యవసర పరిస్థితి అయితే, వెంటనే మీ స్థానిక అత్యవసర సేవలను సంప్రదించండి.",
    "mr": "ही आपत्कालीन स्थिती असल्यास, त्वरित स्थानिक आपत्कालीन सेवांशी संपर्क साधा.",
    "gu": "જો આ કટોકટી હોય, તો તરત જ તમારી સ્થાનિક ઇમરજન્સી સેવાઓનો સંપર્ક કરો.",
    "kn": "ಇದು ತುರ್ತು ಪರಿಸ್ಥಿತಿಯಾದರೆ, ತಕ್ಷಣ ನಿಮ್ಮ ಸ್ಥಳೀಯ ತುರ್ತು ಸೇವೆಗಳನ್ನು ಸಂಪರ್ಕಿಸಿ.",
    "ml": "ഇത് അടിയന്തരാവസ്ഥയാണെങ്കിൽ, ഉടൻ നിങ്ങളുടെ പ്രാദേശിക അടിയന്തര സേവനങ്ങളെ സമീപിക്കുക.",
    "pa": "ਜੇ ਇਹ ਐਮਰਜੈਂਸੀ ਹੈ, ਤਾਂ ਤੁਰੰਤ ਆਪਣੀਆਂ ਸਥਾਨਕ ਐਮਰਜੈਂਸੀ ਸੇਵਾਵਾਂ ਨਾਲ ਸੰਪਰਕ ਕਰੋ।",
    "or": "ଯଦି ଏହା ଆପାତକାଳୀନ ଅଟେ, ତୁରନ୍ତ ଆପଣଙ୍କ ସ୍ଥାନୀୟ ଆପାତକାଳୀନ ସେବାକୁ ସମ୍ପର୍କ କରନ୍ତୁ।",
    "as": "যদি এইটো আপাতকালীন হয়, তৎক্ষণাত আপোনাৰ স্থানীয় আপাতকালীন সেৱাৰ সৈতে যোগাযোগ কৰক।",
    "ur": "اگر یہ ہنگامی صورتحال ہے تو فوراً اپنی مقامی ہنگامی خدمات سے رابطہ کریں۔",
}

# Answer for messages that try to override the assistant's rules (prompt
# injection). Kept separate from SAFE_REFUSAL: the topic is the assistant's
# own guardrails, not a medical-advice request.
INJECTION_REFUSAL = {
    "en": (
        "I can't take instructions from documents, notes or messages — I only "
        "follow the safety rules built into MediLens AI. I can still help you "
        "understand the values on your report."
    ),
    "hi": (
        "मैं दस्तावेज़ों, नोट्स या संदेशों में दिए निर्देशों को नहीं मान सकता — मैं केवल "
        "MediLens AI के सुरक्षा नियमों का पालन करता हूं। मैं आपकी रिपोर्ट के मान समझाने "
        "में मदद कर सकता हूं।"
    ),
    "hinglish": (
        "Main documents, notes ya messages se instructions nahi le sakta — main sirf "
        "MediLens AI ke safety rules follow karta hoon. Main aapki report ki values "
        "samajhne mein phir bhi madad kar sakta hoon."
    ),
    "bn": (
        "আমি নথি, নোট বা বার্তার নির্দেশনা মানতে পারি না — আমি শুধু MediLens AI-এর "
        "অন্তর্নির্মিত সুরক্ষা নিয়ম অনুসরণ করি। আপনার রিপোর্টের মান বুঝতে আমি তবুও "
        "সহায়তা করতে পারি।"
    ),
    "ta": (
        "ஆவணங்கள், குறிப்புகள் அல்லது செய்திகளில் உள்ள வழிமுறைகளை என்னால் பின்பற்ற "
        "முடியாது — நான் MediLens AI-இல் உள்ள பாதுகாப்பு விதிகளை மட்டுமே பின்பற்றுகிறேன். "
        "உங்கள் அறிக்கையின் மதிப்புகளை புரிந்துகொள்ள நான் இன்னும் உதவ முடியும்."
    ),
    "te": (
        "నేను పత్రాలు, నోట్లు లేదా సందేశాల నుండి సూచనలు తీసుకోలేను — నేను MediLens AI "
        "లోని భద్రతా నియమాలను మాత్రమే అనుసరిస్తాను. మీ నివేదికలోని విలువలను అర్థం "
        "చేసుకోవడంలో ఇప్పటికీ సహాయం చేయగలను."
    ),
    "mr": (
        "मी दस्तऐवज, नोट्स किंवा संदेशांमधील सूचना मानू शकत नाही — मी केवळ MediLens AI "
        "मधील सुरक्षा नियमांचे पालन करतो. तुमच्या अहवालातील मूल्ये समजून घेण्यात मी तरीही "
        "मदत करू शकतो."
    ),
    "gu": (
        "હું દસ્તાવેજો, નોંધો કે સંદેશાઓમાંથી સૂચનાઓ લઈ શકતો નથી — હું ફક્ત MediLens AI "
        "માંના સલામતી નિયમોનું પાલન કરું છું. તમારા રિપોર્ટનાં મૂલ્યો સમજવામાં હું હજુ પણ "
        "મદદ કરી શકું છું."
    ),
    "kn": (
        "ನಾನು ದಾಖಲೆಗಳು, ಟಿಪ್ಪಣಿಗಳು ಅಥವಾ ಸಂದೇಶಗಳಿಂದ ಸೂಚನೆಗಳನ್ನು ತೆಗೆದುಕೊಳ್ಳಲಾರೆ — ನಾನು "
        "MediLens AI ನಲ್ಲಿನ ಸುರಕ್ಷತಾ ನಿಯಮಗಳನ್ನು ಮಾತ್ರ ಪಾಲಿಸುತ್ತೇನೆ. ನಿಮ್ಮ ವರದಿಯ ಮೌಲ್ಯಗಳನ್ನು "
        "ಅರ್ಥಮಾಡಿಕೊಳ್ಳಲು ನಾನು ಇನ್ನೂ ಸಹಾಯ ಮಾಡಬಲ್ಲೆ."
    ),
    "ml": (
        "ഞാൻ രേഖകളിൽ നിന്നോ കുറിപ്പുകളിൽ നിന്നോ സന്ദേശങ്ങളിൽ നിന്നോ നിർദ്ദേശങ്ങൾ "
        "സ്വീകരിക്കുന്നില്ല — ഞാൻ MediLens AI-യിലെ സുരക്ഷാ നിയമങ്ങൾ മാത്രം പാലിക്കുന്നു. "
        "നിങ്ങളുടെ റിപ്പോർട്ടിലെ മൂല്യങ്ങൾ മനസ്സിലാക്കാൻ ഞാൻ ഇപ്പോഴും സഹായിക്കാം."
    ),
    "pa": (
        "ਮੈਂ ਦਸਤਾਵੇਜ਼ਾਂ, ਨੋਟਾਂ ਜਾਂ ਸੁਨੇਹਿਆਂ ਤੋਂ ਹਿਦਾਇਤਾਂ ਨਹੀਂ ਲੈ ਸਕਦਾ — ਮੈਂ ਸਿਰਫ਼ MediLens "
        "AI ਦੇ ਅੰਦਰੂਨੀ ਸੁਰੱਖਿਆ ਨਿਯਮਾਂ ਦੀ ਪਾਲਣਾ ਕਰਦਾ ਹਾਂ। ਤੁਹਾਡੀ ਰਿਪੋਰਟ ਦੇ ਮੁੱਲ ਸਮਝਣ ਵਿੱਚ "
        "ਮੈਂ ਫਿਰ ਵੀ ਮਦਦ ਕਰ ਸਕਦਾ ਹਾਂ।"
    ),
    "or": (
        "ମୁଁ ଦସ୍ତାବିଜ, ଟିପ୍ପଣୀ କିମ୍ବା ସନ୍ଦେଶରୁ ନିର୍ଦ୍ଦେଶ ନେଇପାରିବି ନାହିଁ — ମୁଁ କେବଳ "
        "MediLens AI ର ସୁରକ୍ଷା ନିୟମ ଅନୁସରଣ କରେ। ଆପଣଙ୍କ ରିପୋର୍ଟର ମୂଲ୍ୟ ବୁଝିବାରେ ମୁଁ "
        "ତଥାପି ସହାୟତା କରିପାରିବି।"
    ),
    "as": (
        "মই নথি, টোকা বা বাৰ্তাৰ পৰা নিৰ্দেশনা ল'ব নোৱাৰোঁ — মই কেৱল MediLens AI ৰ সুৰক্ষা "
        "নিয়ম অনুসৰণ কৰোঁ। আপোনাৰ ৰিপৰ্টৰ মান বুজিবলৈ মই তথাপিও সহায় কৰিব পাৰোঁ।"
    ),
    "ur": (
        "میں دستاویزات، نوٹس یا پیغامات سے ہدایات نہیں لے سکتا — میں صرف MediLens AI کے "
        "حفاظتی اصولوں کی پابندی کرتا ہوں۔ آپ کی رپورٹ کی اقدار سمجھنے میں میں پھر بھی "
        "مدد کر سکتا ہوں۔"
    ),
}


def detect_blocked_intent(message: str) -> bool:
    return any(pattern.search(message) for pattern in BLOCKED_INTENT_PATTERNS)


def detect_urgent(message: str) -> bool:
    return any(pattern.search(message) for pattern in URGENT_PATTERNS)


def refusal_answer(language: str) -> str:
    base = SAFE_REFUSAL.get(language, SAFE_REFUSAL["en"])
    return base


def injection_refusal(language: str) -> str:
    return INJECTION_REFUSAL.get(language, INJECTION_REFUSAL["en"])


def output_is_unsafe(text: str) -> bool:
    """Cheap heuristic scan of provider output (defense-in-depth, not a filter)."""
    return any(pattern.search(text) for pattern in UNSAFE_OUTPUT_PATTERNS)


def salvage_output(text: str, language: str) -> str:
    """Replace unsafe provider output with a safe refusal-style message."""
    return SAFE_REFUSAL.get(language, SAFE_REFUSAL["en"])


# ---------------------------------------------------------------------------
# Output sanitization (applied to every provider's structured analysis)
# ---------------------------------------------------------------------------
MAX_SUMMARY_CHARS = 1600
MAX_EXPLANATION_CHARS = 1600
MAX_QUESTION_CHARS = 300
MAX_LIST_ITEM_CHARS = 400
MAX_TEST_NAME_CHARS = 160
MAX_ABNORMAL_RESULTS = 100
MAX_NORMAL_RESULTS = 100
MAX_TEST_EXPLANATIONS = 80
MAX_EDUCATIONAL = 20
MAX_QUESTIONS = 10
MAX_LIMITATIONS = 10
MAX_SOURCES = 12


def sanitize_structured_analysis(structured: StructuredAnalysis) -> StructuredAnalysis:
    """Strip markup/active content, clamp lengths and cap list sizes.

    Runs on every provider response before persistence, so a stored payload
    can never carry HTML/script content, runaway text or unbounded lists into
    the UI.
    """
    structured.summary = clean_model_text(structured.summary, max_length=MAX_SUMMARY_CHARS)
    if structured.report_type:
        structured.report_type = (
            clean_model_text(structured.report_type, max_length=MAX_TEST_NAME_CHARS) or None
        )
    if structured.retrieval_note:
        structured.retrieval_note = (
            clean_model_text(structured.retrieval_note, max_length=MAX_LIST_ITEM_CHARS) or None
        )

    structured.abnormal_results = structured.abnormal_results[:MAX_ABNORMAL_RESULTS]
    for item in structured.abnormal_results:
        item.test_name = clean_model_text(item.test_name, max_length=MAX_TEST_NAME_CHARS)
        item.value = clean_model_text(item.value, max_length=60)
        item.unit = clean_model_text(item.unit, max_length=40) if item.unit else None
        item.explanation = clean_model_text(
            item.explanation, max_length=MAX_EXPLANATION_CHARS
        )

    structured.normal_results = structured.normal_results[:MAX_NORMAL_RESULTS]
    for item in structured.normal_results:
        item.test_name = clean_model_text(item.test_name, max_length=MAX_TEST_NAME_CHARS)
        item.value = clean_model_text(item.value, max_length=60)
        item.unit = clean_model_text(item.unit, max_length=40) if item.unit else None

    structured.test_explanations = structured.test_explanations[:MAX_TEST_EXPLANATIONS]
    for item in structured.test_explanations:
        item.test_name = clean_model_text(item.test_name, max_length=MAX_TEST_NAME_CHARS)
        item.test_key = (
            clean_model_text(item.test_key, max_length=80) if item.test_key else None
        )
        item.what_it_measures = clean_model_text(
            item.what_it_measures, max_length=MAX_EXPLANATION_CHARS
        )
        item.meaning = clean_model_text(item.meaning, max_length=MAX_EXPLANATION_CHARS)
        if item.general_factors:
            item.general_factors = (
                clean_model_text(item.general_factors, max_length=MAX_EXPLANATION_CHARS)
                or None
            )
        if item.discuss:
            item.discuss = (
                clean_model_text(item.discuss, max_length=MAX_EXPLANATION_CHARS) or None
            )

    structured.educational_explanations = structured.educational_explanations[:MAX_EDUCATIONAL]
    for item in structured.educational_explanations:
        item.topic = clean_model_text(item.topic, max_length=MAX_TEST_NAME_CHARS)
        item.text = clean_model_text(item.text, max_length=MAX_EXPLANATION_CHARS)

    structured.questions_for_doctor = [
        cleaned
        for question in structured.questions_for_doctor[:MAX_QUESTIONS]
        if (cleaned := clean_model_text(question, max_length=MAX_QUESTION_CHARS))
    ]
    structured.limitations = [
        cleaned
        for item in structured.limitations[:MAX_LIMITATIONS]
        if (cleaned := clean_model_text(item, max_length=MAX_LIST_ITEM_CHARS))
    ]

    structured.sources = structured.sources[:MAX_SOURCES]
    for source in structured.sources:
        source.title = clean_model_text(source.title, max_length=300)
        if source.source_org:
            source.source_org = clean_model_text(source.source_org, max_length=160) or None
        if source.url:
            url = clean_model_text(source.url, max_length=500)
            source.url = url if re.match(r"^https?://", url, re.I) else None
        if source.publication_date:
            source.publication_date = (
                clean_model_text(source.publication_date, max_length=40) or None
            )

    return structured
