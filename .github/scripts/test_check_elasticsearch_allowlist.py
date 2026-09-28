#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("check_elasticsearch_allowlist.py")
SPEC = importlib.util.spec_from_file_location("check_elasticsearch_allowlist", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
LINT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LINT)

# The shipped allowlist, trimmed to the directives and used as the fixture the
# weakenings below are applied to.
ALLOWLIST = """
frontend fe_http
    acl p_elasticsearch path /elasticsearch
    acl p_elasticsearch path_beg /elasticsearch/
    acl es_m_read method GET HEAD
    acl es_m_query method GET HEAD POST
    acl es_m_put method PUT
    acl es_m_options method OPTIONS
    acl es_m_known method GET HEAD POST PUT OPTIONS
    acl es_read_shape path_reg ^/elasticsearch/*$
    acl es_read_shape path_reg ^/elasticsearch/+_cat(/|$)
    acl es_read_shape path_reg ^/elasticsearch/+_cluster/health/*$
    acl es_read_shape path_reg ^/elasticsearch/+(_all|[a-z0-9.*][a-z0-9._,*+-]*)/(_mapping|_settings|_alias|_doc/[^/]+|_source/[^/]+)/*$
    acl es_read_shape path_reg ^/elasticsearch/+(_all|[a-z0-9.*][a-z0-9._,*+-]*)/*$
    acl es_query_shape path_reg ^/elasticsearch/+(_search|_msearch|_count|_mget|_field_caps)/*$
    acl es_query_shape path_reg ^/elasticsearch/+(_all|[a-z0-9.*][a-z0-9._,*+-]*)/(_search|_msearch|_count|_mget|_field_caps|_validate/query|_terms_enum|_explain/[^/]+|_termvectors/[^/]+)/*$
    acl es_memory_doc_path path_reg ^/elasticsearch/+vss-memory(-[a-z0-9._-]+)?/_doc/[^/]+$
    http-request set-var(txn.es_ok) int(1) if h_main p_elasticsearch es_m_options
    http-request set-var(txn.es_ok) int(1) if h_main p_elasticsearch es_m_read es_read_shape
    http-request set-var(txn.es_ok) int(1) if h_main p_elasticsearch es_m_query es_query_shape
    http-request set-var(txn.es_ok) int(1) if h_main p_elasticsearch es_m_put es_memory_doc_path
    acl es_allowed var(txn.es_ok) -m found
    http-request deny status 405 if h_main p_elasticsearch !es_m_known
    http-request deny status 405 if h_main p_elasticsearch es_m_put !es_memory_doc_path
    http-request deny status 403 if h_main p_elasticsearch !es_allowed
    use_backend bk_elasticsearch_strip if h_main p_elasticsearch
"""

# The denylist the allowlist replaced, in the shape it shipped in: POST is open
# wherever a named pattern does not deny it.
DENYLIST = """
frontend fe_http
    acl p_elasticsearch path /elasticsearch
    acl p_elasticsearch path_beg /elasticsearch/
    acl es_m_known method GET HEAD POST PUT OPTIONS
    acl es_write method PUT DELETE
    acl es_admin path_reg ^/elasticsearch/+_(cluster|nodes|snapshot|ilm)(/|$)
    acl es_mutate path_reg ^/elasticsearch/+[^/]+/_(update|close|open)(/|$)
    http-request deny status 405 if h_main p_elasticsearch !es_m_known
    http-request deny status 403 if h_main p_elasticsearch es_write
    http-request deny status 403 if h_main p_elasticsearch es_admin
    http-request deny status 403 if h_main p_elasticsearch es_mutate
    use_backend bk_elasticsearch_strip if h_main p_elasticsearch
"""


def policy(text: str) -> "LINT.Policy":
    return LINT.parse(text, "fixture")


class ParseTest(unittest.TestCase):
    def test_the_shipped_shape_is_recognised(self) -> None:
        parsed = policy(ALLOWLIST)
        self.assertEqual([], LINT.check_shape(parsed, needs_route=True))
        self.assertIn("es_read_shape", parsed.acls)
        self.assertEqual(5, len(parsed.acls["es_read_shape"]))

    def test_comments_and_prose_are_not_directives(self) -> None:
        noisy = ALLOWLIST.replace(
            "    acl es_m_put method PUT",
            "    # acl es_m_put method PUT DELETE -- do not do this\n"
            "    acl es_m_put method PUT",
        )
        self.assertEqual(
            LINT.directive_lines(ALLOWLIST), LINT.directive_lines(noisy)
        )

    def test_unrelated_acls_are_left_out(self) -> None:
        parsed = policy(ALLOWLIST + "    acl p_kibana path /kibana\n")
        self.assertNotIn("p_kibana", parsed.acls)

    def test_a_var_found_acl_is_read_as_one(self) -> None:
        parsed = policy(ALLOWLIST)
        self.assertEqual([("var", "txn.es_ok")], parsed.acls["es_allowed"])


