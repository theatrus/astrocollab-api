"""Micro AstroCollab reference server.

An in-memory server for the contributor API. It shows how the rules in
spec/protocol.md fit together; it is not built for production. It keeps all
state in memory, trusts the clock, and handles one request at a time.

The server reads openapi/astrocollab.yaml at start. It uses the contract to
route requests and validate request bodies, so the code below holds only the
behaviour that a schema cannot express.

Projects, memberships and manual reviews are not part of the API. A real
server manages them in its own web pages; this one offers them as "server
tools": plain methods on Api (create_project, publish, join, assess_artifact,
record_retrieval), and the --join flag.

Run it:

    python -m reference.server --port 8080 --key alice=ALICE_SECRET
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

from reference import planning

CONTRACT_PATH = Path(__file__).resolve().parents[1] / "openapi/astrocollab.yaml"
SAMPLE_PATH = Path(__file__).resolve().parent / "sample_project.json"
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
ASSESSOR_ID = "00000000-0000-4000-8000-0000000a55e5"  # The automatic assessor.
RIG_DEFAULTS = {"binning_x": 1, "binning_y": 1, "rotation": "fixed",
                "confirmed_position_angle_degrees": 0}


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


# ---- Small helpers ------------------------------------------------------------


def now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def ts(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def new_id() -> str:
    return str(uuid.uuid4())


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def public(record: dict) -> dict:
    """Drop internal fields (leading underscore) from a stored record."""
    return {k: v for k, v in record.items() if not k.startswith("_")}


def encode_cursor(value: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(value, sort_keys=True).encode()).decode()


def decode_cursor(text: str) -> dict:
    try:
        value = json.loads(base64.urlsafe_b64decode(text.encode()))
        assert isinstance(value, dict)
        return value
    except Exception:
        raise Problem(400, "invalid_cursor", "The cursor is not valid.") from None


def with_defaults(config: dict) -> dict:
    """A rig description with the contract's defaults filled in."""
    return {**RIG_DEFAULTS, **config}


