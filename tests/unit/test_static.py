"""Static file serving, mostly from the attacker's side.

Path traversal is the classic static-file bug and it has a long tail: encoded
separators, doubly-encoded ones, absolute paths, symlinks pointing out of the
root, null bytes.  Each of those gets its own test, because each one has been a
CVE in some other framework.
"""

from __future__ import annotations

import os

import pytest

from slowfw import SlowAPI
from slowfw.exceptions import ConfigurationError
from slowfw.static import StaticFiles


@pytest.fixture
def public(tmp_path):
    root = tmp_path / "public"
    (root / "css").mkdir(parents=True)
    (root / "index.html").write_text("<h1>home</h1>")
    (root / "css" / "app.css").write_text("body{}")
    (tmp_path / "secret.txt").write_text("PRIVATE")
    return root


class TestPathTraversal:
    @pytest.mark.parametrize(
        "attack",
        [
            "../secret.txt",
            "../../secret.txt",
            "css/../../secret.txt",
            "..%2fsecret.txt",
            "..%2F..%2Fsecret.txt",
            "%2e%2e%2fsecret.txt",
            "/../secret.txt",
            "....//secret.txt",
        ],
    )
    def test_escaping_the_root_is_refused(self, public, attack):
        assert StaticFiles(public).resolve(attack) is None

    def test_a_null_byte_is_refused(self, public):
        """Truncation tricks against the C layer underneath open()."""
        assert StaticFiles(public).resolve("index.html\x00.txt") is None

    def test_an_absolute_path_does_not_escape(self, public):
        assert StaticFiles(public).resolve("/etc/passwd") is None

    def test_a_symlink_out_of_the_root_is_refused_by_default(self, public, tmp_path):
        link = public / "leak.txt"
        try:
            os.symlink(tmp_path / "secret.txt", link)
        except (OSError, NotImplementedError):  # pragma: no cover - platform dependent
            pytest.skip("symlinks unavailable")
        assert StaticFiles(public).resolve("leak.txt") is None

    def test_symlinks_can_be_opted_into(self, public, tmp_path):
        inside = public / "css" / "app.css"
        link = public / "alias.css"
        try:
            os.symlink(inside, link)
        except (OSError, NotImplementedError):  # pragma: no cover - platform dependent
            pytest.skip("symlinks unavailable")
        assert StaticFiles(public, follow_symlinks=True).resolve("alias.css") is not None


class TestResolution:
    def test_a_real_file_resolves(self, public):
        assert StaticFiles(public).resolve("css/app.css").endswith("app.css")

    def test_a_directory_resolves_to_its_index(self, public):
        assert StaticFiles(public).resolve("").endswith("index.html")

    def test_a_directory_without_an_index_is_not_servable(self, public):
        assert StaticFiles(public, index_file=None).resolve("") is None

    def test_a_missing_file_resolves_to_nothing(self, public):
        assert StaticFiles(public).resolve("nope.css") is None

    def test_a_missing_directory_is_rejected_at_construction(self, tmp_path):
        """Fail at import, not on the first request for a stylesheet."""
        with pytest.raises(ConfigurationError, match="does not exist"):
            StaticFiles(tmp_path / "nope")


class TestServing:
    def test_files_are_served_over_both_protocols(self, public, make_client):
        app = SlowAPI()
        app.mount_static("/assets", str(public))

        response = make_client(app).get("/assets/css/app.css")

        assert response.status_code == 200
        assert response.text == "body{}"
        assert response.headers["content-type"].startswith("text/css")

    def test_traversal_over_the_wire_is_a_404_not_a_leak(self, public, make_client):
        app = SlowAPI()
        app.mount_static("/assets", str(public))

        response = make_client(app).get("/assets/../secret.txt")

        assert response.status_code == 404
        assert "PRIVATE" not in response.text

    def test_a_repeat_visit_costs_one_304(self, public, make_client):
        app = SlowAPI()
        app.mount_static("/assets", str(public))
        client = make_client(app)

        first = client.get("/assets/css/app.css")
        second = client.get("/assets/css/app.css", headers={"if-none-match": first.headers["etag"]})

        assert second.status_code == 304
        assert second.text == ""

    def test_immutable_assets_advertise_it(self, public, make_client):
        app = SlowAPI()
        app.mount_static("/assets", str(public), max_age=31536000, immutable=True)

        cache_control = make_client(app).get("/assets/css/app.css").headers["cache-control"]

        assert "immutable" in cache_control and "31536000" in cache_control

    def test_the_spa_fallback_serves_the_shell_for_unknown_paths(self, public, make_client):
        app = SlowAPI()
        app.add_route(
            "/app/{path:path}",
            StaticFiles(public, html_fallback="index.html"),
            methods=["GET"],
            name="spa",
        )

        response = make_client(app).get("/app/deep/client/route")

        assert response.status_code == 200
        assert "home" in response.text

    def test_the_fallback_still_does_not_serve_outside_the_root(self, public):
        """A fallback is not a licence to leave the directory."""
        files = StaticFiles(public, html_fallback="../secret.txt")
        assert files.resolve("../secret.txt") is None