class SemanticsTest(unittest.TestCase):
    """The evaluator has to answer the way HAProxy answers."""

    def test_same_name_acls_are_ored(self) -> None:
        parsed = policy(ALLOWLIST)
        self.assertEqual("forward", parsed.verdict("GET", "/elasticsearch/_cat"))
        self.assertEqual("forward", parsed.verdict("GET", "/elasticsearch/mdx-1"))

    def test_rule_conditions_are_anded(self) -> None:
        # POST satisfies es_m_query but not the query shape, so the set-var
        # does not fire and the fail-closed deny does.
        parsed = policy(ALLOWLIST)
        self.assertEqual("403", parsed.verdict("POST", "/elasticsearch/_bulk"))

    def test_negation_is_honoured(self) -> None:
        parsed = policy(ALLOWLIST)
        self.assertEqual("405", parsed.verdict("DELETE", "/elasticsearch/mdx-1"))

    def test_a_path_outside_the_mount_is_not_routed_here(self) -> None:
        parsed = policy(ALLOWLIST)
        self.assertEqual("unrouted", parsed.verdict("GET", "/vst/api"))

    def test_denies_run_before_the_backend_whatever_the_file_order(self) -> None:
        reordered = ALLOWLIST.replace(
            "    use_backend bk_elasticsearch_strip if h_main p_elasticsearch\n", ""
        ).replace(
            "    http-request deny status 405 if h_main p_elasticsearch !es_m_known",
            "    use_backend bk_elasticsearch_strip if h_main p_elasticsearch\n"
            "    http-request deny status 405 if h_main p_elasticsearch !es_m_known",
        )
        self.assertEqual("403", policy(reordered).verdict("POST", "/elasticsearch/_bulk"))

    def test_a_backendless_snippet_forwards_what_it_does_not_deny(self) -> None:
        # The Helm snippet is Service-scoped, so it carries no use_backend and
        # anything it does not deny has already been routed to Elasticsearch.
        snippet = ALLOWLIST.replace(
            "    use_backend bk_elasticsearch_strip if h_main p_elasticsearch\n", ""
        )
        self.assertEqual("forward", policy(snippet).verdict("GET", "/elasticsearch/_cat"))


class HistoricalBypassTest(unittest.TestCase):
    """The three bypasses this lint exists for, on the config that had them."""

    def setUp(self) -> None:
        self.failures = LINT.check_policy(policy(DENYLIST), [lambda path: path])

    def _named(self, fragment: str) -> None:
        self.assertTrue(
            any(fragment in failure for failure in self.failures),
            f"{fragment} not named in {self.failures}",
        )

    def test_the_bulk_write_is_caught(self) -> None:
        self._named("POST /elasticsearch/_bulk reaches Elasticsearch")

    def test_the_stored_script_write_is_caught(self) -> None:
        self._named("POST /elasticsearch/_scripts/s1 reaches Elasticsearch")

    def test_the_percent_encoded_admin_path_is_caught(self) -> None:
        self._named("GET /elasticsearch/%5Fcluster/settings reaches Elasticsearch")

    def test_the_literal_admin_path_the_denylist_did_name_is_not_reported(self) -> None:
        # The denylist was not uniformly wrong, and a lint that reports the
        # parts that worked would drown the parts that did not.
        self.assertFalse(
            any("/elasticsearch/_cluster/settings " in f for f in self.failures),
            self.failures,
        )

    def test_the_shipped_allowlist_has_none_of_them(self) -> None:
        self.assertEqual([], LINT.check_policy(policy(ALLOWLIST), [lambda path: path]))


class WeakeningTest(unittest.TestCase):
    def test_naming_bulk_as_a_query_shape_is_caught(self) -> None:
        widened = ALLOWLIST.replace("|_field_caps)/*$", "|_field_caps|_bulk)/*$", 1)
        failures = LINT.check_policy(policy(widened), [lambda path: path])
        self.assertTrue(any("_bulk" in failure for failure in failures), failures)

    def test_an_any_segment_read_shape_is_caught(self) -> None:
        # The "simplification" the template's comment warns against: it admits
        # a segment carrying percent-encoding, and the encoded admin paths come
        # straight back.
        loosened = ALLOWLIST.replace(
            "    acl es_read_shape path_reg ^/elasticsearch/+(_all|[a-z0-9.*][a-z0-9._,*+-]*)/*$",
            "    acl es_read_shape path_reg ^/elasticsearch/+[^/]+(/[^/]+)*/*$",
        )
        failures = LINT.check_policy(policy(loosened), [lambda path: path])
        self.assertTrue(any("%5Fcluster" in failure for failure in failures), failures)

    def test_widening_the_memory_write_onto_another_index_is_caught(self) -> None:
        widened = ALLOWLIST.replace(
            "^/elasticsearch/+vss-memory(-[a-z0-9._-]+)?/_doc/[^/]+$",
            "^/elasticsearch/+[a-z0-9.*][a-z0-9._,*+-]*/_doc/[^/]+$",
        )
        failures = LINT.check_policy(policy(widened), [lambda path: path])
        self.assertTrue(
            any("/elasticsearch/mdx-raw-1/_doc/1" in failure for failure in failures),
            failures,
        )

    def test_dropping_the_memory_write_is_caught_as_too_narrow(self) -> None:
        narrowed = ALLOWLIST.replace(
            "    http-request set-var(txn.es_ok) int(1) if h_main p_elasticsearch es_m_put es_memory_doc_path\n",
            "",
        )
        failures = LINT.check_policy(policy(narrowed), [lambda path: path])
        self.assertTrue(any("vss-memory" in failure for failure in failures), failures)
        self.assertTrue(any("too narrow" in failure for failure in failures), failures)

    def test_serving_post_on_every_shape_is_caught(self) -> None:
        opened = ALLOWLIST.replace(
            "    http-request deny status 403 if h_main p_elasticsearch !es_allowed\n", ""
        )
        failures = LINT.check_policy(policy(opened), [lambda path: path])
        self.assertTrue(len(failures) >= 3, failures)


