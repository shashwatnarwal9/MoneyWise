"""MoneyWise Lambdas: create expense (+ upload URL), process bill via Textract, read group."""
import json
import os
import re
import time
import uuid
from decimal import Decimal, InvalidOperation
from urllib.parse import unquote_plus

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key

REGION = os.environ.get("AWS_REGION", "ap-south-1")
s3 = boto3.client("s3", region_name=REGION, endpoint_url=f"https://s3.{REGION}.amazonaws.com",
                  config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}))
textract = boto3.client("textract", region_name=REGION)
rekognition = boto3.client("rekognition", region_name=REGION)
table = boto3.resource("dynamodb", region_name=REGION).Table(os.environ.get("TABLE", "local"))

GROUP_RE = re.compile(r"^[a-z0-9-]{3,40}$")


def resp(code, body):
    return {"statusCode": code, "headers": {"content-type": "application/json"}, "body": json.dumps(body)}


# ---------- pure logic (tested in test_app.py) ----------

def to_cents(text):
    """'Rs. 1,234.50' -> 123450. None if no number."""
    nums = re.findall(r"\d[\d,]*(?:\.\d+)?", str(text))
    if not nums:
        return None
    try:
        return int((Decimal(nums[-1].replace(",", "")) * 100).quantize(Decimal(1)))
    except InvalidOperation:
        return None


def read_expense(doc):
    """Pull vendor, date, total (cents) out of a Textract AnalyzeExpense response."""
    found = {}
    for d in doc.get("ExpenseDocuments", []):
        for f in d.get("SummaryFields", []):
            t = f.get("Type", {}).get("Text")
            v = f.get("ValueDetection", {}).get("Text", "").strip()
            if t and v:
                found.setdefault(t, []).append(v)
    total = None
    # ponytail: largest TOTAL wins (grand total incl. tax beats a sub-total); fall back to amount paid / subtotal
    for t in ("TOTAL", "AMOUNT_PAID", "SUBTOTAL"):
        vals = [c for c in map(to_cents, found.get(t, [])) if c]
        if vals:
            total = max(vals)
            break
    first = lambda t: found.get(t, [""])[0][:60]
    return {"vendor": first("VENDOR_NAME") or first("NAME"), "date": first("INVOICE_RECEIPT_DATE"), "total": total}


TOTAL_RE = re.compile(r"\b(grand\s*total|net\s*amount|amount\s*payable|net\s*payable|to\s*pay|total)\b", re.I)
NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
MONEY_RE = re.compile(r"\d[\d,]*\.\d{2}\b")
DATE_RE = re.compile(r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b")


def read_lines(detections):
    """Rekognition DetectText -> vendor, date, total. Pairs a 'TOTAL' label with amounts on the same row."""
    lines = sorted(((d["DetectedText"], d["Geometry"]["BoundingBox"]) for d in detections if d["Type"] == "LINE"),
                   key=lambda l: l[1]["Top"])
    totals = []
    for text, box in lines:
        if TOTAL_RE.search(text) and "sub" not in text.lower():
            mid = box["Top"] + box["Height"] / 2
            row = [t for t, b in lines if abs(b["Top"] + b["Height"] / 2 - mid) < box["Height"] and b["Left"] >= box["Left"]]
            totals += [to_cents(n) for t in row for n in NUM_RE.findall(t)]
    if not totals:  # ponytail: no labelled total -> biggest x.xx amount on the bill; usually the total
        totals = [to_cents(m) for t, _ in lines for m in MONEY_RE.findall(t)]
    date = next((m.group() for t, _ in lines if (m := DATE_RE.search(t))), "")
    return {"vendor": lines[0][0][:60] if lines else "", "date": date, "total": max(totals, default=None)}


def split(total, members):
    """Split cents evenly; leftover paise go to the first members."""
    base, extra = divmod(total, len(members))
    return {m: base + (1 if i < extra else 0) for i, m in enumerate(members)}


def balances(expenses):
    """Net cents per person: positive = is owed, negative = owes."""
    net = {}
    for e in expenses:
        for m in e["members"] + [e["payer"]]:
            net.setdefault(m, 0)
        if e.get("status") != "done":
            continue
        net[e["payer"]] += e["total"]
        for m, share in split(e["total"], e["members"]).items():
            net[m] -= share
    return net


def settle(net):
    """Greedy: biggest debtor pays biggest creditor. At most n-1 payments."""
    debt = sorted(([-v, k] for k, v in net.items() if v < 0), reverse=True)
    cred = sorted(([v, k] for k, v in net.items() if v > 0), reverse=True)
    out, i, j = [], 0, 0
    while i < len(debt) and j < len(cred):
        amt = min(debt[i][0], cred[j][0])
        out.append({"from": debt[i][1], "to": cred[j][1], "amount": amt})
        debt[i][0] -= amt
        cred[j][0] -= amt
        i += debt[i][0] == 0
        j += cred[j][0] == 0
    return out


def validate(body):
    group = str(body.get("group", "")).strip().lower()
    if not GROUP_RE.match(group):
        raise ValueError("Group code must be 3-40 chars: a-z, 0-9, -")
    raw = body.get("members")
    if not isinstance(raw, list):
        raise ValueError("members must be a list")
    members = list(dict.fromkeys(str(m).strip()[:30] for m in raw if str(m).strip()))
    payer = str(body.get("payer", "")).strip()[:30]
    if not 1 <= len(members) <= 20:
        raise ValueError("Split between 1 and 20 people")
    if not payer:
        raise ValueError("Who paid?")
    note = str(body.get("note", "")).strip()[:60]
    amount = body.get("amount")
    total = None
    if amount not in (None, ""):
        try:
            d = Decimal(str(amount).strip())
        except InvalidOperation:
            d = Decimal(0)
        total = int((d * 100).quantize(Decimal(1))) if d.is_finite() else 0
        if not 0 < total <= 10_000_000_00:
            raise ValueError("Amount must be a positive number")
    return group, payer, members, note, total


# ---------- handlers ----------

def create_expense(event, _ctx):
    """POST /expenses. With `amount`: saved immediately (manual entry / settle-up).
    Without: saved as 'processing' and returns a presigned URL for the bill photo."""
    try:
        group, payer, members, note, total = validate(json.loads(event.get("body") or "{}"))
    except (ValueError, json.JSONDecodeError) as e:
        return resp(400, {"error": str(e)})

    eid = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
    item = {"groupId": group, "expenseId": eid, "payer": payer, "members": members, "note": note}
    if total:
        table.put_item(Item={**item, "status": "done", "total": total, "vendor": note or "Manual entry"})
        return resp(201, {"id": eid})

    table.put_item(Item={**item, "status": "processing"})
    url = s3.generate_presigned_url("put_object", ExpiresIn=300, Params={
        "Bucket": os.environ["BUCKET"], "Key": f"bills/{group}/{eid}", "ContentType": "image/jpeg"})
    return resp(201, {"id": eid, "uploadUrl": url})


def _update(group, eid, **attrs):
    table.update_item(
        Key={"groupId": group, "expenseId": eid},
        UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in attrs),
        ExpressionAttributeNames={f"#{k}": k for k in attrs},
        ExpressionAttributeValues={f":{k}": v for k, v in attrs.items()},
    )


