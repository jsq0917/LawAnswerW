"""历史窗口裁剪测试（阶段 5，全离线，§6.5 规则）。"""

from __future__ import annotations

from app.generation.history import split_rounds, trim_history


def _msg(role: str, content: str) -> dict:
    return {"role": role, "content": content}


class TestSplitRounds:
    def test_basic(self):
        rounds = split_rounds([
            _msg("user", "q1"),
            _msg("assistant", "a1"),
            _msg("user", "q2"),
            _msg("assistant", "a2"),
        ])
        assert len(rounds) == 2
        assert rounds[0] == [_msg("user", "q1"), _msg("assistant", "a1")]

    def test_leading_assistant_tolerated(self):
        """残缺历史（assistant 开头）不丢弃，自成一轮。"""
        rounds = split_rounds([_msg("assistant", "a"), _msg("user", "q")])
        assert len(rounds) == 2
        assert rounds[0][0]["role"] == "assistant"


class TestTrimHistory:
    def test_round_limit(self):
        history = []
        for i in range(8):
            history.append(_msg("user", f"问题{i}"))
            history.append(_msg("assistant", f"回答{i}" + "长" * 50))
        out = trim_history(history, max_rounds=5, token_budget=100000)
        assert len(out) == 10
        assert out[0]["content"] == "问题3"  # 只留最近 5 轮

    def test_zero_rounds_returns_empty(self):
        assert trim_history([_msg("user", "q")], max_rounds=0) == []

    def test_token_budget_drops_oldest_rounds(self):
        history = []
        for i in range(6):
            history.append(_msg("user", f"问题{i}" + "长" * 100))
            history.append(_msg("assistant", f"回答{i}" + "长" * 100))
        # 每轮约 138 token，6 轮共 828；预算 600 → 丢到剩 4 轮（552）
        out = trim_history(history, max_rounds=6, token_budget=600)
        assert out[0]["content"].startswith("问题2")
        assert len(out) == 8

    def test_deep_over_budget_keeps_user_only_for_old(self):
        """丢到 2 轮仍超预算：更早的轮只保留用户问句，最近一轮完整（§6.5）。"""
        history = []
        for i in range(5):
            history.append(_msg("user", f"问题{i}" + "长" * 60))
            history.append(_msg("assistant", f"回答{i}" + "长" * 60))
        # 每轮约 84 token。预算 150：先丢最旧整轮到 2 轮（168 仍超），
        # 再把较旧那轮压缩为只留用户问句（42），最近一轮完整保留
        out = trim_history(history, max_rounds=5, token_budget=150)
        assert [m["content"][:3] for m in out] == ["问题3", "问题4", "回答4"]
        assert sum(1 for m in out if m["role"] == "assistant") == 1

    def test_budget_within_two_rounds_keeps_both(self):
        history = []
        for i in range(4):
            history.append(_msg("user", f"问题{i}" + "长" * 60))
            history.append(_msg("assistant", f"回答{i}" + "长" * 60))
        # 4 轮共 336；预算 250 → 丢到 2 轮（168）即停，两轮都完整
        out = trim_history(history, max_rounds=4, token_budget=250)
        assert [m["content"][:3] for m in out] == ["问题2", "回答2", "问题3", "回答3"]

    def test_latest_user_always_kept(self):
        history = [_msg("user", "老问题" + "长" * 500), _msg("assistant", "老回答" + "长" * 500), _msg("user", "当前问题")]
        out = trim_history(history, max_rounds=5, token_budget=50)
        assert out[-1] == _msg("user", "当前问题")

    def test_empty_and_none(self):
        assert trim_history(None) == []
        assert trim_history([]) == []
        assert trim_history([_msg("user", "  ")]) == []

    def test_order_preserved(self):
        history = [_msg("user", "q1"), _msg("assistant", "a1"), _msg("user", "q2")]
        out = trim_history(history, max_rounds=5, token_budget=1000)
        assert [m["content"] for m in out] == ["q1", "a1", "q2"]

    def test_history_rounds_support_anaphora(self):
        """指代补全依赖历史：最近一轮 user+assistant 成对保留。"""
        history = [
            _msg("user", "试用期最长多久？"),
            _msg("assistant", "劳动合同法第十九条规定……"),
            _msg("user", "那公司违反了怎么办？"),
        ]
        out = trim_history(history, max_rounds=2, token_budget=1000)
        assert out == history  # 预算充足时原样保留，供模型解析"那"
