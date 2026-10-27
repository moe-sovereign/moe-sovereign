from graph.synthesis import _critic_noncompliance_reason, _critic_is_noncompliant_confirmation

FENCE = "`" * 3


def test_reason_trailing_confirmed():
    assert _critic_noncompliance_reason("long thoughts ... CONFIRMED", "plain answer") == "trailing_confirmed"


def test_reason_code_dropped():
    original = FENCE + "rust\nfn x(){}\n" + FENCE
    assert _critic_noncompliance_reason("Reorder the stores.", original) == "code_dropped"


def test_compliant_correction():
    original = FENCE + "rust\nfn x(){}\n" + FENCE
    corrected = FENCE + "rust\nfn y(){}\n" + FENCE
    assert _critic_noncompliance_reason(corrected, original) == ""


def test_bool_wrapper_consistent():
    pairs = [("a CONFIRMED", "b"), (FENCE + "x" + FENCE, FENCE + "y" + FENCE), ("Reorder.", FENCE + "z" + FENCE)]
    for out, orig in pairs:
        assert _critic_is_noncompliant_confirmation(out, orig) == bool(_critic_noncompliance_reason(out, orig))
