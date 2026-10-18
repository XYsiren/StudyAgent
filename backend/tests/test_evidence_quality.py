from app.services import answering
from app.services.evidence import clean_text
from app.services.retrieval import rerank_candidates


def test_definition_excerpts_reject_adjacent_topics_course_promises_and_navigation():
    question="什么是分布式缓存"
    unrelated={"text":"分布式数据库由多个节点组成。关于事务管理器，首先要提到页缓存。"}
    promotion={"text":"教你如何利用它们构建一个分布式缓存服务体系。搞懂如何构建一个大型的分布式缓存服务系统。"}
    assert answering._extract(question,[unrelated,promotion])==answering.INSUFFICIENT_EVIDENCE
    definition={"text":"分布式缓存是部署在多台服务器上的共享缓存，通过分片和副本存储数据，减少对数据库的访问。"}
    result=answering._extract(question,[unrelated,promotion,definition])
    assert "多台服务器" in result and "页缓存" not in result and "教你" not in result
    assert clean_text("* [分布式缓存目录](/%e4%bb%a3.md)\n正文中[缓存](https://example.org)可加速访问。") == "正文中缓存可加速访问。"


def test_focused_evidence_beats_course_marketing_and_adjacent_topic(monkeypatch):
    from app.config import get_settings
    monkeypatch.setenv("RAG_RERANKER_MODEL", "")
    get_settings.cache_clear()
    candidates=[{"id":"a","text":"分布式数据库使用页缓存。","retrieval_score":.03},
                {"id":"b","text":"教你构建分布式缓存服务体系。","retrieval_score":.02},
                {"id":"c","text":"分布式缓存是部署在多个节点的共享内存缓存，通过分片存储数据。","retrieval_score":.01}]
    assert rerank_candidates("什么是分布式缓存",candidates,1)[0]["id"]=="c"
    get_settings.cache_clear()


def test_named_subject_beats_generic_question_words():
    from app.services.evidence import coverage, terms
    assert terms("Go语言中slice由哪些字段组成") == {"slice"}
    assert coverage("Go语言中slice由哪些字段组成", "Go语言的反射由类型和值组成") == 0
    assert terms("Redis字典的底层结构是什么") == {"redis", "字典"}


def test_code_array_indices_are_not_source_numbers():
    from app.services.verification import CITATION_RE
    text = "向 ht[0] 和 ht[1] 查询数据 [2]"
    assert CITATION_RE.findall(text) == ["2"]
    assert CITATION_RE.sub("", text) == "向 ht[0] 和 ht[1] 查询数据 "
    from app.services.verification import normalize_citations
    assert normalize_citations("包含三个字段。[1][2]") == "包含三个字段 [1][2]。"


def test_unit_adjacent_citations_are_recognized_without_changing_arrays():
    from app.config import Settings
    from app.services.verification import CITATION_RE, normalize_citations, verify_answer
    text = "连续作业时间≥4h[1]；充电时间≤2h[1]。电池应符合SJ/T12345[1]。"
    normalized = normalize_citations(text)
    assert CITATION_RE.findall(normalized) == ["1", "1", "1"]
    assert verify_answer(text, [{"text": "连续作业时间≥4h；充电时间≤2h。电池应符合SJ/T12345。"}], Settings(_env_file=None))["passed"]
    assert normalize_citations("ht[0] arr2[1] matrix10[2]") == "ht[0] arr2[1] matrix10[2]"
    assert normalize_citations("作业4h；[1]充电2h。[1]") == "作业4h [1]；充电2h [1]。"


