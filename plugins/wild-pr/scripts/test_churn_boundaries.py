#!/usr/bin/env python3
"""Tests for churn_boundaries.py.

Stdlib-only - no pytest needed. Run from anywhere:

    python3 plugins/wild-pr/scripts/test_churn_boundaries.py

Each test builds its repo tree in a temp directory. Checked-in fixture trees
would lose node_modules/, build/, dist/, and .env files to ignore rules.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent

sys.path.insert(0, str(HERE))
import churn_boundaries as cb  # noqa: E402


def write_tree(root, files):
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)


class BoundaryCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.repo = self.tmp / "work" / "app"
        self.repo.mkdir(parents=True)

    def checkout(self, path):
        """Make `path` look like a git checkout."""
        (path / ".git").mkdir(parents=True)

    def discover(self, files):
        write_tree(self.repo, files)
        return cb.discover(self.repo, home=self.home)

    def entry(self, entries, name):
        return next(e for e in entries if e["name"] == name)

    def names(self, entries, kind):
        return [e["name"] for e in entries if e["kind"] == kind]


CLAUDE_MD = """\
# App

## Related Repos

- **[`acme/api`](https://github.com/acme/api)**
  (`~/dev/api`): the backend. Source of truth for the endpoints this app calls.
- **[`acme/web`](https://github.com/acme/web.git)**: the frontend.
- `~/dev/cli`: a command-line tool.

## Links

See https://github.com/acme/tools/blob/main/README.md.
"""


class TestReposFromDocs(BoundaryCase):
    def setUp(self):
        super().setUp()
        self.checkout(self.home / "dev" / "api")
        self.checkout(self.home / "dev" / "cli")

    def test_links_and_paths_give_one_entry_per_repo(self):
        entries = self.discover({"CLAUDE.md": CLAUDE_MD})
        self.assertEqual(self.names(entries, "repo"),
                         ["acme/api", "acme/tools", "acme/web", "cli"])

    def test_link_and_path_in_one_list_item_merge(self):
        api = self.entry(self.discover({"CLAUDE.md": CLAUDE_MD}), "acme/api")
        self.assertEqual(api["gh_slug"], "acme/api")
        self.assertEqual(api["local_path"], str(self.home / "dev" / "api"))
        self.assertEqual(api["sources"], ["CLAUDE.md:5", "CLAUDE.md:6"])
        self.assertEqual(list(api), list(cb.FIELDS))

    def test_direction_comes_from_the_whole_list_item(self):
        entries = self.discover({"CLAUDE.md": CLAUDE_MD})
        self.assertEqual(self.entry(entries, "acme/api")["direction"], "provider")
        self.assertEqual(self.entry(entries, "acme/web")["direction"], "consumer")
        self.assertEqual(self.entry(entries, "acme/tools")["direction"], "unknown")
        self.assertEqual(self.entry(entries, "cli")["direction"], "unknown")

    def test_each_table_row_is_its_own_block(self):
        table = ("## Client Repos\n\n| Repo | What |\n| --- | --- |\n"
                 "| [a](https://github.com/acme/mobile) | The mobile app. |\n"
                 "| [b](https://github.com/acme/shell) | A command-line tool. |\n"
                 "| [c](https://github.com/acme/api) | The backend. |\n")
        entries = self.discover({"CLAUDE.md": table})
        self.assertEqual(self.entry(entries, "acme/mobile")["direction"], "consumer")
        self.assertEqual(self.entry(entries, "acme/shell")["direction"], "consumer")
        self.assertEqual(self.entry(entries, "acme/api")["direction"], "provider")

    def test_direction_words_do_not_cross_a_blank_line(self):
        entries = self.discover({"CLAUDE.md": "https://github.com/acme/lib\n\nThe backend is elsewhere.\n"})
        self.assertEqual(self.entry(entries, "acme/lib")["direction"], "unknown")

    def test_link_without_a_checkout_keeps_its_slug(self):
        web = self.entry(self.discover({"CLAUDE.md": CLAUDE_MD}), "acme/web")
        self.assertEqual((web["gh_slug"], web["local_path"]), ("acme/web", None))

    def test_sibling_checkout_is_found_for_a_link(self):
        self.checkout(self.repo.parent / "web")
        web = self.entry(self.discover({"CLAUDE.md": CLAUDE_MD}), "acme/web")
        self.assertEqual(web["local_path"], str(self.repo.parent / "web"))

    def test_missing_path_and_non_repo_directory_are_dropped(self):
        (self.home / "plans").mkdir()
        entries = self.discover({"AGENTS.md": "- `~/dev/gone`: old backend.\n- `~/plans`: notes.\n"})
        self.assertEqual(entries, [])

    def test_same_named_repos_from_two_owners_stay_apart(self):
        entries = self.discover({"CLAUDE.md": "- https://github.com/acme/api: the backend.\n"
                                              "- https://github.com/other/api: a vendor service.\n"})
        self.assertEqual(self.names(entries, "repo"), ["acme/api", "other/api"])

    def test_checkout_with_another_origin_is_not_used_for_a_link(self):
        sibling = self.repo.parent / "api"
        subprocess.run(["git", "init", "-q", str(sibling)], check=True)
        subprocess.run(["git", "-C", str(sibling), "remote", "add", "origin",
                        "https://github.com/other/api.git"], check=True)
        api = self.entry(self.discover({"CLAUDE.md": "- https://github.com/acme/api: the backend.\n"}),
                         "acme/api")
        self.assertEqual(api["local_path"], str(self.home / "dev" / "api"))

    def test_links_to_this_repo_and_to_github_site_pages_are_not_boundaries(self):
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "remote", "add", "origin",
                        "git@github.com:acme/app.git"], check=True)
        entries = self.discover({"CLAUDE.md": "Issues: https://github.com/acme/app/issues/1\n"
                                              "Board: https://github.com/orgs/acme/projects/2\n"})
        self.assertEqual(entries, [])


class TestLibraries(BoundaryCase):
    def test_npm_dependencies_with_and_without_an_install(self):
        (self.repo / "node_modules" / "@tanstack" / "react-query").mkdir(parents=True)
        manifest = {"name": "app",
                    "dependencies": {"@tanstack/react-query": "^5", "zod": "^3"},
                    "devDependencies": {"jest": "^29"}}
        entries = self.discover({"package.json": json.dumps(manifest, indent=2)})
        self.assertEqual(self.names(entries, "library"), ["@tanstack/react-query", "zod"])
        rq = self.entry(entries, "@tanstack/react-query")
        self.assertEqual(rq["local_path"], str(self.repo / "node_modules" / "@tanstack" / "react-query"))
        self.assertEqual(rq["doc_url"], "https://www.npmjs.com/package/@tanstack/react-query")
        self.assertEqual(rq["direction"], "provider")
        self.assertEqual(rq["sources"], ["package.json:4"])
        self.assertIsNone(self.entry(entries, "zod")["local_path"])

    def test_pyproject_pep621_and_poetry_dependencies(self):
        pyproject = ('[project]\nname = "app"\ndependencies = [\n  "httpx>=0.27",\n'
                     '  "pydantic[email]",\n]\n\n[tool.poetry.dependencies]\n'
                     'python = "^3.12"\nrich = "^13"\n')
        entries = self.discover({"pyproject.toml": pyproject,
                                 ".venv/lib/python3.12/site-packages/httpx/__init__.py": ""})
        self.assertEqual(self.names(entries, "library"), ["httpx", "pydantic", "rich"])
        httpx = self.entry(entries, "httpx")
        self.assertEqual(httpx["local_path"],
                         str(self.repo / ".venv" / "lib" / "python3.12" / "site-packages" / "httpx"))
        self.assertEqual(httpx["doc_url"], "https://pypi.org/project/httpx/")
        self.assertEqual(httpx["sources"], ["pyproject.toml:4"])
        self.assertEqual(self.entry(entries, "rich")["sources"], ["pyproject.toml:10"])

    def test_go_mod_direct_requirements_only(self):
        gomod = ("module example.com/app\n\ngo 1.22\n\nrequire (\n"
                 "\tgithub.com/acme/lib/v2 v2.1.0\n\tgolang.org/x/sync v0.7.0 // indirect\n)\n\n"
                 "require gopkg.in/yaml.v3 v3.0.1\n")
        entries = self.discover({"go.mod": gomod})
        self.assertEqual(self.names(entries, "library"), ["github.com/acme/lib/v2", "gopkg.in/yaml.v3"])
        lib = self.entry(entries, "github.com/acme/lib/v2")
        self.assertEqual(lib["gh_slug"], "acme/lib")
        self.assertEqual(lib["doc_url"], "https://pkg.go.dev/github.com/acme/lib/v2")
        self.assertEqual(lib["sources"], ["go.mod:6"])

    def test_malformed_manifests_are_skipped(self):
        entries = self.discover({"package.json": "{not json", "pyproject.toml": "[project\n",
                                 "go.mod": ""})
        self.assertEqual(entries, [])

    def test_manifests_with_unexpected_shapes_are_skipped(self):
        entries = self.discover({"package.json": '{"dependencies": ["zod"]}',
                                 "pyproject.toml": 'project = "x"\ntool = 3\n'})
        self.assertEqual(entries, [])

    def test_package_json_that_is_not_an_object_is_skipped(self):
        self.assertEqual(self.discover({"package.json": "[]"}), [])


class TestProviderTable(unittest.TestCase):
    def test_every_entry_has_host_name_and_doc_url(self):
        for provider in cb.load_providers():
            self.assertEqual(set(provider), {"host", "name", "doc_url"})

    def test_malformed_table_raises_value_error(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json") as fh:
            fh.write('[{"host": "x"}]')
            fh.flush()
            with self.assertRaises(ValueError):
                cb.load_providers(Path(fh.name))


    def test_table_that_is_not_a_list_raises_value_error(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json") as fh:
            fh.write("3")
            fh.flush()
            with self.assertRaises(ValueError):
                cb.load_providers(Path(fh.name))


class TestServicesFromCode(BoundaryCase):
    def test_env_template_names_become_services(self):
        entries = self.discover({".env.example": "PAYMENTS_API_URL=\nEXPO_PUBLIC_API_URL=\nSUPABASE_URL=\n"})
        self.assertEqual(self.names(entries, "service"), ["api", "payments", "supabase"])
        self.assertEqual(self.entry(entries, "payments")["sources"], [".env.example:1"])
        self.assertEqual(self.entry(entries, "supabase")["doc_url"], "https://supabase.com/docs/reference")

    def test_client_base_url_maps_to_a_known_provider(self):
        entries = self.discover({"src/pay.ts": 'export const stripe = axios.create({ baseURL: "https://api.stripe.com/v1" });\n'})
        stripe = self.entry(entries, "stripe")
        self.assertEqual((stripe["kind"], stripe["direction"], stripe["doc_url"]),
                         ("service", "provider", "https://docs.stripe.com/api"))
        self.assertEqual(stripe["sources"], ["src/pay.ts:1"])

    def test_unknown_host_is_named_by_its_domain(self):
        entries = self.discover({"app/client.py": 'client = httpx.Client(base_url="https://api-staging.herds.events")\n'})
        self.assertEqual(self.names(entries, "service"), ["herds"])
        self.assertIsNone(self.entry(entries, "herds")["doc_url"])

    def test_local_placeholder_and_uncalled_urls_are_dropped(self):
        entries = self.discover({"src/a.ts": 'fetch("http://localhost:3000/x");\n'
                                             'fetch("https://example.com/y");\n'
                                             'fetch(`https://${HOST}/z`);\n'
                                             "// docs: https://docs.acme.io/guide\n"})
        self.assertEqual(entries, [])

    def test_env_name_and_base_url_merge_into_one_service(self):
        entries = self.discover({".env.example": "STRIPE_API_URL=https://api.stripe.com\n",
                                 "src/pay.ts": 'axios.create({ baseURL: "https://api.stripe.com" });\n'})
        self.assertEqual(self.names(entries, "service"), ["stripe"])
        self.assertEqual(self.entry(entries, "stripe")["sources"], [".env.example:1", "src/pay.ts:1"])

    def test_real_env_file_is_never_read(self):
        with mock.patch.object(cb, "read_text", wraps=cb.read_text) as reader:
            entries = self.discover({".env": "SECRET_API_URL=https://vault.acme.io\n"})
        self.assertEqual(entries, [])
        self.assertNotIn(".env", [call.args[0].name for call in reader.call_args_list])

    def test_vendored_build_and_test_trees_are_skipped(self):
        call = 'fetch("https://api.stripe.com");\n'
        entries = self.discover({"node_modules/sdk/index.js": call, "dist/app.js": call,
                                 ".next/server.js": call, "tests/test_pay.py": call,
                                 "src/__tests__/pay.ts": call, "src/pay.test.ts": call,
                                 "src/pay.min.js": call})
        self.assertEqual(entries, [])

    def test_undecodable_and_oversized_files_do_not_crash(self):
        entries = self.discover({"src/bin.ts": b"\xff\xfe" + b'fetch("https://api.stripe.com");\n',
                                 "src/big.ts": 'fetch("https://api.openai.com");\n' + "x" * cb.MAX_FILE_BYTES})
        self.assertEqual(self.names(entries, "service"), ["stripe"])


OPENAPI = """\
openapi: 3.0.0
info:
  title: Payments
  contact:
    url: https://acme.io/support
servers:
  - url: https://api.payments.io/v1
paths: {}
"""


class TestSchemas(BoundaryCase):
    def test_openapi_file_describes_its_server(self):
        entries = self.discover({"api/openapi.yaml": OPENAPI})
        schema = self.entry(entries, "api/openapi.yaml")
        self.assertEqual((schema["kind"], schema["describes"]), ("schema", "payments"))
        self.assertEqual(schema["local_path"], str(self.repo / "api" / "openapi.yaml"))
        self.assertEqual(self.entry(entries, "payments")["sources"], ["api/openapi.yaml:7"])

    def test_contact_url_outside_servers_is_not_a_service(self):
        entries = self.discover({"api/openapi.yaml": OPENAPI})
        self.assertEqual(self.names(entries, "service"), ["payments"])

    def test_openapi_json_server_url(self):
        doc = json.dumps({"openapi": "3.0.0", "servers": [{"url": "https://api.stripe.com"}]}, indent=2)
        entries = self.discover({"openapi.json": doc})
        self.assertEqual(self.entry(entries, "openapi.json")["describes"], "stripe")

    def test_hostless_server_url_is_skipped(self):
        entries = self.discover({"openapi.yaml": "servers:\n  - url: https:///v1\n"})
        self.assertEqual(self.names(entries, "service"), [])
        self.assertIsNone(self.entry(entries, "openapi.yaml")["describes"])

    def test_graphql_files_and_generated_client_dirs(self):
        entries = self.discover({"schema.graphql": "type Query { me: User }\n",
                                 "src/generated/client.ts": "export {};\n",
                                 "src/generated/types.ts": "export {};\n"})
        self.assertEqual(self.names(entries, "schema"), ["schema.graphql", "src/generated"])
        self.assertIsNone(self.entry(entries, "schema.graphql")["describes"])


if __name__ == "__main__":
    unittest.main()
