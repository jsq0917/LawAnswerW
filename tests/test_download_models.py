"""模型下载脚本测试：本地状态判定、safetensors 完整性、endpoint 别名映射。

这里锁住三个真实踩过的坑：
1. `--endpoint mirror` 传的是别名，直接塞给 HF_ENDPOINT 会拼出相对 URL，
   报 "Request URL is missing an 'http://' or 'https://' protocol"。
2. huggingface_hub 2.x 走 Xet 协议会被镜像站 401，必须设 HF_HUB_DISABLE_XET。
3. 下载中断会留下"大小看着不错、张量数据不全"的 safetensors，加载时才炸。
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from scripts import download_models as dm

BIG = 200 * 1024 * 1024


def _make_model_dir(
    root: Path, weight_size: int = BIG, name: str = "pytorch_model.bin"
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.json").write_text('{"model_type": "xlm-roberta"}', encoding="utf-8")
    with open(root / name, "wb") as f:
        f.seek(max(weight_size - 1, 0))
        f.write(b"x")
    return root


def _safetensors_header(payload: int) -> bytes:
    return json.dumps(
        {"w": {"dtype": "F32", "shape": [payload // 4], "data_offsets": [0, payload]}}
    ).encode("utf-8")


def _make_safetensors_dir(root: Path, payload: int = BIG, keep: int | None = None) -> Path:
    """造一个（可指定截断的）safetensors 模型目录。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.json").write_text("{}", encoding="utf-8")
    header = _safetensors_header(payload)
    actual = payload if keep is None else keep
    with open(root / "model.safetensors", "wb") as f:
        f.write(struct.pack("<Q", len(header)))
        f.write(header)
        if actual:
            f.seek(actual - 1, 1)
            f.write(b"x")
    return root


class TestInspect:
    def test_missing_dir(self, tmp_path):
        target = dm.ModelTarget("t", "BAAI/x", tmp_path / "nope", "")
        ok, message = dm.inspect(target)
        assert ok is False
        assert "目录不存在" in message

    def test_empty_hub_cache_shell_detected(self, tmp_path):
        """真实场景：只有 refs 指针、没有 config.json 的缓存壳子必须判为未就绪。"""
        shell = tmp_path / "models--BAAI--bge-m3"
        (shell / "refs").mkdir(parents=True)
        (shell / "refs" / "main").write_text("5617a9f", encoding="utf-8")
        ok, message = dm.inspect(dm.ModelTarget("t", "BAAI/x", shell, ""))
        assert ok is False
        assert "config.json" in message

    def test_ready_with_pytorch_bin(self, tmp_path):
        root = _make_model_dir(tmp_path / "m")
        ok, message = dm.inspect(dm.ModelTarget("t", "BAAI/x", root, ""))
        assert ok is True
        assert "pytorch_model.bin" in message

    def test_ready_with_intact_safetensors(self, tmp_path):
        root = _make_safetensors_dir(tmp_path / "m")
        ok, message = dm.inspect(dm.ModelTarget("t", "BAAI/x", root, ""))
        assert ok is True
        assert "model.safetensors" in message

    def test_too_small_weight_rejected(self, tmp_path):
        root = _make_model_dir(tmp_path / "m", weight_size=1024)
        ok, message = dm.inspect(dm.ModelTarget("t", "BAAI/x", root, ""))
        assert ok is False
        assert "偏小" in message

    def test_sharded_index_counts_as_ready(self, tmp_path):
        root = tmp_path / "m"
        root.mkdir()
        (root / "config.json").write_text("{}", encoding="utf-8")
        (root / "model.safetensors.index.json").write_text("{}", encoding="utf-8")
        ok, message = dm.inspect(dm.ModelTarget("t", "BAAI/x", root, ""))
        assert ok is True
        assert "分片" in message


