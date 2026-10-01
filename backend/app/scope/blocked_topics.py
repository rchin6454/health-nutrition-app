"""Keyword/regex lists for the input gate (architecture §7, Gate 1).

Patterns run on normalized text (see `normalize`). Blocked topics are refused before any model
call; the lists aim to catch requests *for* dosing, diagnosis, weight-loss drugs,
eating-disorder behaviours and alcohol/drug advice, while letting ordinary food questions
("iron in palak", "I have diabetes, what should I eat?") through.
"""

import re
from dataclasses import dataclass

_QUOTES = {0x2018: "'", 0x2019: "'"}  # curly single quotes → straight


def normalize(text: str) -> str:
    """Lowercase, straighten quotes and collapse whitespace so patterns stay simple."""
    text = text.lower().translate(_QUOTES)
    return re.sub(r"\s+", " ", text).strip()


def _any(*patterns: str) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{p})" for p in patterns))


# --- shared vocabulary ---------------------------------------------------------------------

_DRUGS = (
    r"\b(metformin|insulin|paracetamol|acetaminophen|ibuprofen|aspirin|crocin|dolo|combiflam"
    r"|disprin|atorvastatin|rosuvastatin|statins?|levothyroxine|thyroxine|thyronorm|eltroxin"
    r"|warfarin|glimepiride|gliclazide|sitagliptin|amlodipine|telmisartan|losartan|metoprolol"
    r"|omeprazole|pantoprazole|ranitidine|antibiotics?|amoxicillin|azithromycin|ciprofloxacin"
    r"|cetirizine|antacids?|steroids?|prednisolone|antidepressants?|sertraline|fluoxetine"
    r"|contraceptives?)\b"
)
_MED_FORMS = (
    r"\b(medicines?|medications?|drugs?|tablets?|pills?|capsules?|syrups?|injections?"
    r"|supplements?|multi-?vitamins?|gumm(y|ies)|dawai|dawa|goli|goliyan)\b"
)
_NAMED_SUPPLEMENTS = (
    r"\b(whey|protein powder|creatine|fish oil|ashwagandha|melatonin|biotin|folic acid"
    r"|pre-?workout|mass gainer)\b"
)
_VITAMINS_MINERALS = (
    r"\b(vitamin ?[a-k]\d{0,2}|b ?12|iron|calcium|zinc|magnesium|potassium|omega[- ]?3|iodine)\b"
)
_DOSE = r"\b(dose|doses|dosage|dosing|overdose)\b"
_AMOUNT_UNITS = r"\b(\d+ ?(mg|mcg|iu)|mg|mcg|iu|milligrams?)\b"
_HOW_MUCH_TO_TAKE = (
    r"\bhow (much|many)\b[^.?!]{0,40}\b(take|taking|have|consume|give|per day|a day|daily)\b"
    r"|\bkitn[ia]\b"
)
_TAKE_DECISION = (
    r"\b(should|can|may|do|must) i\b[^.?!]{0,20}\b(take|stop|start|skip|increase|reduce"
    r"|double|quit)\b|\b(stop|start|skip|quit) taking\b"
)

_CONDITIONS = (
    r"(diabetes|diabetic|cancer|thyroid|hypothyroid(ism)?|hyperthyroid(ism)?|pcos|pcod"
    r"|an(a)?emi(a|c)|celiac|coeliac|ibs|ulcers?|typhoid|jaundice|hepatitis|cholera|dengue"
    r"|malaria|gerd|acid reflux|gastritis|fatty liver|kidney stones?|gout|hypertension"
    r"|high (bp|blood pressure)|high cholesterol|(lactose|gluten) intoleran(t|ce)|allerg(y|ic)"
    r"|food poisoning|infection|worms|stomach flu)"
)

_ALCOHOL = r"(alcohol|beers?|wine|whisk(e)?y|vodka|rum|gin|tequila|liquor|daru|sharab|pegs?)"


# --- topics --------------------------------------------------------------------------------


@dataclass(frozen=True)
class BlockedTopic:
    id: str  # recorded as the `failure_type` of the scope_block row
    referral: str  # the fixed, code-written reply


