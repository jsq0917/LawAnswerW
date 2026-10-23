"""文档加载器：Markdown / PDF / PPTX / 图片。

设计取舍
--------
* **优先 Markdown**：同一部法律既有 PDF 又有 md 时，md 是 PDF 转换产物且已排版，
  文本层质量更高（PDF 的康熙部首变体、跨页断行都要额外处理）。pipeline 会优先选 md。
* **PDF 用 PyMuPDF**：抽文本层最快最稳；若整篇几乎抽不出字，说明是扫描件，
  直接报"需要 OCR"而不是把空文本塞进向量库。
* **图片 OCR 走 DashScope**（qwen-vl-ocr）：本地没有 OCR 依赖，而项目本来就要配
  DashScope 密钥，复用同一条通道最省事；没有密钥时给出明确提示。
"""

from __future__ import annotations

import base64
from pathlib import Path

from app.ingest.models import LoadedDocument

MARKDOWN_EXT = {".md", ".markdown", ".txt"}
PDF_EXT = {".pdf"}
PPTX_EXT = {".pptx", ".ppt"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

SUPPORTED_EXT = MARKDOWN_EXT | PDF_EXT | PPTX_EXT | IMAGE_EXT


class UnsupportedDocumentError(RuntimeError):
    """格式不支持或内容无法提取。"""


def detect_file_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in MARKDOWN_EXT:
        return "md"
    if suffix in PDF_EXT:
        return "pdf"
    if suffix in PPTX_EXT:
        return "pptx"
    if suffix in IMAGE_EXT:
        return "image"
    raise UnsupportedDocumentError(f"不支持的文件类型：{suffix or path.name}")


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def load_markdown(path: str | Path) -> LoadedDocument:
    path = Path(path)
    return LoadedDocument(
        source_file=path.name,
        text=_read_text(path),
        file_type="md",
    )


def load_pdf(path: str | Path) -> LoadedDocument:
    path = Path(path)
    try:
        import pymupdf  # PyMuPDF 2.x 的正式包名；fitz 是已废弃别名
        open_pdf = pymupdf.open
    except ImportError:  # pragma: no cover - 兼容 1.x
        try:
            import fitz  # type: ignore[no-redef]

            open_pdf = fitz.open
        except ImportError as exc:
            raise UnsupportedDocumentError(
                "缺少 PyMuPDF，无法解析 PDF：uv add pymupdf"
            ) from exc

    pages: list[str] = []
    empty = 0
    with open_pdf(path) as pdf:
        for page in pdf:
            text = page.get_text("text") or ""
            if len(text.strip()) < 20:
                empty += 1
            pages.append(text)

    joined = "\n".join(pages)
    if not joined.strip():
        raise UnsupportedDocumentError(
            f"{path.name} 没有可提取的文本层（疑似扫描件），请先 OCR 或改用 Markdown 版本"
        )
    return LoadedDocument(
        source_file=path.name,
        text=joined,
        file_type="pdf",
        page_count=len(pages),
        empty_pages=empty,
    )


def load_pptx(path: str | Path) -> LoadedDocument:
    path = Path(path)
    try:
        from pptx import Presentation
    except ImportError as exc:  # pragma: no cover
        raise UnsupportedDocumentError(
            "缺少 python-pptx，无法解析 PPT：uv add python-pptx"
        ) from exc

    prs = Presentation(str(path))
    blocks: list[str] = []
    for idx, slide in enumerate(prs.slides, start=1):
        texts = [shape.text.strip() for shape in slide.shapes if getattr(shape, "has_text_frame", False)]
        note = ""
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
            note = slide.notes_slide.notes_text_frame.text.strip()
        body = "\n".join(t for t in texts if t)
        if note:
            body = f"{body}\n{note}".strip()
        if body:
            blocks.append(f"第{idx}页\n{body}")
    if not blocks:
        raise UnsupportedDocumentError(f"{path.name} 中没有可提取的文字")
    return LoadedDocument(
        source_file=path.name,
        text="\n\n".join(blocks),
        file_type="pptx",
        page_count=len(prs.slides),
    )


def load_image(path: str | Path) -> LoadedDocument:
    path = Path(path)
    """图片 OCR（qwen-vl-ocr）。

    **OCR 与对话模型解耦**：图片识别需要视觉模型，DeepSeek 目前没有视觉接口，
    所以这里固定走 DashScope 的 qwen-vl-ocr，并且只认 ``DASHSCOPE_API_KEY``。
    即使对话模型换成 DeepSeek（LLM_API_KEY / DEEPSEEK_API_KEY），
    只要另外配了 DashScope 的 key，图片仍可解析；没有则明确报错，
    绝不静默产出空文本。
    """
    import os

    from config import get_settings

    #: OCR 专用端点（与 LLM_BASE_URL 无关，避免把图片请求打到 DeepSeek）
    ocr_base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        raise UnsupportedDocumentError(
            "图片解析需要视觉模型（DashScope qwen-vl-ocr），请单独配置环境变量 "
            "DASHSCOPE_API_KEY；DeepSeek 接口目前不支持图片识别，"
            "可先把图片转成 Markdown/PDF 再上传。"
        )

    import httpx

    settings = get_settings()
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    b64 = base64.b64encode(path.read_bytes()).decode()
    payload = {
        "model": "qwen-vl-ocr",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    {"type": "text", "text": "请完整提取图片中的中文文字，保持原有段落顺序，不要总结。"},
                ],
            }
        ],
    }
    url = f"{ocr_base_url.rstrip('/')}/chat/completions"
    with httpx.Client(timeout=settings.llm.timeout_s) as client:
        resp = client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        )
    if resp.status_code >= 400:
        raise UnsupportedDocumentError(f"OCR 失败（HTTP {resp.status_code}）：{resp.text[:200]}")
    data = resp.json()
    try:
        text = data["choices"][0]["message"]["content"]
        if isinstance(text, list):
            text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
    except (KeyError, IndexError) as exc:
        raise UnsupportedDocumentError(f"OCR 返回结构异常：{str(data)[:200]}") from exc
    if not str(text).strip():
        raise UnsupportedDocumentError(f"{path.name} 未识别出文字")
    return LoadedDocument(
        source_file=path.name,
        text=str(text),
        file_type="image",
        page_count=1,
    )


_LOADERS = {
    "md": load_markdown,
    "pdf": load_pdf,
    "pptx": load_pptx,
    "image": load_image,
}


def load_document(path: str | Path) -> LoadedDocument:
    file_path = Path(path)
    if not file_path.exists():
        raise UnsupportedDocumentError(f"文件不存在：{file_path}")
    file_type = detect_file_type(file_path)
    return _LOADERS[file_type](file_path)
