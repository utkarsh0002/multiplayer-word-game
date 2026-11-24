from collections.abc import Set
from typing import Literal, TypedDict

from word_lists import WORD_LISTS


AnswerStatus = Literal["blank", "invalid", "valid", "duplicate"]
POINTS = {"blank": 0, "invalid": 0, "valid": 10, "duplicate": 5}


class AnswerResult(TypedDict):
    status: AnswerStatus
    points: int


def score_answers(
    letter: str,
    categories: tuple[str, ...],
    submitted_answers: dict[str, dict[str, str]],
    *,
    approved: Set[tuple[str, str]] = frozenset(),
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
        valid = {
            player_id for player_id, answer in answers.items()
            if answer.startswith(starting_letter) and (answer in words or (player_id, category) in approved)
        }
        for player_id, opponent_id in ((first, second), (second, first)):
            answer = answers[player_id]
            if not answer:
                status: AnswerStatus = "blank"
            elif player_id not in valid:
                status = "invalid"
            elif opponent_id in valid and answer == answers[opponent_id]:
                status = "duplicate"
            else:
                status = "valid"
            points = POINTS[status]
            results[player_id][category] = {"status": status, "points": points}
            totals[player_id] += points

    return results, totals
