"""The Claude path, verified without calling Claude.

A deep pass runs over a shortlist of real cars and costs real money, so its
failure modes have to be pinned down before they happen in production: what
exactly gets sent, and what happens when the call goes wrong.
"""

from __future__ import annotations

import anthropic
import httpx2
import pytest

from carexpert.expert.analyst import ExpertAnalyst
from carexpert.expert.cost import estimate_cost
from carexpert.expert.schema import ExpertReport
from carexpert.normalize import enrich
from carexpert.schemas import ListingData, Photo

VALUATION = {
    "delta_pct": 0.21, "comps_count": 16, "fair_price_eur": 17000.0,
    "low_eur": 16000.0, "high_eur": 18000.0, "confidence": 0.85,
    "method": "comparables:strict",
}


def _listing(photos: int = 0) -> ListingData:
    listing = ListingData(
        source="test", source_id="1", url="https://site.fr/annonce/1",
        title="Volkswagen Golf 1.6 TDI 115 Confortline 2019",
        description="Carnet d'entretien complet, premiere main, CT vierge.",
        price=13500, km=92_000, year=2019,
        photos=[Photo(url=f"https://site.fr/p{i}.jpg") for i in range(photos)],
    )
    return enrich(listing)


def _report() -> ExpertReport:
    return ExpertReport(
        summary="Voiture saine, prix sous le marche, rien de suspect.",
        condition_score=82, consistency_score=88, photo_findings=[],
        red_flags=[], strengths=["Carnet complet"], known_issues_to_check=[],
        questions_to_seller=["La distribution a-t-elle ete faite ?"],
        negotiation_levers=[], estimated_repairs_eur=0, verdict="grab", confidence=80,
    )


class StubResponse:
    def __init__(self, parsed=None, stop_reason="end_turn", tokens=(42000, 2200)):
        self.parsed_output = parsed
        self.stop_reason = stop_reason
        self.stop_details = None
        self.usage = type("U", (), {"input_tokens": tokens[0], "output_tokens": tokens[1]})()


class StubClient:
    """Records the request instead of sending it."""

    def __init__(self, response=None, error: Exception | None = None):
        self.calls: list[dict] = []
        self._response = response or StubResponse(_report())
        self._error = error
        outer = self

        class _Messages:
            def parse(self, **kwargs):
                outer.calls.append(kwargs)
                if outer._error is not None:
                    raise outer._error
                return outer._response

        self.messages = _Messages()


def _http_error(cls, status: int):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(status, request=request)
    return cls("erreur", response=response, body=None)


# --- what gets sent --------------------------------------------------------

def test_the_request_has_the_expected_shape():
    client = StubClient()
    ExpertAnalyst(client=client, model="claude-opus-5").analyze(
        _listing(), valuation=VALUATION, with_photos=False
    )
    sent = client.calls[0]
    assert sent["model"] == "claude-opus-5"
    assert sent["max_tokens"] >= 16000
    assert sent["output_format"] is ExpertReport
    assert sent["thinking"] == {"type": "adaptive"}
    assert sent["messages"][0]["role"] == "user"
    assert "expert automobile" in sent["system"]


def test_the_dossier_carries_the_facts_and_the_valuation():
    client = StubClient()
    ExpertAnalyst(client=client).analyze(_listing(), valuation=VALUATION, with_photos=False)
    text = client.calls[0]["messages"][0]["content"][-1]["text"]
    assert "Volkswagen" in text
    assert "92 000 km" in text
    assert "21% sous marche" in text or "21% sous" in text
    assert "16 annonces comparables" in text
    assert "Aucune photo" in text          # honest about what it could not see


def test_photos_are_sent_before_the_question(monkeypatch):
    """The text refers to photo indices, so block order is meaningful."""
    from carexpert.expert import analyst as module
    from carexpert.expert.photos import PreparedPhoto

    monkeypatch.setattr(
        module, "prepare_photos",
        lambda photos, limit=None: [
            PreparedPhoto(index=i, url=p.url, media_type="image/jpeg", data_b64="Zm9v")
            for i, p in enumerate(photos[:3])
        ],
    )
    client = StubClient()
    ExpertAnalyst(client=client).analyze(_listing(photos=5), valuation=VALUATION)
    content = client.calls[0]["messages"][0]["content"]
    assert [block["type"] for block in content] == ["image", "image", "image", "text"]
    assert content[0]["source"]["type"] == "base64"
    assert "index 0 a 2" in content[-1]["text"]
    assert "publie 5" in content[-1]["text"]      # says how many were left out


# --- what comes back -------------------------------------------------------

def test_a_successful_analysis_is_returned_with_its_cost():
    result = ExpertAnalyst(client=StubClient()).analyze(_listing(), with_photos=False)
    assert not result.degraded
    assert result.used_model
    assert result.report.verdict == "grab"
    assert result.input_tokens == 42000
    assert result.cost().eur == pytest.approx(estimate_cost("claude-opus-5", 42000, 2200).eur)


def test_a_refusal_degrades_instead_of_crashing():
    client = StubClient(StubResponse(None, stop_reason="refusal"))
    result = ExpertAnalyst(client=client).analyze(_listing(), with_photos=False)
    assert result.degraded
    assert "refusee" in result.degraded_reason
    assert result.report.summary          # the rule-based report still stands in


def test_a_truncated_answer_says_how_to_fix_it():
    client = StubClient(StubResponse(_report(), stop_reason="max_tokens"))
    result = ExpertAnalyst(client=client).analyze(_listing(), with_photos=False)
    assert result.degraded
    assert "MAX_TOKENS" in result.degraded_reason.upper()


def test_an_empty_parse_degrades():
    client = StubClient(StubResponse(None, stop_reason="end_turn"))
    result = ExpertAnalyst(client=client).analyze(_listing(), with_photos=False)
    assert result.degraded
    assert not result.used_model


@pytest.mark.parametrize(
    "error,expected",
    [
        (_http_error(anthropic.RateLimitError, 429), "debit"),
        (_http_error(anthropic.AuthenticationError, 401), "ANTHROPIC_API_KEY"),
        (_http_error(anthropic.NotFoundError, 404), "CAREXPERT_MODEL"),
        (anthropic.APIConnectionError(
            request=httpx2.Request("POST", "https://api.anthropic.com/")), "reseau"),
        (RuntimeError("panne inattendue"), "panne inattendue"),
    ],
)
def test_every_api_failure_degrades_with_an_actionable_message(error, expected):
    """One bad call must never cost the whole batch."""
    client = StubClient(error=error)
    result = ExpertAnalyst(client=client).analyze(_listing(), with_photos=False)
    assert result.degraded
    assert expected in result.degraded_reason
    assert result.report.verdict in ("grab", "check", "avoid")


def test_a_degraded_analysis_costs_nothing():
    client = StubClient(error=RuntimeError("panne"))
    result = ExpertAnalyst(client=client).analyze(_listing(), with_photos=False)
    assert result.cost().eur == 0.0
