"""The built-in template engine."""

from __future__ import annotations

import pytest

from slowfw.templating import Markup, TemplateEngine, TemplateError


@pytest.fixture
def engine(tmp_path):
    (tmp_path / "base.html").write_text(
        "<html><body>{% block content %}base{% endblock %}</body></html>"
    )
    (tmp_path / "child.html").write_text(
        '{% extends "base" %}{% block content %}<h1>{{ title }}</h1>{% endblock %}'
    )
    (tmp_path / "partial.html").write_text("<footer>{{ year }}</footer>")
    (tmp_path / "page.html").write_text('{{ body }}{% include "partial" %}')
    return TemplateEngine(tmp_path, cache=False)


class TestRendering:
    def test_interpolation(self, engine):
        assert engine.render_string("Hi {{ name }}", {"name": "Ada"}) == "Hi Ada"

    def test_dictionaries_support_dot_access(self, engine):
        assert engine.render_string("{{ u.name }}", {"u": {"name": "Ada"}}) == "Ada"

    def test_missing_names_render_as_empty_rather_than_crashing_a_page(self, engine):
        assert engine.render_string("[{{ absent }}]") == "[]"

    def test_conditionals_pick_one_branch(self, engine):
        template = "{% if n > 2 %}big{% elif n == 2 %}two{% else %}small{% endif %}"
        assert engine.render_string(template, {"n": 5}) == "big"
        assert engine.render_string(template, {"n": 2}) == "two"
        assert engine.render_string(template, {"n": 1}) == "small"

    def test_loops_expose_a_loop_object(self, engine):
        out = engine.render_string(
            "{% for x in xs %}{{ loop.index }}:{{ x }}{% if not loop.last %},{% endif %}{% endfor %}",
            {"xs": ["a", "b"]},
        )
        assert out == "1:a,2:b"

    def test_tuple_unpacking_in_loops(self, engine):
        out = engine.render_string(
            "{% for k, v in items %}{{ k }}={{ v }};{% endfor %}", {"items": [("a", 1), ("b", 2)]}
        )
        assert out == "a=1;b=2;"

    def test_includes_pull_in_another_file(self, engine):
        assert engine.render("page", {"body": "X", "year": 2026}) == "X<footer>2026</footer>"

    def test_inheritance_fills_the_parent_block(self, engine):
        assert engine.render("child", {"title": "Hi"}) == "<html><body><h1>Hi</h1></body></html>"

    def test_set_binds_a_local(self, engine):
        assert engine.render_string("{% set n = 1 + 1 %}{{ n }}") == "2"


class TestEscaping:
    def test_interpolation_escapes_by_default(self, engine):
        out = engine.render_string("{{ x }}", {"x": "<script>alert(1)</script>"})
        assert out == "&lt;script&gt;alert(1)&lt;/script&gt;"

    def test_quotes_are_escaped_so_attributes_are_safe(self, engine):
        assert engine.render_string('<a title="{{ x }}">', {"x": '"onmouseover="evil()'}) == (
            '<a title="&quot;onmouseover=&quot;evil()">'
        )

    def test_the_safe_filter_opts_out_deliberately(self, engine):
        assert engine.render_string("{{ x | safe }}", {"x": "<b>ok</b>"}) == "<b>ok</b>"

    def test_markup_objects_are_not_double_escaped(self, engine):
        assert engine.render_string("{{ x }}", {"x": Markup("<b>ok</b>")}) == "<b>ok</b>"


class TestFilters:
    def test_filters_chain_left_to_right(self, engine):
        assert engine.render_string("{{ x | trim | upper }}", {"x": "  hi  "}) == "HI"

    def test_filters_take_arguments(self, engine):
        assert engine.render_string("{{ x | default('none') }}", {"x": ""}) == "none"

    def test_an_unknown_filter_is_an_error(self, engine):
        with pytest.raises(TemplateError, match="Unknown filter"):
            engine.render_string("{{ x | nope }}", {"x": 1})


class TestSafety:
    def test_a_template_name_cannot_escape_the_root(self, engine):
        with pytest.raises(TemplateError, match="escapes the template root"):
            engine.render("../../etc/passwd")

    def test_a_missing_template_says_which_path_was_tried(self, engine):
        with pytest.raises(TemplateError, match="Template not found"):
            engine.render("absent")

    def test_an_unclosed_tag_is_a_syntax_error(self, engine):
        with pytest.raises(TemplateError, match="Unclosed"):
            engine.render_string("{% if x %}oops")

    def test_an_unknown_tag_is_rejected(self, engine):
        with pytest.raises(TemplateError, match="Unknown tag"):
            engine.render_string("{% while x %}{% endwhile %}")
