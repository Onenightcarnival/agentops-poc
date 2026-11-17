"""Offline reference checks; never imported by the serving application."""


def evaluate(
    answer: str, runs: list[dict], expected_contains: list[str], expected_skill: str | None
) -> dict[str, float]:
    nonempty = bool(answer.strip())
    expected_executed = expected_skill is None or any(run["name"] == expected_skill for run in runs)
    matches = [part.casefold() in answer.casefold() for part in expected_contains]
    scores = {
        "response_nonempty": float(nonempty),
        "task_success": float(nonempty and expected_executed and all(matches)),
    }
    if matches:
        scores["quality"] = sum(matches) / len(matches)
    return scores
