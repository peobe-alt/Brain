"""What an expert analysis actually costs.

A founder deciding whether to scan daily needs the number, not a shrug. The
rates below are the published per-million-token prices; they move, so they
live in one table with the date they were checked.
"""

from __future__ import annotations

from dataclasses import dataclass

#: USD per million tokens (input, output), checked 2026-09.
PRICING_USD: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

USD_TO_EUR = 0.92
DEFAULT_PRICING = (5.0, 25.0)


@dataclass(slots=True)
class Cost:
    input_tokens: int
    output_tokens: int
    eur: float

    def __add__(self, other: "Cost") -> "Cost":
        return Cost(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.eur + other.eur,
        )

    def label(self) -> str:
        if not self.input_tokens and not self.output_tokens:
            return "aucun appel modele"
        return (
            f"{self.input_tokens + self.output_tokens:,} tokens, "
            f"environ {self.eur:.2f} EUR"
        ).replace(",", " ")


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> Cost:
    """Convert a token count into euros, for the model actually used."""
    rate_in, rate_out = PRICING_USD.get(model, DEFAULT_PRICING)
    usd = (input_tokens / 1_000_000) * rate_in + (output_tokens / 1_000_000) * rate_out
    return Cost(input_tokens, output_tokens, round(usd * USD_TO_EUR, 4))


def zero() -> Cost:
    return Cost(0, 0, 0.0)
