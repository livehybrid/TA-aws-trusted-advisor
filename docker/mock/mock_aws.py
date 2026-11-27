"""
Mock AWS endpoint for the add-on integration tests (stdlib only).

Speaks the two wire protocols botocore uses for the APIs the collectors call:

  * AWS JSON 1.1   POST /  with  X-Amz-Target: <prefix>.<Operation>
  * Smithy RPC v2 CBOR   POST /service/<prefix>/operation/<Operation>
    (newer botocore prefers CBOR for AWS Health; older ones send JSON)

Services and operations implemented (anything else answers 400
UnknownOperationException and is recorded, so a collector that starts calling
something new fails loudly rather than silently):

  AWS Health (AWSHealth_20160804)
    DescribeEventsForOrganization            paged (nextToken), honours
                                             filter.lastUpdatedTime from/to
    DescribeAffectedAccountsForOrganization  paged
    DescribeAffectedEntitiesForOrganization  paged
    DescribeEventDetailsForOrganization
    DescribeEventDetails
  AWS Support / Trusted Advisor (AWSSupport_20130415)
    DescribeTrustedAdvisorChecks
    DescribeTrustedAdvisorCheckResult
  AWS STS (query protocol: form-encoded POST /, XML response)
    AssumeRole                               issues fresh temporary credentials
                                             per call; a RoleArn whose role name
                                             starts with "mock-denied" gets 403
                                             AccessDenied

Temporary credentials are enforced: a Health or Support call signed with an
ASIA... key id must carry the session token issued with that key, or it gets
403 UnrecognizedClientException. So "the role's credentials were used" is
something the mock checks, not something the collector reports.

SigV4 is not verified (botocore signs, nobody checks), but the access key id
and signing service from the Authorization header, and any
X-Amz-Security-Token, are recorded, so a test can prove which credentials
reached the wire. AssumeRole entries also record the credentials issued.

Every recorded call also carries `client` (the address it came from: the
proxy's container when a proxy relayed it, Splunk's when it came direct) and
`tls` (whether it arrived on the HTTPS port).

HTTPS: when /mock/tls/cert.pem and key.pem exist (CI writes a throwaway
self-signed pair for the name "mockaws"), the same handler also listens with
TLS on MOCK_AWS_TLS_PORT (8443), as real AWS endpoints are https only.

Test-only control endpoints (plain GET/POST, JSON):
  GET  /_mock/health     liveness
  GET  /_mock/dataset    the data this mock serves (what the tests expect)
  GET  /_mock/requests   every AWS call received, in order
  POST /_mock/reset      clear the request log

The dataset is built once at start-up, relative to the start time, so the
Health events always fall inside a collector's "last day" window.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import ssl
import struct
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HEALTH = "AWSHealth_20160804"
SUPPORT = "AWSSupport_20130415"
STS = "sts"
DENIED_ROLE_PREFIX = "mock-denied"
EVENTS_PAGE = 2      # events per DescribeEventsForOrganization page
ACCOUNTS_PAGE = 1    # accounts per DescribeAffectedAccountsForOrganization page
ENTITIES_PAGE = 2    # entities per DescribeAffectedEntitiesForOrganization page


class Ts(float):
    """An epoch-seconds timestamp (CBOR tag 1 / JSON number on the wire)."""


# --------------------------------------------------------------------- CBOR
def _head(major, n):
    if n < 24:
        return bytes([(major << 5) | n])
    for ai, fmt in ((24, ">B"), (25, ">H"), (26, ">I"), (27, ">Q")):
        if n < (1 << (8 * struct.calcsize(fmt))):
            return bytes([(major << 5) | ai]) + struct.pack(fmt, n)
    raise ValueError("integer too large for CBOR")


def cbor_dumps(v):
    if v is None:
        return b"\xf6"
    if v is True:
        return b"\xf5"
    if v is False:
        return b"\xf4"
    if isinstance(v, Ts):
        return b"\xc1" + b"\xfb" + struct.pack(">d", float(v))
    if isinstance(v, int):
        return _head(0, v) if v >= 0 else _head(1, -1 - v)
    if isinstance(v, float):
        return b"\xfb" + struct.pack(">d", v)
    if isinstance(v, str):
        b = v.encode()
        return _head(3, len(b)) + b
    if isinstance(v, (bytes, bytearray)):
        return _head(2, len(v)) + bytes(v)
    if isinstance(v, (list, tuple)):
        return _head(4, len(v)) + b"".join(cbor_dumps(x) for x in v)
    if isinstance(v, dict):
        return _head(5, len(v)) + b"".join(cbor_dumps(k) + cbor_dumps(x) for k, x in v.items())
    raise TypeError(f"cannot CBOR-encode {type(v)}")


class _Break:
    pass


def cbor_loads(data):
    pos = 0

    def arg(ai):
        nonlocal pos
        if ai < 24:
            return ai
        size = {24: 1, 25: 2, 26: 4, 27: 8}[ai]
        val = int.from_bytes(data[pos:pos + size], "big")
        pos += size
        return val

    def item():
        nonlocal pos
        ib = data[pos]
        pos += 1
        major, ai = ib >> 5, ib & 31
        if ib == 0xFF:
            return _Break
        if major == 0:
            return arg(ai)
        if major == 1:
            return -1 - arg(ai)
        if major in (2, 3):
            if ai == 31:
                parts = []
                while (p := item()) is not _Break:
                    parts.append(p)
                return (b"" if major == 2 else "").join(parts)
            n = arg(ai)
            raw = data[pos:pos + n]
            pos += n
            return raw if major == 2 else raw.decode()
        if major == 4:
            if ai == 31:
                out = []
                while (x := item()) is not _Break:
                    out.append(x)
                return out
            return [item() for _ in range(arg(ai))]
        if major == 5:
            out = {}
            if ai == 31:
                while (k := item()) is not _Break:
                    out[k] = item()
                return out
            for _ in range(arg(ai)):
                k = item()
                out[k] = item()
            return out
        if major == 6:
            tag = arg(ai)
            inner = item()
            return Ts(inner) if tag == 1 else inner
        # major 7
        if ai == 20:
            return False
        if ai == 21:
            return True
        if ai in (22, 23):
            return None
        if ai == 25:
            v = struct.unpack(">e", data[pos:pos + 2])[0]
            pos += 2
            return v
        if ai == 26:
            v = struct.unpack(">f", data[pos:pos + 4])[0]
            pos += 4
            return v
        if ai == 27:
            v = struct.unpack(">d", data[pos:pos + 8])[0]
            pos += 8
            return v
        raise ValueError(f"unsupported CBOR simple value {ai}")

    if not data:
        return {}
    return item()


# ------------------------------------------------------------------ dataset
def build_dataset(now=None):
    now = now or _dt.datetime.now(_dt.timezone.utc)

    def ago(hours):
        # A non-zero fraction on purpose: the collector's checkpoint keeps
        # whole seconds, and the add-on's TIME_FORMAT expects %6Q.
        return (now - _dt.timedelta(hours=hours)).replace(microsecond=735000).timestamp()

    def ev(n, hours, service, code, scope="ACCOUNT_SPECIFIC"):
        return {
            "arn": f"arn:aws:health:eu-west-2::event/{service}/{code}/mock-{n:02d}",
            "service": service,
            "eventTypeCode": code,
            "eventTypeCategory": "issue" if scope != "PUBLIC" else "accountNotification",
            "region": "eu-west-2",
            "startTime": ago(hours + 1),
            "lastUpdatedTime": ago(hours),
            "statusCode": "open",
            "eventScopeCode": scope,
        }

    events = [  # newest first, as AWS returns them
        ev(1, 1, "EC2", "AWS_EC2_INSTANCE_RETIREMENT_SCHEDULED"),
        ev(2, 2, "RDS", "AWS_RDS_MAINTENANCE_SCHEDULED"),
        ev(3, 3, "LAMBDA", "AWS_LAMBDA_OPERATIONAL_ISSUE"),
        ev(4, 4, "S3", "AWS_S3_OPERATIONAL_NOTIFICATION"),
        ev(5, 5, "CLOUDFRONT", "AWS_CLOUDFRONT_PUBLIC_NOTICE", scope="PUBLIC"),
        # Near the far edge of a one-day window. A window computed from local
        # wall time east of UTC (Asia/Kolkata, +05:30) starts too late and
        # misses it.
        ev(6, 22, "DYNAMODB", "AWS_DYNAMODB_MAINTENANCE_SCHEDULED"),
        # Outside a one-day window: must never be collected on first run.
        ev(9, 72, "EBS", "AWS_EBS_OLD_EVENT"),
    ]
    accounts = {
        events[0]["arn"]: ["111111111111", "222222222222"],   # two pages
        events[1]["arn"]: ["111111111111"],
        events[2]["arn"]: ["333333333333"],
        events[3]["arn"]: ["111111111111"],
        events[4]["arn"]: [],                                 # public: no accounts
        events[5]["arn"]: ["444444444444"],
        events[6]["arn"]: ["111111111111"],
    }
    entities = {
        events[0]["arn"]: ["i-0aaa0000000000001", "i-0aaa0000000000002", "i-0aaa0000000000003"],  # two pages
        events[1]["arn"]: ["db-mock-primary"],
        events[2]["arn"]: ["arn:aws:lambda:eu-west-2:333333333333:function:mock"],
        events[3]["arn"]: ["mock-bucket"],
        events[4]["arn"]: [],
        events[5]["arn"]: ["mock-table"],
        events[6]["arn"]: ["vol-0old"],
    }
    descriptions = {e["arn"]: f"Mock description for {e['eventTypeCode']}." for e in events}

    ta_ts = (now - _dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    checks = [
        {
            "id": "mockChk001",
            "name": "Amazon EBS Snapshots",
            "description": "Mock check one.",
            "category": "fault_tolerance",
            "metadata": ["Region", "Volume ID", "Volume Name", "Status"],
        },
        {
            "id": "mockChk002",
            "name": "Low Utilization Amazon EC2 Instances",
            "description": "Mock check two.",
            "category": "cost_optimizing",
            "metadata": ["Region", "Instance ID", "Estimated Monthly Savings", "Status"],
        },
        {
            "id": "mockChk003",
            "name": "MFA on Root Account",
            "description": "Mock check three, nothing flagged.",
            "category": "security",
            "metadata": ["Status", "Reason"],
        },
    ]
    results = {
        "mockChk001": {
            "checkId": "mockChk001", "timestamp": ta_ts, "status": "warning",
            "resourcesSummary": {"resourcesProcessed": 5, "resourcesFlagged": 2,
                                 "resourcesIgnored": 0, "resourcesSuppressed": 1},
            "categorySpecificSummary": {"costOptimizing": {"estimatedMonthlySavings": 0.0,
                                                           "estimatedPercentMonthlySavings": 0.0}},
            "flaggedResources": [
                {"status": "warning", "region": "eu-west-2", "resourceId": "mockres-ebs-1",
                 "isSuppressed": False, "metadata": ["eu-west-2", "vol-0mock1", "data-1", "Yellow"]},
                {"status": "error", "region": "eu-west-1", "resourceId": "mockres-ebs-2",
                 "isSuppressed": False, "metadata": ["eu-west-1", "vol-0mock2", "data-2", "Red"]},
            ],
        },
        "mockChk002": {
            "checkId": "mockChk002", "timestamp": ta_ts, "status": "warning",
            "resourcesSummary": {"resourcesProcessed": 9, "resourcesFlagged": 1,
                                 "resourcesIgnored": 0, "resourcesSuppressed": 0},
            "categorySpecificSummary": {"costOptimizing": {"estimatedMonthlySavings": 12.5,
                                                           "estimatedPercentMonthlySavings": 7.0}},
            "flaggedResources": [
                {"status": "warning", "region": "eu-west-2", "resourceId": "mockres-ec2-1",
                 "isSuppressed": False, "metadata": ["eu-west-2", "i-0mock1", "$12.50", "Yellow"]},
            ],
        },
        "mockChk003": {
            "checkId": "mockChk003", "timestamp": ta_ts, "status": "ok",
            "resourcesSummary": {"resourcesProcessed": 1, "resourcesFlagged": 0,
                                 "resourcesIgnored": 0, "resourcesSuppressed": 0},
            "categorySpecificSummary": {"costOptimizing": {"estimatedMonthlySavings": 0.0,
                                                           "estimatedPercentMonthlySavings": 0.0}},
            "flaggedResources": [],
        },
    }
    return {
        "health": {"events": events, "accounts": accounts, "entities": entities,
                   "descriptions": descriptions,
                   "page_sizes": {"events": EVENTS_PAGE, "accounts": ACCOUNTS_PAGE,
                                  "entities": ENTITIES_PAGE}},
        "support": {"checks": checks, "results": results},
    }


DATA = build_dataset()
REQUESTS: list = []
LOCK = threading.Lock()
ISSUED: dict = {}      # temporary access key id -> session token


class ApiError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _ts(v):
    """Wrap epoch floats as timestamps so CBOR tags them (JSON: plain number)."""
    return Ts(v)


def _event_out(e):
    return {k: (_ts(v) if k in ("startTime", "lastUpdatedTime") else v) for k, v in e.items()}


def _page(items, token, size):
    # Tokens look like "mock-page-0002" (Health requires at least 4 chars).
    try:
        start = int(token.rsplit("-", 1)[1]) if token else 0
    except (IndexError, ValueError):
        raise ApiError("InvalidPaginationToken", f"bad nextToken {token!r}")
    if start < 0 or start > len(items):
        raise ApiError("InvalidPaginationToken", f"bad nextToken {token!r}")
    nxt = start + size
    return items[start:nxt], (f"mock-page-{nxt:04d}" if nxt < len(items) else None)


def _with_token(body, token):
    if token:
        body["nextToken"] = token
    return body


def _need(params, *keys):
    for k in keys:
        if k not in params:
            raise ApiError("ValidationException", f"missing required parameter {k}")


def _health(op, p):
    h = DATA["health"]
    by_arn = {e["arn"]: e for e in h["events"]}
    if op == "DescribeEventsForOrganization":
        rng = (p.get("filter") or {}).get("lastUpdatedTime") or {}
        lo, hi = rng.get("from"), rng.get("to")
        evs = [e for e in h["events"]
               if (lo is None or e["lastUpdatedTime"] >= float(lo))
               and (hi is None or e["lastUpdatedTime"] <= float(hi))]
        page, tok = _page(evs, p.get("nextToken"), EVENTS_PAGE)
        return _with_token({"events": [_event_out(e) for e in page]}, tok)
    if op == "DescribeAffectedAccountsForOrganization":
        _need(p, "eventArn")
        if p["eventArn"] not in by_arn:
            raise ApiError("ValidationException", "unknown eventArn")
        page, tok = _page(h["accounts"][p["eventArn"]], p.get("nextToken"), ACCOUNTS_PAGE)
        return _with_token({"affectedAccounts": page, "eventScopeCode": by_arn[p["eventArn"]]["eventScopeCode"]}, tok)
    if op == "DescribeAffectedEntitiesForOrganization":
        _need(p, "organizationEntityFilters")
        f = p["organizationEntityFilters"][0]
        arn, acct = f.get("eventArn"), f.get("awsAccountId")
        if arn not in by_arn or acct not in h["accounts"][arn]:
            raise ApiError("ValidationException", "unknown eventArn/awsAccountId")
        page, tok = _page(h["entities"][arn], p.get("nextToken"), ENTITIES_PAGE)
        ents = [{"entityArn": f"{arn}/entity/{v}", "eventArn": arn, "entityValue": v,
                 "awsAccountId": acct, "statusCode": "IMPAIRED",
                 "lastUpdatedTime": _ts(by_arn[arn]["lastUpdatedTime"])} for v in page]
        return _with_token({"entities": ents}, tok)
    if op == "DescribeEventDetailsForOrganization":
        _need(p, "organizationEventDetailFilters")
        ok, failed = [], []
        for f in p["organizationEventDetailFilters"]:
            arn = f.get("eventArn")
            if arn in by_arn:
                ok.append({"awsAccountId": f.get("awsAccountId"), "event": _event_out(by_arn[arn]),
                           "eventDescription": {"latestDescription": h["descriptions"][arn]},
                           "eventMetadata": {"mock": "true"}})
            else:
                failed.append({"awsAccountId": f.get("awsAccountId"), "eventArn": arn,
                               "errorName": "NotFound", "errorMessage": "no such event"})
        return {"successfulSet": ok, "failedSet": failed}
    if op == "DescribeEventDetails":
        _need(p, "eventArns")
        ok = [{"event": _event_out(by_arn[a]),
               "eventDescription": {"latestDescription": h["descriptions"][a]},
               "eventMetadata": {"mock": "true"}} for a in p["eventArns"] if a in by_arn]
        failed = [{"eventArn": a, "errorName": "NotFound", "errorMessage": "no such event"}
                  for a in p["eventArns"] if a not in by_arn]
        return {"successfulSet": ok, "failedSet": failed}
    raise ApiError("UnknownOperationException", f"mock does not implement {HEALTH}.{op}")


def _support(op, p):
    s = DATA["support"]
    if op == "DescribeTrustedAdvisorChecks":
        _need(p, "language")
        return {"checks": s["checks"]}
    if op == "DescribeTrustedAdvisorCheckResult":
        _need(p, "checkId")
        if p["checkId"] not in s["results"]:
            raise ApiError("InvalidParameterValueException", f"unknown checkId {p['checkId']}")
        return {"result": s["results"][p["checkId"]]}
    raise ApiError("UnknownOperationException", f"mock does not implement {SUPPORT}.{op}")


def _sts(op, p, entry):
    """AssumeRole over the query protocol. Returns (xml_bytes, status)."""
    if op != "AssumeRole":
        raise ApiError("InvalidAction", f"mock does not implement STS {op}")
    _need(p, "RoleArn", "RoleSessionName")
    duration = int(p.get("DurationSeconds", 3600))
    if duration < 900:
        raise ApiError("ValidationError", "DurationSeconds must be at least 900")
    arn = p["RoleArn"]
    role = arn.rsplit("/", 1)[-1]
    if role.startswith(DENIED_ROLE_PREFIX):
        raise ApiError("AccessDenied", f"User is not authorized to perform: sts:AssumeRole on resource: {arn}",
                       status=403)
    with LOCK:
        n = len(ISSUED) + 1
        key = f"ASIAMOCK{n:012d}"
        token = f"mock-session-token-{n:04d}-{role}"
        ISSUED[key] = token
    entry["issued"] = {"AccessKeyId": key, "SessionToken": token, "RoleArn": arn}
    exp = (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=duration)).strftime("%Y-%m-%dT%H:%M:%SZ")
    account = arn.split(":")[4] if arn.count(":") >= 5 else "000000000000"
    return (
        '<AssumeRoleResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/">'
        "<AssumeRoleResult><Credentials>"
        f"<AccessKeyId>{key}</AccessKeyId><SecretAccessKey>mock-temp-secret-{n:04d}</SecretAccessKey>"
        f"<SessionToken>{token}</SessionToken><Expiration>{exp}</Expiration>"
        "</Credentials><AssumedRoleUser>"
        f"<AssumedRoleId>AROAMOCK{n:012d}:{p['RoleSessionName']}</AssumedRoleId>"
        f"<Arn>arn:aws:sts::{account}:assumed-role/{role}/{p['RoleSessionName']}</Arn>"
        "</AssumedRoleUser></AssumeRoleResult>"
        "<ResponseMetadata><RequestId>mock-request-id</RequestId></ResponseMetadata>"
        "</AssumeRoleResponse>"
    ).encode(), 200


def _sts_error(e):
    kind = "Sender" if e.status < 500 else "Receiver"
    return (
        '<ErrorResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/">'
        f"<Error><Type>{kind}</Type><Code>{e.code}</Code><Message>{e.message}</Message></Error>"
        "<RequestId>mock-request-id</RequestId></ErrorResponse>"
    ).encode()


def _check_temporary_credentials(key_id, token):
    """A temporary key id must arrive with the session token issued for it."""
    if key_id and key_id.startswith("ASIA"):
        with LOCK:
            want = ISSUED.get(key_id)
        if want is None or token != want:
            raise ApiError("UnrecognizedClientException",
                           "The security token included in the request is invalid.", status=403)


SERVICES = {HEALTH: _health, SUPPORT: _support}


def _plain(v):
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_plain(x) for x in v]
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    return v


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("mock_aws: " + (fmt % args) + "\n")

    def _send(self, status, body, ctype, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("x-amzn-RequestId", "mock-request-id")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, obj):
        self._send(status, json.dumps(obj, default=float).encode(), "application/json")

    def do_GET(self):
        if self.path == "/_mock/health":
            return self._json(200, {"ok": True})
        if self.path == "/_mock/dataset":
            return self._json(200, DATA)
        if self.path == "/_mock/requests":
            with LOCK:
                return self._json(200, list(REQUESTS))
        self._json(404, {"error": "not found"})

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path == "/_mock/reset":
            with LOCK:
                REQUESTS.clear()
                ISSUED.clear()
            return self._json(200, {"ok": True})

        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if self.path.startswith("/service/"):
            proto = "rpc-v2-cbor"
            parts = self.path.strip("/").split("/")  # service/<prefix>/operation/<op>
            prefix, op = (parts[1], parts[3]) if len(parts) == 4 else ("", "")
        elif ctype == "application/x-www-form-urlencoded" and not self.headers.get("X-Amz-Target"):
            proto = "query"
            prefix, op = STS, ""
        else:
            proto = "json"
            target = self.headers.get("X-Amz-Target", "")
            prefix, _, op = target.partition(".")

        auth = self.headers.get("Authorization", "")
        scope = auth.split("Credential=", 1)[1].split(",", 1)[0].split("/") if "Credential=" in auth else []
        key_id = scope[0] if scope else None
        signing_service = scope[3] if len(scope) > 3 else None
        token = self.headers.get("X-Amz-Security-Token")
        try:
            if proto == "rpc-v2-cbor":
                params = cbor_loads(raw)
            elif proto == "query":
                params = dict(urllib.parse.parse_qsl(raw.decode(), keep_blank_values=True))
                op = params.pop("Action", "")
                params.pop("Version", None)
            else:
                params = json.loads(raw or b"{}")
        except Exception as exc:  # malformed body: record and refuse
            params, err = {}, ApiError("SerializationException", f"cannot decode body: {exc}")
        else:
            err = None
        entry = {"protocol": proto, "service": prefix, "operation": op,
                 "params": _plain(params), "access_key_id": key_id,
                 "signing_service": signing_service, "session_token": token,
                 "client": self.client_address[0], "tls": isinstance(self.connection, ssl.SSLSocket)}
        with LOCK:
            REQUESTS.append(entry)

        if proto == "query":
            try:
                if err:
                    raise err
                body, status = _sts(op, params, entry)
            except ApiError as e:
                entry["error"] = e.code
                body, status = _sts_error(e), e.status
            return self._send(status, body, "text/xml")

        try:
            if err:
                raise err
            _check_temporary_credentials(key_id, token)
            fn = SERVICES.get(prefix)
            if not fn:
                raise ApiError("UnknownOperationException", f"mock does not serve {prefix!r}")
            body, status = fn(op, params), 200
        except ApiError as e:
            entry["error"] = e.code
            body, status = {"__type": e.code, "message": e.message}, e.status

        if proto == "rpc-v2-cbor":
            self._send(status, cbor_dumps(body), "application/cbor", {"smithy-protocol": "rpc-v2-cbor"})
        else:
            self._send(status, json.dumps(body, default=float).encode(), "application/x-amz-json-1.1")


def _serve_tls(port, cert, key):
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    sys.stderr.write(f"mock_aws listening with TLS on :{port}\n")
    srv.serve_forever()


def main():
    port = int(os.environ.get("MOCK_AWS_PORT", "8080"))
    tls_dir = os.environ.get("MOCK_AWS_TLS_DIR", "/mock/tls")
    cert, key = os.path.join(tls_dir, "cert.pem"), os.path.join(tls_dir, "key.pem")
    if os.path.isfile(cert) and os.path.isfile(key):
        tls_port = int(os.environ.get("MOCK_AWS_TLS_PORT", "8443"))
        threading.Thread(target=_serve_tls, args=(tls_port, cert, key), daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    sys.stderr.write(f"mock_aws listening on :{port}\n")
    srv.serve_forever()


if __name__ == "__main__":
    main()
