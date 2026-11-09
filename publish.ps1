# One-time publish helper: creates the PUBLIC GitHub repo, pushes, enables GitHub Pages
# (source: GitHub Actions) and shows the first workflow run. Safe to re-run.
$ErrorActionPreference = "Stop"
$repo = "nse-ath-breakout-dashboard"
$gh = "C:\Users\skuma\AppData\Local\Microsoft\WinGet\Packages\GitHub.cli_Microsoft.Winget.Source_8wekyb3d8bbwe\bin\gh.exe"
if (-not (Test-Path $gh)) { $gh = "gh" }
$git = "C:\Program Files\Git\cmd\git.exe"
if (-not (Test-Path $git)) { $git = "git" }
Set-Location $PSScriptRoot

$user = & $gh api user --jq .login
Write-Host "GitHub account: $user"

$exists = $false
try { & $gh repo view "$user/$repo" --json name 2>$null | Out-Null; $exists = $true } catch {}
if (-not $exists) {
  Write-Host "Creating public repository $user/$repo and pushing..."
  & $gh repo create $repo --public --source . --remote origin --push --description "NSE cup / double-bottom all-time-high breakout screener with near-real-time prices"
} else {
  Write-Host "Repository exists; pushing main..."
  if (-not (& $git remote | Select-String -Quiet "^origin$")) { & $git remote add origin "https://github.com/$user/$repo.git" }
  & $git push -u origin main
}

Write-Host "Enabling GitHub Pages (source: GitHub Actions)..."
try { & $gh api -X POST "repos/$user/$repo/pages" -f build_type=workflow --silent } catch { try { & $gh api -X PUT "repos/$user/$repo/pages" -f build_type=workflow --silent } catch { Write-Host "  (Pages may already be enabled)" } }

Start-Sleep -Seconds 5
Write-Host ""
Write-Host "Workflow runs (the first one takes ~12 minutes):"
& $gh run list --repo "$user/$repo" --limit 3
Write-Host ""
Write-Host "Site URL once the run finishes:  https://$user.github.io/$repo/"
Write-Host "Watch progress:                  https://github.com/$user/$repo/actions"
