export type User = { id: string; email: string; name: string }
export type KnowledgeBase = { id: string; name: string; description: string; created_at: string; document_count: number }
export type Document = { id: string; knowledge_base_id: string; name: string; size_bytes: number; status: string; chunk_count: number; error?: string; created_at: string; job_id?: string }
export type Job = { id: string; status: string; progress: number; error?: string; document_id: string }
export type Chunk = { id: string; text: string; page: number | null; ordinal: number }
export type Citation = { chunk_id: string; document_id: string; document_name: string; knowledge_base_id: string; page: number | null; text: string; score: number }
export type Trace = { step: string; detail: unknown }
export type Message = { id: string; role: string; content: string; citations?: Citation[]; trace?: Trace[]; created_at?: string }
export type Conversation = { id: string; title: string; created_at: string }
export type EvaluationCase = { question: string; reference: string; expected_document_ids?: string[]; message_id?: string }
export type Evaluation = { id: string; name: string; status: string; metrics: Record<string, unknown>; results: Record<string, unknown>[]; created_at: string; error?: string }
export type Comparison = { dense: Record<string, unknown>; hybrid: Record<string, unknown>; relative_hit_rate_change: number | null }
export type Runtime = { mode: string; embedding_provider: string; embedding_model?: string; llm_provider: string; llm_backend?: string; llm_model?: string; reranker: unknown; vector_store: unknown; queue: unknown; max_upload_mb: number; features: Record<string, unknown> }
export type ChatResponse = { answer: string; citations: Citation[]; trace: Trace[]; cached: boolean; conversation_id: string; message_id: string; provider: string; usage: Record<string, unknown> }

export const API_BASE = (import.meta.env.VITE_API_BASE || '/api').replace(/\/$/, '')
export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); this.name = 'ApiError' }
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  const token = localStorage.getItem('zhiheng_token')
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  let response: Response
  try { response = await fetch(`${API_BASE}${path}`, { ...options, headers }) }
  catch { throw new ApiError('无法连接服务，请确认后端已启动并检查 API 地址。', 0) }
  if (!response.ok) {
    let message = `请求失败（${response.status}）`
    try {
      const error = await response.json()
      if (typeof error.detail === 'string') message = error.detail
      else if (Array.isArray(error.detail)) message = error.detail.map((item: { msg: string; loc?: string[] }) => `${item.loc?.slice(1).join('.') || '参数'}: ${item.msg}`).join('；')
    } catch { /* A non-JSON server response still has a useful HTTP status. */ }
    throw new ApiError(message, response.status)
  }
  if (response.status === 204) return undefined as T
  return response.json()
}

export async function downloadDocument(documentId: string, filename: string) {
  const response = await fetch(`${API_BASE}/documents/${encodeURIComponent(documentId)}/download`, {
    headers: { Authorization: `Bearer ${localStorage.getItem('zhiheng_token') || ''}` },
  })
  if (!response.ok) throw new ApiError(`原文下载失败（${response.status}）`, response.status)
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url; anchor.download = filename; anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}