MEDICATION_DOSING = BlockedTopic(
    "medication_dosing",
    "I can't advise on doses of medicines or supplements, or on starting or stopping them. "
    "Please ask your doctor or pharmacist, who can take your health and your other medicines "
    "into account.\n\nI'm happy to help with general food and nutrition questions, for "
    "example which foods are good sources of a nutrient.",
)
DIAGNOSIS = BlockedTopic(
    "diagnosis",
    "I can't diagnose health conditions or tell you whether you have one. Please see a doctor, "
    "who can examine you and order tests if needed.\n\nI can help with general questions about "
    "food, nutrition and food safety, for example what to eat with a condition your doctor "
    "has already diagnosed.",
)
WEIGHT_LOSS_DRUGS = BlockedTopic(
    "weight_loss_drugs",
    "I can't give advice about weight-loss medicines, injections, fat burners or slimming "
    "pills. Please talk to a doctor before using any of them.\n\nI can help with general "
    "questions about balanced, filling Indian meals and healthy eating habits.",
)
EATING_DISORDER = BlockedTopic(
    "eating_disorder",
    "It sounds like you may be going through something difficult with food or your body. "
    "I can't help with that here, but you don't have to deal with it alone. Please talk to a "
    "doctor, a counsellor or someone you trust. In India you can call the free Tele-MANAS "
    "mental health helpline on **14416**, any time of day.",
)
ALCOHOL_OR_DRUGS = BlockedTopic(
    "alcohol_or_drugs",
    "I can't give advice about drinking alcohol or using drugs. Please speak to a doctor. "
    "If you're worried about your own or someone else's use, the free national drug "
    "de-addiction helpline is **14446**.\n\nI'm happy to help with food, nutrition and "
    "food-safety questions.",
)


def _cooccur(a: str, b: str) -> re.Pattern[str]:
    """`a` and `b` both appear in the same sentence, in any order and possibly overlapping.

    The match is the whole sentence.
    """
    return re.compile(rf"(?:^|(?<=[.?!]))(?=[^.?!]*(?:{a}))(?=[^.?!]*(?:{b}))[^.?!]*")


_RULES: list[tuple[BlockedTopic, list[re.Pattern[str]]]] = [
    (
        WEIGHT_LOSS_DRUGS,
        [
            _any(
                r"\b(ozempic|wegovy|semaglutide|rybelsus|mounjaro|tirzepatide|zepbound|saxenda"
                r"|liraglutide|orlistat|xenical|phentermine|glp-?1)\b",
                r"\bfat[- ]?burners?\b",
                r"\bfat[- ]?burning (pills?|tablets?|capsules?|supplements?|injections?)\b",
                r"\b(slimming|diet|weight[- ]?loss|fat[- ]?loss) (pills?|tablets?|capsules?"
                r"|injections?|drugs?|medicines?|medications?|powders?|goli)\b",
                r"\bappetite suppressants?\b",
            )
        ],
    ),
    (
        EATING_DISORDER,
        [
            _any(
                r"\bmake (myself|me|yourself) (throw up|vomit|puke|sick)\b",
                r"\b(purge|purging)\b",
                r"\bstarv(e|ing) (myself|yourself)\b",
                r"\b(pro[- ]?ana|pro[- ]?mia|thinspo|thinspiration)\b",
                r"\blaxatives?\b[^.?!]{0,40}\b(lose|weight|slim|thin|flat)\b",
                r"\bchew and spit\b",
                r"\bhid(e|ing) (that i('m| am) not eating|my not eating|not eating)\b",
                r"\b(stop|quit) eating (completely|altogether|entirely)\b",
                r"\bnot eat(ing)? (anything )?for (\d+|a|one|two|three|several|many) "
                r"(days|weeks)\b",
                r"(?<!\d)([1-7]\d{2}|\d{2}) ?(k?cals?|calories) (a|per|each|every) day\b",
                r"\bskip(ping)? (all )?(my )?meals\b[^.?!]{0,30}\b(lose|weight|thin|slim)\b",
            )
        ],
    ),
    (
        MEDICATION_DOSING,
        [
            _cooccur(_DRUGS, "|".join([_DOSE, _AMOUNT_UNITS, _HOW_MUCH_TO_TAKE, _TAKE_DECISION])),
            _cooccur(
                f"{_MED_FORMS}|{_NAMED_SUPPLEMENTS}",
                "|".join([_DOSE, _AMOUNT_UNITS, _HOW_MUCH_TO_TAKE]),
            ),
            _cooccur(_VITAMINS_MINERALS, _DOSE),
        ],
    ),
    (
        DIAGNOSIS,
        [
            _any(
                r"\b(diagnose|diagnosis) (me|my|this|it|him|her)\b",
                r"\bcan you diagnose\b",
                r"\bwhat('s| is) my diagnosis\b",
                rf"\bdo i have (?!to\b)[^.?!]{{0,30}}\b{_CONDITIONS}\b",
                rf"\b(am i|is (he|she|my \w+)) (a |an )?{_CONDITIONS}\b",
                rf"\b(could|can|does|might|is) (this|it|that) (be|mean)( i have| that i have)?"
                rf" (a |an )?{_CONDITIONS}\b",
                r"\bwhat (disease|illness|condition|infection|problem) do i have\b",
                r"\bwhat('s| is) wrong with me\b",
                rf"\bkya (mujhe|mujhko)\b[^.?!]{{0,30}}\b{_CONDITIONS}\b",
            )
        ],
    ),
    (
        ALCOHOL_OR_DRUGS,
        [
            _any(
                r"\b(get|getting|got|become) (drunk|high|stoned|wasted|tipsy)\b",
                r"\bhangovers?\b",
                rf"\bhow (much|many)\b[^.?!]{{0,20}}\b{_ALCOHOL}\b[^.?!]{{0,30}}"
                r"\b(drink|drinking|have|safe|okay|ok|limit|a day|per day|a week|per week"
                r"|before)\b",
                rf"\b(drink|drinking|have|having) {_ALCOHOL}\b[^.?!]{{0,40}}\b(safe|ok|okay"
                r"|fine|limit|how much|per day|daily|every day|healthy|good|bad)\b",
                rf"\b{_ALCOHOL}\b[^.?!]{{0,30}}\b(good|healthy|bad) for (you|health|the heart"
                r"|heart|me|weight|liver)\b",
                rf"\b{_ALCOHOL}\b[^.?!]{{0,20}}\b(with|and|after|on)\b[^.?!]{{0,20}}"
                r"\b(medicines?|medications?|tablets?|pills?|antibiotics?)\b",
                r"\b(cannabis|marijuana|weed|ganja|charas|hashish|bhang|cocaine|mdma|ecstasy"
                r"|lsd|heroin|meth|methamphetamine|magic mushrooms?|shrooms|psilocybin|opium"
                r"|afeem|ketamine|thc|cbd)\b",
            )
        ],
    ),
]


