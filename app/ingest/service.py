"""摄入/文档服务层：供 API 与 CLI 共用，避免业务逻辑写进路由。

职责：
* 上传落盘与校验（扩展名、大小、切块参数）
* 后台入库任务（解析慢，必须异步，前端轮询 documents.status/progress）
* 单块编辑/删除后的向量同步（MySQL 是事实源，Milvus 跟随）
"""

from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.logger import get_logger
from app.ingest.embedder import get_embedder
from app.ingest.loaders import SUPPORTED_EXT, UnsupportedDocumentError
from app.ingest.pipeline import ingest, new_id, prepare
from app.ingest.writer import chunk_to_entity, delete_chunk_vector, refresh_kb_counts, update_document_status
from app.ingest.writer import delete_document_vectors, upsert_vectors
from config import get_settings

logger = get_logger("lawanswer.ingest")

MAX_UPLOAD_BYTES = 80 * 1024 * 1024


class UploadRejected(ValueError):
    """上传校验失败（前端可直接展示 message）。"""


def validate_upload(filename: str, size: int, chunk_size: int, overlap: int) -> None:
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_EXT:
        raise UploadRejected(
            f"不支持的文件类型 {suffix or '(无扩展名)'}，支持："
            + "、".join(sorted(SUPPORTED_EXT))
        )
    if size <= 0:
        raise UploadRejected("文件为空")
    if size > MAX_UPLOAD_BYTES:
        raise UploadRejected(f"文件超过 {MAX_UPLOAD_BYTES // 1024 // 1024} MB 上限")
    if chunk_size < 50 or chunk_size > 2000:
        raise UploadRejected("切块大小需在 50–2000 之间")
    if overlap < 0 or overlap >= chunk_size:
        raise UploadRejected("重叠大小必须 >= 0 且小于切块大小")


def save_upload(data: bytes, kb_id: str, filename: str) -> Path:
    settings = get_settings()
    target_dir = settings.upload_path / kb_id
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(filename).name
    path = target_dir / f"{new_id()}_{safe_name}"
    path.write_bytes(data)
    return path


def _placeholder_sha1() -> str:
    """每次唯一的 40 位占位哈希（仅用于 pending 行，入库后会被真实 sha1 覆盖）。"""
    import secrets

    return "p" + secrets.token_hex(19) + "0"


def create_pending_document(
    session: Session,
    *,
    doc_id: str,
    kb_id: str,
    filename: str,
    file_type: str,
    file_size: int,
    storage_path: str,
    chunk_size: int,
    overlap: int,
    retrieval_mode: str,
) -> None:
    import json

    session.execute(
        text(
            """
            INSERT INTO documents
                (id, kb_id, filename, file_type, file_size, content_sha1, file_sha1,
                 storage_path, status, progress, parse_config)
            VALUES
                (:id, :kb_id, :filename, :file_type, :file_size, :placeholder, :placeholder,
                 :storage_path, 'pending', 0, :parse_config)
            """
        ),
        {
            "id": doc_id,
            "kb_id": kb_id,
            "filename": filename,
            "file_type": file_type,
            "file_size": file_size,
            # 占位 sha1 必须每次不同：documents 上有 uk_kb_content(kb_id, content_sha1)，
            # 全用 "0"*40 会让同一知识库的第二条 pending 记录直接撞唯一键（上传即 500）。
            # 解析出真实内容后由入库流程覆盖成 content_sha1。
            "placeholder": _placeholder_sha1(),
            "storage_path": storage_path,
            "parse_config": json.dumps(
                {"chunk_size": chunk_size, "overlap": overlap, "retrieval_mode": retrieval_mode},
                ensure_ascii=False,
                sort_keys=True,
            ),
        },
    )


def run_ingest_job(
    path: str | Path,
    *,
    kb_id: str,
    doc_id: str,
    chunk_size: int,
    overlap: int,
    retrieval_mode: str = "hybrid",
    force: bool = False,
) -> dict:
    """后台任务入口：任何异常都要落库成 failed，日志留痕。"""
    try:
        prepared = prepare(
            path,
            chunk_size=chunk_size,
            overlap=overlap,
            retrieval_mode=retrieval_mode,
        )
        result = ingest(
            prepared,
            kb_id=kb_id,
            doc_id=doc_id,
            force=force,
        )
        logger.info(
            "入库完成 doc=%s 文件=%s 状态=%s 条=%d 子块=%d 耗时=%dms",
            result.doc_id,
            Path(path).name,
            result.status,
            result.article_count,
            result.child_count,
            result.elapsed_ms,
        )
        return {
            "doc_id": result.doc_id,
            "status": result.status,
            "article_count": result.article_count,
            "child_count": result.child_count,
        }
    except Exception as exc:  # noqa: BLE001 - 后台任务必须吞掉异常并落库
        logger.exception("入库失败 文件=%s", Path(path).name)
        from app.db.mysql import session_scope

        with session_scope() as db:
            update_document_status(db, doc_id, status="failed", error=str(exc))
        return {"doc_id": doc_id, "status": "failed", "error": str(exc)[:300]}