class ShapeTest(unittest.TestCase):
    def test_a_removed_guard_is_reported_as_checking_nothing(self) -> None:
        failures = LINT.check_shape(policy("frontend fe_http\n    bind :7777\n"), needs_route=True)
        self.assertTrue(any("not checking anything" in f for f in failures), failures)

    def test_acls_without_a_deny_are_reported(self) -> None:
        toothless = "\n".join(
            line for line in ALLOWLIST.splitlines() if "deny" not in line
        )
        failures = LINT.check_shape(policy(toothless), needs_route=True)
        self.assertTrue(any("does not deny" in f for f in failures), failures)

    def test_http_request_allow_is_refused(self) -> None:
        # `allow` also skips the backend's replace-path rules, so the request
        # would arrive with the /elasticsearch prefix still on it.
        allowed = ALLOWLIST.replace(
            "    http-request set-var(txn.es_ok) int(1) if h_main p_elasticsearch es_m_options",
            "    http-request allow if h_main p_elasticsearch es_m_options",
        )
        parsed = policy(allowed)
        parsed.rules.append(("allow", "", ["h_main", "p_elasticsearch"]))
        failures = LINT.check_shape(parsed, needs_route=True)
        self.assertTrue(any("http-request allow" in f for f in failures), failures)


class ParityTest(unittest.TestCase):
    def test_two_identical_edges_agree(self) -> None:
        self.assertEqual([], LINT.check_parity(policy(ALLOWLIST), policy(ALLOWLIST)))

    def test_one_edge_left_open_is_caught(self) -> None:
        failures = LINT.check_parity(policy(ALLOWLIST), policy(DENYLIST))
        self.assertTrue(any("_bulk" in failure for failure in failures), failures)


class StripMountTest(unittest.TestCase):
    def test_the_backend_sees_the_stripped_path(self) -> None:
        self.assertEqual("/", LINT.strip_mount("/elasticsearch"))
        self.assertEqual("/_bulk", LINT.strip_mount("/elasticsearch/_bulk"))
        self.assertEqual("//_cat", LINT.strip_mount("/elasticsearch//_cat"))

    def test_an_unprefixed_path_is_left_alone(self) -> None:
        self.assertEqual("/_bulk", LINT.strip_mount("/_bulk"))


class DocCopyTest(unittest.TestCase):
    def test_a_verbatim_copy_passes(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory) / "haproxy.cfg.template"
            template.write_text(ALLOWLIST)
            doc = Path(directory) / "ingress.md"
            doc.write_text("Copy this verbatim:\n\n```\n" + ALLOWLIST + "```\n")
            self.assertEqual([], LINT.check_doc_copy(template, doc))

    def test_reworded_prose_around_it_is_free(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory) / "haproxy.cfg.template"
            template.write_text("    # one wording\n" + ALLOWLIST)
            doc = Path(directory) / "ingress.md"
            doc.write_text("    # quite another wording\n" + ALLOWLIST)
            self.assertEqual([], LINT.check_doc_copy(template, doc))

    def test_a_dropped_directive_is_caught(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory) / "haproxy.cfg.template"
            template.write_text(ALLOWLIST)
            doc = Path(directory) / "ingress.md"
            doc.write_text(
                ALLOWLIST.replace(
                    "    http-request deny status 403 if h_main p_elasticsearch !es_allowed\n",
                    "",
                )
            )
            failures = LINT.check_doc_copy(template, doc)
            self.assertTrue(any("missing" in failure for failure in failures), failures)

    def test_a_doc_with_no_guard_at_all_is_caught(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory) / "haproxy.cfg.template"
            template.write_text(ALLOWLIST)
            doc = Path(directory) / "ingress.md"
            doc.write_text("# Ingress\n\nNothing about Elasticsearch here.\n")
            failures = LINT.check_doc_copy(template, doc)
            self.assertTrue(any("no /elasticsearch guard" in f for f in failures), failures)


class RepositoryTest(unittest.TestCase):
    def test_the_lint_passes_on_the_tree(self) -> None:
        self.assertEqual(0, LINT.main([]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