@dataclass(frozen=True)
class TopicMatch:
    topic: BlockedTopic
    matched: str  # the text that matched, for the failure row


def find_blocked_topic(text: str) -> TopicMatch | None:
    """The first blocked topic `text` asks about, or None."""
    normalized = normalize(text)
    for topic, patterns in _RULES:
        for pattern in patterns:
            match = pattern.search(normalized)
            if match:
                return TopicMatch(topic, match.group(0))
    return None


# --- flags (the request continues) ---------------------------------------------------------

EMERGENCY = _any(
    r"\b(can't|cant|cannot|can not|unable to|difficulty|trouble|hard to|struggling to) "
    r"breath(e|ing)?\b",
    r"\bshort(ness)? of breath\b",
    r"\bnot breathing\b",
    r"\bsaans (nahi|nahin|lene mein)\b",
    r"\b(throat|tongue|lips?|face|mouth) (is |are |has |have |started )?(swell|swelling"
    r"|swollen)\b",
    r"\bswollen (throat|tongue|lips?|face)\b",
    r"\bswelling (of|in) (the |my |his |her )?(throat|tongue|lips?|face)\b",
    r"\b(unconscious|unresponsive|fainted|fainting|passed out|collapsed|behosh)\b",
    r"\bblood in (the |his |her |my )?(stool|stools|poop|potty|vomit|urine)\b",
    r"\b(bloody|black) (stool|stools|diarrh(o)?ea|vomit)\b",
    r"\b(vomiting|throwing up|coughing up) blood\b",
    r"\b(seizures?|convulsions?|convulsing|having fits|getting fits)\b",
    r"\banaphyla(xis|ctic)\b",
    r"\bchest pain\b",
    r"\b(double|blurred|blurry) vision\b",
    r"\b(difficulty|trouble) swallowing\b",
    r"\b(swallowed|drank|drunk|ate|consumed)\b[^.?!]{0,20}\b(poison|bleach|pesticide|phenyl"
    r"|kerosene|rat poison|detergent|harpic|toilet cleaner)\b",
    r"\b(can't|cannot) stop (vomiting|throwing up)\b",
    r"\bsevere (dehydration|bleeding)\b",
    r"\bnot (peed|urinated|passing urine)\b",
)

INJECTION = _any(
    r"\b(ignore|disregard|forget|override)\b[^.?!]{0,30}\b(instructions?|prompts?|rules"
    r"|guidelines|directions)\b",
    r"\b(system|developer|hidden) (prompt|message|instructions?)\b",
    r"\b(reveal|show|print|repeat|output)\b[^.?!]{0,20}\b(your|the) (prompt|instructions"
    r"|rules)\b",
    r"\byou are now\b",
    r"\bfrom now on,? you (are|will|must)\b",
    r"\bpretend (to be|you are|you're)\b",
    r"\b(i want you to|you must|you will) (act|pretend|behave|role-?play)\b",
    r"\brole-?play as\b",
    r"\b(jailbreak|jailbroken|dan mode|developer mode|do anything now)\b",
    r"</?(user_question|context|question_analysis|system)>",
)


def is_emergency(text: str) -> bool:
    return EMERGENCY.search(normalize(text)) is not None


def find_injection(text: str) -> str | None:
    """The suspicious text, if any. Recorded only; the message is still treated as data."""
    match = INJECTION.search(normalize(text))
    return match.group(0) if match else None
