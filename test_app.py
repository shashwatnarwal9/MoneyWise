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
assert app.read_expense(doc) == {"vendor": "Hostel Canteen", "date": "26/11/2026", "total": 22050, "items": []}
assert app.read_expense({})["total"] is None

# Rekognition lines -> fields: label and amount are separate boxes on the same row; Sub Total ignored
def line(text, top, left=0.05, h=0.02):
    return {"Type": "LINE", "DetectedText": text, "Geometry": {"BoundingBox": {"Top": top, "Left": left, "Height": h}}}
rek = [line("Rs. 330.75", 0.60, 0.6), line("THAPAR HOSTEL CANTEEN", 0.04), line("Date: 26/11/2026", 0.10),
       line("Bill No: 4821", 0.10, 0.6), line("Sub Total", 0.50), line("315.00", 0.50, 0.7),
       line("TOTAL", 0.60), line("Paneer Paratha 1 x 70.00", 0.30), line("999.00", 0.30, 0.7)]
assert app.read_lines(rek) == {"vendor": "THAPAR HOSTEL CANTEEN", "date": "26/11/2026", "total": 33075, "items": [{"name": "Paneer Paratha", "price": 99900}]}, app.read_lines(rek)
# no TOTAL label -> largest x.xx amount
assert app.read_lines([line("Chai Point", 0.1), line("45.00", 0.3), line("12.50", 0.4)])["total"] == 4500
assert app.read_lines([])["total"] is None

# split keeps every paisa
assert app.split(100, ["a", "b", "c"]) == {"a": 34, "b": 33, "c": 33}
# unequal: by parts, and by rupee amounts (₹330.75 bill, A ate ₹210, B ₹120 -> tax shared in ratio)
assert app.split(1000, ["a", "b"], [3, 1]) == {"a": 750, "b": 250}
assert app.split(33075, ["A", "B"], [210, 120]) == {"A": 21048, "B": 12027}
assert sum(app.split(100, ["a", "b", "c"], [1, 1, 2]).values()) == 100

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
# weighted bill in balances: A pays 300, A:B = 2:1
assert app.balances([{"payer": "A", "members": ["A", "B"], "weights": [2, 1], "status": "done", "total": 30000}]) == {"A": 10000, "B": -10000}
# paying a settlement (payer=debtor, members=[creditor]) zeroes it out
exp += [{"payer": t["from"], "members": [t["to"]], "status": "done", "total": t["amount"]} for t in s]
assert all(v == 0 for v in app.balances(exp).values())

# validation
g, p, m, w, n, t = app.validate({"group": "Room-204", "payer": "A", "members": ["A", " B ", "A", ""], "amount": "12.5"})
assert (g, p, m, w, t) == ("room-204", "A", ["A", "B"], None, 1250)
# unequal: zero share drops the person, duplicates keep the first share
_, _, m, w, _, _ = app.validate({"group": "room", "payer": "A", "members": ["A", "B", "C", "A"], "weights": [2, "1.5", 0, 9]})
assert m == ["A", "B"] and [float(x) for x in w] == [2, 1.5]
assert app.validate({"group": "room", "payer": "A", "members": ["A", "B"], "weights": [3, 3]})[3] is None
for bad in ({"group": "x", "payer": "A", "members": ["A"]},
            {"group": "room", "payer": "A", "members": ["A", "B"], "weights": [1]},
            {"group": "room", "payer": "A", "members": ["A"], "weights": ["-1"]},
            {"group": "room", "payer": "A", "members": ["A"], "weights": [0]},
            {"group": "room", "payer": "A", "members": "A"},
            {"group": "room", "payer": "A", "members": ["A"], "amount": "-5"},
            {"group": "room", "payer": "A", "members": ["A"], "amount": "Infinity"},
            {"group": "room", "payer": "A", "members": ["A"], "amount": "abc"}):
    try:
        app.validate(bad)
        raise AssertionError(f"should reject {bad}")
    except ValueError:
        pass

# ---- item-wise splitting ----
# Textract line items
tdoc = {"ExpenseDocuments": [{"SummaryFields": [], "LineItemGroups": [{"LineItems": [
    {"LineItemExpenseFields": [{"Type": {"Text": "ITEM"}, "ValueDetection": {"Text": "Masala Dosa"}},
                               {"Type": {"Text": "PRICE"}, "ValueDetection": {"Text": "120.00"}}]},
    {"LineItemExpenseFields": [{"Type": {"Text": "ITEM"}, "ValueDetection": {"Text": "Samosa"}}]},  # no price: skipped
]}]}]}
assert app.read_expense(tdoc)["items"] == [{"name": "Masala Dosa", "price": 12000}]

# Rekognition rows: the real layout of the sample canteen bill (Samosa's box sits a hair below its numbers)
real = [("THAPAR HOSTEL CANTEEN", .042, .058), ("Patiala, Punjab", .101, .058), ("Date: 26/11/2026", .153, .057),
        ("Bill No: 4821", .153, .41), ("Masala Dosa", .258, .056), ("2 X 60.00", .259, .373), ("120.00", .259, .596),
        ("Paneer Paratha", .309, .058), ("1 X 70.00", .31, .373), ("70.00", .311, .613), ("Cold Coffee", .362, .057),
        ("2 X 40.00", .363, .373), ("80.00", .363, .613), ("3 X 15.00", .415, .373), ("45.00", .415, .612),
        ("Samosa", .416, .057), ("Sub Total", .518, .057), ("315.00", .52, .577), ("GST 5%", .57, .056),
        ("15.75", .571, .596), ("Rs. 330.75", .623, .617), ("TOTAL", .624, .057), ("Thank you! Visit again", .732, .058)]
got = app.read_lines([line(t, top, left, 0.018) for t, top, left in real])
assert got["total"] == 33075, got
assert got["items"] == [{"name": "Masala Dosa", "price": 12000}, {"name": "Paneer Paratha", "price": 7000},
                        {"name": "Cold Coffee", "price": 8000}, {"name": "Samosa", "price": 4500}], got["items"]
# charges rows are not items; "Cardamom Tea" is (whole-word filter)
rows = [["Cardamom Tea", "30.00"], ["Service Charge", "10.00"], ["CGST 2.5%", "1.50"], ["Grand Total", "41.50"], ["Pen", "5.00"]]
assert app.items_from_rows(rows) == [{"name": "Cardamom Tea", "price": 3000}]

# assign items -> weights -> split incl. tax: A had dosa+coffee, B paratha, samosa shared by A,B,C
items = got["items"]
m, w = app.split_by_items(items, [["A"], ["B"], ["A"], ["A", "B", "C"]])
assert m == ["A", "B", "C"] and [float(x) for x in w] == [21500, 8500, 1500]
s = app.split(33075, m, w)
assert s == {"A": 22575, "B": 8925, "C": 1575} and sum(s.values()) == 33075, s  # 5% GST shared in ratio
assert app.clean_assign([["A", " A ", ""], ["B"]], 2) == [["A"], ["B"]]
for bad in ([["A"]], [["A"], []], "A"):
    try:
        app.clean_assign(bad, 2)
        raise AssertionError(f"should reject {bad}")
    except ValueError:
        pass

# body parsing: plain and base64 (API Gateway does that without a JSON content-type)
assert app.parse_body({"body": '{"a": 1}'}) == {"a": 1}
assert app.parse_body({"body": "eyJhIjogMX0=", "isBase64Encoded": True}) == {"a": 1}
assert app.parse_body({}) == {}

print("all checks pass")