def test_requested_section_keeps_all_items_and_excludes_sibling_requirements():
    from app.services.evidence import requirements_question, section_context
    question = "电池技术性能要求 是什么"
    assert requirements_question(question) and not requirements_question("电池充电时间要求是多少？")
    text = ("5.2.3 走行轨\n轨距满足其他要求。\n5.2.4 电池技术性能要求\n"
            "⑴ 一次充电作业时间≥12h。\n⑵ 循环次数≥2000次。\n"
            "⑶ 充电时间≤2h。\n⑷ 采用锂离子电池时应符合SJ/T12345。\n"
            "5.2.5 数据存储容量\n保存图像数据不少于8个标准组。")
    unrelated = {"text": "6.6.2 性能要求\nUPS符合GB/T1234标准。"}
    context = section_context(question, [{"text": text}, unrelated])
    assert len(context) == 1 and "2000" in context[0]["text"]
    assert "轨距" not in context[0]["text"] and "数据存储" not in context[0]["text"]
    excerpt = answering._extract(question, [unrelated, {"text": text}])
    assert all(value in excerpt for value in ["12h", "2000", "2h", "SJ/T12345", "[2]"])
    assert "UPS" not in excerpt and "轨距" not in excerpt
    assert answering._extract(question, [{"text": "5.2.4 电池技术性能要求"}, unrelated]) == answering.INSUFFICIENT_EVIDENCE


def test_streamed_requirement_list_survives_verification_without_fallback(client, monkeypatch):
    import json

    from app.config import get_settings
    from app.services.verification import CITATION_RE
    from langchain_core.messages import AIMessage, AIMessageChunk
    settings = get_settings()
    for key, value in {"cache_ttl": 0, "llm_provider": "ollama", "llm_model": "fixture", "citation_verifier": "llm"}.items():
        monkeypatch.setattr(settings, key, value)
    body = "①一次充电作业时间≥12h[1]；②循环次数≥2000次[1]；③充电时间≤2h[1]。\n④采用锂离子电池时应符合SJ/T12345[1]。"
    candidate = {"id": "c", "chunk_id": "c", "document_id": "d", "knowledge_base_id": "k", "page": 2,
                 "document_name": "合成设备规范.txt", "score": .03,
                 "text": "5.2.4 电池技术性能要求\n一次充电作业时间≥12h。循环次数≥2000次。充电时间≤2h。采用锂离子电池时应符合SJ/T12345。\n5.2.5 存储容量\n不少于8组。"}
    monkeypatch.setattr(answering, "retrieve", lambda *args, **kwargs: [candidate])
    class Model:
        def stream(self, messages):
            yield AIMessageChunk(content=body)
        def invoke(self, messages):
            assert "核验器" in messages[0].content
            data = json.loads(messages[-1].content)
            assert len(data["claims"]) == 4
            return AIMessage(content=json.dumps({"answerable": True, "checks": [
                {"claim_id": claim["claim_id"], "supported": True, "relevant": True, "source_ids": [1]}
                for claim in data["claims"]]}))
    monkeypatch.setattr(answering, "configured_llm", lambda *args: Model())
    events = []
    result = answering.answer_question("电池技术性能要求是什么", [candidate], emit=lambda *event: events.append(event))
    assert all(value in result["answer"] for value in ["12h", "2000", "2h", "SJ/T12345"])
    assert result["provider"] == "fixture" and result["verification"]["passed"]
    assert result["usage"]["model_calls"] == 2 and len(CITATION_RE.findall(result["answer"])) == 4
    assert "存储容量" not in result["citations"][0]["text"]
    assert any(event[0] == "draft" for event in events)


def test_pdf_font_decorations_and_full_width_numbers():
    from app.config import Settings
    from app.services.evidence import clean_pdf_text
    from app.services.verification import verify_answer
    assert clean_pdf_text("标题\r\r\n\ue001\ue001\ue001\n正文") == "标题\n正文"
    result = verify_answer("上限500元 [1]", [{"text":"上限５００元"}], Settings(_env_file=None))
    assert result["passed"]


def test_chinese_query_matches_different_token_granularity(monkeypatch):
    from app.services import evidence
    monkeypatch.setattr(evidence, "tokenize", lambda value: ["阶段", "提交"] if value == "阶段提交" else ["两阶段", "提交"])
    assert evidence.coverage("阶段提交", "两阶段提交") == 1


