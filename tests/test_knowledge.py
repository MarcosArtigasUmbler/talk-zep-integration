from app.zep.facts import FactTriple
from app.zep.knowledge import chunk_text, serialize


def test_chunk_text_keeps_paragraphs_whole():
    paras = [f"Parágrafo {i}. " + ("x" * 900) for i in range(20)]
    text = "\n\n".join(paras)  # ~18k chars
    chunks = chunk_text(text)
    assert len(chunks) >= 3
    assert all(len(c) <= 10_000 for c in chunks)
    assert "\n\n".join(chunks) == text  # nada perdido


def test_chunk_text_small_and_empty():
    assert chunk_text("curto") == ["curto"]
    assert chunk_text("   ") == []


def test_chunk_text_splits_giant_paragraph_on_sentences():
    text = ("Frase longa. " * 2000).strip()  # > 10k sem quebra de paragrafo
    chunks = chunk_text(text)
    assert all(len(c) <= 10_000 for c in chunks)
    assert len(chunks) > 1


def test_serialize_json_is_stable():
    assert serialize({"b": 1, "a": "é"}, "json") == '{"a": "é", "b": 1}'
    assert serialize("bruto", "json") == "bruto"


def test_fact_triple_validates_and_truncates():
    t = FactTriple(
        fact="x" * 300,
        fact_name="TEM_TAG",
        source_name="a",
        source_label="User",
        target_name="b",
        target_label="Tag",
    )
    assert len(t.fact) == 250
    try:
        FactTriple(
            fact="ok",
            fact_name="tem tag",
            source_name="a",
            source_label="User",
            target_name="b",
            target_label="Tag",
        )
    except ValueError:
        pass
    else:
        raise AssertionError("fact_name invalido deveria falhar")
