"""Micro AstroCollab reference server.

An in-memory server for the draft protocol. It shows how the rules in
spec/protocol.md fit together; it is not built for production. It keeps all
state in memory, trusts the clock, and handles one request at a time.

The server reads openapi/astrocollab.yaml at start. It uses the contract to
route requests, validate request bodies and find each operation's credential
context and scopes, so the code below holds only the behaviour that a schema
cannot express.

Run it:

    python -m reference.server --port 8080 --key owner=OWNER_SECRET --key alice=ALICE_SECRET
"""
from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import parse_qs, urlsplit
import uuid

from jsonschema import Draft202012Validator, FormatChecker
import yaml

CONTRACT_PATH = Path(__file__).resolve().parents[1] / "openapi/astrocollab.yaml"

ACCOUNT_SCOPES = {"account:read", "participation:manage", "project:create"}
PARTICIPANT_SCOPES = {"project:read", "offer:write", "intent:write", "status:write",
                      "submission:write", "submission:read-own"}
MAINTAINER_SCOPES = PARTICIPANT_SCOPES | {"project:manage", "participation:review",
                                          "assessment:write", "submission:read-all"}
ROLE_SCOPES = {"contributor": PARTICIPANT_SCOPES, "maintainer": MAINTAINER_SCOPES,
               "owner": MAINTAINER_SCOPES}
ALL_SCOPES = frozenset(ACCOUNT_SCOPES | MAINTAINER_SCOPES)
INACTIVE = {"paused", "withdrawn", "revoked"}
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
ASSESSOR_ID = "00000000-0000-4000-8000-0000000a55e5"  # The automatic assessor.
PAGE_SIZE = 50


class Problem(Exception):
    """An RFC 9457 problem. Handlers raise it; the dispatcher renders it."""

    def __init__(self, status: int, code: str, detail: str, errors: list | None = None):
        super().__init__(detail)
        self.status, self.code, self.detail, self.errors = status, code, detail, errors


@dataclass
class Credential:
    """An API key. Its ID serves as the client ID."""
    account_id: str
    client_id: str
    scopes: frozenset
    project_ids: frozenset | None = None  # None: the key covers every project.


@dataclass
class Request:
    op: dict
    params: dict
    query: dict
    headers: dict
    body: object
    raw: bytes
    cred: Credential | None