def process_bill(event, _ctx):
    """S3 ObjectCreated on bills/{group}/{id} -> Textract (or Rekognition if Textract is refused) -> DynamoDB."""
    for rec in event["Records"]:
        bucket = rec["s3"]["bucket"]["name"]
        key = unquote_plus(rec["s3"]["object"]["key"])
        _, group, eid = key.split("/")
        obj = {"S3Object": {"Bucket": bucket, "Name": key}}
        try:
            try:
                data, engine = read_expense(textract.analyze_expense(Document=obj)), "textract"
            except ClientError as e:
                if e.response["Error"]["Code"] not in ("SubscriptionRequiredException", "AccessDeniedException"):
                    raise
                # ponytail: Rekognition reads max 100 words/image; very long bills may lose the total
                data, engine = read_lines(rekognition.detect_text(Image=obj)["TextDetections"]), "rekognition"
            print(json.dumps({"key": key, "engine": engine, "extracted": data}))  # -> CloudWatch
            if not data["total"]:
                raise ValueError("Couldn't find a total on this bill. Try a clearer photo or enter it manually.")
            _update(group, eid, status="done", **data)
        except Exception as e:  # mark failed so the UI stops waiting; details in CloudWatch
            print(f"FAILED {key}: {e!r}")
            msg = str(e) if isinstance(e, ValueError) else "Couldn't read this bill. Try a clearer photo."
            _update(group, eid, status="failed", error=msg)


def delete_expense(event, _ctx):
    """DELETE /groups/{group}/expenses/{id}: undo a misread bill or a wrong entry."""
    group, eid = event["pathParameters"]["group"].lower(), event["pathParameters"]["id"]
    if not GROUP_RE.match(group) or not re.match(r"^\d{13}-[0-9a-f]{8}$", eid):
        return resp(400, {"error": "Bad request"})
    table.delete_item(Key={"groupId": group, "expenseId": eid})
    return resp(200, {"deleted": eid})


def get_group(event, _ctx):
    """GET /groups/{group} -> expenses, balances, who-pays-whom."""
    group = event["pathParameters"]["group"].lower()
    if not GROUP_RE.match(group):
        return resp(400, {"error": "Bad group code"})
    # ponytail: single Query page (1 MB, thousands of bills); paginate if a group ever outgrows it
    items = table.query(KeyConditionExpression=Key("groupId").eq(group), ScanIndexForward=False)["Items"]
    expenses = [{
        "id": i["expenseId"], "payer": i["payer"], "members": i["members"], "status": i["status"],
        "total": int(i["total"]) if "total" in i else None, "vendor": i.get("vendor", ""),
        "date": i.get("date", ""), "note": i.get("note", ""), "error": i.get("error", ""),
    } for i in items]
    net = balances(expenses)
    return resp(200, {"expenses": expenses, "balances": net, "settlements": settle(net)})
