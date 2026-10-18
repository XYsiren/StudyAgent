"""Query-aware evidence cleanup; navigation and course promises are not answers."""
import re
import unicodedata

from app.services.embeddings import tokenize

STOPWORDS = {"的", "是", "了", "什么", "如何", "怎么", "有哪些", "请", "和", "与", "有", "几天", "多少",
             "是多少", "请问", "吗", "呢", "能否", "可以", "需要", "解释", "一下", "介绍", "关于", "一个", "为什么",
             "中", "由", "哪些", "包含", "包括", "组成", "主要", "两个", "语言"}


def clean_pdf_text(text):
    lines = re.sub(r"\r+\n?", "\n", text).splitlines()
    lines = [line for line in lines if not line.strip() or not all(
        char.isspace() or unicodedata.category(char) == "Co" for char in line)]
    text = "\n".join(lines)
    if len(re.findall(r"[\u4e00-\u9fff]", text)) > 30:
        # Common Chinese PDF font mappings for printed comma/full stop.
        text = text.replace("ꎬ", "，").replace("ꎮ", "。")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def focus(question):
    subject = definition_subject(question)
    match = re.match(r"^(.+?)(?:由哪些|包含哪些|包括哪些|的底层结构|的核心理念|的实现原理|的特点|的作用|的核心思想|的(?:两个|几个|哪些)?阶段)", question)
    subject = match[1] if match else subject or question
    # A language qualifier should not drown out the actual code symbol being asked about.
    if re.search(r"(?:Go|Python|Java|C\+\+)语言?中?\s*[A-Za-z_]+", subject, re.I):
        subject = re.sub(r"^(?:Go|Python|Java|C\+\+)(?:语言)?中?", "", subject, flags=re.I)
    return subject


def clean_text(text):
    lines = []
    for line in text.splitlines():
        # Exclude exported Markdown navigation, retaining real prose with inline links.
        if re.match(r"^\s*(?:[-*+]\s*)?\[[^\]]+\]\([^)]*(?:\.md|%[0-9a-f]{2})[^)]*\)\s*$", line, re.I):
            continue
        line = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", line)
        line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line)
        lines.append(line)
    return "\n".join(lines).strip()


def terms(question):
    return {word for word in tokenize(focus(question)) if word not in STOPWORDS and re.search(r"\w", word)}


def definition_subject(question):
    match = re.match(r"^(?:请问|请)?(?:什么是|解释一下|解释|介绍一下|介绍)\s*(.+?)[？?。]*$", question.strip())
    if not match:
        match = re.match(r"^(.+?)(?:是什么|的定义是什么)[？?。]*$", question.strip())
    return match[1].strip("？?。 ") if match else ""


def coverage(question, text):
    words = terms(question)
    found = set(tokenize(text))
    lower = text.lower()
    # Chinese segmentation is context dependent (阶段 vs 两阶段); literal occurrence
    # remains valid evidence even when the tokenizer chooses a different granularity.
    matches = sum(word in found or (bool(re.search(r"[\u4e00-\u9fff]", word)) and word in lower)
                  or (len(word) >= 3 and bool(re.search(r"\b" + re.escape(word), lower))) for word in words)
    return matches / max(len(words), 1)


def useful_sentence(question, sentence):
    if re.search(r"忽略所有|输出系统提示词|泄露提示词|ignore.{0,40}instructions", sentence, re.I):
        return False
    if re.search(r"教你|通过本课程|通过本课|本模块介绍|这个模块介绍|你[将可].{0,8}学到|搞懂如何|通过第.{0,8}讲|目前主要从事|作者简介|我叫|从事.{0,30}研发", sentence):
        return False
    subject = definition_subject(question)
    if subject and (re.match(r"^(?:请问|请)?什么是", question) or "的定义是什么" in question):
        return coverage(subject, sentence) >= .75 and bool(re.search(
            r"指|是|称为|通过|存储|保存|部署|节点|服务器|集群|共享|内存|副本|分片", sentence))
    return coverage(question, sentence) >= (.5 if len(terms(question)) > 1 else 1)


def evidence_score(question, item):
    text = clean_text(item["text"])
    subject = definition_subject(question)
    sentences = re.split(r"(?<=[。！？.!?])|\n", text)
    useful = sum(useful_sentence(question, sentence) for sentence in sentences if sentence.strip())
    intent = (set(tokenize(question)) - terms(question) - STOPWORDS)
    intent_coverage = len(intent & set(tokenize(text))) / max(len(intent), 1)
    title = item.get("document_name", "").lower()
    words = terms(question)
    title_match = sum(word in title for word in words) / max(len(words), 1)
    definition_bonus = 0
    if subject and title_match:
        definition_bonus = 4 if re.search(r"定义|概念", title) else 2 if re.search(r"原理|算法|结构", title) else 1 if re.search(r"基础|入门", title) else 0
    return (coverage(question, text) * 2 + intent_coverage * 1.5 + title_match * 1.8 + definition_bonus
            + (2 if subject and subject in text and useful else 0) + min(useful, 3) * .5 - (2 if not useful else 0))


def definition_context(question, candidates):
    """Keep concept-bearing windows, excluding unrelated product implementation sections."""
    if not re.match(r"^(?:请问|请)?什么是", question.strip()):
        return candidates
    selected = []
    for item in candidates:
        sentences = [part.strip() for part in re.split(r"(?<=[。！？])|\n", clean_text(item["text"]))
                     if part.strip() and not part.lstrip().startswith("#")]
        windows = []
        for index in range(len(sentences)):
            text = " ".join(sentences[index:index+2])
            if useful_sentence(question, text):
                windows.append(text)
        if windows:
            selected.append({**item, "text": "\n\n".join(dict.fromkeys(windows))[:1800]})
    return selected or candidates