def test_semantic_verifier_receives_explicit_citation_numbers(monkeypatch):
    import json

    from app.config import Settings
    from app.services.verification import verify_answer
    from langchain_core.messages import AIMessage
    seen = []
    class Judge:
        def invoke(self, messages):
            seen.append(json.loads(messages[-1].content))
            return AIMessage(content='{"answerable":true,"checks":[{"claim_id":1,"supported":true,"relevant":true,"source_ids":[1]}]}')
    monkeypatch.setattr(answering, "configured_llm", lambda *args: Judge())
    result = verify_answer("报销上限500元 [1]", [{"text":"报销上限500元", "chunk_id":"opaque-id"}],
                           Settings(_env_file=None, citation_verifier="llm"), "报销上限")
    assert result["passed"] and seen[0]["sources"][0]["citation"] == 1
    assert "chunk_id" not in seen[0]["sources"][0]


def test_metric_lookup_rejects_generic_average_time_matches(client):
    from app.services.evidence import quantity_subject, terms
    question = "设备可靠性上平均无故障时间为多少？"
    right = {"id": "metric", "text": "设备可靠性：平均无故障时间≥120小时，无故障时间占比≥98%。"}
    wrong = {"id": "kanban", "text": "软件开发的平均吞吐率与平均前置时间有关。设备上任务切换影响平均时间。",
             "document_name": "设备可靠性平均无故障时间.md", "retrieval_score": 1}
    assert not {"上", "为", "多少"} & terms(question)
    assert quantity_subject(question) == "设备可靠性上平均无故障时间"
    assert not quantity_subject("设备寿命和检修周期分别是多少？")
    assert rerank_candidates(question, [wrong, right], 5) == [right]
    assert "120" in answering._extract(question, [right])


def test_supported_but_irrelevant_claim_fails_verification(monkeypatch):
    import json

    from app.config import Settings
    from app.services.verification import verify_answer
    from langchain_core.messages import AIMessage
    class Judge:
        def invoke(self, messages):
            return AIMessage(content=json.dumps({"answerable": True, "checks": [
                {"claim_id": 1, "supported": True, "relevant": True, "source_ids": [1]},
                {"claim_id": 2, "supported": True, "relevant": False, "source_ids": [2]}]}))
    monkeypatch.setattr(answering, "configured_llm", lambda *args: Judge())
    result = verify_answer("平均无故障时间≥120小时 [1]。精益看板管理开发任务 [2]。",
                           [{"text": "平均无故障时间≥120小时"}, {"text": "精益看板管理开发任务"}],
                           Settings(_env_file=None, citation_verifier="llm"), "平均无故障时间为多少？")
    assert not result["passed"] and not result["abstain"]
    assert result["issues"] == ["irrelevant_claim"]
    assert result["irrelevant_claims"] == ["精益看板管理开发任务 [2]。"]


def test_final_sources_exclude_uncited_results_and_preserve_code_indices():
    from app.services.verification import compact_citations
    sources = [{"text": "unused"}, {"text": "second", "page": 8}, {"text": "third", "page": 12}]
    answer, used = compact_citations("查看 ht[0]：指标≥120小时 [3]，条件见 [2][3]。", sources)
    assert answer == "查看 ht[0]：指标≥120小时 [1]，条件见 [2][1]。"
    assert used == [sources[2], sources[1]]