def merge_patch(target, patch):
    """RFC 7396 merge patch: null removes a field; arrays replace whole."""
    if not isinstance(patch, dict):
        return patch
    result = dict(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = merge_patch(result.get(key), value)
    return result


def captures_of(manifest: dict) -> list[tuple]:
    """(origin_id, capture_id) pairs in an artifact: one for a sub, every sub for a master."""
    if "stack" in manifest:
        return [(s["origin_id"], s["capture_id"]) for s in manifest["stack"]["subs"]]
    return [(manifest["origin_id"], manifest["capture_id"])]


def frames_in(manifest: dict) -> int:
    return manifest["stack"]["sub_count"] if "stack" in manifest else 1


def integration_of(manifest: dict) -> float:
    return manifest["stack"]["integration_seconds"] if "stack" in manifest else manifest["exposure_seconds"]


def objectives_served(requirements: dict, objective: dict, passbands: list, exposure: float) -> list:
    """The objective, plus others on the same target and processing group that a
    frame through these passbands also serves (a dual-band filter serves two)."""
    served = [objective["id"]]
    for other in requirements["objectives"]:
        if other["id"] != objective["id"] and other["target_id"] == objective["target_id"] \
                and other["processing_group_id"] == objective["processing_group_id"] \
                and other["exposure"]["min_seconds"] <= exposure <= other["exposure"]["max_seconds"] \
                and planning.serves(passbands, other["bandpasses"]):
            served.append(other["id"])
    return served


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
                op["parameters"] = [self.resolve(p) for p in op.get("parameters", [])]
                self.routes.append((method.upper(), pattern, op))
        self.server_id = new_id()
        self.limits = {
            "max_json_bytes": 1 << 20, "max_artifacts_per_submission": 100,
            "max_artifact_bytes": 256 << 20, "max_chunk_bytes": max(part_size, 1 << 20),
            "upload_staging_seconds": 86400, "max_decoded_pixels": 200000000,
        }
        # State. Every dict maps an ID to a stored record unless noted.
        self.accounts: dict[str, str] = {}  # account name -> account ID
        self.keys: dict[str, dict] = {}  # sha256(secret) -> key record
        self.pairing_codes: dict[str, dict] = {}  # sha256(code) -> pending grant
        self.projects: dict[str, dict] = {}  # project ID -> {"revisions": [...], "state": ...}
        self.members: dict[tuple, dict] = {}  # (account, project) -> membership
        self.equipment: dict[tuple, list] = {}  # (account, equipment) -> revisions
        self.assignments: dict[str, dict] = {}
        self.latest: dict[str, str] = {}  # equipment -> latest assignment ID
        self.reported: dict[str, list] = {}  # equipment -> latest unsubmitted_captures report
        self.panel_objectives: dict[str, list] = {}  # panel -> objectives it was assigned for
        self.layouts: dict[tuple, list] = {}  # (project, target) -> panel grids
        self.panels: dict[str, dict] = {}  # panel ID -> project, target and footprint
        self.submissions: dict[str, dict] = {}
        self.uploads: dict[str, dict] = {}
        self.artifacts: dict[str, dict] = {}
        self.credits: dict[tuple, dict] = {}  # (project, objective, artifact) -> credit
        self.claimed: dict[tuple, str] = {}  # (project, origin, capture) -> crediting artifact
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

    # ---- Server tools ------------------------------------------------------
    # What a real server does through its own web pages. Not part of the API.

    def add_account(self, name: str, secret: str) -> str:
        """Create an account (if new) with an API key. For tests and demos."""
        with self.lock:
            account_id = self.accounts.setdefault(name, new_id())
            self.keys[sha256(secret.encode())] = {
                "id": new_id(), "account_id": account_id, "client_name": "direct key",
                "installation_id": None, "project_ids": None, "expires_at": None, "revoked": False}
            return account_id

    def issue_pairing_code(self, account: str, code: str | None = None,
                           project_ids: list | None = None, key_ttl: int | None = None,
                           equipment_id: str | None = None) -> str:
        """Issue a single-use code that expires in an hour, as the account pages do.

        project_ids and key_ttl stand for the choices the user makes when issuing
        the code; the paired key inherits them. equipment_id names the rig the
        code was issued for, which pairing returns to the client.
        """
        with self.lock:
            account_id = self.accounts.setdefault(account, new_id())
            code = code or "acpc_" + secrets.token_urlsafe(24)
            self.pairing_codes[sha256(code.encode())] = {
                "account_id": account_id, "project_ids": sorted(project_ids) if project_ids else None,
                "key_ttl": key_ttl, "equipment_id": equipment_id,
                "expires_at": now() + timedelta(hours=1)}
            return code

    def create_project(self, requirements: dict, project_id: str | None = None,
                       state: str = "open") -> str:
        """Create a project and publish its first revision."""
        with self.lock:
            project_id = project_id or new_id()
            if project_id in self.projects:
                raise Problem(409, "id_conflict", "A project with this ID exists.")
            self.projects[project_id] = {"revisions": [], "state": state}
            self.publish(project_id, requirements)
            return project_id

    def publish(self, project_id: str, requirements: dict, state: str | None = None) -> int:
        """Publish new requirements as the project's next immutable revision."""
        with self.lock:
            self.validate("RequirementSet", requirements)
            self.check_requirements(requirements)
            project = self.projects[project_id]
            project["revisions"].append({"requirements": requirements, "published_at": ts(now())})
            if state:
                project["state"] = state
            return len(project["revisions"])

    def join(self, account: str, project_id: str, membership: str = "active") -> None:
        """Make an account a member of a project, consenting to its current terms."""
        with self.lock:
            account_id = self.accounts.setdefault(account, new_id())
            if project_id not in self.projects:
                raise Problem(404, "not_found", "No such project.")
            self.members[(account_id, project_id)] = {
                "state": membership, "terms": self.requirements(project_id)["terms"]}

    def assess_artifact(self, artifact_id: str, decision: str, reasons: list | None = None) -> dict:
        """Record a maintainer's decision on a validated artifact, replacing the
        automatic one. Acceptance credits it as automatic acceptance would."""
        with self.lock:
            artifact = self.artifacts[artifact_id]
            if artifact["state"] not in ("accepted", "rejected"):
                raise Problem(409, "artifact_not_validated", "Only validated artifacts can be assessed.")
            sub = self.submissions[artifact["_submission_id"]]
            manifest = artifact["_manifest"]
            if decision == "accepted":
                if self.held_elsewhere(sub["project_id"], manifest):
                    raise Problem(409, "duplicate_capture", "Another artifact holds credit for a capture.")
                self.uncredit(manifest["id"])
                self.record(sub, artifact, "accepted", [], self.credits_for(sub, manifest))
            else:
                self.record(sub, artifact, "rejected", reasons or ["maintainer_rejected"], [])
            return public(artifact)

    def record_retrieval(self, artifact_id: str, outcome: str, sha256_hex: str | None = None,
                         size_bytes: int | None = None) -> dict:
        """Report the result of fetching an externally shared file.

        outcome is verified, unavailable or digest_mismatch. verified needs the
        manifest's hash and size, then runs the normal assessment.
        """
        with self.lock:
            artifact = self.artifacts[artifact_id]
            sub = self.submissions[artifact["_submission_id"]]
            if artifact["state"] != "awaiting_retrieval" or not sub["_finalized"]:
                raise Problem(409, "invalid_transition", "The artifact is not finalized and awaiting retrieval.")
            manifest = artifact["_manifest"]
            if outcome == "verified":
                if (sha256_hex, size_bytes) != (manifest["sha256"], manifest["size_bytes"]):
                    raise Problem(422, "digest_mismatch", "verified requires the manifest's hash and size.")
                artifact["state"] = "validating"
                self.record(sub, artifact, *self.assess(sub, manifest, None))
            else:
                reason = "external_unavailable" if outcome == "unavailable" else "digest_mismatch"
                self.record(sub, artifact, "rejected", [reason], [])
            self.finish_if_done(sub)
            return public(artifact)

    def load_samples(self, path: Path = SAMPLE_PATH) -> list[str]:
        """Create the sample projects in reference/sample_project.json."""
        projects = json.loads(path.read_text(encoding="utf-8"))
        return [self.create_project(p["requirements"], p["id"]) for p in projects]

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

    # ---- Dispatch -------------------------------------------------------------

    def handle(self, method: str, target: str, headers: dict, raw: bytes):
        """Return (status, headers, body bytes) for one HTTP request."""
        request_id = new_id()
        headers = {k.lower(): v for k, v in headers.items()}
        op, extra = None, {}
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
                if name == "part_number":
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
            status, extra = error.status, {}
            body = {"type": "about:blank", "title": error.code.replace("_", " ").capitalize(),
                    "status": error.status, "code": error.code, "detail": error.detail,
                    "request_id": request_id}
            if error.errors:
                body["errors"] = error.errors[:100]
        if self.check_responses and op is not None:
            try:
                self.check_response(op, status, body)
            except AssertionError as error:
                status, body = 500, {"type": "about:blank", "title": "Contract violation",
                                     "status": 500, "code": "contract_violation",
                                     "detail": str(error)[:2000], "request_id": request_id}
        out = {"X-Request-Id": request_id, **extra}
        out["Content-Type"] = "application/problem+json" if status >= 400 else "application/json"
        return status, out, json.dumps(body, ensure_ascii=False).encode()

    def dispatch(self, op, params, query, headers, raw):
        cred = self.authenticate(headers)
        if cred is None and {} not in (op.get("security") or [{"bearerAuth": []}]):
            raise Problem(401, "authentication_required", "Send Authorization: Bearer <api key>.")
        body = None
        media = op.get("requestBody", {}).get("content", {})
        kind = next((k for k in ("application/json", "application/merge-patch+json") if k in media), None)
        if kind:
            if len(raw) > self.limits["max_json_bytes"]:
                raise Problem(413, "payload_too_large", "The JSON body is too large.")
            try:
                body = json.loads(raw or b"null")
            except ValueError:
                raise Problem(400, "invalid_json", "The body is not valid JSON.") from None
            self.validate(media[kind]["schema"]["$ref"].split("/")[-1], body)
        result = getattr(self, "op_" + op["operationId"])(Request(op, params, query, headers, body,
                                                                   raw, cred))
        status, body, extra = (result + ({},))[:3]
        return status, body, extra

    def validate(self, schema: str, value: object) -> None:
        errors = sorted(self.validator(schema).iter_errors(value), key=lambda e: list(e.path))
        if errors:
            raise Problem(422, "invalid_request", "The body does not match the schema.", [
                {"pointer": "/" + "/".join(str(p) for p in e.absolute_path), "code": e.validator}
                for e in errors])

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
        errors = list(self.validator(media["schema"]["$ref"].split("/")[-1]).iter_errors(body))
        if errors:
            raise AssertionError(f"{op['operationId']} {status} breaks the contract: "
                                 + "; ".join(f"{list(e.absolute_path)}: {e.message}" for e in errors))

    # ---- Keys and membership -------------------------------------------------

    def authenticate(self, headers) -> Credential | None:
        header = headers.get("authorization")
        if header is None:
            return None
        scheme, _, secret = header.partition(" ")
        if scheme.lower() != "bearer" or not secret.strip():
            raise Problem(401, "invalid_credentials", "Send Authorization: Bearer <api key>.")
        key = self.keys.get(sha256(secret.strip().encode()))
        if key is None or key["revoked"] or (key["expires_at"] and parse_ts(key["expires_at"]) <= now()):
            raise Problem(401, "invalid_credentials", "The API key is unknown, expired or revoked.")
        projects = frozenset(key["project_ids"]) if key["project_ids"] else None
        return Credential(key["account_id"], key["id"], projects)

    @staticmethod
    def covers(cred: Credential, project_id: str) -> bool:
        return cred.project_ids is None or project_id in cred.project_ids

    def member(self, req: Request, project_id: str) -> dict:
        """Require an active membership in a project the key covers."""
        membership = self.members.get((req.cred.account_id, project_id))
        if membership is None or not self.covers(req.cred, project_id):
            raise Problem(404, "not_found", "No such resource.")
        if membership["state"] != "active":
            raise Problem(403, "membership_inactive", "Your membership in this project is not active.")
        return membership

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

    # ---- Discovery, pairing and projects ----------------------------------------

    def op_getCapabilities(self, req):
        return 200, {
            "server_id": self.server_id, "api_root": self.base_url + "/v1",
            "protocol_versions": ["v1"], "spec_version": self.contract["info"]["version"],
            "features": [], "account_url": self.base_url + "/account",
            "artifact_formats": ["fits", "xisf"], "limits": self.limits,
        }

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
        key = {"id": new_id(), "account_id": grant["account_id"], "client_name": req.body["client_name"],
               "installation_id": installation, "project_ids": grant["project_ids"], "revoked": False,
               "expires_at": ts(now() + timedelta(seconds=grant["key_ttl"])) if grant["key_ttl"] else None}
        self.keys[sha256(secret.encode())] = key
        body = {"key_id": key["id"], "api_key": secret, "account_id": key["account_id"],
                "client_name": key["client_name"], "installation_id": installation}
        for field in ("project_ids", "expires_at"):
            if key[field]:
                body[field] = key[field]
        if grant["equipment_id"]:
            body["equipment_id"] = grant["equipment_id"]
        return 201, body, {"Cache-Control": "no-store"}

    def requirements(self, project_id: str, revision: int | None = None) -> dict:
        revisions = self.projects[project_id]["revisions"]
        if revision is None:
            return revisions[-1]["requirements"]
        if not 1 <= revision <= len(revisions):
            raise Problem(422, "invalid_revision", "No such project revision.")
        return revisions[revision - 1]["requirements"]

    def project_view(self, project_id: str) -> dict:
        project = self.projects[project_id]
        latest = project["revisions"][-1]
        return {"id": project_id, "title": latest["requirements"]["title"], "state": project["state"],
                "revision": len(project["revisions"]), "published_at": latest["published_at"],
                "requirements": latest["requirements"]}

    def op_listMyProjects(self, req):
        items = []
        for (account_id, project_id), membership in self.members.items():
            if account_id == req.cred.account_id and self.covers(req.cred, project_id):
                view = self.project_view(project_id)
                items.append({"project_id": project_id, "title": view["title"], "state": view["state"],
                              "membership": membership["state"]})
        return 200, self.page(req, items, "projects")

    def op_getProject(self, req):
        if req.params["project_id"] not in self.projects:
            raise Problem(404, "not_found", "No such project.")
        return 200, self.project_view(req.params["project_id"])

    # ---- Rigs ------------------------------------------------------------------

    def rig(self, account_id: str, equipment_id: str) -> dict:
        history = self.equipment.get((account_id, equipment_id))
        if not history:
            raise Problem(404, "not_found", "No such rig.")
        return history[-1]

    def save_rig(self, account_id: str, eid: str, config: dict) -> dict:
        if any(key[1] == eid and key[0] != account_id for key in self.equipment):
            raise Problem(409, "id_conflict", "Another account uses this equipment ID.")
        filters = [f["id"] for f in config.get("filters", [])]
        if len(set(filters)) != len(filters):
            raise Problem(422, "duplicate_id", "Filter IDs must be unique.")
        history = self.equipment.setdefault((account_id, eid), [])
        if history and history[-1]["configuration"] == config:
            return history[-1]  # No change keeps the revision.
        record = {"id": eid, "account_id": account_id, "revision": len(history) + 1,
                  "observed_at": ts(now()), "configuration": config}
        history.append(record)
        return record

    def op_registerEquipment(self, req):
        return 200, self.save_rig(req.cred.account_id, req.params["equipment_id"], req.body)

    def op_updateEquipment(self, req):
        """Apply a JSON merge patch (RFC 7396)."""
        current = self.rig(req.cred.account_id, req.params["equipment_id"])["configuration"]
        merged = merge_patch(current, req.body)
        self.validate("EquipmentInput", merged)
        return 200, self.save_rig(req.cred.account_id, req.params["equipment_id"], merged)

    def op_getEquipment(self, req):
        return 200, self.rig(req.cred.account_id, req.params["equipment_id"])

    def op_listEquipment(self, req):
        items = [history[-1] for (owner, _), history in self.equipment.items()
                 if owner == req.cred.account_id]
        return 200, self.page(req, items, "equipment")

    # ---- Check-in and assignment ---------------------------------------------------
    # reference/planning.py holds the framing rules. These methods gather the
    # inputs, keep the shared panel layouts and track depth per panel.

    def op_checkIn(self, req):
        """One rig asks for work. The server picks the project and the panel.

        Repeating a check-in changes nothing: unsubmitted_captures is a total
        that replaces the rig's last report, and unchanged work comes back as
        the same assignment.
        """
        body = req.body
        eid = body["equipment_id"]
        equipment = self.rig(req.cred.account_id, eid)
        if "unsubmitted_captures" in body:
            self.reported[eid] = [entry for entry in body["unsubmitted_captures"]
                                  if entry["panel_id"] in self.panels]

        if planning.missing(equipment["configuration"]):
            return 200, {"action": "wait", "next_checkin_seconds": 3600, "reason_codes": ["rig_incomplete"]}
        wanted = set(body.get("project_ids") or [])
        projects = [project_id for (account_id, project_id), m in self.members.items()
                    if account_id == req.cred.account_id and m["state"] == "active"
                    and self.covers(req.cred, project_id) and (not wanted or project_id in wanted)]
        if not projects:
            return 200, {"action": "wait", "next_checkin_seconds": 3600, "reason_codes": ["no_active_projects"]}

        reasons, best = [], None
        for project_id in projects:
            outcome = self.plan(project_id, eid, equipment)
            if isinstance(outcome, list):
                reasons += outcome
            elif best is None or outcome[0] > best[0]:
                best = outcome
        if best is None:
            return 200, {"action": "wait", "next_checkin_seconds": 3600, "reason_codes": sorted(set(reasons))}
        assignment = best[1]
        previous = self.assignments.get(self.latest.get(eid, ""))
        if previous and parse_ts(previous["expires_at"]) > now() and self.same_work(previous, assignment):
            if body.get("assignment_id") == previous["id"]:
                return 200, {"action": "continue", "next_checkin_seconds": 1800, "reason_codes": []}
            assignment = previous  # Same work as last time: the same assignment.
        self.assignments[assignment["id"]] = assignment
        self.latest[eid] = assignment["id"]
        return 200, {"action": "image", "next_checkin_seconds": 1800, "reason_codes": [],
                     "assignment": public(assignment)}

    @staticmethod
    def same_work(a: dict, b: dict) -> bool:
        def shape(assignment):
            return [(p["id"], p["filter_id"], p["equipment"], p["exposure_seconds"])
                    for p in assignment["panels"]]
        return (a["project_id"], a["project_revision"], shape(a)) == \
            (b["project_id"], b["project_revision"], shape(b))

    def depth(self, project_id: str, objective_id: str, panel_id: str | None) -> tuple:
        """Accepted (frames, seconds) for an objective on one panel (None: no panel)."""
        credits = [c for k, c in self.credits.items() if k[:2] == (project_id, objective_id)
                   and not c["surplus"] and c["panel_id"] == panel_id]
        return sum(c["frames"] for c in credits), sum(c["seconds"] for c in credits)

    def panel_deficit(self, project_id: str, objective: dict, panel_id: str | None) -> float:
        """Seconds of accepted integration a panel still needs. Each panel needs the full goal."""
        frames, seconds = self.depth(project_id, objective["id"], panel_id)
        goal = objective["goal"]
        return max(goal.get("accepted_integration_seconds", 0) - seconds,
                   (goal.get("accepted_frames", 0) - frames) * objective["exposure"]["min_seconds"], 0)

    def objective_complete(self, project_id: str, objective: dict) -> bool:
        """Complete when every panel of some layout over the target has the full goal.
        Before any layout exists, frames without a panel stand for the whole target."""
        layouts = self.layouts.get((project_id, objective["target_id"]), [])
        if not layouts:
            return self.panel_deficit(project_id, objective, None) <= 0
        return any(all(self.panel_deficit(project_id, objective, cell["id"]) <= 0
                       for cell in grid["panels"]) for grid in layouts)

    def deficits(self, project_id: str, requirements: dict) -> dict:
        """Per objective, the seconds its neediest panel still needs (0 when complete)."""
        out = {}
        for objective in requirements["objectives"]:
            layouts = self.layouts.get((project_id, objective["target_id"]), [])
            if self.objective_complete(project_id, objective):
                out[objective["id"]] = 0
            elif not layouts:
                out[objective["id"]] = self.panel_deficit(project_id, objective, None)
            else:
                out[objective["id"]] = max(self.panel_deficit(project_id, objective, cell["id"])
                                           for grid in layouts for cell in grid["panels"])
        return out

    def plan(self, project_id: str, eid: str, equipment: dict):
        """Plan one rig for one project. Returns (deficit, assignment) or reason codes."""
        if self.projects[project_id]["state"] != "open":
            return ["project_not_open"]
        requirements = self.requirements(project_id)
        if now() >= parse_ts(requirements["capture_deadline"]):
            return ["project_closed"]
        config = with_defaults(equipment["configuration"])
        deficits = self.deficits(project_id, requirements)
        choices, why = planning.candidates(config, requirements, deficits)
        if not choices:
            return why
        deficit, objective, chosen_filter = choices[0]
        return deficit, self.assignment(project_id, requirements, eid, equipment, objective,
                                        chosen_filter, deficits)

    def camera_angle(self, config: dict, target: dict) -> float:
        """Fixed cameras keep their confirmed angle; others turn to the target's."""
        if config["rotation"] == "fixed":
            return config["confirmed_position_angle_degrees"]
        return target["footprint"]["position_angle_degrees"]

    def layout_for(self, project_id: str, target: dict, fov, angle: float) -> dict:
        """The shared panel grid this rig works on.

        Among the target's layouts at this camera angle, pick the one with the
        largest panels that still fit the rig's field. If none fits, lay a new
        grid sized to this rig. A rig never gets a panel larger than its field.
        """
        layouts = self.layouts.setdefault((project_id, target["id"]), [])
        fitting = [g for g in layouts if planning.angle_difference(g["angle"], angle) <= 1
                   and g["fov"][0] <= fov[0] * 1.001 and g["fov"][1] <= fov[1] * 1.001]
        if fitting:
            return max(fitting, key=lambda g: g["fov"][0] * g["fov"][1])
        # Cover the target as the camera sees it: its bounding box at this angle.
        footprint = target["footprint"]
        turn = math.radians(footprint["position_angle_degrees"] - angle)
        width, height = footprint["width_degrees"], footprint["height_degrees"]
        area = {"center": footprint["center"], "position_angle_degrees": angle,
                "width_degrees": width * abs(math.cos(turn)) + height * abs(math.sin(turn)),
                "height_degrees": width * abs(math.sin(turn)) + height * abs(math.cos(turn))}
        columns, rows = planning.layout(area, fov)
        centers = planning.panel_centers(area, fov, columns, rows)
        grid = {"columns": columns, "rows": rows, "fov": fov, "angle": angle, "panels": [
            {"id": new_id(), "center": center, "column": i % columns + 1, "row": i // columns + 1}
            for i, center in enumerate(centers)]}
        for cell in grid["panels"]:
            self.panels[cell["id"]] = {"project_id": project_id, "target_id": target["id"], "footprint": {
                "center": cell["center"], "width_degrees": fov[0], "height_degrees": fov[1],
                "position_angle_degrees": angle}}
        layouts.append(grid)
        return grid

    def reported_frames(self, panel_id: str, exclude: str | None = None) -> int:
        """Frames rigs (other than exclude) report captured for a panel but not yet
        submitted. Reports stop counting after the project's submission deadline."""
        project_id = self.panels[panel_id]["project_id"]
        if now() >= parse_ts(self.requirements(project_id)["submission_deadline"]):
            return 0
        return sum(entry["frames"] for rig_id, report in self.reported.items() if rig_id != exclude
                   for entry in report if entry["panel_id"] == panel_id)

    def settle_reports(self, manifest: dict) -> None:
        """Submitted frames are no longer unsubmitted: take them off the rig's report."""
        for artifact in manifest["artifacts"]:
            report = self.reported.get(artifact["equipment"]["equipment_id"], [])
            for entry in report:
                if entry["panel_id"] == artifact.get("panel_id"):
                    entry["frames"] = max(0, entry["frames"] - frames_in(artifact))
            self.reported[artifact["equipment"]["equipment_id"]] = [e for e in report if e["frames"] > 0]

    def planned_by_others(self, panel_id: str, eid: str) -> int:
        """Frames other rigs' live assignments suggest for a panel."""
        stamp, planned = now(), 0
        for rig_id, assignment_id in self.latest.items():
            assignment = self.assignments[assignment_id]
            if rig_id != eid and parse_ts(assignment["expires_at"]) > stamp:
                planned += sum(p["suggested_frames"] for p in assignment["panels"] if p["id"] == panel_id)
        return planned

    def assignment(self, project_id, requirements, eid, equipment, objective, chosen_filter, deficits):
        config = with_defaults(equipment["configuration"])
        target = next(t for t in requirements["targets"] if t["id"] == objective["target_id"])
        footprint = target["footprint"]
        fov = planning.field_of_view(config)
        angle = self.camera_angle(config, target)
        grid = self.layout_for(project_id, target, fov, angle)
        count = grid["columns"] * grid["rows"]
        notes = []
        if count == 1 and footprint["width_degrees"] < planning.SMALL_TARGET * fov[0]:
            notes.append(f"The target is small in this field "
                         f"({footprint['width_degrees'] * 60:.2g}′ in {fov[0]:.2g}°).")
        elif count == 1:
            notes.append("The whole target fits in one field.")
        else:
            notes.append(f"Part of a shared {grid['columns']}×{grid['rows']} mosaic; "
                         f"each panel needs the full goal.")

        exposure = objective["exposure"]["min_seconds"]

        # Each panel needs the full goal. Rank panels by the frames they still
        # need after other rigs' live assignments; the neediest comes first.
        def need(cell):
            seconds = self.panel_deficit(project_id, objective, cell["id"])
            return math.ceil(seconds / (exposure * planning.PASS_RATE)) - \
                self.planned_by_others(cell["id"], eid) - self.reported_frames(cell["id"], eid)

        ranked = sorted(((need(cell), cell) for cell in grid["panels"]), key=lambda nc: -nc[0])

        # One panel; add the next only while the work still fits in one night.
        night = int(planning.NIGHT_SECONDS // (exposure * planning.OVERHEAD))
        chosen, total = [], 0
        for frames, cell in ranked:
            if frames < 1 or len(chosen) >= planning.MAX_PLAN_PANELS or (chosen and total >= night):
                break
            chosen.append((cell, frames))
            total += frames
        if not chosen:  # Other rigs already cover every panel; share the neediest.
            chosen, total = [(ranked[0][1], night)], night
        if len(chosen) > 1:
            notes.append("Image the panels in order; move on when one is done.")
        served = [oid for oid in objectives_served(requirements, objective, chosen_filter["bandpasses"],
                                                    exposure) if deficits[oid] > 0]
        if len(served) > 1:
            notes.append("These frames serve several objectives through this filter's passbands.")
        for cell, _ in chosen:
            self.panel_objectives[cell["id"]] = served
        stamp = now()
        return {
            "id": new_id(), "project_id": project_id,
            "project_revision": len(self.projects[project_id]["revisions"]),
            "created_at": ts(stamp), "expires_at": ts(stamp + timedelta(days=1)),
            "panels": [{
                "id": cell["id"], "target_id": target["id"], "target_name": target["name"],
                "objective_ids": served,
                "footprint": {"center": cell["center"], "width_degrees": round(grid["fov"][0], 4),
                              "height_degrees": round(grid["fov"][1], 4), "position_angle_degrees": angle},
                "layout": {"columns": grid["columns"], "rows": grid["rows"],
                           "column": cell["column"], "row": cell["row"]},
                "overlap_fraction": planning.OVERLAP if count > 1 else 0,
                "equipment": {"equipment_id": eid, "revision": equipment["revision"]},
                "filter_id": chosen_filter["id"], "exposure_seconds": exposure,
                "suggested_frames": frames,
            } for cell, frames in chosen],
            "estimated_rig_seconds": total * exposure * planning.OVERHEAD,
            "estimated_accepted_integration_seconds": total * exposure * planning.PASS_RATE,
            "notes": notes, "_equipment_id": eid,
        }

    # ---- Progress ----------------------------------------------------------------

    def op_getProgress(self, req):
        project_id = req.params["project_id"]
        if project_id not in self.projects:
            raise Problem(404, "not_found", "No such project.")
        requirements = self.requirements(project_id)
        stamp = now()
        live = [self.assignments[a] for a in self.latest.values()]
        live = [a for a in live if a["project_id"] == project_id and parse_ts(a["expires_at"]) > stamp]
        objectives = []
        for objective in requirements["objectives"]:
            oid = objective["id"]
            credits = [c for k, c in self.credits.items() if k[:2] == (project_id, oid)]
            accepted = [c for c in credits if not c["surplus"]]
            artifacts = [a for a in self.artifacts.values()
                         if a["_project_id"] == project_id and oid in a["_manifest"]["objective_ids"]]
            objectives.append({
                "objective_id": oid, "goal": objective["goal"],
                "assigned_frames": sum(p["suggested_frames"] for a in live for p in a["panels"]
                                       if oid in p["objective_ids"]),
                "reported_frames": sum(self.reported_frames(panel_id)
                                       for panel_id, served in self.panel_objectives.items()
                                       if oid in served and self.panels[panel_id]["project_id"] == project_id),
                "pending_frames": sum(frames_in(a["_manifest"]) for a in artifacts if a["state"] in
                                      ("uploading", "awaiting_retrieval", "received", "validating")),
                "accepted_frames": sum(c["frames"] for c in accepted),
                "accepted_integration_seconds": sum(c["seconds"] for c in accepted),
                "rejected_frames": sum(frames_in(a["_manifest"]) for a in artifacts
                                       if a["state"] == "rejected"),
                "surplus_frames": sum(frames_in(self.artifacts[c["artifact_id"]]["_manifest"])
                                      for c in credits if c["surplus"]),
                "complete": self.objective_complete(project_id, objective),
            })
        captures = {}  # Each credited capture counts once, however many objectives it serves.
        for (pid, _, _), credit in self.credits.items():
            if pid == project_id and not credit["surplus"]:
                for capture in credit["captures"]:
                    captures[capture] = credit["seconds_each"]
        return 200, {"project_id": project_id, "revision": len(self.projects[project_id]["revisions"]),
                     "as_of": ts(stamp), "objectives": objectives, "accepted_frames": len(captures),
                     "accepted_integration_seconds": sum(captures.values())}

    # ---- Submissions and uploads ----------------------------------------------------

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

    def check_artifact(self, reqs: dict, artifact: dict, account_id: str) -> None:
        """Checks for one manifest entry that JSON Schema cannot express."""
        kind = artifact.get("kind", "calibrated_sub")
        wanted = "stacked_master" if reqs["deliverable"] == "stacked_masters" else "calibrated_sub"
        if kind != wanted:
            raise Problem(422, "deliverable_mismatch", f"This project accepts {reqs['deliverable']}.")
        objectives = {o["id"] for o in reqs["objectives"]}
        groups = {g["id"] for g in reqs["processing_groups"]}
        if not set(artifact["objective_ids"]) <= objectives \
                or artifact["processing_group_id"] not in groups:
            raise Problem(422, "invalid_reference", "An artifact names an unknown objective or group.")
        ref = artifact["equipment"]
        if not 1 <= ref["revision"] <= len(self.equipment.get((account_id, ref["equipment_id"]), [])):
            raise Problem(422, "invalid_reference", "The equipment reference does not exist.")
        if kind == "stacked_master":
            self.check_stack(reqs, artifact)
        last_capture = artifact["stack"]["last_captured_at"] if kind == "stacked_master" \
            else artifact["captured_at"]
        if parse_ts(last_capture) >= parse_ts(reqs["capture_deadline"]):
            raise Problem(422, "capture_deadline_passed", "A capture started after the deadline.")
        if artifact["size_bytes"] > self.limits["max_artifact_bytes"]:
            raise Problem(413, "artifact_too_large", "An artifact exceeds max_artifact_bytes.")
        if artifact.get("delivery") == "external":
            accepted = reqs.get("external_delivery", {}).get("providers", [])
            if artifact["external"]["provider"] not in accepted:
                raise Problem(422, "external_delivery_not_accepted",
                              "This project revision does not accept files from that provider.")
        if "supersedes_artifact_id" in artifact:
            old = self.artifacts.get(artifact["supersedes_artifact_id"])
            if old is None or old["_account_id"] != account_id \
                    or captures_of(old["_manifest"]) != captures_of(artifact):
                raise Problem(422, "invalid_supersede", "A replacement must keep the capture identities.")

    @staticmethod
    def check_stack(reqs: dict, artifact: dict) -> None:
        stack, rules = artifact["stack"], reqs["master_rules"]
        subs = stack["subs"]
        if stack["sub_count"] != len(subs):
            raise Problem(422, "invalid_stack", "sub_count must equal the number of subs listed.")
        if len({(s["origin_id"], s["capture_id"]) for s in subs}) != len(subs):
            raise Problem(422, "invalid_stack", "A sub appears twice in the stack.")
        if stack["sub_count"] < rules["min_sub_count"]:
            raise Problem(422, "too_few_subs", f"A master needs at least {rules['min_sub_count']} subs.")
        if abs(stack["integration_seconds"] - stack["sub_count"] * artifact["exposure_seconds"]) > 1:
            raise Problem(422, "invalid_stack", "integration_seconds must equal sub_count × exposure_seconds.")
        times = [parse_ts(s["captured_at"]) for s in subs]
        if (min(times), max(times)) != (parse_ts(stack["first_captured_at"]),
                                        parse_ts(stack["last_captured_at"])):
            raise Problem(422, "invalid_stack", "first/last_captured_at must match the subs.")
        if "drizzle_scale" in stack and not rules["allow_drizzle"]:
            raise Problem(422, "drizzle_not_allowed", "This project does not accept drizzled masters.")

    def op_createSubmission(self, req):
        project_id = req.params["project_id"]
        membership = self.member(req, project_id)
        manifest = req.body
        existing = self.submissions.get(manifest["id"])
        if existing:  # A retry: same ID and body returns the submission.
            if existing["manifest"] == manifest and existing["_account_id"] == req.cred.account_id:
                return 200, self.submission_view(existing)
            raise Problem(409, "id_conflict", "This submission ID has different content.")
        reqs = self.requirements(project_id, manifest["project_revision"])
        if membership["terms"] != self.requirements(project_id)["terms"]:
            raise Problem(409, "terms_consent_required", "Accept the project's current terms first.")
        if now() >= parse_ts(reqs["submission_deadline"]):
            raise Problem(409, "submission_deadline_passed", "The submission deadline has passed.")
        if len(manifest["artifacts"]) > self.limits["max_artifacts_per_submission"]:
            raise Problem(413, "too_many_artifacts", "Too many artifacts in one submission.")
        ids = [a["id"] for a in manifest["artifacts"]]
        if len(set(ids)) != len(ids) or any(i in self.artifacts for i in ids):
            raise Problem(409, "id_conflict", "Artifact IDs must be new and unique.")
        for artifact in manifest["artifacts"]:
            self.check_artifact(reqs, artifact, req.cred.account_id)
        stamp = now()
        sub = {"id": manifest["id"], "project_id": project_id,
               "project_revision": manifest["project_revision"], "state": "uploading",
               "created_at": ts(stamp), "manifest": manifest, "_upload_ids": [],
               "_finalized": False, "_account_id": req.cred.account_id}
        for artifact in manifest["artifacts"]:
            self.artifacts[artifact["id"]] = {
                "artifact_id": artifact["id"], "state": "uploading", "reason_codes": [],
                "_manifest": artifact, "_submission_id": sub["id"], "_project_id": project_id,
                "_account_id": req.cred.account_id}
            if artifact.get("delivery") == "external":
                # A shared file: no upload session. The project fetches it later.
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
        self.settle_reports(manifest)
        return 201, self.submission_view(sub)

    def own_submission(self, req, submission_id: str) -> dict:
        sub = self.submissions.get(submission_id)
        if sub is None or sub["_account_id"] != req.cred.account_id \
                or not self.covers(req.cred, sub["project_id"]):
            raise Problem(404, "not_found", "No such submission.")
        return sub

    def op_getSubmission(self, req):
        return 200, self.submission_view(self.own_submission(req, req.params["submission_id"]))

    def own_upload(self, req) -> dict:
        upload = self.uploads.get(req.params["upload_id"])
        if upload is None:
            raise Problem(404, "not_found", "No such upload.")
        self.own_submission(req, upload["submission_id"])
        return upload

    def op_getUpload(self, req):
        return 200, self.upload_view(self.own_upload(req))

    def op_putUploadPart(self, req):
        upload = self.own_upload(req)
        self.member(req, self.submissions[upload["submission_id"]]["project_id"])
        number = req.params["part_number"]
        if upload["_finalized"]:
            raise Problem(409, "upload_finalized", "A finalized upload cannot change.")
        if parse_ts(upload["expires_at"]) <= now():
            raise Problem(409, "upload_expired", "The upload session expired; submit again.")
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
        # Each accepted part keeps the session alive for another staging period.
        upload["expires_at"] = ts(now() + timedelta(seconds=self.limits["upload_staging_seconds"]))
        return 200, receipt

    def op_finalizeSubmission(self, req):
        sub = self.own_submission(req, req.params["submission_id"])
        if sub["_finalized"]:
            return 202, self.submission_view(sub)  # A retry changes nothing.
        membership = self.member(req, sub["project_id"])
        uploads = [self.uploads[u] for u in sub["_upload_ids"]]
        if any(len(u["_parts"]) != u["part_count"] for u in uploads):
            raise Problem(409, "upload_incomplete", "Some parts are missing; read the upload sessions.")
        if membership["terms"] != self.requirements(sub["project_id"])["terms"]:
            raise Problem(409, "terms_consent_required", "Accept the project's current terms first.")
        reqs = self.requirements(sub["project_id"], sub["project_revision"])
        if now() >= parse_ts(reqs["submission_deadline"]):
            raise Problem(409, "submission_deadline_passed", "The submission deadline has passed.")
        sub.update(state="processing", _finalized=True)
        for upload in uploads:
            upload["_finalized"] = True
            self.artifacts[upload["artifact_id"]]["state"] = "received"
        threading.Thread(target=self.run_assessment, args=(sub["id"],), daemon=True).start()
        return 202, self.submission_view(sub)

    # ---- Assessment and credit ---------------------------------------------------------

    def run_assessment(self, submission_id: str) -> None:
        """Background worker: verify each uploaded artifact and assess it."""
        time.sleep(0.1)
        with self.lock:
            sub = self.submissions[submission_id]
            for upload_id in sub["_upload_ids"]:
                upload = self.uploads[upload_id]
                artifact = self.artifacts[upload["artifact_id"]]
                artifact["state"] = "validating"
                data = b"".join(upload["_parts"][n]["data"] for n in sorted(upload["_parts"]))
                for stored in upload["_parts"].values():
                    stored["data"] = b""  # Drop the bytes; this server keeps no files.
                self.record(sub, artifact, *self.assess(sub, artifact["_manifest"], data))
            self.finish_if_done(sub)

    def finish_if_done(self, sub: dict) -> None:
        """Complete the submission once no artifact is waiting."""
        states = {self.artifacts[a["id"]]["state"] for a in sub["manifest"]["artifacts"]}
        if not states & {"uploading", "awaiting_retrieval", "received", "validating"}:
            sub["state"] = "complete"

    def panel_of(self, project_id: str, manifest: dict) -> str | None:
        """The frame's panel, if it names one this project laid out."""
        panel = self.panels.get(manifest.get("panel_id", ""))
        return manifest["panel_id"] if panel and panel["project_id"] == project_id else None

    def held_elsewhere(self, project_id: str, manifest: dict) -> bool:
        """True if another artifact already holds credit for one of these captures."""
        replaced = manifest.get("supersedes_artifact_id")
        return any(self.claimed.get((project_id, *capture)) not in (None, manifest["id"], replaced)
                   for capture in captures_of(manifest))

    @staticmethod
    def credited_objectives(reqs: dict, manifest: dict) -> list:
        """Every objective a frame serves: those listed, and others on the same
        target and group that its filter's passbands also cover."""
        objectives = {o["id"]: o for o in reqs["objectives"]}
        served = []
        for oid in manifest["objective_ids"]:
            for other in objectives_served(reqs, objectives[oid], manifest["bandpasses"],
                                           manifest["exposure_seconds"]):
                if other not in served:
                    served.append(other)
        return served

    def assess(self, sub: dict, manifest: dict, data: bytes | None):
        """Automatic checks. Returns (decision, reasons, credits).

        This server does not decode FITS or XISF or measure quality. It checks
        the file hash, passbands, exposure range, solve evidence, coverage of
        the named panel and the submitted measurements, then applies goal and
        duplicate rules. data is None when the project already verified an
        external file's hash.
        """
        reqs = self.requirements(sub["project_id"], sub["project_revision"])
        objectives = {o["id"]: o for o in reqs["objectives"]}
        reasons = []
        if data is not None and sha256(data) != manifest["sha256"]:
            reasons.append("digest_mismatch")
        measured = {m["metric"]: m["value"] for m in manifest["measurements"]}
        for oid in manifest["objective_ids"]:
            objective = objectives[oid]
            if not planning.serves(manifest["bandpasses"], objective["bandpasses"]):
                reasons.append("bandpass_mismatch")
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
        panel_id = self.panel_of(sub["project_id"], manifest)
        if panel_id and manifest.get("solve"):
            covered = planning.coverage(manifest["solve"]["center"], self.panels[panel_id]["footprint"])
            if any(covered < objectives[oid]["minimum_coverage_fraction"] for oid in manifest["objective_ids"]):
                reasons.append("coverage_too_low")
        if reasons:
            return "rejected", sorted(set(reasons)), []
        if self.held_elsewhere(sub["project_id"], manifest):
            return "rejected", ["duplicate_capture"], []
        return "accepted", [], self.credits_for(sub, manifest)

    def credits_for(self, sub: dict, manifest: dict) -> list:
        """Credit per objective. Surplus once the frame's panel already has the full goal."""
        reqs = self.requirements(sub["project_id"], sub["project_revision"])
        objectives = {o["id"]: o for o in reqs["objectives"]}
        panel_id = self.panel_of(sub["project_id"], manifest)
        replaced = manifest.get("supersedes_artifact_id")
        credits = []
        for oid in self.credited_objectives(reqs, manifest):
            held = self.credits.get((sub["project_id"], oid, replaced)) if replaced else None
            surplus = held["surplus"] if held else \
                self.panel_deficit(sub["project_id"], objectives[oid], panel_id) <= 0
            credits.append({"objective_id": oid, "surplus": surplus,
                            "frames": 0 if surplus else frames_in(manifest),
                            "seconds": 0 if surplus else integration_of(manifest)})
        return credits

    def uncredit(self, artifact_id: str) -> None:
        for key in [k for k, c in self.credits.items() if c["artifact_id"] == artifact_id]:
            del self.credits[key]
        for key in [k for k, a in self.claimed.items() if a == artifact_id]:
            del self.claimed[key]

    def record(self, sub, artifact, decision, reasons, credits) -> None:
        """Apply an assessment and update credit in one step."""
        manifest = artifact["_manifest"]
        project_id = sub["project_id"]
        self.uncredit(manifest["id"])
        artifact.update(state=decision, reason_codes=reasons)
        artifact.pop("credited_frames", None)
        artifact.pop("credited_integration_seconds", None)
        if decision != "accepted":
            return
        replaced = manifest.get("supersedes_artifact_id")
        if replaced:
            self.uncredit(replaced)
            self.artifacts[replaced]["state"] = "superseded"
        for capture in captures_of(manifest):
            self.claimed[(project_id, *capture)] = manifest["id"]
        for credit in credits:
            self.credits[(project_id, credit["objective_id"], manifest["id"])] = {
                "artifact_id": manifest["id"], "surplus": credit["surplus"], "frames": credit["frames"],
                "seconds": credit["seconds"], "seconds_each": manifest["exposure_seconds"],
                "captures": captures_of(manifest), "panel_id": self.panel_of(project_id, manifest)}
        if any(not c["surplus"] for c in credits):
            artifact.update(credited_frames=frames_in(manifest),
                            credited_integration_seconds=integration_of(manifest))


# ---- HTTP ---------------------------------------------------------------------------


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
          verbose: bool = False, sample: bool = True) -> ThreadingHTTPServer:
    """Create a server bound to host:port. Call serve_forever() to run it.

    keys maps account names to API key secrets. With sample, the server loads
    the sample projects and makes every account in keys an active member.
    The returned server has api (the server tools), issue_pairing_code and
    sample_project_ids.
    """
    httpd = ThreadingHTTPServer((host, port), Handler)
    bound = httpd.server_address[1]
    httpd.api = Api(f"http://{host}:{bound}", keys or {}, part_size, check_responses)
    httpd.issue_pairing_code = httpd.api.issue_pairing_code
    httpd.sample_project_ids = httpd.api.load_samples() if sample else []
    for name in keys or {}:
        for project_id in httpd.sample_project_ids:
            httpd.api.join(name, project_id)
    httpd.verbose = verbose
    return httpd


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--key", action="append", default=[], metavar="ACCOUNT=SECRET",
                        help="Create an account with an API key. Repeatable.")
    parser.add_argument("--pairing-code", action="append", default=[], metavar="ACCOUNT=CODE",
                        help="Issue a pairing code (valid one hour). Repeatable.")
    parser.add_argument("--join", action="append", default=[], metavar="ACCOUNT=PROJECT",
                        help="Make an account an active member of a project. Repeatable.")
    parser.add_argument("--part-size", type=int, default=1 << 20, help="Upload part size in bytes.")
    parser.add_argument("--check-responses", action="store_true",
                        help="Validate every response against the contract (for testing).")
    parser.add_argument("--verbose", action="store_true", help="Log each request.")
    parser.add_argument("--no-sample", action="store_true", help="Do not load the sample projects.")
    args = parser.parse_args()
    keys = dict(item.split("=", 1) for item in args.key)
    codes = dict(item.split("=", 1) for item in args.pairing_code)
    if not keys and not codes:
        keys = {name: secrets.token_urlsafe(24) for name in ("alice", "bob")}
        codes = {name: None for name in keys}
    httpd = serve(args.host, args.port, keys, args.part_size, args.check_responses, args.verbose,
                  sample=not args.no_sample)
    api = httpd.api
    for name in codes:  # Pairing-code accounts join the samples too.
        for project_id in httpd.sample_project_ids:
            api.join(name, project_id)
    for item in args.join:
        name, project_id = item.split("=", 1)
        api.join(name, project_id)
    print(f"AstroCollab reference server at {api.base_url}/v1", flush=True)
    for project_id in httpd.sample_project_ids:
        print(f"  Sample project {api.project_view(project_id)['title']}: {project_id}", flush=True)
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
