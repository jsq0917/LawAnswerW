/** 知识库 / 文档 / 块（P4-P6）。字段与后端 app/api/knowledge.py 对应。 */
import {http} from './http'

export interface KnowledgeBase {
  id: string
  name: string
  description: string | null
  embedding_model: string
  doc_count: number
  chunk_count: number
  updated_at: string | null
}

export interface DocumentItem {
  id: string
  filename: string
  file_type: string
  file_size: number
  status: 'pending' | 'parsing' | 'ready' | 'failed'
  progress: number
  error_msg: string | null
  article_count: number
  chunk_count: number
  version: number
  content_sha1: string | null
  created_at: string | null
  updated_at: string | null
}

export interface DocumentDetail extends DocumentItem {
  kb_id: string
  storage_path: string
  parse_config: Record<string, unknown> | null
}

export interface ChunkItem {
  id: string
  chunk_type: 'parent' | 'child'
  parent_id: string | null
  chunk_index: number
  article_no: string | null
  chapter_path: string | null
  title: string | null
  content: string
  char_count: number
  in_milvus: number
  is_enabled: number
}

export interface ChunkPage {
  total: number
  limit: number
  offset: number
  items: ChunkItem[]
}

export function listKbs(): Promise<KnowledgeBase[]> {
  return http.get<KnowledgeBase[]>('/kbs').then((r) => r.data)
}

export function createKb(payload: {id: string; name: string; description?: string}): Promise<{id: string; name: string}> {
  return http.post('/kbs', payload).then((r) => r.data)
}

export function listDocs(kbId: string): Promise<DocumentItem[]> {
  return http.get<DocumentItem[]>(`/kbs/${kbId}/documents`).then((r) => r.data)
}

export function uploadDocs(
  kbId: string,
  files: File[],
  params: {chunk_size: number; overlap: number; retrieval_mode: 'dense' | 'hybrid'},
): Promise<{accepted: Array<{doc_id: string; filename: string; status: string}>; count: number}> {
  const form = new FormData()
  files.forEach((f) => form.append('files', f))
  form.append('chunk_size', String(params.chunk_size))
  form.append('overlap', String(params.overlap))
  form.append('retrieval_mode', params.retrieval_mode)
  return http
    .post(`/kbs/${kbId}/documents`, form, {headers: {'Content-Type': 'multipart/form-data'}, timeout: 300000})
    .then((r) => r.data)
}

export function getDoc(docId: string): Promise<DocumentDetail> {
  return http.get<DocumentDetail>(`/documents/${docId}`).then((r) => r.data)
}

export function deleteDoc(docId: string): Promise<{deleted: string}> {
  return http.delete<{deleted: string}>(`/documents/${docId}`).then((r) => r.data)
}

export function reingestDoc(docId: string): Promise<{doc_id: string; status: string}> {
  return http.post(`/documents/${docId}/reingest`).then((r) => r.data)
}

export function listChunks(
  docId: string,
  params: {limit?: number; offset?: number; q?: string; chunk_type?: 'parent' | 'child' | ''} = {},
): Promise<ChunkPage> {
  return http.get<ChunkPage>(`/documents/${docId}/chunks`, {params}).then((r) => r.data)
}

export function patchChunk(chunkId: string, content: string): Promise<Record<string, unknown>> {
  return http.patch(`/chunks/${chunkId}`, {content}).then((r) => r.data)
}

export function deleteChunk(chunkId: string): Promise<{deleted: string}> {
  return http.delete<{deleted: string}>(`/chunks/${chunkId}`).then((r) => r.data)
}
