# Deploys the whole stack, then uploads the website.
#   powershell -ExecutionPolicy Bypass -File .\deploy.ps1
# Needs: AWS CLI + SAM CLI, credentials via `aws configure`. No `sam build` needed: no dependencies beyond boto3.
# CloudFront is created once by hand in the console (see README); this script just prints its link if it exists.
$stack = "moneywise"

sam deploy --stack-name $stack --resolve-s3 --capabilities CAPABILITY_IAM `
  --no-confirm-changeset --no-fail-on-empty-changeset
if ($LASTEXITCODE) { exit 1 }

$o = @{}
foreach ($key in "ApiUrl", "AppUrl", "WebsiteUrl", "WebsiteBucketName") {
  $o[$key] = aws cloudformation describe-stacks --stack-name $stack `
    --query "Stacks[0].Outputs[?OutputKey=='$key'].OutputValue" --output text
}
if (-not $o.ApiUrl) { Write-Error "Couldn't read stack outputs"; exit 1 }

"window.API = '$($o.ApiUrl)';" | Out-File web/config.js -Encoding ascii
aws s3 sync web "s3://$($o.WebsiteBucketName)" --delete --cache-control "no-cache"
if ($LASTEXITCODE) { exit 1 }

$origin = $o.WebsiteUrl -replace '^http://', ''
$cdn = aws cloudfront list-distributions `
  --query "DistributionList.Items[?Origins.Items[0].DomainName=='$origin'].DomainName | [0]" --output text

Write-Host "`nAPI:  $($o.ApiUrl)"
Write-Host "Backup site (API Gateway): $($o.AppUrl)"
if ($cdn -and $cdn -ne "None") { Write-Host "Site: https://$cdn/" -ForegroundColor Green }
else { Write-Host "Site: $($o.AppUrl)  (add CloudFront in the console for a CDN link, see README)" -ForegroundColor Green }
