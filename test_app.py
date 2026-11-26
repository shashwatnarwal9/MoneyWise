"""Run: python test_app.py"""
import sys

sys.path.insert(0, "src")
import app  # noqa: E402

# amount parsing
assert app.to_cents("Rs. 1,234.50") == 123450
assert app.to_cents("₹ 450") == 45000
assert app.to_cents("TOTAL") is None

# Textract response -> fields (largest TOTAL wins)
doc = {"ExpenseDocuments": [{"SummaryFields": [
    {"Type": {"Text": "VENDOR_NAME"}, "ValueDetection": {"Text": "Hostel Canteen"}},
    {"Type": {"Text": "INVOICE_RECEIPT_DATE"}, "ValueDetection": {"Text": "26/11/2026"}},
    {"Type": {"Text": "SUBTOTAL"}, "ValueDetection": {"Text": "200.00"}},
    {"Type": {"Text": "TOTAL"}, "ValueDetection": {"Text": "210.00"}},
    {"Type": {"Text": "TOTAL"}, "ValueDetection": {"Text": "Rs 220.50"}},
]}]}
assert app.read_expense(doc) == {"vendor": "Hostel Canteen", "date": "26/11/2026", "total": 22050}
assert app.read_expense({})["total"] is None

# Rekognition lines -> fields: label and amount are separate boxes on the same row; Sub Total ignored
def line(text, top, left=0.05, h=0.02):
    return {"Type": "LINE", "DetectedText": text, "Geometry": {"BoundingBox": {"Top": top, "Left": left, "Height": h}}}
rek = [line("Rs. 330.75", 0.60, 0.6), line("THAPAR HOSTEL CANTEEN", 0.04), line("Date: 26/11/2026", 0.10),
       line("Bill No: 4821", 0.10, 0.6), line("Sub Total", 0.50), line("315.00", 0.50, 0.7),
       line("TOTAL", 0.60), line("Paneer Paratha 1 x 70.00", 0.30), line("999.00", 0.30, 0.7)]
assert app.read_lines(rek) == {"vendor": "THAPAR HOSTEL CANTEEN", "date": "26/11/2026", "total": 33075}, app.read_lines(rek)
# no TOTAL label -> largest x.xx amount
assert app.read_lines([line("Chai Point", 0.1), line("45.00", 0.3), line("12.50", 0.4)])["total"] == 4500
assert app.read_lines([])["total"] is None

# split keeps every paisa
assert app.split(100, ["a", "b", "c"]) == {"a": 34, "b": 33, "c": 33}

# balances + settle-up
exp = [
    {"payer": "A", "members": ["A", "B", "C"], "status": "done", "total": 30000},
    {"payer": "B", "members": ["B", "C"], "status": "done", "total": 10000},
    {"payer": "C", "members": ["A"], "status": "processing", "total": None},
]
net = app.balances(exp)
assert net == {"A": 20000, "B": -5000, "C": -15000}, net
assert sum(net.values()) == 0
s = app.settle(net)
assert s == [{"from": "C", "to": "A", "amount": 15000}, {"from": "B", "to": "A", "amount": 5000}], s
# paying a settlement (payer=debtor, members=[creditor]) zeroes it out
exp += [{"payer": t["from"], "members": [t["to"]], "status": "done", "total": t["amount"]} for t in s]
assert all(v == 0 for v in app.balances(exp).values())

# validation
g, p, m, n, t = app.validate({"group": "Room-204", "payer": "A", "members": ["A", " B ", "A", ""], "amount": "12.5"})
assert (g, p, m, t) == ("room-204", "A", ["A", "B"], 1250)
for bad in ({"group": "x", "payer": "A", "members": ["A"]},
            {"group": "room", "payer": "A", "members": "A"},
            {"group": "room", "payer": "A", "members": ["A"], "amount": "-5"},
            {"group": "room", "payer": "A", "members": ["A"], "amount": "Infinity"},
            {"group": "room", "payer": "A", "members": ["A"], "amount": "abc"}):
    try:
        app.validate(bad)
        raise AssertionError(f"should reject {bad}")
    except ValueError:
        pass

print("all checks pass")
