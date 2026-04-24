from .schemas import JudgeHandoff


class JudgeAgentSubgraph:
    @staticmethod
    def build_handoff(passed: bool, issues: list[str]) -> JudgeHandoff:
        actions: list[str] = []
        if not passed:
            risk = "high" if len(issues) >= 2 else "medium"
            actions.append("补充更具体的场景信息（地点、时间、行为）后重试。")
            actions.append("如涉及处罚金额或扣分，请明确要求返回对应法条原文依据。")
        else:
            risk = "low"
            actions.append("结果通过一致性校验，可直接展示。")

        return {
            "passed": bool(passed),
            "risk_level": risk,  # type: ignore[typeddict-item]
            "issues": list(issues or []),
            "actions": actions,
        }
