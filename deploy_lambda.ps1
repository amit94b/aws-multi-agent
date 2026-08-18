# deploy_lambda.ps1
# -----------------
# Helper script to zip and deploy all 5 Lambda functions to AWS.
#
# Usage:
#   .\deploy_lambda.ps1 [OPTIONS]
#
# Options:
#   -FunctionPrefix  Prefix for Lambda function names (default: "bedrock")
#   -Region          AWS region (default: reads from agent_ids.json or eu-west-1)
#   -Validate        Only validate (dry run) without deploying
#
# Prerequisites:
#   - AWS CLI configured (aws configure)
#   - Lambda functions already exist (create them manually or via Terraform)
#   - All lambda_*.py files present

param(
    [string]$FunctionPrefix = "bedrock",
    [string]$Region = "",
    [switch]$Validate = $false
)

$ErrorActionPreference = "Stop"

# ── Determine region ──────────────────────────────────────────────────────────
if (-not $Region) {
    if (Test-Path "agent_ids.json") {
        $ids = Get-Content "agent_ids.json" | ConvertFrom-Json
        $Region = $ids.region
    }
    if (-not $Region) { $Region = "eu-west-1" }
}

Write-Host ""
Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  AWS Lambda Deployment — $FunctionPrefix-* ($Region)" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""

if ($Validate) {
    Write-Host "[DRY RUN MODE — no changes will be made]" -ForegroundColor Yellow
    Write-Host ""
}

# ── Agent definitions ─────────────────────────────────────────────────────────
$Agents = @(
    @{ Key = "s3";            FunctionName = "$FunctionPrefix-s3-agent";            HandlerFile = "lambda_s3.py" },
    @{ Key = "iam";           FunctionName = "$FunctionPrefix-iam-agent";           HandlerFile = "lambda_iam.py" },
    @{ Key = "observability"; FunctionName = "$FunctionPrefix-observability-agent"; HandlerFile = "lambda_observability.py" },
    @{ Key = "compute";       FunctionName = "$FunctionPrefix-compute-agent";       HandlerFile = "lambda_compute.py" },
    @{ Key = "vpc";           FunctionName = "$FunctionPrefix-vpc-agent";           HandlerFile = "lambda_vpc.py" },
    @{ Key = "database";      FunctionName = "$FunctionPrefix-database-agent";      HandlerFile = "lambda_database.py" },
    @{ Key = "finops";        FunctionName = "$FunctionPrefix-finops-agent";        HandlerFile = "lambda_finops.py" }
)

$TempDir = "$env:TEMP\lambda_deploy_$(Get-Random)"
New-Item -ItemType Directory -Path $TempDir -Force | Out-Null

$Success = 0
$Failed  = 0

foreach ($agent in $Agents) {
    $key          = $agent.Key
    $functionName = $agent.FunctionName
    $handlerFile  = $agent.HandlerFile
    $zipPath      = "$TempDir\$key.zip"

    Write-Host "─────────────────────────────────────────────────────" -ForegroundColor DarkGray
    Write-Host "  [$key] → $functionName" -ForegroundColor White

    # Check handler file exists
    if (-not (Test-Path $handlerFile)) {
        Write-Host "  ❌ Handler file not found: $handlerFile" -ForegroundColor Red
        $Failed++
        continue
    }

    # Zip the handler file
    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
    Compress-Archive -Path $handlerFile -DestinationPath $zipPath -Force
    $zipSize = (Get-Item $zipPath).Length / 1KB
    Write-Host "  📦 Zipped $handlerFile ($([math]::Round($zipSize, 1)) KB)" -ForegroundColor Gray

    if ($Validate) {
        Write-Host "  ✓ [DRY RUN] Would deploy to $functionName" -ForegroundColor Yellow
        $Success++
        continue
    }

    # Check function exists
    $functionExists = $false
    try {
        $null = aws lambda get-function --function-name $functionName --region $Region 2>$null
        if ($LASTEXITCODE -eq 0) {
            $functionExists = $true
        }
    } catch {
        $functionExists = $false
    }

    if (-not $functionExists) {
        Write-Host "  ⚠️  Function $functionName not found in AWS." -ForegroundColor Yellow
        Write-Host "  💡 Tip: Run 'python agent_as_code.py' to automatically create all IAM roles, Lambdas & Bedrock Agents in 1 click!" -ForegroundColor Cyan
        $Failed++
        continue
    }

    # Deploy
    try {
        $result = aws lambda update-function-code `
            --function-name $functionName `
            --zip-file "fileb://$zipPath" `
            --region $Region `
            --output json | ConvertFrom-Json

        # Set AGENT_KEY env var
        $null = aws lambda update-function-configuration `
            --function-name $functionName `
            --environment "Variables={AGENT_KEY=$key,AWS_REGION=$Region}" `
            --region $Region `
            --output json 2>&1

        Write-Host "  ✅ Deployed successfully (CodeSize: $($result.CodeSize) bytes)" -ForegroundColor Green
        $Success++
    } catch {
        Write-Host "  ❌ Deployment failed: $_" -ForegroundColor Red
        $Failed++
    }
}

# ── Cleanup ───────────────────────────────────────────────────────────────────
Remove-Item -Path $TempDir -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  Results: $Success succeeded, $Failed failed" -ForegroundColor $(if ($Failed -gt 0) { "Yellow" } else { "Green" })
Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""

if (-not $Validate) {
    Write-Host "Next steps:" -ForegroundColor White
    Write-Host "  1. Run: python setup_agents.py  (if agents not created yet)" -ForegroundColor Gray
    Write-Host "  2. Run: streamlit run streamlit_app.py" -ForegroundColor Gray
    Write-Host ""
}

exit $(if ($Failed -gt 0) { 1 } else { 0 })
