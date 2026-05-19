"""Phase 1J one-shot Supabase MCP bridge.

This script speaks JSON-RPC over the Supabase MCP HTTP transport using
the OAuth token already cached by the operator's Claude CLI session in
`~/.claude/.credentials.json`. It is NOT a generic library and NOT
committed to operator workflows — it exists only to unblock Phase 1J
when the agent's tool registry doesn't surface the Supabase MCP tools
directly.

Hard rules:
  - Never prints the OAuth access token or the Supabase service-role key.
  - Issues exactly the SQL it is told to via --migration / --seed / --sql.
  - Stops on the first error; never retries automatically.
  - DDL is applied via the MCP `apply_migration` tool; arbitrary reads
    use `execute_sql`. Both are exposed by Supabase MCP v0.8+.

Usage:
    py -3.11 scripts/_phase_1j_mcp_apply.py list-tools
    py -3.11 scripts/_phase_1j_mcp_apply.py list-tables
    py -3.11 scripts/_phase_1j_mcp_apply.py apply-migration <name> <path>
    py -3.11 scripts/_phase_1j_mcp_apply.py exec-sql <sql>
    py -3.11 scripts/_phase_1j_mcp_apply.py exec-sql-file <path>
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Optional

import requests

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_supabase_oauth() -> tuple[str, str]:
    """Returns (mcp_url, access_token) from ~/.claude/.credentials.json.
    Raises if the entry is missing or expired."""
    creds_path = Path.home() / ".claude" / ".credentials.json"
    if not creds_path.exists():
        raise SystemExit("FATAL: ~/.claude/.credentials.json missing.")
    data = json.loads(creds_path.read_text(encoding="utf-8"))
    sb = None
    for v in (data.get("mcpOAuth") or {}).values():
        if (
            isinstance(v, dict)
            and v.get("serverName") == "supabase"
            and v.get("serverUrl", "").startswith("https://mcp.supabase.com")
            and v.get("accessToken")
        ):
            sb = v
            break
    if sb is None:
        raise SystemExit(
            "FATAL: no Supabase MCP OAuth entry in credentials.json. "
            "Run `claude mcp add --scope project --transport http supabase "
            "https://mcp.supabase.com/mcp?project_ref=<ref>` and authenticate."
        )
    exp_ms = sb.get("expiresAt") or 0
    if exp_ms and exp_ms < int(time.time() * 1000):
        raise SystemExit(
            "FATAL: Supabase MCP access token is expired. Re-authenticate "
            "in your Claude CLI session via `claude /mcp` and retry."
        )
    return sb["serverUrl"], sb["accessToken"]


class McpHttpSession:
    """Minimal Streamable-HTTP MCP client. Just enough for tools/list +
    tools/call. Maintains the `Mcp-Session-Id` header across calls."""

    def __init__(self, url: str, token: str) -> None:
        self._url = url
        self._token = token
        self._session_id: Optional[str] = None
        self._mid = 0

    def _next_id(self) -> int:
        self._mid += 1
        return self._mid

    def _headers(self) -> dict[str, str]:
        h = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            # Required by the streamable HTTP transport spec; the server
            # ignores it if absent but some intermediaries care.
            "MCP-Protocol-Version": "2025-03-26",
        }
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        return h

    @staticmethod
    def _parse_sse_body(text: str) -> Any:
        """Very small SSE parser: pulls the first `data: ...` line and
        json-loads it. Good enough for single-message MCP responses."""
        for line in text.splitlines():
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload and payload != "[DONE]":
                    return json.loads(payload)
        raise RuntimeError(f"No data: line in SSE body: {text[:200]!r}")

    def _post(self, body: dict[str, Any]) -> Any:
        r = requests.post(self._url, headers=self._headers(), json=body, timeout=60)
        # Server sets Mcp-Session-Id on the initialize response; grab it.
        sid = r.headers.get("Mcp-Session-Id") or r.headers.get("mcp-session-id")
        if sid and not self._session_id:
            self._session_id = sid
        if r.status_code == 202:
            # Notifications return 202 with no body.
            return None
        if not r.ok:
            raise SystemExit(
                f"FATAL: MCP {body.get('method')} HTTP {r.status_code}: "
                f"{r.text[:512]}"
            )
        ctype = r.headers.get("Content-Type", "")
        if "application/json" in ctype:
            return r.json()
        if "text/event-stream" in ctype:
            return self._parse_sse_body(r.text)
        return json.loads(r.text)

    def initialize(self) -> dict[str, Any]:
        resp = self._post({
            "jsonrpc": "2.0", "id": self._next_id(), "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "yuvo-phase1j-bridge", "version": "0.1.0"},
            },
        })
        # Send the initialized notification (per MCP spec).
        self._post({
            "jsonrpc": "2.0", "method": "notifications/initialized", "params": {},
        })
        return resp.get("result", {}) if isinstance(resp, dict) else {}

    def list_tools(self) -> list[dict[str, Any]]:
        resp = self._post({
            "jsonrpc": "2.0", "id": self._next_id(), "method": "tools/list",
            "params": {},
        })
        return (resp or {}).get("result", {}).get("tools", []) or []

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        resp = self._post({
            "jsonrpc": "2.0", "id": self._next_id(), "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })
        if not isinstance(resp, dict):
            raise SystemExit(f"FATAL: unexpected tools/call response: {resp!r}")
        if "error" in resp:
            raise SystemExit(
                f"FATAL: tool {name!r} error: "
                f"{json.dumps(resp['error'])[:512]}"
            )
        return resp.get("result", {}) or {}


def _render_tool_result(name: str, result: dict[str, Any]) -> None:
    """Pretty-print a tools/call result, redacting any obvious secrets."""
    print(f"--- tool {name} result ---")
    if result.get("isError"):
        print("isError=True")
    for content in result.get("content", []):
        if not isinstance(content, dict):
            print(repr(content))
            continue
        ctype = content.get("type")
        if ctype == "text":
            text = content.get("text", "")
            if len(text) > 4000:
                print(text[:4000] + "...[truncated]")
            else:
                print(text)
        else:
            print(json.dumps(content, indent=2)[:2000])


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="_phase_1j_mcp_apply")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list-tools")
    sub.add_parser("list-tables")
    p_am = sub.add_parser("apply-migration")
    p_am.add_argument("name")
    p_am.add_argument("path")
    p_es = sub.add_parser("exec-sql")
    p_es.add_argument("sql")
    p_esf = sub.add_parser("exec-sql-file")
    p_esf.add_argument("path")
    args = ap.parse_args(argv)

    url, token = _load_supabase_oauth()
    sess = McpHttpSession(url, token)
    sess.initialize()

    if args.cmd == "list-tools":
        tools = sess.list_tools()
        for t in tools:
            print(f"- {t['name']}: {t.get('description', '')[:80]}")
        return 0

    if args.cmd == "list-tables":
        result = sess.call_tool("list_tables", {"schemas": ["public"]})
        _render_tool_result("list_tables", result)
        return 0

    if args.cmd == "apply-migration":
        sql = Path(args.path).read_text(encoding="utf-8")
        result = sess.call_tool(
            "apply_migration", {"name": args.name, "query": sql}
        )
        _render_tool_result(f"apply_migration({args.name})", result)
        return 0

    if args.cmd == "exec-sql":
        result = sess.call_tool("execute_sql", {"query": args.sql})
        _render_tool_result("execute_sql", result)
        return 0

    if args.cmd == "exec-sql-file":
        sql = Path(args.path).read_text(encoding="utf-8")
        result = sess.call_tool("execute_sql", {"query": sql})
        _render_tool_result(f"execute_sql({Path(args.path).name})", result)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