@pytest.mark.parametrize("payload", [0, 1, 1024, 99 * 1024 * 1024])
def test_size_threshold_boundary(tmp_path, payload):
    root = _make_model_dir(tmp_path / "m", weight_size=max(payload, 1))
    ok, _ = dm.inspect(dm.ModelTarget("t", "BAAI/x", root, ""))
    assert ok is (payload >= 100 * 1024 * 1024)


class TestSafetensorsIntegrity:
    def test_intact_file(self, tmp_path):
        path = _make_safetensors_dir(tmp_path / "m", payload=1024) / "model.safetensors"
        ok, detail = dm.safetensors_intact(path)
        assert ok is True
        assert "1 个张量" in detail

    def test_truncated_payload_detected(self, tmp_path):
        """下载中断的典型表现：头部完整、数据少一截。"""
        root = _make_safetensors_dir(tmp_path / "m", payload=1024, keep=512)
        ok, detail = dm.safetensors_intact(root / "model.safetensors")
        assert ok is False
        assert "截断" in detail

    def test_too_short_for_header(self, tmp_path):
        path = tmp_path / "model.safetensors"
        path.write_bytes(b"12")
        ok, detail = dm.safetensors_intact(path)
        assert ok is False
        assert "文件头不完整" in detail

    def test_invalid_json_header(self, tmp_path):
        path = tmp_path / "model.safetensors"
        bad = b"not-json"
        path.write_bytes(struct.pack("<Q", len(bad)) + bad)
        ok, detail = dm.safetensors_intact(path)
        assert ok is False
        assert "合法 JSON" in detail

    def test_absurd_header_length(self, tmp_path):
        path = tmp_path / "model.safetensors"
        path.write_bytes(struct.pack("<Q", 10**12))
        ok, detail = dm.safetensors_intact(path)
        assert ok is False
        assert "异常" in detail

    def test_empty_header(self, tmp_path):
        path = tmp_path / "model.safetensors"
        path.write_bytes(struct.pack("<Q", 2) + b"{}")
        ok, detail = dm.safetensors_intact(path)
        assert ok is False
        assert "没有任何张量" in detail

    def test_inspect_rejects_truncated_safetensors(self, tmp_path):
        root = _make_safetensors_dir(tmp_path / "m", payload=BIG)
        ok, _ = dm.inspect(dm.ModelTarget("t", "BAAI/x", root, ""))
        assert ok is True  # 截断前是完整的

        weight = root / "model.safetensors"
        with open(weight, "r+b") as f:
            f.truncate(weight.stat().st_size - 1024)
        ok, message = dm.inspect(dm.ModelTarget("t", "BAAI/x", root, ""))
        assert ok is False
        assert "截断" in message


