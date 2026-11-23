import secrets
from dataclasses import dataclass
from math import ceil
from string import ascii_uppercase
from time import monotonic, time

from scoring import AnswerResult, score_answers


MAX_ANSWER_LENGTH = 80


def choose_letter() -> str:
    return secrets.choice(ascii_uppercase)


@dataclass
class Round:
    letter: str
    categories: tuple[str, ...]
    started_at: int
    deadline: int
    deadline_monotonic: float
    drafts: dict[str, dict[str, str]]
    number: int = 1
    ended_by: str | None = None
    submitted_answers: dict[str, dict[str, str]] | None = None
    answer_results: dict[str, dict[str, AnswerResult]] | None = None
    round_scores: dict[str, int] | None = None

    @classmethod
    def begin(cls, categories: tuple[str, ...], duration_seconds: float, player_ids: list[str]) -> "Round":
        started_at = int(time() * 1000)
        return cls(
            letter=choose_letter(),
            categories=categories,
            started_at=started_at,
            deadline=started_at + int(duration_seconds * 1000),
            deadline_monotonic=monotonic() + duration_seconds,
            drafts={player_id: dict.fromkeys(categories, "") for player_id in player_ids},
        )

    def seconds_remaining(self) -> float:
        return max(0.0, self.deadline_monotonic - monotonic())

    def save_draft(self, player_id: str, category: object, answer: object) -> bool:
        if (
            not isinstance(category, str)
            or category not in self.categories
            or not isinstance(answer, str)
            or len(answer) > MAX_ANSWER_LENGTH
        ):
            return False
        # Unpaired Unicode surrogates can arrive in JSON escapes but cannot be sent as UTF-8.
        try:
            answer.encode("utf-8")
        except UnicodeEncodeError:
            return False
        self.drafts[player_id][category] = answer
        return True

    def submit(self, reason: str) -> dict[str, int]:
        submitted_answers = {
            player_id: {category: answer.strip() for category, answer in answers.items()}
            for player_id, answers in self.drafts.items()
        }
        answer_results, round_scores = score_answers(self.letter, self.categories, submitted_answers)
        self.submitted_answers = submitted_answers
        self.answer_results = answer_results
        self.round_scores = round_scores
        self.ended_by = reason
        return round_scores

    def snapshot(self) -> dict:
        return {
            "number": self.number,
            "letter": self.letter,
            "categories": self.categories,
            "started_at": self.started_at,
            "deadline": self.deadline,
            "remaining_ms": 0 if self.ended_by else ceil(self.seconds_remaining() * 1000),
            "ended_by": self.ended_by,
            "submitted_answers": self.submitted_answers,
            "answer_results": self.answer_results,
            "round_scores": self.round_scores,
        }
