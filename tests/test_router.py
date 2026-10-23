"""路由矩阵单测：§6.3 六分支全覆盖 + 知识库解析 + 用户勾选交集。"""

from __future__ import annotations

from app.orchestration.models import IntentResult
from app.orchestration.router import ALL_KB_IDS, kbs_for_laws, route


def _intent(**overrides) -> IntentResult:
    base = dict(
        intent="labor_law",
        rewrite_type="direct",
        sub_queries=["q"],
        complexity="medium",
        hit_structured=False,
        structured_topic=None,
        law_hints=["中华人民共和国劳动合同法", "中华人民共和国劳动法"],
        need_history=False,
    )
    base.update(overrides)
    return IntentResult(**base)


class TestKbsForLaws:
    def test_dedup_and_order(self):
        assert kbs_for_laws(
            ["中华人民共和国劳动合同法", "中华人民共和国劳动法", "中华人民共和国民法典"]
        ) == ["kb_labor", "kb_civil"]

    def test_empty(self):
        assert kbs_for_laws([]) == []

    def test_unknown_law_ignored(self):
        assert kbs_for_laws(["假法律"]) == []


class TestGreetingRoute:
    def test_greeting_no_sources(self):
        decision = route(_intent(intent="greeting", law_hints=[]))
        assert decision.route == "greeting"
        assert decision.use_mysql_qa is False
        assert decision.use_milvus is False
        assert decision.kb_ids == []


class TestNoDataRoute:
    def test_other_intent(self):
        decision = route(_intent(intent="other", law_hints=[]))
        assert decision.route == "no_data"
        assert decision.use_milvus is False

    def test_selected_kb_excludes_relevant_law(self):
        # 用户只勾了民法典库，问劳动问题 → 无数据
        decision = route(_intent(intent="labor_law"), selected_kb_ids=["kb_civil"])
        assert decision.route == "no_data"
        assert decision.use_milvus is False
        assert "kb_civil" in decision.reason

    def test_no_law_hints_falls_back_to_all_kbs(self):
        decision = route(_intent(law_hints=[]))
        assert decision.route == "pure_vector"
        assert decision.kb_ids == list(ALL_KB_IDS)


class TestPureStructuredRoute:
    def test_hit_goes_mysql_only(self):
        decision = route(
            _intent(hit_structured=True, structured_topic="试用期"),
            structured_hit=True,
        )
        assert decision.route == "pure_structured"
        assert decision.use_mysql_qa is True
        assert decision.use_milvus is False  # 关键约束：不碰 Milvus
        assert decision.kb_ids == ["kb_labor"]


class TestHybridRoute:
    def test_hit_but_no_exact_answer_uses_both(self):
        decision = route(
            _intent(hit_structured=True, structured_topic="试用期"),
            structured_hit=False,
        )
        assert decision.route == "hybrid"
        assert decision.use_mysql_qa is True
        assert decision.use_milvus is True


class TestPureVectorRoute:
    def test_no_structured_hit_skips_mysql(self):
        decision = route(_intent(hit_structured=False))
        assert decision.route == "pure_vector"
        assert decision.use_mysql_qa is False  # 关键约束：不查问答表
        assert decision.use_milvus is True

    def test_civil_law_routes_to_civil_kb(self):
        decision = route(
            _intent(intent="civil_code", law_hints=["中华人民共和国民法典"], hit_structured=False)
        )
        assert decision.route == "pure_vector"
        assert decision.kb_ids == ["kb_civil"]


class TestCrossLawRoute:
    def test_general_ignores_structured_flag(self):
        decision = route(
            _intent(
                intent="general",
                hit_structured=True,  # 跨法问题忽略结构化命中
                law_hints=[
                    "中华人民共和国劳动合同法",
                    "中华人民共和国劳动法",
                    "中华人民共和国民法典",
                ],
            )
        )
        assert decision.route == "cross_law"
        assert decision.use_milvus is True
        assert decision.use_mysql_qa is False
        assert decision.kb_ids == ["kb_labor", "kb_civil"]

    def test_general_with_no_selection_uses_all_kbs(self):
        decision = route(_intent(intent="general", law_hints=[]))
        assert decision.route == "cross_law"
        assert set(decision.kb_ids) == set(ALL_KB_IDS)


class TestSelectedKbs:
    def test_user_selection_intersects(self):
        decision = route(
            _intent(intent="civil_code", law_hints=["中华人民共和国民法典"]),
            selected_kb_ids=["kb_labor", "kb_civil"],
        )
        # 民法典库在勾选范围内 → 保留用户勾选（不缩窄）
        assert decision.kb_ids == ["kb_labor", "kb_civil"]

    def test_user_selection_deduped(self):
        decision = route(
            _intent(intent="civil_code", law_hints=["中华人民共和国民法典"]),
            selected_kb_ids=["kb_civil", "kb_civil"],
        )
        assert decision.kb_ids == ["kb_civil"]

    def test_structured_hit_with_user_selection(self):
        decision = route(
            _intent(hit_structured=True, structured_topic="试用期"),
            selected_kb_ids=["kb_labor"],
            structured_hit=True,
        )
        assert decision.route == "pure_structured"
        assert decision.use_milvus is False
