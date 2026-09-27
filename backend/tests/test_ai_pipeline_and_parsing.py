import json
import pytest
from pydantic import ValidationError

from ai_pipeline import (
    EvidenceItem,
    SynthesisOutput,
    SupportingEvidenceItem,
    ThesisOutput,
    PitchOutput,
    clean_json_string,
    validate_thesis_sources
)
from verifier import parse_gemini_json_response, VerificationResult, VerifierError


def test_clean_json_string():
    # Markdown json fence
    raw1 = "```json\n{\"skills\": [\"Python\", \"Rust\"]}\n```"
    assert clean_json_string(raw1) == '{"skills": ["Python", "Rust"]}'

    # Leading and trailing conversational text
    raw2 = "Here is the resulting JSON:\n{\"skills\": [\"Go\"]}\nHope this helps!"
    assert clean_json_string(raw2) == '{"skills": ["Go"]}'


def test_synthesis_schema_validation():
    valid_data = {
        "skills": ["Distributed Systems", "PostgreSQL"],
        "notable_projects": ["Engine: fast DB core"],
        "evidence": [
            {"claim": "Engineered distributed replication protocol", "source_repo": "db-core"}
        ]
    }
    synthesis = SynthesisOutput.model_validate(valid_data)
    assert len(synthesis.skills) == 2
    assert synthesis.evidence[0].source_repo == "db-core"

    # Missing evidence should fail
    with pytest.raises(ValidationError):
        SynthesisOutput.model_validate({
            "skills": ["Python"],
            "notable_projects": ["Project 1"],
            "evidence": []  # min_length=1
        })


def test_thesis_sources_validation():
    valid_repos = ["fastapi", "sqlmodel"]
    company_evidence = [
        "Supabase Job Posting: 'Requirements include deep internals of PostgreSQL and logical replication.'"
    ]

    # Valid thesis where every claim links to a known repo or company signal
    valid_thesis = ThesisOutput(
        role_title="Senior Database Infrastructure Engineer",
        justification="Deeply experienced in PostgreSQL abstractions and high performance.",
        supporting_evidence=[
            SupportingEvidenceItem(claim="Built relational abstractions", source="sqlmodel"),
            SupportingEvidenceItem(claim="Matches company postgres requirements", source="Supabase Job Posting")
        ]
    )
    invalid = validate_thesis_sources(valid_thesis, valid_repos, company_evidence)
    assert len(invalid) == 0

    # Invalid thesis with hallucinated repo
    invalid_thesis = ThesisOutput(
        role_title="Senior Database Infrastructure Engineer",
        justification="Deeply experienced in PostgreSQL abstractions and high performance.",
        supporting_evidence=[
            SupportingEvidenceItem(claim="Built relational abstractions", source="hallucinated-repo-999")
        ]
    )
    invalid_results = validate_thesis_sources(invalid_thesis, valid_repos, company_evidence)
    assert len(invalid_results) == 1
    assert "hallucinated-repo-999" in invalid_results[0]


def test_verifier_gemini_parsing():
    # Test clean json
    raw1 = '{"verdict": "SUPPORTED", "reason": "Claims match repository code exactly."}'
    result1 = parse_gemini_json_response(raw1)
    assert result1.verdict == "SUPPORTED"
    assert "repository code" in result1.reason

    # Test markdown fence + partial support
    raw2 = '```json\n{"verdict": "Partially_Supported", "reason": "Some claims match, but role title is ambitious."}\n```'
    result2 = parse_gemini_json_response(raw2)
    assert result2.verdict == "PARTIALLY_SUPPORTED"

    # Test overreaching
    raw3 = '{"verdict": "overreaching", "reason": "Candidate has not touched C++ or kernel code."}'
    result3 = parse_gemini_json_response(raw3)
    assert result3.verdict == "OVERREACHING"

    # Malformed text
    with pytest.raises(VerifierError):
        parse_gemini_json_response("This is not JSON at all.")


def test_pitch_schema_validation():
    valid_pitch = {
        "subject": "Staff Platform Engineer — FastAPI ergonomics for Supabase",
        "outreach_message": "Hi Team, love what you are building with Postgres...",
        "highlighted_claims": ["Creator of FastAPI", "PostgreSQL schema validation"],
        "call_to_action": "Let's connect next Tuesday."
    }
    pitch = PitchOutput.model_validate(valid_pitch)
    assert pitch.subject.startswith("Staff Platform")
    assert len(pitch.highlighted_claims) == 2