class TestEndpointMapping:
    def test_alias_is_converted_to_url(self, tmp_path, monkeypatch):
        seen: list[str] = []

        def fake_snapshot(repo_id, dest, endpoint):
            seen.append(endpoint)
            _make_model_dir(Path(dest))
            return 0

        monkeypatch.setattr(dm, "_run_snapshot", fake_snapshot)
        target = dm.ModelTarget("bge-m3", "BAAI/bge-m3", tmp_path / "m", "")
        assert dm.download(target, "mirror") is True
        assert seen == ["https://hf-mirror.com"]

    def test_hf_alias(self, tmp_path, monkeypatch):
        seen: list[str] = []

        def fake_snapshot(repo_id, dest, endpoint):
            seen.append(endpoint)
            _make_model_dir(Path(dest))
            return 0

        monkeypatch.setattr(dm, "_run_snapshot", fake_snapshot)
        target = dm.ModelTarget("bge-m3", "BAAI/bge-m3", tmp_path / "m", "")
        assert dm.download(target, "hf") is True
        assert seen == ["https://huggingface.co"]

    def test_auto_falls_back_to_second_endpoint(self, tmp_path, monkeypatch):
        seen: list[str] = []
        monkeypatch.setattr(dm.time, "sleep", lambda *_: None)

        def flaky(repo_id, dest, endpoint):
            seen.append(endpoint)
            if len(seen) == 1:
                return 1  # 镜像失败
            _make_model_dir(Path(dest))
            return 0

        monkeypatch.setattr(dm, "_run_snapshot", flaky)
        target = dm.ModelTarget("bge-m3", "BAAI/bge-m3", tmp_path / "m", "")
        assert dm.download(target, "auto", retries=1) is True
        assert seen == ["https://hf-mirror.com", "https://huggingface.co"]

    def test_retries_same_endpoint_before_falling_back(self, tmp_path, monkeypatch):
        """网络抖动（代理 502）常见：同一源重试成功就不要换源。"""
        seen: list[str] = []
        monkeypatch.setattr(dm.time, "sleep", lambda *_: None)

        def flaky(repo_id, dest, endpoint):
            seen.append(endpoint)
            if len(seen) < 2:
                return 1
            _make_model_dir(Path(dest))
            return 0

        monkeypatch.setattr(dm, "_run_snapshot", flaky)
        target = dm.ModelTarget("bge-m3", "BAAI/bge-m3", tmp_path / "m", "")
        assert dm.download(target, "auto", retries=2) is True
        assert seen == ["https://hf-mirror.com", "https://hf-mirror.com"]

    def test_skip_when_already_ready(self, tmp_path, monkeypatch):
        root = _make_model_dir(tmp_path / "m")

        def must_not_call(*args, **kwargs):  # pragma: no cover
            raise AssertionError("已就绪时不应再下载")

        monkeypatch.setattr(dm, "_run_snapshot", must_not_call)
        target = dm.ModelTarget("bge-m3", "BAAI/bge-m3", root, "")
        assert dm.download(target, "mirror") is True

    def test_modelscope_prints_manual_steps(self, tmp_path, capsys):
        target = dm.ModelTarget("reranker", "BAAI/bge-reranker-v2-m3", tmp_path / "m", "")
        assert dm.download(target, "modelscope") is False
        out = capsys.readouterr().out
        assert "modelscope download" in out


class TestCli:
    def test_check_returns_nonzero_when_missing(self, tmp_path, monkeypatch):
        targets = [dm.ModelTarget("bge-m3", "BAAI/bge-m3", tmp_path / "m", "")]
        monkeypatch.setattr(dm, "_targets", lambda: targets)
        assert dm.main(["--check", "--model", "bge-m3"]) == 1

    def test_check_returns_zero_when_ready(self, tmp_path, monkeypatch):
        root = _make_model_dir(tmp_path / "m")
        monkeypatch.setattr(
            dm, "_targets", lambda: [dm.ModelTarget("bge-m3", "BAAI/bge-m3", root, "")]
        )
        assert dm.main(["--check", "--model", "bge-m3"]) == 0


class TestTargets:
    def test_two_targets_with_expected_repos(self):
        repos = {t.key: t.repo_id for t in dm._targets()}
        assert repos == {"bge-m3": "BAAI/bge-m3", "reranker": "BAAI/bge-reranker-v2-m3"}

    def test_onnx_is_ignored(self):
        assert any("onnx" in pattern for pattern in dm._IGNORE_PATTERNS)

    def test_xet_disabled_and_endpoint_passed_to_subprocess(self, tmp_path, monkeypatch):
        """镜像站不代理 Xet 的 CAS 域名，必须显式关闭，否则 401。"""
        captured: dict[str, str] = {}

        def fake_call(cmd, env=None, **kwargs):
            captured.update(env or {})
            return 0

        monkeypatch.setattr(dm.subprocess, "call", fake_call)
        dm._run_snapshot("BAAI/bge-m3", tmp_path / "m", "https://hf-mirror.com")
        assert captured.get("HF_HUB_DISABLE_XET") == "1"
        assert captured.get("HF_ENDPOINT") == "https://hf-mirror.com"
