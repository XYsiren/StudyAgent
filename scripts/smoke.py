"""Run a real local RAG end-to-end check; credentials remain in ignored artifacts."""
from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sys
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
QUESTION = "员工出差住宿报销上限是多少？"
REFERENCE = "员工出差住宿报销上限为每人每晚500元。"


class SmokeFailure(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise SmokeFailure(message)


def log(message):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


@contextmanager
def progress(label):
    done = threading.Event()
    started = time.monotonic()

    def heartbeat():
        while not done.wait(20):
            log(f"{label}仍在执行，已等待 {int(time.monotonic() - started)} 秒")

    worker = threading.Thread(target=heartbeat, daemon=True)
    worker.start()
    try:
        yield
    finally:
        done.set()
        worker.join(timeout=1)


def request(client, method, path, label=None, **kwargs):
    with progress(label or path):
        response = client.request(method, path, **kwargs)
    if response.status_code >= 400:
        # Do not print a raw response: provider errors can include secrets.
        raise SmokeFailure(f"{label or path} 返回 HTTP {response.status_code}")
    return response.json() if response.content else None


def account_for(client):
    account_path = ROOT / "artifacts" / "smoke-account.json"
    account_path.parent.mkdir(parents=True, exist_ok=True)
    account = json.loads(account_path.read_text(encoding="utf-8")) if account_path.exists() else {
        "email": f"smoke-{secrets.token_hex(6)}@example.test",
        "password": secrets.token_urlsafe(32), "name": "本地验收用户",
    }
    account_path.write_text(json.dumps(account, ensure_ascii=False, indent=2), encoding="utf-8")
    registration = client.post("auth/register", json={key: account[key] for key in ("email", "password", "name")})
    require(registration.status_code in {201, 409}, "本地验收账户注册失败")
    auth = registration.json() if registration.status_code == 201 else request(client, "POST", "auth/login",
        json={key: account[key] for key in ("email", "password")}, label="验收账户登录")
    client.headers["Authorization"] = "Bearer " + auth["access_token"]
    knowledge_bases = request(client, "GET", "knowledge-bases")
    known = {item["id"] for item in knowledge_bases}
    if account.get("knowledge_base_id") not in known:
        kb = request(client, "POST", "knowledge-bases", json={"name": "本地验收 · 示例知识库",
            "description": "使用仓库两份虚构示例文档验证真实模型、引用、历史和评测。"})
        account["knowledge_base_id"] = kb["id"]
        account_path.write_text(json.dumps(account, ensure_ascii=False, indent=2), encoding="utf-8")
    return account


def wait_job(client, job_id, timeout):
    started = time.monotonic()
    deadline, announced, next_notice = started + timeout, "", started + 20
    while time.monotonic() < deadline:
        job = request(client, "GET", "jobs/" + job_id, label="异步入库任务")
        if job["status"] != announced:
            log(f"入库任务状态：{job['status']}（{job['progress']}%）")
            announced = job["status"]
        if job["status"] == "completed":
            return job
        require(job["status"] != "failed", "文档入库失败，请检查本机 worker 日志")
        if time.monotonic() >= next_notice:
            log(f"真实模型入库仍在执行，已等待 {int(time.monotonic() - started)} 秒")
            next_notice = time.monotonic() + 20
        time.sleep(2)
    raise SmokeFailure("入库任务超时，请检查 worker 与模型服务")


def validate_answer(answer, chunks, kb_id, expected_document, expected_text, model):
    require(expected_text in answer.get("answer", ""), f"答案缺少预期事实 {expected_text}")
    require(bool(answer.get("citations")), "答案没有来源引用")
    require(answer.get("provider") == model, "答案未使用已配置的真实模型")
    allowed = {chunk["id"]: chunk for chunk in chunks}
    for citation in answer["citations"]:
        require(citation.get("chunk_id") in allowed, "引用片段不属于本次知识库")
        require(citation.get("knowledge_base_id") == kb_id, "引用知识库错误")
        require(citation.get("text") == allowed[citation["chunk_id"]]["text"], "引用文本与持久化片段不符")
    require(any(c["document_id"] == expected_document for c in answer["citations"]), "答案没有引用预期制度文档")


def qdrant_evidence(document_id, chunk_ids):
    settings = dotenv_values(ROOT / ".env")
    headers = {"api-key": settings["RAG_QDRANT_API_KEY"]} if settings.get("RAG_QDRANT_API_KEY") else {}
    base = settings["RAG_QDRANT_URL"].rstrip("/")
    prefix = settings.get("RAG_QDRANT_COLLECTION_PREFIX", "rag_assistant") + "_"
    with httpx.Client(timeout=20, trust_env=False, headers=headers) as client:
        collections = client.get(base + "/collections")
        require(collections.is_success, "无法验证真实 Qdrant collection")
        for collection in collections.json()["result"]["collections"]:
            name = collection["name"]
            if not name.startswith(prefix):
                continue
            response = client.post(base + f"/collections/{name}/points/scroll", json={
                "filter": {"must": [{"key": "document_id", "match": {"value": document_id}}]},
                "limit": 1, "with_payload": True, "with_vector": True})
            require(response.is_success, "Qdrant 向量验证请求失败")
            points = response.json()["result"]["points"]
            if points:
                point = points[0]
                require(point["payload"]["chunk_id"] in chunk_ids, "Qdrant point 与 SQL chunk 不一致")
                require(len(point["vector"]) == 4096, "实际向量维度不是4096")
                require(any(value != 0 for value in point["vector"]), "实际向量为空")
                return {"collection": name, "embedding_dimension": len(point["vector"]),
                        "sql_vector_chunk_match": True, "nonzero_vector": True}
    raise SmokeFailure("未找到此次文档的真实 Qdrant 向量")


def run(args, report):
    with httpx.Client(base_url=args.base_url.rstrip("/") + "/", timeout=httpx.Timeout(args.timeout, connect=10),
                      trust_env=False) as client:
        require(request(client, "GET", "health")["status"] == "ok", "API 未健康运行")
        log("API 健康，创建或复用本机验收账户")
        account = account_for(client)
        kb_id = account["knowledge_base_id"]
        runtime = request(client, "GET", "system")
        require(runtime["embedding_provider"] == "ollama", "Embedding provider 未配置为 Ollama")
        require(runtime.get("llm_backend") == "ollama", "LLM backend 未配置为 Ollama")
        report["runtime"] = {key: runtime.get(key) for key in (
            "mode", "embedding_provider", "embedding_model", "llm_backend", "llm_model", "queue", "vector_store", "reranker")}
        documents, all_chunks = {}, []
        for name in ("company-handbook.md", "product-guide.md"):
            path = ROOT / "examples" / name
            log("上传并等待真实模型入库：" + name)
            with path.open("rb") as file:
                upload = request(client, "POST", "documents/upload", data={"knowledge_base_id": kb_id},
                                 files={"file": (name, file, "text/markdown")}, label="示例文档上传")
            wait_job(client, upload["job_id"], args.timeout)
            document_id = upload["document"]["id"]
            chunks = request(client, "GET", f"documents/{document_id}/chunks")
            require(bool(chunks), "文档没有持久化片段")
            all_chunks.extend(chunks)
            documents[name] = {"id": document_id, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                               "chunk_count": len(chunks), "deduplicated": upload.get("duplicate", False)}
        report["documents"] = {name: {key: value for key, value in row.items() if key != "id"}
                               for name, row in documents.items()}
        handbook_id = documents["company-handbook.md"]["id"]
        report["vectors"] = qdrant_evidence(handbook_id, {chunk["id"] for chunk in all_chunks})
        standard_body = {"question": QUESTION, "knowledge_base_ids": [kb_id], "top_k": 5, "mode": "standard"}
        log("验证标准问答与来源引用")
        standard = request(client, "POST", "chat", json=standard_body, label="标准问答")
        validate_answer(standard, all_chunks, kb_id, handbook_id, "500", runtime["llm_model"])
        report["standard"] = {"answer": standard["answer"], "provider": standard["provider"],
                              "citation_count": len(standard["citations"]), "cached": standard["cached"],
                              "usage": standard.get("usage", {})}
        log("验证同一会话追问")
        followup = request(client, "POST", "chat", json={**standard_body, "question": "那报销申请最晚应在什么时候提交？",
                           "conversation_id": standard["conversation_id"]}, label="多轮追问")
        validate_answer(followup, all_chunks, kb_id, handbook_id, "30", runtime["llm_model"])
        history = request(client, "GET", "conversations/" + standard["conversation_id"])
        require([row["role"] for row in history["messages"]] == ["user", "assistant", "user", "assistant"], "会话历史顺序或数量错误")
        report["followup"] = {"answer": followup["answer"], "same_conversation": followup["conversation_id"] == standard["conversation_id"],
                              "history_messages": len(history["messages"]), "citation_count": len(followup["citations"])}
        log("验证 Agent 问答及查询缓存")
        agent = request(client, "POST", "chat", json={**standard_body, "mode": "agent"}, label="Agent 问答")
        validate_answer(agent, all_chunks, kb_id, handbook_id, "500", runtime["llm_model"])
        repeated = request(client, "POST", "chat", json=standard_body, label="缓存回放")
        require(repeated["cached"] is True and repeated["answer"] == standard["answer"], "相同查询未命中真实缓存")
        report["agent"] = {"answer": agent["answer"], "citation_count": len(agent["citations"]),
                           "trace_steps": [item["step"] for item in agent.get("trace", [])]}
        report["answer_cache"] = {"hit": repeated["cached"], "identical_answer": repeated["answer"] == standard["answer"]}
        case = {"question": QUESTION, "reference": REFERENCE, "expected_document_ids": [handbook_id]}
        log("评测相同语料的 dense / hybrid 检索")
        comparison = request(client, "POST", "evaluations/compare", json={"cases": [case], "knowledge_base_ids": [kb_id]},
                             label="Dense 与 hybrid 评测")
        report["retrieval_comparison"] = {"dense": comparison["dense"]["metrics"], "hybrid": comparison["hybrid"]["metrics"],
            "relative_hit_rate_change": comparison["relative_hit_rate_change"], "case_count": 1,
            "note": "仅两份示例语料的一条标注案例；不能据此宣称普遍性能提升。"}
        require(comparison["hybrid"]["metrics"]["hit_at_5"] == 1, "真实 hybrid 检索未命中标注文档")
        evaluation_body = {"name": "真实本地模型单案例验收", "cases": [case], "knowledge_base_ids": [kb_id],
                           "use_ragas": args.with_ragas}
        log("运行真实 Ragas 四指标评测" if args.with_ragas else "运行检索与答案参考词项评测")
        evaluation = request(client, "POST", "evaluations", json=evaluation_body, label="真实模型评测")
        require(evaluation["status"] == "completed", "真实模型评测未完成")
        report["evaluation"] = {"status": evaluation["status"], "metrics": evaluation["metrics"]}
        replay_case = {**case, "message_id": standard["message_id"]}
        replay_body = {**evaluation_body, "name": "已存答案离线复评验收", "cases": [replay_case], "use_ragas": False}
        log("验证 message_id 已存答案离线复评")
        replay = request(client, "POST", "evaluations", json=replay_body, label="离线历史复评")
        require(replay["metrics"]["history_replayed_cases"] == 1, "历史答案未被离线复用")
        require(replay["results"][0]["answer"] == standard["answer"], "历史复评答案发生变化")
        report["history_evaluation"] = {"metrics": replay["metrics"], "identical_answer": True,
                                        "provider": replay["results"][0]["provider"]}
        if args.with_ragas:
            names = ("faithfulness", "answer_relevancy", "context_precision", "context_recall")
            require(all(isinstance(evaluation["metrics"].get(name), (float, int)) for name in names), "Ragas 四指标没有全部得到有效数值")
            log("验证 Ragas 历史复评命中评审缓存")
            judged_replay = request(client, "POST", "evaluations", json={**replay_body, "use_ragas": True,
                                     "name": "Ragas 评审缓存回放验收"}, label="Ragas 评审缓存回放")
            require(judged_replay["metrics"]["judge_cached_cases"] == 1, "Ragas 评审缓存未命中")
            require(judged_replay["metrics"]["judge_scored_cases"] == 0, "缓存回放仍然调用了评审模型")
            require(all(judged_replay["metrics"][name] == evaluation["metrics"][name] for name in names), "评审缓存指标发生变化")
            report["ragas_cache_replay"] = {"metrics": judged_replay["metrics"], "identical_scores": True}
        report["checks"] = {"real_embeddings": True, "persisted_chunks": True, "valid_citations": True,
            "standard_answer": True, "bounded_agent_answer": True, "multi_turn_history": True,
            "answer_cache": True, "dense_hybrid_comparison": True, "historical_answer_replay": True,
            "real_ragas_four_metrics": args.with_ragas, "ragas_judge_cache": args.with_ragas}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8010/api")
    parser.add_argument("--with-ragas", action="store_true", help="调用真实本地评审模型，运行四个 Ragas 指标及缓存回放")
    parser.add_argument("--timeout", type=float, default=1800, help="单次本地模型/入库操作超时秒数")
    parser.add_argument("--report", type=Path, default=ROOT / "docs" / "artifacts" / "smoke-2026-10-15.json")
    args = parser.parse_args()
    parsed = urlsplit(args.base_url)
    require(parsed.hostname in {"127.0.0.1", "localhost"} and not parsed.username and not parsed.password,
            "本脚本仅允许无内嵌凭证的本机 API URL")
    report = {"executed_at": datetime.now(UTC).isoformat(), "status": "running", "ragas_requested": args.with_ragas,
              "test_scope": "真实本地 Ollama + PostgreSQL + Redis + Qdrant；两份虚构示例文档、一条标注案例"}
    started = time.monotonic()
    exit_code = 0
    try:
        run(args, report)
        report["status"] = "passed"
        log("真实端到端验收通过")
    except Exception as error:
        report["status"], report["failure_type"] = "failed", type(error).__name__
        report["failure"] = str(error) if isinstance(error, SmokeFailure) else "调用失败，请检查本机服务日志；未输出请求凭证"
        log("验收未通过：" + report["failure"])
        exit_code = 1
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - started, 2)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        log("安全验收结果已写入报告")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
