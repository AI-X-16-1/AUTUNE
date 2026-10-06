from autune_intelligence import retrieval_eval


def test_scores_are_recall_at_1_and_3_and_mrr() -> None:
    ranked = [["a", "b", "c"], ["x", "y", "b"], ["q", "r", "s"]]
    expected = ["a", "b", "z"]

    s = retrieval_eval.score(ranked, expected)

    assert s == {"recall@1": 1 / 3, "recall@3": 2 / 3, "mrr": (1 + 1 / 3) / 3}


def test_every_question_names_a_passage_that_exists() -> None:
    from autune_intelligence import glossary

    keys = {p.key for p in glossary.passages()}
    questions = retrieval_eval.questions()
    assert 25 <= len(questions) <= 40
    assert all(q["expected"] in keys for q in questions)
