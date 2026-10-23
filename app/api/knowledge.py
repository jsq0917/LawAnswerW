"""知识库 / 文档 / 文档块接口。

阶段 2 先打通"上传 → 解析 → 入库 → 预览 → 编辑/删除"闭环；
鉴权（JWT）在阶段 4 接入，届时用 `Depends(current_user)` 统一挂上。
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, ValidationFailed
from app.core.logger import get_logger
from app.db.mysql import get_db
from app.ingest.loaders import detect_file_type
from app.ingest.pipeline import ingest_file, new_id
from app.ingest.service import (
    UploadRejected,
    create_pending_document,
    delete_document as delete_document_service,
    disable_chunk,
    run_ingest_job,
    save_upload,
    update_chunk_content,
    validate_upload,
)
from config import BASE_DIR

logger = get_logger("lawanswer.kb")

router = APIRouter(prefix="/api", tags=["kbs"])


# ---------------------------------------------------------------------------
# 知识库
# ---------------------------------------------------------------------------
class KbCreate(BaseModel):
    id: str = Field(min_length=2, max_length=32, pattern=r"^[a-z0-9_]+$")
    name: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)


@router.get("/kbs", summary="知识库列表（含文档数/块数）")
def list_kbs(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(
        text(
            "SELECT id, name, description, embedding_model, doc_count, chunk_count, updated_at "
            "FROM knowledge_bases ORDER BY id"
        )
    ).mappings().all()
    return [
        {
            **dict(r),
            "updated_at": r["updated_at"].isoformat(sep=" ", timespec="seconds") if r["updated_at"] else None,
        }
        for r in rows
    ]


@router.post("/kbs", status_code=201, summary="新建知识库")
def create_kb(payload: KbCreate, db: Session = Depends(get_db)) -> dict:
    exists = db.execute(
        text("SELECT 1 FROM knowledge_bases WHERE id = :id OR name = :name"),
        {"id": payload.id, "name": payload.name},
    ).scalar()
    if exists:
        raise ConflictError("知识库 id 或名称已存在")
    db.execute(
        text(
            "INSERT INTO knowledge_bases (id, name, description, embedding_model) "
            "VALUES (:id, :name, :description, 'bge-m3')"
        ),
        {"id": payload.id, "name": payload.name, "description": payload.description},
    )
    db.commit()
    return {"id": payload.id, "name": payload.name}


# ---------------------------------------------------------------------------
# 文档
# ---------------------------------------------------------------------------
@router.get("/kbs/{kb_id}/documents", summary="文档列表")
def list_documents(kb_id: str, db: Session = Depends(get_db)) -> list[dict]:
    _require_kb(db, kb_id)
    rows = db.execute(
        text(
            "SELECT id, filename, file_type, file_size, status, progress, error_msg, "
            "article_count, chunk_count, version, content_sha1, created_at, updated_at "
            "FROM documents WHERE kb_id = :kb_id ORDER BY created_at DESC"
        ),
        {"kb_id": kb_id},
    ).mappings().all()
    return [
        {
            **dict(r),
            "content_sha1": r["content_sha1"][:12],
            "created_at": _iso(r["created_at"]),
            "updated_at": _iso(r["updated_at"]),
        }
        for r in rows
    ]


@router.post("/kbs/{kb_id}/documents", status_code=202, summary="上传并异步解析文档")
async def upload_documents(
    kb_id: str,
    background: BackgroundTasks,
    files: list[UploadFile] = File(...),
    chunk_size: int = Form(300),
    overlap: int = Form(50),
    retrieval_mode: str = Form("hybrid"),
    db: Session = Depends(get_db),
) -> dict:
    _require_kb(db, kb_id)
    if retrieval_mode not in {"dense", "hybrid"}:
        raise ValidationFailed("retrieval_mode 只能是 dense 或 hybrid")

    accepted: list[dict] = []
    for upload in files:
        data = await upload.read()
        try:
            validate_upload(upload.filename or "", len(data), chunk_size, overlap)
        except UploadRejected as exc:
            raise ValidationFailed(str(exc)) from exc
        path = save_upload(data, kb_id, upload.filename or "unnamed")
        doc_id = new_id()
        create_pending_document(
            db,
            doc_id=doc_id,
            kb_id=kb_id,
            filename=Path(upload.filename or "unnamed").name,
            file_type=detect_file_type(path),
            file_size=len(data),
            storage_path=_rel(path),
            chunk_size=chunk_size,
            overlap=overlap,
            retrieval_mode=retrieval_mode,
        )
        background.add_task(
            run_ingest_job,
            path,
            kb_id=kb_id,
            doc_id=doc_id,
            chunk_size=chunk_size,
            overlap=overlap,
            retrieval_mode=retrieval_mode,
        )
        accepted.append({"doc_id": doc_id, "filename": upload.filename, "status": "pending"})
    db.commit()
    return {"accepted": accepted, "count": len(accepted)}


@router.get("/documents/{doc_id}", summary="文档详情")
def get_document(doc_id: str, db: Session = Depends(get_db)) -> dict:
    row = db.execute(
        text(
            "SELECT id, kb_id, filename, file_type, file_size, status, progress, error_msg, "
            "article_count, chunk_count, version, parse_config, storage_path, created_at, updated_at "
            "FROM documents WHERE id = :id"
        ),
        {"id": doc_id},
    ).mappings().first()
    if row is None:
        raise NotFoundError("文档不存在")
    data = dict(row)
    if isinstance(data.get("parse_config"), str):
        import json

        try:
            data["parse_config"] = json.loads(data["parse_config"])
        except json.JSONDecodeError:
            data["parse_config"] = {}
    return {
        **data,
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
    }


@router.delete("/documents/{doc_id}", summary="删除文档（含向量）")
def remove_document(doc_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        delete_document_service(db, doc_id)
    except LookupError as exc:
        raise NotFoundError(str(exc)) from exc
    db.commit()
    return {"deleted": doc_id}


@router.post("/documents/{doc_id}/reingest", status_code=202, summary="重新解析（源文件缺失则报错）")
def reingest_document(doc_id: str, background: BackgroundTasks, db: Session = Depends(get_db)) -> dict:
    row = db.execute(
        text("SELECT kb_id, storage_path, parse_config FROM documents WHERE id = :id"),
        {"id": doc_id},
    ).mappings().first()
    if row is None:
        raise NotFoundError("文档不存在")
    path = _resolve(row["storage_path"])
    if path is None or not path.exists():
        raise NotFoundError("源文件已不存在，请重新上传")
    # MySQL 的 JSON 列经 PyMySQL/驱动读回来是字符串，必须先解码再 dict()
    # （否则 dict("{...}") 直接抛 ValueError，重新解析接口 500）
    raw_config = row["parse_config"]
    if isinstance(raw_config, str):
        try:
            raw_config = json.loads(raw_config)
        except json.JSONDecodeError:
            raw_config = {}
    config = dict(raw_config or {})
    background.add_task(
        run_ingest_job,
        path,
        kb_id=row["kb_id"],
        doc_id=doc_id,
        chunk_size=int(config.get("chunk_size", 300)),
        overlap=int(config.get("overlap", 50)),
        retrieval_mode=config.get("retrieval_mode", "hybrid"),
        force=True,
    )
    return {"doc_id": doc_id, "status": "pending"}


# ---------------------------------------------------------------------------
# 文档块
# ---------------------------------------------------------------------------
@router.get("/documents/{doc_id}/chunks", summary="块列表（分页 + 关键词 + 类型过滤）")
def list_chunks(
    doc_id: str,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    q: str | None = Query(None, max_length=64),
    chunk_type: str | None = Query(None, pattern="^(parent|child)$"),
    db: Session = Depends(get_db),
) -> dict:
    if db.execute(text("SELECT 1 FROM documents WHERE id = :id"), {"id": doc_id}).scalar() is None:
        raise NotFoundError("文档不存在")

    where = ["doc_id = :doc_id"]
    params: dict = {"doc_id": doc_id, "limit": limit, "offset": offset}
    if q:
        where.append("(content LIKE :kw OR article_no LIKE :kw)")
        params["kw"] = f"%{q}%"
    if chunk_type:
        where.append("chunk_type = :chunk_type")
        params["chunk_type"] = chunk_type
    clause = " AND ".join(where)

    total = db.execute(
        text(f"SELECT COUNT(*) FROM document_chunks WHERE {clause}"), params
    ).scalar()
    rows = db.execute(
        text(
            f"SELECT id, chunk_type, parent_id, chunk_index, article_no, chapter_path, title, "
            f"content, char_count, in_milvus, is_enabled FROM document_chunks "
            f"WHERE {clause} ORDER BY chunk_index LIMIT :limit OFFSET :offset"
        ),
        params,
    ).mappings().all()
    return {"total": int(total or 0), "limit": limit, "offset": offset, "items": [dict(r) for r in rows]}


class ChunkUpdate(BaseModel):
    content: str = Field(min_length=1, max_length=8000)


@router.patch("/chunks/{chunk_id}", summary="编辑块（触发重新向量化）")
def patch_chunk(chunk_id: str, payload: ChunkUpdate, db: Session = Depends(get_db)) -> dict:
    try:
        result = update_chunk_content(db, chunk_id, payload.content.strip())
    except LookupError as exc:
        raise NotFoundError(str(exc)) from exc
    db.commit()
    return result


@router.delete("/chunks/{chunk_id}", summary="删除块（软删 + 删向量）")
def remove_chunk(chunk_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        disable_chunk(db, chunk_id)
    except LookupError as exc:
        raise NotFoundError(str(exc)) from exc
    db.commit()
    return {"deleted": chunk_id}


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _require_kb(db: Session, kb_id: str) -> None:
    if db.execute(text("SELECT 1 FROM knowledge_bases WHERE id = :id"), {"id": kb_id}).scalar() is None:
        raise NotFoundError(f"知识库 {kb_id} 不存在")


def _iso(value) -> str | None:
    return value.isoformat(sep=" ", timespec="seconds") if value else None


def _rel(path: Path) -> str:
    try:
        return path.relative_to(BASE_DIR).as_posix()
    except ValueError:
        return str(path)


def _resolve(storage_path: str | None) -> Path | None:
    if not storage_path:
        return None
    path = Path(storage_path)
    return path if path.is_absolute() else BASE_DIR / path


@router.post("/ingest/local", summary="把 data/raw 下的本地文件直接入库（调试用）")
def ingest_local(
    kb_id: str = Query(...),
    filename: str = Query(...),
    chunk_size: int = Query(300),
    overlap: int = Query(50),
    force: bool = Query(False),
    db: Session = Depends(get_db),
) -> dict:
    _require_kb(db, kb_id)
    path = BASE_DIR / "data" / "raw" / filename
    if not path.exists():
        raise NotFoundError(f"文件不存在：{filename}")
    result = ingest_file(path, kb_id=kb_id, chunk_size=chunk_size, overlap=overlap, force=force)
    return {
        "doc_id": result.doc_id,
        "status": result.status,
        "article_count": result.article_count,
        "child_count": result.child_count,
        "elapsed_ms": result.elapsed_ms,
    }
