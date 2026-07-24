#!/usr/bin/env python3
"""
Dump Elasticsearch index data to NDJSON using scroll API (stdlib only).
Replaces elasticdump for integration test data extraction.
"""
import argparse
import json
import sys
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser(description="Dump ES index data to NDJSON")
    parser.add_argument("--url", default="http://localhost:9200", help="Elasticsearch URL")
    parser.add_argument("--index", required=True, help="Index name or pattern (e.g. mdx-alerts*)")
    parser.add_argument("--output", required=True, help="Output file path")
    parser.add_argument("--limit", type=int, default=None, help="Max documents to dump (default: all)")
    parser.add_argument("--scroll", default="10m", help="Scroll timeout")
    args = parser.parse_args()

    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be a positive integer")

    base = args.url.rstrip("/")
    index_url = f"{base}/{args.index}/_search"
    scroll_url = f"{base}/_search/scroll"
    count = 0
    scroll_id = None

    def _clear_scroll():
        if not scroll_id:
            return
        try:
            req = urllib.request.Request(
                scroll_url,
                data=json.dumps({"scroll_id": scroll_id}).encode(),
                headers={"Content-Type": "application/json"},
                method="DELETE",
            )
            with urllib.request.urlopen(req, timeout=10):
                pass
        except urllib.error.URLError as e:
            print(f"Warning: failed to clear scroll context: {e}", file=sys.stderr)

    try:
        with open(args.output, "w") as out:
            # Initial search
            body = {"size": 1000, "sort": ["_doc"]}
            req = urllib.request.Request(
                f"{index_url}?scroll={args.scroll}",
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode())
            scroll_id = data.get("_scroll_id")
            hits = data.get("hits", {}).get("hits", [])

            while hits:
                for h in hits:
                    if args.limit is not None and count >= args.limit:
                        break
                    # Match format expected by compare_mdx_data.py: {"_id": "...", "_source": {...}}
                    record = {"_id": h.get("_id", ""), "_source": h.get("_source", h)}
                    out.write(json.dumps(record) + "\n")
                    count += 1
                if args.limit is not None and count >= args.limit:
                    break
                if not scroll_id:
                    break
                req = urllib.request.Request(
                    scroll_url,
                    data=json.dumps({"scroll": args.scroll, "scroll_id": scroll_id}).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = json.loads(r.read().decode())
                scroll_id = data.get("_scroll_id")
                hits = data.get("hits", {}).get("hits", [])

        _clear_scroll()
        print(f"Dumped {count} documents to {args.output}")
        return 0
    except urllib.error.URLError as e:
        _clear_scroll()
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except (KeyError, json.JSONDecodeError) as e:
        _clear_scroll()
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
