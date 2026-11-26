# MoneyWise

Serverless expense sharing with AWS Lambda and Amazon Textract. Snap a bill, and the total is read and split across your group.

```
Phone ──► S3 website (index.html)
  │  1. POST /expenses ──► API Gateway ──► Lambda create_expense ──► DynamoDB (status: processing)
  │                                              └─ returns presigned S3 URL
  │  2. PUT photo ──────────────────────────────► S3 bills bucket
  │                                                 │ ObjectCreated event
  │                                                 ▼
  │                                         Lambda process_bill ──► Textract AnalyzeExpense
  │                                                 └──────────────► DynamoDB (vendor, date, total)
  └─ 3. GET /groups/{code} (polls) ──► Lambda get_group ──► balances + who pays whom
All Lambda logs ──► CloudWatch Logs
```

| AWS service | Used for |
|---|---|
| S3 | Static website hosting + bill photo storage (auto-deleted after 90 days) |
| API Gateway (HTTP API) | `POST /expenses`, `GET /groups/{group}`, `DELETE /groups/{group}/expenses/{id}` (throttled to 5 req/s) |
| Lambda (Python 3.12) | 4 small functions in [src/app.py](src/app.py) (create, process, read, delete) |
| Textract | `AnalyzeExpense` reads shop name, total, date |
| DynamoDB | `Expenses` table, key = group code + expense id |
| CloudWatch | Lambda logs, including what Textract extracted per bill |

## Deploy

1. `aws configure` with an IAM user that can create CloudFormation stacks, IAM roles, S3, Lambda, API Gateway, DynamoDB. Region: `ap-south-1` (or any Textract region).
2. `.\deploy.ps1`
3. Open the printed **Site** URL on your phone.

If the website bucket policy fails with *AccessDenied*, your account has S3 "Block Public Access" turned on at the account level. Turn off "Block public access... bucket policies" under S3 → Block Public Access settings for this account, then re-run.

Tear down: `sam delete --stack-name moneywise` (empty both buckets first).

## Use

1. Make up a group code (e.g. `room-204`), add names, share the link.
2. Pick who paid + who's in, tap **Scan a bill**, take a photo.
3. ~3 s later the total appears and balances update. **Settle** records a payment.

## Test

`python test_app.py` covers amount parsing, Textract field extraction, the paisa-exact split, balances and settle-up.

## Known limits

- No login: anyone with the group code can view/add. Pick a code that's hard to guess.
- S3 website hosting is HTTP only. Add CloudFront in front if you need HTTPS.
- No editing bills. If Textract misreads one, delete it (×) and type the amount in.
