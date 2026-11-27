# Deploys the whole stack, then uploads the website.
#   .\deploy.ps1        site served over HTTPS by API Gateway
#   .\deploy.ps1 -Cdn   also put CloudFront in front (needs an AWS-verified account).
#                       Keep passing -Cdn on later deploys, or the distribution is removed.
# Needs: AWS CLI + SAM CLI, credentials via `aws configure`. No `sam build` needed: no dependencies beyond boto3.
param([switch]$Cdn)
$stack = "moneywise"

sam deploy --stack-name $stack --resolve-s3 --capabilities CAPABILITY_IAM `
  --no-confirm-changeset --no-fail-on-empty-changeset `
  --parameter-overrides "EnableCdn=$($Cdn.IsPresent.ToString().ToLower())"
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
