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
            return AIMessage(content='{"answerable":true,"checks":[{"claim_id":1,"supported":true,"source_ids":[1]}]}')
    monkeypatch.setattr(answering, "configured_llm", lambda *args: Judge())
    result = verify_answer("报销上限500元 [1]", [{"text":"报销上限500元", "chunk_id":"opaque-id"}],
                           Settings(_env_file=None, citation_verifier="llm"), "报销上限")
    assert result["passed"] and seen[0]["sources"][0]["citation"] == 1
    assert "chunk_id" not in seen[0]["sources"][0]


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
