from typing import Literal, TypedDict

from word_lists import WORD_LISTS


AnswerStatus = Literal["blank", "invalid", "valid", "duplicate"]
POINTS = {"blank": 0, "invalid": 0, "valid": 10, "duplicate": 5}


class AnswerResult(TypedDict):
    status: AnswerStatus
    points: int


def classify_answer(answer: str, opponent: str, letter: str, words: frozenset[str]) -> AnswerStatus:
    if not answer:
        return "blank"
    if not answer.startswith(letter) or answer not in words:
        return "invalid"
    # An identical normalized answer passes the same letter and category checks.
    if answer == opponent:
        return "duplicate"
    return "valid"


def score_answers(
    letter: str, categories: tuple[str, ...], submitted_answers: dict[str, dict[str, str]]
) -> tuple[dict[str, dict[str, AnswerResult]], dict[str, int]]:
    """Score the two players' frozen answers independently by category."""
    first, second = submitted_answers
    results: dict[str, dict[str, AnswerResult]] = {first: {}, second: {}}
    totals = {first: 0, second: 0}
    starting_letter = letter.casefold()

    for category in categories:
        words = WORD_LISTS[category]
        answers = {
            player_id: submitted_answers[player_id][category].strip().casefold()
            for player_id in (first, second)
        }
        for player_id, opponent_id in ((first, second), (second, first)):
            status = classify_answer(answers[player_id], answers[opponent_id], starting_letter, words)
            points = POINTS[status]
            results[player_id][category] = {"status": status, "points": points}
            totals[player_id] += points

    return results, totals