def now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def ts(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def new_id() -> str:
    return str(uuid.uuid4())


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def etag(value: object) -> str:
    return '"' + hashlib.sha256(canonical(value)).hexdigest()[:32] + '"'


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def public(record: dict) -> dict:
    """Drop internal fields (leading underscore) from a stored record."""
    return {k: v for k, v in record.items() if not k.startswith("_")}


def encode_cursor(value: dict) -> str:
    return base64.urlsafe_b64encode(canonical(value)).decode()


def decode_cursor(text: str) -> dict:
    try:
        value = json.loads(base64.urlsafe_b64decode(text.encode()))
        assert isinstance(value, dict)
        return value
    except Exception:
        raise Problem(400, "invalid_cursor", "The cursor is not valid.") from None


class Api:
    """Protocol behaviour, independent of the HTTP server."""

    def __init__(self, base_url: str, keys: dict[str, str], part_size: int = 1 << 20,
                 check_responses: bool = False):
        self.base_url = base_url.rstrip("/")
        self.part_size = part_size
        self.check_responses = check_responses
        self.lock = threading.RLock()
        self.contract = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
        self.routes = []
        for path, item in self.contract["paths"].items():
            pattern = re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", path) + "$")
            for method, op in item.items():
                # Resolve shared parameters once, so handlers see plain names.
                op["parameters"] = [self.resolve(p) for p in op.get("parameters", [])]
                self.routes.append((method.upper(), pattern, op))
        self.server_id = new_id()
        self.limits = {
            "max_json_bytes": 1 << 20, "max_artifacts_per_submission": 100,
            "max_artifact_bytes": 256 << 20, "max_chunk_bytes": max(part_size, 1 << 20),
            "idempotency_retention_seconds": 86400, "snapshot_ttl_seconds": 900,
            "change_retention_seconds": 2592000, "upload_staging_seconds": 86400,
            "max_decoded_pixels": 200000000,
        }
        # State. Every dict maps an ID to a stored record.
        self.accounts: dict[str, str] = {}  # account name -> account ID
        self.keys: dict[str, dict] = {}  # sha256(secret) -> key record
        self.pairing_codes: dict[str, dict] = {}  # sha256(code) -> pending grant
        self.projects: dict[str, dict] = {}
        self.drafts: dict[str, dict] = {}
        self.revisions: dict[str, list] = {}
        self.participations: dict[str, dict] = {}
        self.equipment: dict[tuple, list] = {}  # (participation, equipment) -> revisions
        self.capacity: dict[str, list] = {}
        self.policies: dict[str, list] = {}
        self.intents: dict[tuple, list] = {}
        self.status: dict[tuple, dict] = {}
        self.submissions: dict[str, dict] = {}
        self.uploads: dict[str, dict] = {}
        self.artifacts: dict[str, dict] = {}
        self.assessments: dict[str, dict] = {}
        self.credits: dict[tuple, dict] = {}  # (project, objective, origin, capture) -> credit
        self.jobs: dict[str, dict] = {}
        self.snapshots: dict[str, dict] = {}
        self.events: list[dict] = []
        self.idempotency: dict[tuple, dict] = {}
        for name, secret in keys.items():
            self.add_account(name, secret)

    def resolve(self, node: dict) -> dict:
        """Follow a local $ref such as #/components/parameters/Limit."""
        while "$ref" in node:
            target = self.contract
            for part in node["$ref"][2:].split("/"):
                target = target[part]
            node = target
        return node

    def add_account(self, name: str, secret: str) -> str:
        """Create an account (if new) with an API key that holds every scope. For tests."""
        account_id = self.accounts.setdefault(name, new_id())
        self.keys[sha256(secret.encode())] = {
            "id": new_id(), "account_id": account_id, "scopes": ALL_SCOPES,
            "client_name": "direct key", "installation_id": None, "project_ids": None,
            "expires_at": None, "revoked": False}
        return account_id

    def issue_pairing_code(self, account: str, code: str | None = None, scopes=ALL_SCOPES,
                           project_ids: list | None = None, key_ttl: int | None = None) -> str:
        """Do what the account pages do: issue a single-use code that expires in an hour.

        scopes, project_ids and key_ttl stand for the choices the user makes
        when issuing the code; the paired key inherits them.
        """
        with self.lock:
            account_id = self.accounts.setdefault(account, new_id())
            code = code or "acpc_" + secrets.token_urlsafe(24)
            self.pairing_codes[sha256(code.encode())] = {
                "account_id": account_id, "scopes": frozenset(scopes),
                "project_ids": sorted(project_ids) if project_ids else None,
                "key_ttl": key_ttl, "expires_at": now() + timedelta(hours=1)}
            return code

    # ---- Dispatch -------------------------------------------------------

    def handle(self, method: str, target: str, headers: dict, raw: bytes):
        """Return (status, headers, body bytes) for one HTTP request."""
        request_id = new_id()
        headers = {k.lower(): v for k, v in headers.items()}
        op = None
        try:
            url = urlsplit(target)
            if not url.path.startswith("/v1/"):
                raise Problem(404, "not_found", "No such route.")
            path = url.path[3:]
            matches = [(m, p, o) for m, p, o in self.routes if p.match(path)]
            if not matches:
                raise Problem(404, "not_found", "No such route.")
            found = [(p, o) for m, p, o in matches if m == method]
            if not found:
                raise Problem(405, "method_not_allowed", "The route does not support this method.")
            pattern, op = found[0]
            params = pattern.match(path).groupdict()
            for name, value in params.items():
                if name in ("revision", "part_number"):
                    if not value.isdigit() or int(value) < 1:
                        raise Problem(404, "not_found", "No such resource.")
                    params[name] = int(value)
                elif not UUID_RE.match(value):
                    raise Problem(404, "not_found", "No such resource.")
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            with self.lock:
                status, body, extra = self.dispatch(op, params, query, headers, raw)
        except Exception as error:  # A bug: answer 500 rather than drop the connection.
            if not isinstance(error, Problem):
                detail = f"{type(error).__name__}: {error}"[:2000] if self.check_responses \
                    else "The server failed to handle the request."
                error = Problem(500, "internal_error", detail)
            problem = error
            status, extra = problem.status, {}
            body = {"type": "about:blank", "title": problem.code.replace("_", " ").capitalize(),
                    "status": problem.status, "code": problem.code, "detail": problem.detail,
                    "request_id": request_id}
            if problem.errors:
                body["errors"] = problem.errors[:100]
            if problem.status == 429:
                extra["Retry-After"] = "1"
        if self.check_responses and op is not None and body is not None:
            try:
                self.check_response(op, status, body)
            except AssertionError as error:
                status, body = 500, {"type": "about:blank", "title": "Contract violation",
                                     "status": 500, "code": "contract_violation",
                                     "detail": str(error)[:2000], "request_id": request_id}
        out = {"X-Request-Id": request_id, **extra}
        if body is None:
            return status, out, b""
        out["Content-Type"] = "application/problem+json" if status >= 400 else "application/json"
        return status, out, json.dumps(body, ensure_ascii=False).encode()

    def dispatch(self, op, params, query, headers, raw):
        cred = self.authenticate(headers)
        names = {p["name"] for p in op.get("parameters", [])}
        body = None
        media = op.get("requestBody", {}).get("content", {})
        if "application/json" in media:
            if len(raw) > self.limits["max_json_bytes"]:
                raise Problem(413, "payload_too_large", "The JSON body is too large.")
            try:
                body = json.loads(raw or b"null")
            except ValueError:
                raise Problem(400, "invalid_json", "The body is not valid JSON.") from None
            self.validate(media["application/json"]["schema"]["$ref"].split("/")[-1], body)
        req = Request(op, params, query, headers, body, raw, cred)

        # Retries: replay a stored result for the same key and body.
        slot = None
        if "Idempotency-Key" in names:
            key = headers.get("idempotency-key", "")
            if not UUID_RE.match(key.lower()):
                raise Problem(400, "idempotency_key_required", "Send a UUID Idempotency-Key header.")
            if cred is None:
                raise Problem(401, "authentication_required", "Send an API key.")
            slot = (cred.client_id, op["operationId"], json.dumps(params, sort_keys=True), key.lower())
            stored = self.idempotency.get(slot)
            if stored:
                if stored["hash"] != sha256(raw):
                    raise Problem(409, "idempotency_conflict",
                                  "This Idempotency-Key was used with a different request.")
                part = self.participations.get(stored["participation_id"] or "")
                if part and part["state"] != "active":
                    raise Problem(403, "participation_inactive", "The participation is not active.")
                return stored["status"], stored["body"], dict(stored["headers"])

        result = getattr(self, "op_" + op["operationId"])(req)
        status, body, tag, extra = (result + (None, None))[:4]
        extra = dict(extra or {})
        if body is not None and status < 300 and tag is not False:
            extra["ETag"] = tag or etag(body)
        if status in (200, 201, 202) and "If-None-Match" in names and op["responses"].get("304"):
            if headers.get("if-none-match") == extra["ETag"]:
                return 304, None, extra
        if slot:
            part_id = self.participation_for(cred.account_id, params)
            self.idempotency[slot] = {"hash": sha256(raw), "status": status, "body": body,
                                      "headers": extra, "participation_id": part_id}
        return status, body, extra

    def participation_for(self, account_id, params):
        """The caller's participation named by a route, for replay checks."""
        pid = params.get("participation_id")
        if pid and pid in self.participations:
            return pid
        project_id = params.get("project_id")
        for part in self.participations.values():
            if part["project_id"] == project_id and part["account_id"] == account_id:
                return part["id"]
        return None

    def validate(self, schema: str, value: object) -> None:
        errors = sorted(self.validator(schema).iter_errors(value), key=lambda e: list(e.path))
        if errors:
            raise Problem(422, "invalid_request", "The body does not match the schema.", [
                {"pointer": "/" + "/".join(str(p) for p in e.absolute_path) or "/",
                 "code": e.validator} for e in errors])

    def validator(self, schema: str) -> Draft202012Validator:
        return Draft202012Validator({"$ref": f"#/components/schemas/{schema}",
                                     "components": self.contract["components"]},
                                    format_checker=FormatChecker())

    def check_response(self, op, status, body):
        """Test aid: fail loudly when a response breaks the contract."""
        response = op["responses"].get(str(status)) or op["responses"]["default"]
        media = next(iter(response.get("content", {}).values()), None)
        if media is None:
            return
        if op["operationId"] == "getCapabilities":  # Local roots use http://.
            body = json.loads(json.dumps(body).replace('"http://', '"https://'))
        errors = list(self.validator(media["schema"]["$ref"].split("/")[-1]).iter_errors(body))
        if errors:
            raise AssertionError(f"{op['operationId']} {status} breaks the contract: "
                                 + "; ".join(f"{list(e.absolute_path)}: {e.message}" for e in errors))

    # ---- Credentials and access ----------------------------------------

    def authenticate(self, headers) -> Credential | None:
        header = headers.get("authorization")
        if header is None:
            return None
        scheme, _, secret = header.partition(" ")
        if scheme.lower() != "bearer" or not secret.strip():
            raise Problem(401, "invalid_credentials", "Send Authorization: Bearer <credential>.")
        key = self.keys.get(sha256(secret.strip().encode()))
        if key is None or key["revoked"] or (key["expires_at"] and parse_ts(key["expires_at"]) <= now()):
            raise Problem(401, "invalid_credentials", "The API key is unknown, expired or revoked.")
        projects = frozenset(key["project_ids"]) if key["project_ids"] else None
        return Credential(key["account_id"], key["id"], key["scopes"], projects)

    def alternatives(self, req: Request, context: str) -> list:
        rules = req.op.get("x-scope-rules") or {}
        return rules.get(context) or [req.op["x-required-scopes"]]

    def account(self, req: Request) -> str:
        """Require an API key with the operation's account scopes."""
        cred = req.cred
        if cred is None:
            raise Problem(401, "authentication_required", "Send an API key.")
        if not any(set(alt) <= cred.scopes for alt in self.alternatives(req, "account")):
            raise Problem(403, "insufficient_scope", "The credential lacks a required scope.")
        return cred.account_id

    def scopes(self, cred: Credential, part: dict) -> set:
        return set(cred.scopes) & ROLE_SCOPES[part["role"]]

    def member(self, req: Request, project_id: str, alternatives: list | None = None) -> dict:
        """Require active project access; return the caller's participation.

        The key acts through its account's participation in the project. The
        request may use only scopes that both the key and the role allow.
        """
        cred = req.cred
        if cred is None:
            raise Problem(401, "authentication_required", "Send an API key.")
        part = next((p for p in self.participations.values()
                     if p["project_id"] == project_id and p["account_id"] == cred.account_id), None)
        if part is None or (cred.project_ids is not None and project_id not in cred.project_ids):
            raise Problem(404, "not_found", "No such resource.")
        if part["state"] != "active":
            raise Problem(403, "participation_inactive", "The participation is not active.")
        alternatives = alternatives or self.alternatives(req, "project")
        if not any(set(alt) <= self.scopes(cred, part) for alt in alternatives):
            raise Problem(403, "insufficient_scope", "The credential or role lacks a required scope.")
        return part

    def own(self, req: Request, participation_id: str) -> dict:
        """Require that the caller acts as this participation."""
        part = self.participations.get(participation_id)
        if part is None or self.member(req, part["project_id"])["id"] != participation_id:
            raise Problem(404, "not_found", "No such resource.")
        return part

    def own_account_participation(self, req: Request, participation_id: str) -> dict:
        account_id = self.account(req)
        part = self.participations.get(participation_id)
        if part is None or part["account_id"] != account_id:
            raise Problem(404, "not_found", "No such resource.")
        return part

    def visible_project(self, req: Request, project_id: str) -> dict:
        """Public published projects are open to all; others need membership."""
        project = self.projects.get(project_id)
        if project is None:
            raise Problem(404, "not_found", "No such resource.")
        if project["visibility"] == "public" and project["current_revision"] is not None:
            return project
        self.member(req, project_id)
        return project

    @staticmethod
    def precondition(req: Request, current: str | None, create_or_replace: bool = False) -> None:
        """Check If-Match / If-None-Match against the current ETag (None if absent)."""
        match = req.headers.get("if-match")
        none_match = req.headers.get("if-none-match") if create_or_replace else None
        if match and none_match:
            raise Problem(400, "conflicting_preconditions", "Send If-Match or If-None-Match, not both.")
        if not match and not none_match:
            raise Problem(428, "precondition_required", "Send If-Match with the current ETag.")
        if none_match:
            if none_match != "*":
                raise Problem(400, "invalid_precondition", "If-None-Match must be *.")
            if current is not None:
                raise Problem(412, "precondition_failed", "The resource already exists.")
        elif current is None or match != current:
            raise Problem(412, "precondition_failed", "The ETag is stale or the resource is missing.")

    # ---- Paging and events ---------------------------------------------

    def page(self, req: Request, items: list, name: str) -> dict:
        limit = req.query.get("limit", "50")
        if not limit.isdigit() or not 1 <= int(limit) <= 100:
            raise Problem(400, "invalid_limit", "limit must be 1-100.")
        start = 0
        if "cursor" in req.query:
            cursor = decode_cursor(req.query["cursor"])
            if cursor.get("list") != name or not isinstance(cursor.get("offset"), int):
                raise Problem(400, "invalid_cursor", "The cursor is not valid here.")
            start = cursor["offset"]
        end = start + int(limit)
        more = end < len(items)
        return {"items": items[start:end],
                "next_cursor": encode_cursor({"list": name, "offset": end}) if more else None}

    def emit(self, project_id: str, kind: str, resource: dict, account_id: str | None = None,
             remove_id: str | None = None) -> int:
        """Append to the change feed. account_id limits the event to one account."""
        sequence = len(self.events) + 1
        self.events.append({"sequence": sequence, "project_id": project_id, "kind": kind,
                            "resource": resource, "account_id": account_id, "remove_id": remove_id})
        return sequence

    # ---- Capabilities and projects ---------------------------------------

    def op_pairClient(self, req):
        """Trade a single-use pairing code for a new API key."""
        grant = self.pairing_codes.pop(sha256(req.body["pairing_code"].encode()), None)
        if grant is None or grant["expires_at"] <= now():
            raise Problem(401, "invalid_pairing_code", "The pairing code is unknown, used or expired.")
        installation = req.body["installation_id"]
        for key in self.keys.values():  # Pairing again replaces the installation's old key.
            if key["account_id"] == grant["account_id"] and key["installation_id"] == installation:
                key["revoked"] = True
        secret = "acpk_" + secrets.token_urlsafe(32)
        key = {"id": new_id(), "account_id": grant["account_id"], "scopes": grant["scopes"],
               "client_name": req.body["client_name"], "installation_id": installation,
               "project_ids": grant["project_ids"], "revoked": False, "expires_at": None}
        if grant["key_ttl"]:
            key["expires_at"] = ts(now() + timedelta(seconds=grant["key_ttl"]))
        self.keys[sha256(secret.encode())] = key
        body = {"key_id": key["id"], "api_key": secret, "account_id": key["account_id"],
                "client_name": key["client_name"], "installation_id": installation,
                "scopes": sorted(key["scopes"])}
        if key["project_ids"]:
            body["project_ids"] = key["project_ids"]
        if key["expires_at"]:
            body["expires_at"] = key["expires_at"]
        return 201, body, False, {"Cache-Control": "no-store"}

    def op_getCapabilities(self, req):
        return 200, {
            "server_id": self.server_id, "api_root": self.base_url + "/v1",
            "protocol_versions": ["v1"], "spec_version": self.contract["info"]["version"],
            "features": [], "account_url": self.base_url + "/account",
            "artifact_formats": ["fits", "xisf"], "limits": self.limits,
        }

    def check_requirements(self, reqs: dict) -> None:
        """Cross-reference checks that JSON Schema cannot express."""
        targets = [t["id"] for t in reqs["targets"]]
        groups = [g["id"] for g in reqs["processing_groups"]]
        objectives = [o["id"] for o in reqs["objectives"]]
        for ids in (targets, groups, objectives):
            if len(set(ids)) != len(ids):
                raise Problem(422, "duplicate_id", "IDs must be unique within requirements.")
        for objective in reqs["objectives"]:
            if objective["target_id"] not in targets or objective["processing_group_id"] not in groups:
                raise Problem(422, "invalid_reference", "An objective names a missing target or group.")
            if objective["exposure"]["min_seconds"] > objective["exposure"]["max_seconds"]:
                raise Problem(422, "invalid_range", "Exposure min_seconds exceeds max_seconds.")
        if parse_ts(reqs["capture_deadline"]) > parse_ts(reqs["submission_deadline"]):
            raise Problem(422, "invalid_range", "capture_deadline must not follow submission_deadline.")

    def op_listProjects(self, req):
        account_id = self.account(req) if req.cred else None
        mine = {p["project_id"] for p in self.participations.values() if p["account_id"] == account_id}
        items = [public(p) for p in self.projects.values()
                 if p["id"] in mine or (p["visibility"] == "public" and p["current_revision"])]
        return 200, self.page(req, items, "projects")

    def op_createProject(self, req):
        account_id = self.account(req)
        body = req.body
        if body["id"] in self.projects:
            raise Problem(409, "already_exists", "A project with this ID exists.")
        reqs = body["requirements"]
        self.check_requirements(reqs)
        stamp = ts(now())
        project = {"id": body["id"], "owner_account_id": account_id, "title": reqs["title"],
                   "visibility": reqs["visibility"], "state": "draft", "current_revision": None,
                   "updated_at": stamp}
        owner = {"id": new_id(), "project_id": body["id"], "account_id": account_id,
                 "role": "owner", "state": "active", "revision": 1,
                 "accepted_terms": reqs["terms"], "updated_at": stamp, "_self_paused": False}
        self.projects[body["id"]] = project
        self.drafts[body["id"]] = {"project_id": body["id"], "draft_revision": 1, "requirements": reqs}
        self.revisions[body["id"]] = []
        self.participations[owner["id"]] = owner
        return 201, {"project": public(project), "owner_participation": public(owner)}

    def op_getProject(self, req):
        return 200, public(self.visible_project(req, req.params["project_id"]))

    def op_getDraft(self, req):
        self.member(req, req.params["project_id"])
        return 200, self.drafts[req.params["project_id"]]

    def op_replaceDraft(self, req):
        project_id = req.params["project_id"]
        self.member(req, project_id)
        draft = self.drafts[project_id]
        self.precondition(req, etag(draft))
        self.check_requirements(req.body)
        draft.update(draft_revision=draft["draft_revision"] + 1, requirements=req.body)
        return 200, draft

    def op_publishProject(self, req):
        project_id = req.params["project_id"]
        self.member(req, project_id)
        draft = self.drafts[project_id]
        self.precondition(req, etag(draft))
        reqs = draft["requirements"]
        revisions = self.revisions[project_id]
        revision = {"project_id": project_id, "revision": len(revisions) + 1,
                    "published_at": ts(now()), "requirements": reqs}
        revisions.append(revision)
        project = self.projects[project_id]
        project.update(title=reqs["title"], visibility=reqs["visibility"], state=reqs["state"],
                       current_revision=revision["revision"], updated_at=revision["published_at"])
        self.emit(project_id, "project", public(project))
        self.emit(project_id, "project_revision", revision)
        return 201, revision

    def revision(self, project_id: str, number: int) -> dict:
        revisions = self.revisions.get(project_id, [])
        if not 1 <= number <= len(revisions):
            raise Problem(404, "not_found", "No such revision.")
        return revisions[number - 1]

    def current_requirements(self, project_id: str) -> dict | None:
        revisions = self.revisions.get(project_id) or [None]
        return revisions[-1]["requirements"] if revisions[-1] else None

    def op_getProjectRevision(self, req):
        self.visible_project(req, req.params["project_id"])
        return 200, self.revision(req.params["project_id"], req.params["revision"])

    def revision_list(self, req, field):
        self.visible_project(req, req.params["project_id"])
        number = req.query.get("revision", "")
        if not number.isdigit():
            raise Problem(400, "invalid_revision", "Send ?revision=<number>.")
        items = self.revision(req.params["project_id"], int(number))["requirements"][field]
        return 200, self.page(req, items, field)

    def op_listTargets(self, req):
        return self.revision_list(req, "targets")

    def op_listObjectives(self, req):
        return self.revision_list(req, "objectives")

    # ---- Participations ------------------------------------------------------

    def require_current_terms(self, project_id: str, terms: dict | None) -> None:
        current = self.current_requirements(project_id)
        if current and terms != current["terms"]:
            raise Problem(409, "terms_consent_required", "Accept the current project terms first.")

    def change_participation(self, part: dict, **changes) -> dict:
        was_active = part["state"] == "active"
        part.update(changes, revision=part["revision"] + 1, updated_at=ts(now()))
        self.emit(part["project_id"], "participation", public(part), part["account_id"])
        if was_active and part["state"] in INACTIVE:
            self.emit(part["project_id"], "project_access", None, part["account_id"], part["id"])
        return public(part)

    def last_owner(self, part: dict) -> bool:
        owners = [p for p in self.participations.values() if p["project_id"] == part["project_id"]
                  and p["role"] == "owner" and p["state"] == "active"]
        return owners == [part]

    def op_joinProject(self, req):
        account_id = self.account(req)
        project_id = req.params["project_id"]
        project = self.projects.get(project_id)
        if project is None or (project["visibility"] != "public" and not any(
                p["project_id"] == project_id and p["account_id"] == account_id
                for p in self.participations.values())):
            raise Problem(404, "not_found", "No such resource.")
        for part in self.participations.values():
            if part["project_id"] == project_id and part["account_id"] == account_id:
                return 201, public(part)  # One participation per account and project.
        reqs = self.current_requirements(project_id)
        if project["state"] != "open" or reqs is None:
            raise Problem(409, "project_not_open", "The project is not open for enrollment.")
        self.require_current_terms(project_id, req.body["accepted_terms"])
        part = {"id": new_id(), "project_id": project_id, "account_id": account_id,
                "role": "contributor",
                "state": "active" if reqs["enrollment"] == "open" else "requested",
                "revision": 1, "accepted_terms": req.body["accepted_terms"],
                "updated_at": ts(now()), "_self_paused": False}
        self.participations[part["id"]] = part
        self.emit(project_id, "participation", public(part), account_id)
        return 201, public(part)

    def op_listProjectParticipations(self, req):
        project_id = req.params["project_id"]
        self.member(req, project_id)
        items = [public(p) for p in self.participations.values() if p["project_id"] == project_id]
        return 200, self.page(req, items, "participations")

    def op_listMyParticipations(self, req):
        account_id = self.account(req)
        items = [public(p) for p in self.participations.values() if p["account_id"] == account_id]
        return 200, self.page(req, items, "mine")

    def op_getParticipation(self, req):
        part = self.participations.get(req.params["participation_id"])
        if part is None:
            raise Problem(404, "not_found", "No such resource.")
        if req.cred and req.cred.account_id == part["account_id"]:
            self.account(req)
        else:
            self.member(req, part["project_id"], self.alternatives(req, "project"))
        return 200, public(part)

    def op_changeParticipation(self, req):
        part = self.own_account_participation(req, req.params["participation_id"])
        self.precondition(req, etag(public(part)))
        action, state = req.body["action"], part["state"]
        if action == "pause" and state == "active":
            return 200, self.change_participation(part, state="paused", _self_paused=True)
        if action == "resume" and state == "paused" and part["_self_paused"]:
            return 200, self.change_participation(part, state="active", _self_paused=False)
        if action == "withdraw" and state in ("active", "paused", "requested"):
            if self.last_owner(part):
                raise Problem(409, "last_owner", "The last active owner cannot leave.")
            return 200, self.change_participation(part, state="withdrawn")
        if action in ("rerequest", "accept_terms"):
            self.require_current_terms(part["project_id"], req.body["accepted_terms"])
            if action == "accept_terms" and state != "revoked":
                return 200, self.change_participation(part, accepted_terms=req.body["accepted_terms"])
            if action == "rerequest" and state == "withdrawn":
                reqs = self.current_requirements(part["project_id"])
                new = "active" if reqs and reqs["enrollment"] == "open" else "requested"
                return 200, self.change_participation(part, state=new,
                                                      accepted_terms=req.body["accepted_terms"])
        raise Problem(409, "invalid_transition", f"Cannot {action} a {state} participation.")

    def op_reviewParticipation(self, req):
        part = self.participations.get(req.params["participation_id"])
        if part is None:
            raise Problem(404, "not_found", "No such resource.")
        self.member(req, part["project_id"])
        self.precondition(req, etag(public(part)))
        action, state = req.body["action"], part["state"]
        if action == "approve" and state == "requested":
            return 200, self.change_participation(part, state="active")
        if action == "revoke" and state != "revoked":
            if self.last_owner(part):
                raise Problem(409, "last_owner", "The last active owner cannot be revoked.")
            return 200, self.change_participation(part, state="revoked", _self_paused=False)
        if action == "restore_request" and state == "revoked":
            return 200, self.change_participation(part, state="requested")
        raise Problem(409, "invalid_transition", f"Cannot {action} a {state} participation.")

    # ---- Offers, planning, intent and status ------------------------------

    def advice(self) -> dict:
        # This server has no recommendation engine; clients plan locally.
        return {"action": "unavailable", "demand_sequence": len(self.events),
                "next_checkin_seconds": 3600, "reason_codes": ["recommendations_unsupported"]}

    def put_revision(self, req, history: list, make) -> dict:
        """Create-or-replace with revision history. make(revision) builds the record."""
        self.precondition(req, etag(history[-1]) if history else None, create_or_replace=True)
        record = make(len(history) + 1)
        history.append(record)
        return record

    def get_revision(self, history: list | None, number: int | None = None) -> dict:
        if not history:
            raise Problem(404, "not_found", "No such resource.")
        if number is None:
            return history[-1]
        if not 1 <= number <= len(history):
            raise Problem(404, "not_found", "No such revision.")
        return history[number - 1]

    def check_equipment_refs(self, pid: str, refs: list) -> None:
        for ref in refs:
            history = self.equipment.get((pid, ref["equipment_id"])) or []
            if not 1 <= ref["revision"] <= len(history):
                raise Problem(422, "invalid_reference", "An equipment reference does not exist.")

    def op_registerEquipment(self, req):
        pid, eid = req.params["participation_id"], req.params["equipment_id"]
        self.own(req, pid)
        if any(key[1] == eid and key[0] != pid for key in self.equipment):
            raise Problem(409, "id_conflict", "Another participation uses this equipment ID.")
        filters = [f["id"] for f in req.body["filters"]]
        if len(set(filters)) != len(filters):
            raise Problem(422, "duplicate_id", "Filter IDs must be unique.")
        history = self.equipment.setdefault((pid, eid), [])
        record = self.put_revision(req, history, lambda n: {
            "id": eid, "participation_id": pid, "revision": n, "observed_at": ts(now()),
            "configuration": req.body})
        return 200, {"equipment": record, "advice": self.advice()}, etag(record)

    def op_getEquipment(self, req):
        pid = req.params["participation_id"]
        self.own(req, pid)
        return 200, self.get_revision(self.equipment.get((pid, req.params["equipment_id"])))

    def op_getEquipmentRevision(self, req):
        pid = req.params["participation_id"]
        self.own(req, pid)
        return 200, self.get_revision(self.equipment.get((pid, req.params["equipment_id"])),
                                      req.params["revision"])

    def op_setCapacity(self, req):
        pid = req.params["participation_id"]
        self.own(req, pid)
        offer = req.body
        if any((pid, eid) not in self.equipment for eid in offer["equipment_ids"]):
            raise Problem(422, "invalid_reference", "Capacity names unknown equipment.")
        if any(parse_ts(a["start"]) >= parse_ts(a["end"]) for a in offer["availability"]):
            raise Problem(422, "invalid_range", "Each availability interval must end after it starts.")
        history = self.capacity.setdefault(pid, [])
        return 200, self.put_revision(req, history, lambda n: {
            "participation_id": pid, "revision": n, "offer": offer})

    def op_getCapacity(self, req):
        self.own(req, req.params["participation_id"])
        return 200, self.get_revision(self.capacity.get(req.params["participation_id"]))

    def op_getCapacityRevision(self, req):
        self.own(req, req.params["participation_id"])
        return 200, self.get_revision(self.capacity.get(req.params["participation_id"]),
                                      req.params["revision"])

    def op_setPlanningPolicy(self, req):
        pid = req.params["participation_id"]
        part = self.own(req, pid)
        self.check_equipment_refs(pid, req.body["equipment"])
        reqs = self.current_requirements(part["project_id"])
        targets = {t["id"] for t in reqs["targets"]} if reqs else set()
        if any(r["target_id"] not in targets for r in req.body["allowed_regions"]):
            raise Problem(422, "invalid_reference", "An allowed region names an unknown target.")
        history = self.policies.setdefault(pid, [])
        return 200, self.put_revision(req, history, lambda n: {
            "participation_id": pid, "revision": n, "policy": req.body})

    def op_getPlanningPolicy(self, req):
        self.own(req, req.params["participation_id"])
        return 200, self.get_revision(self.policies.get(req.params["participation_id"]))

    def op_getPlanningPolicyRevision(self, req):
        self.own(req, req.params["participation_id"])
        return 200, self.get_revision(self.policies.get(req.params["participation_id"]),
                                      req.params["revision"])

    def op_checkIn(self, req):
        pid = req.params["participation_id"]
        self.own(req, pid)
        body = req.body
        self.check_equipment_refs(pid, body["equipment"])
        self.get_revision(self.capacity.get(pid), body["capacity_revision"])
        self.get_revision(self.policies.get(pid), body["planning_policy_revision"])
        return 200, self.advice()

    def op_requestRecommendation(self, req):
        self.member(req, req.params["project_id"])
        raise Problem(404, "unsupported_feature", "This server does not offer recommendations.")

    def op_setIntent(self, req):
        pid, intent_id = req.params["participation_id"], req.params["intent_id"]
        part = self.own(req, pid)
        body = req.body
        reqs = self.revision(part["project_id"], body["project_revision"])["requirements"]
        objectives = {o["id"] for o in reqs["objectives"]}
        for panel in body["panels"]:
            if not set(panel["objective_ids"]) <= objectives:
                raise Problem(422, "invalid_reference", "A panel names an unknown objective.")
            self.check_equipment_refs(pid, [panel["equipment"]])
        history = self.intents.setdefault((pid, intent_id), [])
        return 200, self.put_revision(req, history, lambda n: {
            "id": intent_id, "participation_id": pid, "revision": n, "intent": body})

    def op_getIntent(self, req):
        pid = req.params["participation_id"]
        self.own(req, pid)
        return 200, self.get_revision(self.intents.get((pid, req.params["intent_id"])))

    def op_reportStatus(self, req):
        pid, writer = req.params["participation_id"], req.params["writer_id"]
        self.own(req, pid)
        current = self.status.get((pid, writer))
        if current and current["_client_id"] != req.cred.client_id:
            raise Problem(409, "writer_conflict", "Another client owns this writer ID.")
        sequence = req.body["sequence"]
        if current and sequence < current["last_sequence"]:
            return 200, {**current["receipt"], "applied": False}
        if current and sequence == current["last_sequence"]:
            if current["status"] != req.body:
                raise Problem(409, "status_conflict", "This sequence was sent with different content.")
            return 200, current["receipt"]
        receipt = {"writer_id": writer, "applied": True, "last_sequence": sequence,
                   "received_at": ts(now())}
        self.status[(pid, writer)] = {"_client_id": req.cred.client_id, "status": req.body,
                                      "last_sequence": sequence, "receipt": receipt}
        return 200, receipt

    def op_listActivity(self, req):
        project_id = req.params["project_id"]
        mine = self.member(req, project_id)
        items = []
        for (pid, writer), entry in self.status.items():
            part = self.participations[pid]
            shared = entry["status"]["visibility"] == "project" and part["state"] == "active"
            if part["project_id"] == project_id and (pid == mine["id"] or shared):
                items.append({"participation_id": pid, "writer_id": writer, "status": entry["status"],
                              "received_at": entry["receipt"]["received_at"],
                              "stale": parse_ts(entry["status"]["expires_at"]) <= now()})
        return 200, self.page(req, items, "activity")

    # ---- Progress ----------------------------------------------------------

    def progress(self, project_id: str) -> dict:
        reqs = self.current_requirements(project_id)
        if reqs is None:
            raise Problem(404, "not_found", "The project has no published revision.")
        stamp = now()
        objectives = []
        for objective in reqs["objectives"]:
            oid = objective["id"]
            credits = [c for k, c in self.credits.items() if k[0] == project_id and k[1] == oid]
            accepted = [c for c in credits if not c["surplus"]]
            intended = sum(panel["suggested_frames"]
                           for (pid, _), history in self.intents.items()
                           if self.participations[pid]["project_id"] == project_id
                           for intent in [history[-1]["intent"]]
                           if intent["state"] == "active" and parse_ts(intent["expires_at"]) > stamp
                           for panel in intent["panels"] if oid in panel["objective_ids"])
            reported = sum(entry["status"]["captured_reported_frames"]
                           for (pid, _), entry in self.status.items()
                           if self.participations[pid]["project_id"] == project_id
                           and entry["status"].get("objective_id") == oid)
            artifacts = [a for a in self.artifacts.values()
                         if a["_project_id"] == project_id and oid in a["_manifest"]["objective_ids"]]
            frames = len(accepted)
            seconds = sum(c["seconds"] for c in accepted)
            goal = objective["goal"]
            objectives.append({
                "objective_id": oid, "goal": goal, "intended_frames": intended,
                "captured_reported_frames": reported,
                "received_pending_frames": sum(a["state"] in ("received", "validating") for a in artifacts),
                "accepted_frames": frames, "accepted_integration_seconds": seconds,
                "rejected_frames": sum(a["state"] == "rejected" for a in artifacts),
                "surplus_frames": len(credits) - frames,
                "complete": frames >= goal.get("accepted_frames", 0)
                and seconds >= goal.get("accepted_integration_seconds", 0),
            })
        captures = {}
        for (pid, _, origin, capture), credit in self.credits.items():
            if pid == project_id and not credit["surplus"]:
                captures[(origin, capture)] = credit["seconds"]
        return {"project_id": project_id,
                "project_revision": self.projects[project_id]["current_revision"],
                "event_sequence": len(self.events), "as_of": ts(stamp), "objectives": objectives,
                "distinct_credited_captures": len(captures),
                "distinct_credited_integration_seconds": sum(captures.values())}

    def op_getProgress(self, req):
        self.visible_project(req, req.params["project_id"])
        return 200, self.progress(req.params["project_id"])

    # ---- Sync ----------------------------------------------------------------

    def active_part(self, account_id: str, project_id: str) -> dict | None:
        return next((p for p in self.participations.values() if p["project_id"] == project_id
                     and p["account_id"] == account_id and p["state"] == "active"), None)

    def op_createSnapshot(self, req):
        account_id = self.account(req)
        project_ids = sorted(set(req.body["project_ids"]))
        items = []
        for project_id in project_ids:
            part = self.active_part(account_id, project_id)
            if part is None:
                raise Problem(404, "not_found", "No such resource.")
            items.append({"kind": "project", "resource": public(self.projects[project_id])})
            if self.revisions[project_id]:
                items.append({"kind": "project_revision", "resource": self.revisions[project_id][-1]})
                items.append({"kind": "progress", "resource": self.progress(project_id)})
            items.append({"kind": "participation", "resource": public(part)})
            items += [{"kind": "assessment", "resource": a} for a in self.assessments.values()
                      if a["participation_id"] == part["id"]]
        snapshot = {"id": new_id(), "account_id": account_id, "client_id": req.cred.client_id,
                    "project_ids": project_ids, "items": items, "sequence": len(self.events),
                    "expires_at": ts(now() + timedelta(seconds=self.limits["snapshot_ttl_seconds"]))}
        self.snapshots[snapshot["id"]] = snapshot
        return 201, self.snapshot_page(snapshot, 0)

    def snapshot_page(self, snapshot: dict, start: int) -> dict:
        end = start + PAGE_SIZE
        page = {"snapshot_id": snapshot["id"], "expires_at": snapshot["expires_at"],
                "items": snapshot["items"][start:end], "next_page_cursor": None}
        if end < len(snapshot["items"]):
            page["next_page_cursor"] = encode_cursor({"snapshot": snapshot["id"], "offset": end})
        else:
            page["changes_cursor"] = encode_cursor({
                "account": snapshot["account_id"], "client": snapshot["client_id"],
                "projects": snapshot["project_ids"], "sequence": snapshot["sequence"]})
        return page

    def op_getSnapshotPage(self, req):
        account_id = self.account(req)
        snapshot = self.snapshots.get(req.params["snapshot_id"])
        if snapshot is None or snapshot["account_id"] != account_id \
                or snapshot["client_id"] != req.cred.client_id:
            raise Problem(404, "not_found", "No such snapshot.")
        if parse_ts(snapshot["expires_at"]) <= now():
            raise Problem(410, "snapshot_expired", "Start a new snapshot.")
        if any(self.active_part(account_id, p) is None for p in snapshot["project_ids"]):
            raise Problem(409, "snapshot_invalidated", "Project access changed; start a new snapshot.")
        start = 0
        if "cursor" in req.query:
            cursor = decode_cursor(req.query["cursor"])
            if cursor.get("snapshot") != snapshot["id"]:
                raise Problem(400, "invalid_cursor", "The cursor belongs to another snapshot.")
            start = cursor["offset"]
        return 200, self.snapshot_page(snapshot, start)

    def op_getChanges(self, req):
        account_id = self.account(req)
        cursor = decode_cursor(req.query["cursor"])
        if cursor.get("account") != account_id or cursor.get("client") != req.cred.client_id:
            raise Problem(400, "invalid_cursor", "The cursor belongs to another client.")
        limit = int(req.query.get("limit", "50"))
        projects, items, last = set(cursor["projects"]), [], cursor["sequence"]
        for event in self.events[cursor["sequence"]:]:
            if len(items) >= limit:
                break
            last = event["sequence"]
            if event["project_id"] not in projects:
                continue
            if event["account_id"] not in (None, account_id):
                continue
            if event["kind"] == "project_access":
                items.append({"sequence": last, "operation": "remove", "kind": "project_access",
                              "project_id": event["project_id"], "resource_id": event["remove_id"]})
            elif self.active_part(account_id, event["project_id"]):
                items.append({"sequence": last, "operation": "upsert",
                              "entry": {"kind": event["kind"], "resource": event["resource"]}})
        return 200, {"items": items, "next_cursor": encode_cursor({**cursor, "sequence": last}),
                     "poll_after_seconds": 30}

    # ---- Submissions and uploads ----------------------------------------------

    def submission_view(self, sub: dict) -> dict:
        view = public(sub)
        view["uploads"] = [self.upload_view(self.uploads[u]) for u in sub["_upload_ids"]]
        view["artifacts"] = [public(self.artifacts[a["id"]]) for a in sub["manifest"]["artifacts"]]
        return view

    @staticmethod
    def upload_view(upload: dict) -> dict:
        view = public(upload)
        view["received_parts"] = [upload["_parts"][n]["receipt"] for n in sorted(upload["_parts"])]
        return view

    def op_createSubmission(self, req):
        project_id = req.params["project_id"]
        part = self.member(req, project_id)
        manifest = req.body
        existing = self.submissions.get(manifest["id"])
        if existing:
            if existing["manifest"] == manifest and existing["participation_id"] == part["id"]:
                return 201, self.submission_view(existing)
            raise Problem(409, "submission_conflict", "This submission ID has different content.")
        if manifest["participation_id"] != part["id"]:
            raise Problem(422, "participation_mismatch", "participation_id must be the caller's.")
        reqs = self.revision(project_id, manifest["project_revision"])["requirements"]
        self.require_current_terms(project_id, part.get("accepted_terms"))
        if now() >= parse_ts(reqs["submission_deadline"]):
            raise Problem(409, "submission_deadline_passed", "The submission deadline has passed.")
        if len(manifest["artifacts"]) > self.limits["max_artifacts_per_submission"]:
            raise Problem(413, "too_many_artifacts", "Too many artifacts in one submission.")
        objectives = {o["id"] for o in reqs["objectives"]}
        groups = {g["id"] for g in reqs["processing_groups"]}
        ids = [a["id"] for a in manifest["artifacts"]]
        if len(set(ids)) != len(ids) or any(i in self.artifacts for i in ids):
            raise Problem(409, "artifact_conflict", "Artifact IDs must be new and unique.")
        for artifact in manifest["artifacts"]:
            if not set(artifact["objective_ids"]) <= objectives \
                    or artifact["processing_group_id"] not in groups:
                raise Problem(422, "invalid_reference", "An artifact names an unknown objective or group.")
            self.check_equipment_refs(part["id"], [artifact["equipment"]])
            if parse_ts(artifact["captured_at"]) >= parse_ts(reqs["capture_deadline"]):
                raise Problem(422, "capture_deadline_passed", "The capture started after the deadline.")
            if artifact["size_bytes"] > self.limits["max_artifact_bytes"]:
                raise Problem(413, "artifact_too_large", "An artifact exceeds max_artifact_bytes.")
            if artifact.get("delivery") == "external":
                accepted = reqs.get("external_delivery", {}).get("providers", [])
                if artifact["external"]["provider"] not in accepted:
                    raise Problem(422, "external_delivery_not_accepted",
                                  "This project revision does not accept files from that provider.")
            old = self.artifacts.get(artifact.get("supersedes_artifact_id", ""))
            if "supersedes_artifact_id" in artifact and (
                    old is None or old["_participation_id"] != part["id"]
                    or (old["_manifest"]["origin_id"], old["_manifest"]["capture_id"])
                    != (artifact["origin_id"], artifact["capture_id"])):
                raise Problem(422, "invalid_supersede", "A replacement must keep the capture identity.")
        stamp = now()
        sub = {"id": manifest["id"], "project_id": project_id, "participation_id": part["id"],
               "project_revision": manifest["project_revision"], "state": "uploading",
               "created_at": ts(stamp), "manifest": manifest, "_upload_ids": []}
        for artifact in manifest["artifacts"]:
            self.artifacts[artifact["id"]] = {
                "artifact_id": artifact["id"], "state": "uploading", "reason_codes": [],
                "_manifest": artifact, "_submission_id": sub["id"], "_project_id": project_id,
                "_participation_id": part["id"]}
            if artifact.get("delivery") == "external":
                # A shared file: no upload session. A maintainer fetches it later.
                self.artifacts[artifact["id"]]["state"] = "awaiting_retrieval"
                continue
            upload = {"id": new_id(), "submission_id": sub["id"], "artifact_id": artifact["id"],
                      "size_bytes": artifact["size_bytes"], "part_size_bytes": self.part_size,
                      "part_count": math.ceil(artifact["size_bytes"] / self.part_size),
                      "expires_at": ts(stamp + timedelta(seconds=self.limits["upload_staging_seconds"])),
                      "_parts": {}, "_finalized": False}
            if upload["part_count"] > 10000:
                raise Problem(413, "artifact_too_large", "The artifact needs more than 10,000 parts.")
            self.uploads[upload["id"]] = upload
            sub["_upload_ids"].append(upload["id"])
        self.submissions[sub["id"]] = sub
        return 201, self.submission_view(sub)

    def readable_submission(self, req, sub: dict | None, context: str = "project") -> dict:
        """Own submissions need read-own; others need read-all."""
        if sub is None:
            raise Problem(404, "not_found", "No such resource.")
        part = self.member(req, sub["project_id"], self.alternatives(req, context))
        if part["id"] != sub["participation_id"] \
                and "submission:read-all" not in self.scopes(req.cred, part):
            raise Problem(404, "not_found", "No such resource.")
        return sub

    def op_listProjectSubmissions(self, req):
        project_id = req.params["project_id"]
        self.member(req, project_id)
        items = [self.submission_view(s) for s in self.submissions.values()
                 if s["project_id"] == project_id]
        return 200, self.page(req, items, "submissions")

    def op_getSubmission(self, req):
        sub = self.readable_submission(req, self.submissions.get(req.params["submission_id"]))
        return 200, self.submission_view(sub)

    def own_upload(self, req) -> dict:
        upload = self.uploads.get(req.params["upload_id"])
        if upload is None:
            raise Problem(404, "not_found", "No such upload.")
        self.own(req, self.submissions[upload["submission_id"]]["participation_id"])
        return upload

    def op_getUpload(self, req):
        return 200, self.upload_view(self.own_upload(req))

    def op_renewUpload(self, req):
        upload = self.own_upload(req)
        if upload["_finalized"]:
            raise Problem(409, "upload_finalized", "A finalized upload cannot change.")
        sub = self.submissions[upload["submission_id"]]
        self.require_current_terms(sub["project_id"],
                                   self.participations[sub["participation_id"]].get("accepted_terms"))
        stamp = now() + timedelta(seconds=self.limits["upload_staging_seconds"])
        upload["expires_at"] = ts(stamp)
        return 200, self.upload_view(upload)

    def op_putUploadPart(self, req):
        upload = self.own_upload(req)
        number = req.params["part_number"]
        if upload["_finalized"]:
            raise Problem(409, "upload_finalized", "A finalized upload cannot change.")
        if parse_ts(upload["expires_at"]) <= now():
            raise Problem(409, "upload_expired", "Renew the upload session first.")
        if number > upload["part_count"]:
            raise Problem(422, "invalid_part_number", "The part number exceeds part_count.")
        last = upload["size_bytes"] - upload["part_size_bytes"] * (upload["part_count"] - 1)
        expected = last if number == upload["part_count"] else upload["part_size_bytes"]
        if len(req.raw) != expected:
            raise Problem(422, "part_size_mismatch", f"Part {number} must contain {expected} bytes.")
        digest = sha256(req.raw)
        if req.headers.get("x-part-sha256") != digest:
            raise Problem(422, "digest_mismatch", "X-Part-SHA256 does not match the bytes.")
        existing = upload["_parts"].get(number)
        if existing:
            if existing["receipt"]["sha256"] != digest:
                raise Problem(409, "part_conflict", "This part already holds different bytes.")
            return 200, existing["receipt"]
        receipt = {"part_number": number, "size_bytes": len(req.raw), "sha256": digest}
        upload["_parts"][number] = {"receipt": receipt, "data": req.raw}
        return 200, receipt

    def op_finalizeSubmission(self, req):
        sub = self.submissions.get(req.params["submission_id"])
        if sub is None:
            raise Problem(404, "not_found", "No such resource.")
        part = self.own(req, sub["participation_id"])
        if sub.get("job_id"):
            return 202, public(self.jobs[sub["job_id"]])  # Retries get the same job.
        uploads = [self.uploads[u] for u in sub["_upload_ids"]]
        if any(len(u["_parts"]) != u["part_count"] for u in uploads):
            raise Problem(409, "upload_incomplete", "Some parts are missing; read the upload sessions.")
        self.require_current_terms(sub["project_id"], part.get("accepted_terms"))
        reqs = self.revision(sub["project_id"], sub["project_revision"])["requirements"]
        if now() >= parse_ts(reqs["submission_deadline"]):
            raise Problem(409, "submission_deadline_passed", "The submission deadline has passed.")
        job = {"id": new_id(), "kind": "assessment", "state": "queued", "poll_after_seconds": 1,
               "_submission_id": sub["id"]}
        self.jobs[job["id"]] = job
        sub.update(state="processing", job_id=job["id"])
        for upload in uploads:
            upload["_finalized"] = True
            self.artifacts[upload["artifact_id"]]["state"] = "received"
        threading.Thread(target=self.run_assessment, args=(job["id"],), daemon=True).start()
        return 202, public(job)

    # ---- Assessment and credit ---------------------------------------------------

    def run_assessment(self, job_id: str) -> None:
        """Background worker: verify each artifact and assess it."""
        time.sleep(0.1)
        with self.lock:
            job = self.jobs[job_id]
            sub = self.submissions[job["_submission_id"]]
            job["state"] = "running"
            for upload_id in sub["_upload_ids"]:
                upload = self.uploads[upload_id]
                artifact = self.artifacts[upload["artifact_id"]]
                artifact["state"] = "validating"
                data = b"".join(upload["_parts"][n]["data"] for n in sorted(upload["_parts"]))
                for stored in upload["_parts"].values():
                    stored["data"] = b""  # Drop the bytes; this server keeps no files.
                self.record_assessment(sub, artifact, *self.assess(sub, artifact["_manifest"], data),
                                       measurements=artifact["_manifest"]["measurements"],
                                       assessment_id=new_id(), assessor_id=ASSESSOR_ID)
            self.finish_if_done(sub)

    def finish_if_done(self, sub: dict) -> None:
        """Complete the submission and its job once no artifact is waiting."""
        states = {self.artifacts[a["id"]]["state"] for a in sub["manifest"]["artifacts"]}
        if states & {"uploading", "awaiting_retrieval", "received", "validating"}:
            return
        sub["state"] = "complete"
        self.jobs[sub["job_id"]]["state"] = "succeeded"

    def assess(self, sub: dict, manifest: dict, data: bytes | None):
        """Automatic checks. Returns (decision, reasons, credits).

        This server does not decode FITS or XISF, compute coverage or measure
        quality. It checks the file hash, exposure range, solve evidence and
        submitted measurements, then applies goal and duplicate rules. data is
        None when a maintainer already verified an external file's hash.
        """
        reqs = self.revision(sub["project_id"], sub["project_revision"])["requirements"]
        objectives = {o["id"]: o for o in reqs["objectives"]}
        reasons = []
        if data is not None and sha256(data) != manifest["sha256"]:
            reasons.append("digest_mismatch")
        measured = {m["metric"]: m["value"] for m in manifest["measurements"]}
        for oid in manifest["objective_ids"]:
            objective = objectives[oid]
            exposure = objective["exposure"]
            if not exposure["min_seconds"] <= manifest["exposure_seconds"] <= exposure["max_seconds"]:
                reasons.append("exposure_out_of_range")
            solve = manifest.get("solve")
            if objective["fresh_pixel_solve"] and not (
                    solve and solve["source"] == "fresh_pixel_solve"
                    and solve["artifact_sha256"] == manifest["sha256"]):
                reasons.append("fresh_solve_missing")
            for rule in objective["quality_rules"]:
                value = measured.get(rule["metric"])
                if value is None:
                    if rule["required"]:
                        reasons.append("required_measurement_missing")
                elif value < rule.get("min_value", value) or value > rule.get("max_value", value):
                    reasons.append("quality_limit")
        if reasons:
            return "rejected", sorted(set(reasons)), []
        credits = []
        replaced = manifest.get("supersedes_artifact_id")
        for oid in manifest["objective_ids"]:
            key = (sub["project_id"], oid, manifest["origin_id"], manifest["capture_id"])
            held = self.credits.get(key)
            if held and held["artifact_id"] != replaced:
                return "rejected", ["duplicate_capture"], []
            if replaced and held:
                surplus = held["surplus"]  # A replacement keeps its place in the goal.
            else:
                accepted = [c for k, c in self.credits.items()
                            if k[:2] == key[:2] and not c["surplus"]]
                goal = objectives[oid]["goal"]
                surplus = len(accepted) >= goal.get("accepted_frames", 0) and \
                    sum(c["seconds"] for c in accepted) >= goal.get("accepted_integration_seconds", 0)
            if surplus and reqs["surplus_policy"] == "reject_excess":
                continue
            credits.append({"objective_id": oid, "accepted_frames": 0 if surplus else 1,
                            "accepted_integration_seconds": 0 if surplus else manifest["exposure_seconds"],
                            "coverage_fraction": 1.0, "surplus": surplus})
        if not credits:
            return "rejected", ["goal_already_met"], []
        return "accepted", [], credits

    def record_assessment(self, sub, artifact, decision, reasons, credits, *, measurements,
                          assessment_id, assessor_id) -> dict:
        """Append an assessment and update credit in one step."""
        manifest = artifact["_manifest"]
        replaced = manifest.get("supersedes_artifact_id")
        for key in [k for k, c in self.credits.items() if c["artifact_id"] == manifest["id"]]:
            del self.credits[key]
        if decision == "accepted":
            if replaced:
                for key in [k for k, c in self.credits.items() if c["artifact_id"] == replaced]:
                    del self.credits[key]
                self.artifacts[replaced]["state"] = "superseded"
            for credit in credits:
                key = (sub["project_id"], credit["objective_id"], manifest["origin_id"],
                       manifest["capture_id"])
                self.credits[key] = {"artifact_id": manifest["id"], "surplus": credit["surplus"],
                                     "seconds": credit["accepted_integration_seconds"]}
        sequence = len(self.events) + 1
        assessment = {
            "id": assessment_id, "artifact_id": manifest["id"],
            "policy_revision": self.revision(sub["project_id"], sub["project_revision"])
            ["requirements"]["acceptance_policy_revision"],
            "decision": decision, "reasons": reasons, "measurements": measurements, "credits": credits,
            "submission_id": sub["id"], "project_id": sub["project_id"],
            "participation_id": sub["participation_id"], "origin_id": manifest["origin_id"],
            "capture_id": manifest["capture_id"], "assessed_at": ts(now()),
            "assessor_id": assessor_id, "event_sequence": sequence}
        self.assessments[assessment_id] = assessment
        artifact.update(state=decision, assessment_id=assessment_id, reason_codes=reasons)
        owner = self.participations[sub["participation_id"]]["account_id"]
        self.emit(sub["project_id"], "assessment", assessment, owner)
        self.emit(sub["project_id"], "progress", self.progress(sub["project_id"]))
        return assessment

    def op_recordRetrieval(self, req):
        """A maintainer reports the result of fetching an externally shared file."""
        sub = self.submissions.get(req.params["submission_id"])
        if sub is None:
            raise Problem(404, "not_found", "No such resource.")
        part = self.member(req, sub["project_id"])
        self.precondition(req, etag(self.submission_view(sub)))
        artifact = self.artifacts.get(req.params["artifact_id"])
        if artifact is None or artifact["_submission_id"] != sub["id"]:
            raise Problem(404, "not_found", "No such artifact in this submission.")
        if artifact["state"] != "awaiting_retrieval" or not sub.get("job_id"):
            raise Problem(409, "invalid_transition", "The artifact is not finalized and awaiting retrieval.")
        body, manifest = req.body, artifact["_manifest"]
        if body["outcome"] == "verified":
            if (body["sha256"], body["size_bytes"]) != (manifest["sha256"], manifest["size_bytes"]):
                raise Problem(422, "digest_mismatch", "verified requires the manifest's hash and size.")
            artifact["state"] = "validating"
            result = self.assess(sub, manifest, None)
        else:
            reason = "external_unavailable" if body["outcome"] == "unavailable" else "digest_mismatch"
            result = ("rejected", [reason], [])
        self.record_assessment(sub, artifact, *result, measurements=manifest["measurements"],
                               assessment_id=new_id(), assessor_id=part["account_id"])
        self.finish_if_done(sub)
        return 200, self.submission_view(sub)

    def op_listAssessments(self, req):
        sub = self.readable_submission(req, self.submissions.get(req.params["submission_id"]))
        items = [a for a in self.assessments.values() if a["submission_id"] == sub["id"]]
        return 200, self.page(req, items, "assessments")

    def op_appendAssessment(self, req):
        sub = self.submissions.get(req.params["submission_id"])
        if sub is None:
            raise Problem(404, "not_found", "No such resource.")
        part = self.member(req, sub["project_id"])
        self.precondition(req, etag(self.submission_view(sub)))
        body = req.body
        if body["id"] in self.assessments:
            raise Problem(409, "already_exists", "An assessment with this ID exists.")
        artifact = self.artifacts.get(body["artifact_id"])
        if artifact is None or artifact["_submission_id"] != sub["id"]:
            raise Problem(422, "invalid_reference", "The artifact is not in this submission.")
        if artifact["state"] not in ("accepted", "rejected"):
            raise Problem(409, "artifact_not_validated", "Only validated artifacts can be assessed.")
        manifest = artifact["_manifest"]
        for credit in body["credits"]:
            if credit["objective_id"] not in manifest["objective_ids"]:
                raise Problem(422, "invalid_reference", "A credit names an objective the artifact lacks.")
            if not credit["surplus"] and credit["accepted_integration_seconds"] != manifest["exposure_seconds"]:
                raise Problem(422, "invalid_credit", "Credit must equal the artifact's exposure.")
            key = (sub["project_id"], credit["objective_id"], manifest["origin_id"], manifest["capture_id"])
            held = self.credits.get(key)
            if held and held["artifact_id"] not in (manifest["id"], manifest.get("supersedes_artifact_id")):
                raise Problem(409, "duplicate_capture", "Another artifact holds credit for this capture.")
        assessment = self.record_assessment(
            sub, artifact, body["decision"], body["reasons"], body["credits"],
            measurements=body["measurements"], assessment_id=body["id"],
            assessor_id=part["account_id"])
        return 201, assessment

    def op_getJob(self, req):
        job = self.jobs.get(req.params["job_id"])
        if job is None:
            raise Problem(404, "not_found", "No such job.")
        sub = self.readable_submission(req, self.submissions[job["_submission_id"]], job["kind"])
        view = public(job)
        if job["state"] == "succeeded":
            view["result"] = self.submission_view(sub)
        return 200, view


# ---- HTTP ----------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "AstroCollabReference/0.1"

    def handle_one(self):
        api: Api = self.server.api
        length = self.headers.get("Content-Length")
        if length is not None and (not length.isdigit()
                                   or int(length) > max(api.part_size, api.limits["max_json_bytes"])):
            self.send_error(413)
            self.close_connection = True
            return
        raw = self.rfile.read(int(length or 0))
        if "/parts/" in self.path and self.command == "PUT" and length is None:
            status, headers, body = 411, {"Content-Type": "text/plain"}, b"Content-Length required\n"
        else:
            status, headers, body = api.handle(self.command, self.path, dict(self.headers.items()), raw)
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = handle_one

    def log_message(self, format, *args):
        if self.server.verbose:
            super().log_message(format, *args)


def serve(host: str = "127.0.0.1", port: int = 0, keys: dict[str, str] | None = None,
          part_size: int = 1 << 20, check_responses: bool = False,
          verbose: bool = False) -> ThreadingHTTPServer:
    """Create a server bound to host:port. Call serve_forever() to run it.

    keys maps account names to API key secrets. The returned server has
    issue_pairing_code(account) for minting pairing codes.
    """
    httpd = ThreadingHTTPServer((host, port), Handler)
    bound = httpd.server_address[1]
    httpd.api = Api(f"http://{host}:{bound}", keys or {}, part_size, check_responses)
    httpd.issue_pairing_code = httpd.api.issue_pairing_code
    httpd.verbose = verbose
    return httpd


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--key", action="append", default=[], metavar="ACCOUNT=SECRET",
                        help="Create an account with an API key that holds every scope. Repeatable.")
    parser.add_argument("--pairing-code", action="append", default=[], metavar="ACCOUNT=CODE",
                        help="Issue a pairing code (valid one hour) with every scope. Repeatable.")
    parser.add_argument("--part-size", type=int, default=1 << 20, help="Upload part size in bytes.")
    parser.add_argument("--check-responses", action="store_true",
                        help="Validate every response against the contract (for testing).")
    parser.add_argument("--verbose", action="store_true", help="Log each request.")
    args = parser.parse_args()
    keys = dict(item.split("=", 1) for item in args.key)
    codes = dict(item.split("=", 1) for item in args.pairing_code)
    if not keys and not codes:
        keys = {name: secrets.token_urlsafe(24) for name in ("owner", "alice", "bob")}
        codes = {name: None for name in keys}
    httpd = serve(args.host, args.port, keys, args.part_size, args.check_responses, args.verbose)
    print(f"AstroCollab reference server at {httpd.api.base_url}/v1", flush=True)
    for name, secret in keys.items():
        print(f"  API key for {name}: {secret}", flush=True)
    for name, code in codes.items():
        print(f"  Pairing code for {name}: {httpd.issue_pairing_code(name, code)}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
