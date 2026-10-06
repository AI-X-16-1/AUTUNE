from autune_intelligence import retrieval, retrieval_eval


def test_scores_are_recall_at_1_and_3_and_mrr() -> None:
    ranked = [["a", "b", "c"], ["x", "y", "b"], ["q", "r", "s"]]
    expected = ["a", "b", "z"]

    s = retrieval_eval.score(ranked, expected)

    assert s == {"recall@1": 1 / 3, "recall@3": 2 / 3, "mrr": (1 + 1 / 3) / 3}


def test_every_question_names_a_passage_that_exists() -> None:
    from autune_intelligence import glossary

    keys = {p.key for p in glossary.passages()}
    dev, holdout = retrieval_eval.questions("dev"), retrieval_eval.questions("holdout")
    assert 25 <= len(dev) <= 40 and 20 <= len(holdout) <= 30
    assert all(q["expected"] in keys for q in [*dev, *holdout])
    assert not {q["question"] for q in dev} & {q["question"] for q in holdout}


def test_a_model_that_cannot_load_skips_the_dense_rows(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("offline")

    monkeypatch.setattr(retrieval, "DenseRetriever", boom)

    assert retrieval_eval.main([]) == 0
    out = capsys.readouterr().out
    assert "dev (" in out and "holdout (" in out and "bm25" in out
    assert "skipped (model unavailable: OSError)" in out