def test_irrelevant_answer_is_repaired_and_only_used_sources_returned(client, monkeypatch):
    import json

    from app.config import get_settings
    from langchain_core.messages import AIMessage
    settings = get_settings()
    monkeypatch.setattr(settings, "cache_ttl", 0)
    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "llm_model", "fixture")
    monkeypatch.setattr(settings, "citation_verifier", "llm")
    candidate = {"id": "c", "chunk_id": "c", "document_id": "d", "knowledge_base_id": "k",
                 "document_name": "合成设备规范.txt", "text": "设备可靠性：平均无故障时间≥120小时，无故障时间占比≥98%。", "score": .03}
    monkeypatch.setattr(answering, "retrieve", lambda *args, **kwargs: [candidate])
    calls = []
    class Model:
        def invoke(self, messages):
            prompt = messages[0].content
            calls.append(prompt)
            if "核验器" in prompt:
                data = json.loads(messages[-1].content)
                return AIMessage(content=json.dumps({"answerable": True, "checks": [
                    {"claim_id": claim["claim_id"], "supported": True,
                     "relevant": "占比" not in claim["text"] and "看板" not in claim["text"], "source_ids": [1]}
                    for claim in data["claims"]]}))
            if "证据整理助手" in prompt:
                return AIMessage(content="平均无故障时间应不小于120小时 [1]。")
            return AIMessage(content="平均无故障时间≥120小时 [1]。无故障时间占比≥98% [1]。无法回答精益看板问题 [1]。")
    monkeypatch.setattr(answering, "configured_llm", lambda *args: Model())
    result = answering.answer_question("设备可靠性上平均无故障时间为多少？", [candidate])
    assert result["answer"] == "平均无故障时间≥120小时 [1]。"
    assert result["verification"]["passed"] and result["usage"]["model_calls"] == 3
    assert len(result["citations"]) == 1 and len(calls) == 3
    assert result["usage"]["repair"]["mode"] == "retain_verified_claims"


def test_standalone_short_question_does_not_inherit_previous_topic(client, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "cache_ttl", 0)
    seen=[]
    monkeypatch.setattr(answering,"retrieve",lambda question,*args,**kwargs:seen.append(question) or [])
    answering.answer_question("什么是分布式缓存",[],history=[{"role":"user","content":"分布式数据库事务如何提交？"}])
    assert seen==["什么是分布式缓存"]


def test_phase_question_does_not_return_a_related_course_recap():
    question = "分布式事务两阶段提交的两个阶段是什么"
    assert answering._extract(question, [{"text":"Spanner是一种两阶段提交实现，参与者准备阶段可能无响应。"}]) == answering.INSUFFICIENT_EVIDENCE
    assert answering._extract(question, [{"text":"第一阶段是准备阶段。"}]) == answering.INSUFFICIENT_EVIDENCE
    text = "分布式事务两阶段提交包含准备阶段和提交阶段。"
    assert "准备阶段" in answering._extract(question, [{"text":text}])


def test_neighbor_expansion_preserves_same_page_and_active_revision(client):
    from app.models import Chunk, User, new_id
    from app.services.corpus import CorpusScope
    from sqlalchemy import select
    from test_api import create_kb, register, upload, wait_job
    headers = register(client)
    kb = create_kb(client, headers)
    result = upload(client, headers, kb)
    wait_job(client, headers, result["job_id"])
    with client.app.state.database.session() as db:
        user = db.scalar(select(User).where(User.email == "owner@example.com"))
        original = db.scalar(select(Chunk).where(Chunk.document_id == result["document"]["id"]))
        original.text = "SliceHeader包含三个字段。"
        sibling = Chunk(id=new_id(), document_id=original.document_id, revision_id=original.revision_id,
                        ordinal=1, page=1, text="```go\ntype SliceHeader struct { Data uintptr; Len int; Cap int }\n```")
        other_page = Chunk(id=new_id(), document_id=original.document_id, revision_id=original.revision_id,
                           ordinal=2, page=2, text="不应该混入第一页的内容")
        db.add_all([sibling, other_page])
        db.commit()
        scope = CorpusScope(db, user, [kb])
        source = scope.expand_neighbors([scope.hydrate([sibling.id])[sibling.id]])[0]
        assert "三个字段" in source["text"] and "Data uintptr" in source["text"]
        assert "不应该" not in source["text"] and len(source["context_chunk_ids"]) == 2
        excerpt = answering._extract("Go语言中slice由哪些字段组成", [source])
        assert "Cap int" in excerpt and "Len int" in excerpt
