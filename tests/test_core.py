from evaluators.rules import evaluate


def test_evaluation_uses_native_skill_reads_and_reference_answer():
    assert evaluate("25.00", [], ["25.00"], "order-total")["task_success"] == 0
    calls = [{"name": "order-total"}]
    assert evaluate("25.00 CNY", calls, ["25.00", "CNY"], "order-total")["task_success"] == 1
    assert evaluate("25.00", calls, ["25.00", "CNY"], "order-total")["quality"] == 0.5
    assert "quality" not in evaluate("hi", [], [], None)
