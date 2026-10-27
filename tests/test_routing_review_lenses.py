import json


def test_review_lenses_passed_through(monkeypatch):
    import services.routing as routing
    tmpl = {"experts": {"code_reviewer": {
        "models": [{"role": "primary", "model": "m", "endpoint": "EP"}],
        "review_lenses": ["security"], "review_replaces_self_critique": True,
    }}}
    monkeypatch.setattr(routing, "_resolve_template_selection",
                        lambda *a, **k: {"template": tmpl, "authorized": True})
    experts = routing._resolve_user_experts("{}", override_tmpl_id="x")
    cfg = experts["code_reviewer"][0]
    assert cfg["_review_lenses"] == ["security"]
    assert cfg["_review_replaces_self_critique"] is True