def update_chunk_content(session: Session, chunk_id: str, content: str) -> dict:
    """编辑单块：写 MySQL → 重新向量化 → upsert Milvus（同 chunk_id 覆盖）。"""
    row = session.execute(
        text(
            "SELECT c.id, c.doc_id, c.kb_id, c.parent_id, c.chunk_type, c.chunk_index, "
            "c.article_no, c.chapter_path, c.title, d.content_sha1, d.filename "
            "FROM document_chunks c JOIN documents d ON d.id = c.doc_id WHERE c.id = :cid"
        ),
        {"cid": chunk_id},
    ).mappings().first()
    if row is None:
        raise LookupError("块不存在")

    session.execute(
        text(
            "UPDATE document_chunks SET content = :content, char_count = :len, in_milvus = 0 "
            "WHERE id = :cid"
        ),
        {"content": content, "len": len(content), "cid": chunk_id},
    )

    settings = get_settings()
    from app.db.milvus_client import get_milvus_db_client

    chunk = _row_to_chunk(row, content)
    embedding = get_embedder().encode([chunk.embed_text])[0]
    entity = chunk_to_entity(
        chunk,
        embedding,
        law_name=_law_name_of(session, row["doc_id"]),
        source_file=row["filename"],
        doc_sha1=row["content_sha1"],
    )
    upsert_vectors(get_milvus_db_client(), settings.milvus_collection, [entity])
    session.execute(
        text("UPDATE document_chunks SET in_milvus = 1 WHERE id = :cid"), {"cid": chunk_id}
    )
    return {"chunk_id": chunk_id, "char_count": len(content)}


def disable_chunk(session: Session, chunk_id: str) -> None:
    """删除块：软删 MySQL + 物理删向量（检索侧不再命中）。"""
    existed = session.execute(
        text("SELECT id FROM document_chunks WHERE id = :cid"), {"cid": chunk_id}
    ).scalar()
    if existed is None:
        raise LookupError("块不存在")
    session.execute(
        text("UPDATE document_chunks SET is_enabled = 0, in_milvus = 0 WHERE id = :cid"),
        {"cid": chunk_id},
    )
    settings = get_settings()
    from app.db.milvus_client import get_milvus_db_client

    delete_chunk_vector(get_milvus_db_client(), settings.milvus_collection, chunk_id)


def delete_document(session: Session, doc_id: str) -> None:
    row = session.execute(
        text("SELECT kb_id FROM documents WHERE id = :id"), {"id": doc_id}
    ).mappings().first()
    if row is None:
        raise LookupError("文档不存在")
    settings = get_settings()
    from app.db.milvus_client import get_milvus_db_client

    delete_document_vectors(get_milvus_db_client(), settings.milvus_collection, doc_id)
    session.execute(text("DELETE FROM documents WHERE id = :id"), {"id": doc_id})
    refresh_kb_counts(session, row["kb_id"])


def _law_name_of(session: Session, doc_id: str) -> str:
    name = session.execute(
        text("SELECT filename FROM documents WHERE id = :id"), {"id": doc_id}
    ).scalar()
    if not name:
        return ""
    from app.ingest.structure import law_name_from_filename

    return law_name_from_filename(name)


def _row_to_chunk(row, content: str):
    from app.ingest.models import Chunk

    return Chunk(
        chunk_id=row["id"],
        doc_id=row["doc_id"],
        kb_id=row["kb_id"],
        parent_id=row["parent_id"],
        chunk_type=row["chunk_type"],
        chunk_index=int(row["chunk_index"]),
        article_no=row["article_no"] or "",
        chapter_path=row["chapter_path"] or "",
        title=row["title"] or "",
        content=content,
        char_count=len(content),
    )


def copy_local_file(path: Path, kb_id: str) -> Path:
    """把已在本地磁盘的文件（如 data/raw）复制进上传目录，保持存储路径统一。"""
    target = get_settings().upload_path / kb_id / path.name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)
    return target
