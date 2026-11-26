# Deploys the whole stack, then uploads the website. Usage: .\deploy.ps1
# Needs: AWS CLI + SAM CLI, credentials via `aws configure`. No `sam build` needed: no dependencies beyond boto3.
$stack = "moneywise"

sam deploy --stack-name $stack --resolve-s3 --capabilities CAPABILITY_IAM `
  --no-confirm-changeset --no-fail-on-empty-changeset
if ($LASTEXITCODE) { exit 1 }

$o = @{}
foreach ($key in "ApiUrl", "AppUrl", "WebsiteBucketName") {
  $o[$key] = aws cloudformation describe-stacks --stack-name $stack `
    --query "Stacks[0].Outputs[?OutputKey=='$key'].OutputValue" --output text
}
if (-not $o.ApiUrl) { Write-Error "Couldn't read stack outputs"; exit 1 }

"window.API = '$($o.ApiUrl)';" | Out-File web/config.js -Encoding ascii
aws s3 sync web "s3://$($o.WebsiteBucketName)" --delete --cache-control "no-cache"
if ($LASTEXITCODE) { exit 1 }

Write-Host "`nAPI:  $($o.ApiUrl)"
Write-Host "Site: $($o.AppUrl)" -ForegroundColor Green
