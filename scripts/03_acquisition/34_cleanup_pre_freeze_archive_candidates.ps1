<#
34_cleanup_pre_freeze_archive_candidates.ps1

Conservative pre-freeze cleanup for MentalWellbeingByGeography.

Default behaviour:
- Finds the latest outputs/audits/pre_freeze_archive_candidate_audit_v19_*.csv
- Archives only rows with level:
    archive_now
    archive_now_after_latest_confirmed
- Does not delete files.
- Does not move keep_active_for_now rows.
- Does not move duplicate_review rows unless -ArchiveDuplicateReview is supplied.
- Does not move optional_archive_after_confirming_final_script rows unless -ArchiveSupersededScripts is supplied.
- Does not move archive_now_or_keep_as_page_snapshot rows unless -ArchivePageSnapshots is supplied.

Run dry:
  powershell -ExecutionPolicy Bypass -File scripts\03_acquisition\34_cleanup_pre_freeze_archive_candidates.ps1

Apply:
  powershell -ExecutionPolicy Bypass -File scripts\03_acquisition\34_cleanup_pre_freeze_archive_candidates.ps1 -Apply
#>

[CmdletBinding()]
param(
    [switch]$Apply,
    [switch]$ArchiveSupersededScripts,
    [switch]$ArchiveDuplicateReview,
    [switch]$ArchivePageSnapshots,
    [string]$ProjectRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-ProjectRoot {
    param([string]$ExplicitRoot)

    if ($ExplicitRoot -and (Test-Path -LiteralPath $ExplicitRoot)) {
        return (Resolve-Path -LiteralPath $ExplicitRoot).Path
    }

    # Prefer current directory when command is run from project root.
    $cwd = (Get-Location).Path
    if ((Test-Path -LiteralPath (Join-Path $cwd "scripts")) -and
        (Test-Path -LiteralPath (Join-Path $cwd "outputs")) -and
        (Test-Path -LiteralPath (Join-Path $cwd "data"))) {
        return $cwd
    }

    # Otherwise walk upward from this script until the project root is found.
    $dir = (Resolve-Path -LiteralPath $PSScriptRoot).Path
    while ($true) {
        if ((Test-Path -LiteralPath (Join-Path $dir "scripts")) -and
            (Test-Path -LiteralPath (Join-Path $dir "outputs")) -and
            (Test-Path -LiteralPath (Join-Path $dir "data"))) {
            return $dir
        }
        $parent = Split-Path -Parent $dir
        if ($parent -eq $dir -or [string]::IsNullOrWhiteSpace($parent)) {
            break
        }
        $dir = $parent
    }

    throw "Could not resolve project root. Re-run from the project root or pass -ProjectRoot 'D:\Good Measure\MentalWellbeingbyGeography'."
}

function ConvertTo-RelativePath {
    param(
        [string]$BasePath,
        [string]$FullPath
    )
    $baseUri = New-Object System.Uri(($BasePath.TrimEnd('\') + '\'))
    $fullUri = New-Object System.Uri($FullPath)
    return [System.Uri]::UnescapeDataString($baseUri.MakeRelativeUri($fullUri).ToString()).Replace('/', '\')
}

$root = Resolve-ProjectRoot -ExplicitRoot $ProjectRoot
$auditDir = Join-Path $root "outputs\audits"
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"

if (-not (Test-Path -LiteralPath $auditDir)) {
    throw "Audit directory not found: $auditDir"
}

$auditFile = Get-ChildItem -LiteralPath $auditDir -Filter "pre_freeze_archive_candidate_audit_v19_*.csv" -File |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1

if (-not $auditFile) {
    throw "No pre_freeze_archive_candidate_audit_v19_*.csv file found in $auditDir. Run 33_audit_pre_freeze_archive_candidates.py first."
}

$rows = Import-Csv -LiteralPath $auditFile.FullName
if (-not $rows -or $rows.Count -eq 0) {
    throw "Archive candidate audit is empty: $($auditFile.FullName)"
}

$allowedLevels = @("archive_now", "archive_now_after_latest_confirmed")
if ($ArchiveSupersededScripts) { $allowedLevels += "optional_archive_after_confirming_final_script" }
if ($ArchiveDuplicateReview) { $allowedLevels += "duplicate_review" }
if ($ArchivePageSnapshots) { $allowedLevels += "archive_now_or_keep_as_page_snapshot" }

$plan = New-Object System.Collections.Generic.List[object]

foreach ($row in $rows) {
    $level = [string]$row.level
    if ($allowedLevels -notcontains $level) { continue }

    $relPath = [string]$row.path
    if ([string]::IsNullOrWhiteSpace($relPath)) { continue }

    $src = Join-Path $root $relPath
    $archiveSubfolder = [string]$row.suggested_archive_subfolder
    if ([string]::IsNullOrWhiteSpace($archiveSubfolder)) {
        $archiveSubfolder = "outputs/archive/pre_freeze_misc"
    }
    $archiveSubfolder = $archiveSubfolder -replace '/', '\'
    $destDir = Join-Path $root $archiveSubfolder
    $dest = Join-Path $destDir (Split-Path -Leaf $src)

    $exists = Test-Path -LiteralPath $src
    $actionStatus = if ($exists) { if ($Apply) { "planned_move" } else { "dry_run_planned" } } else { "source_missing_skipped" }

    if ($exists -and $Apply) {
        New-Item -ItemType Directory -Force -Path $destDir | Out-Null
        if (Test-Path -LiteralPath $dest) {
            $stem = [System.IO.Path]::GetFileNameWithoutExtension($dest)
            $ext = [System.IO.Path]::GetExtension($dest)
            $dest = Join-Path $destDir ("{0}_ARCHIVED_{1}{2}" -f $stem, $timestamp, $ext)
        }
        Move-Item -LiteralPath $src -Destination $dest -Force
        $actionStatus = "moved"
    }

    $sizeMb = 0.0
    if (Test-Path -LiteralPath $dest) {
        $item = Get-Item -LiteralPath $dest
        if (-not $item.PSIsContainer) { $sizeMb = [math]::Round($item.Length / 1MB, 6) }
    } elseif ($exists) {
        $item = Get-Item -LiteralPath $src
        if (-not $item.PSIsContainer) { $sizeMb = [math]::Round($item.Length / 1MB, 6) }
    }

    $plan.Add([pscustomobject]@{
        run_timestamp = $timestamp
        mode = if ($Apply) { "APPLIED" } else { "DRY_RUN" }
        source_path = $relPath
        source_exists_at_start = $exists
        path_type = $row.path_type
        level = $level
        priority = $row.priority
        recommendation = $row.recommendation
        rationale = $row.rationale
        destination_path = if ($exists) { ConvertTo-RelativePath -BasePath $root -FullPath $dest } else { "" }
        action_status = $actionStatus
        size_mb = $sizeMb
    })
}

$planPath = Join-Path $auditDir ("pre_freeze_cleanup_plan_v20_{0}.csv" -f $timestamp)
$summaryPath = Join-Path $auditDir ("pre_freeze_cleanup_summary_v20_{0}.csv" -f $timestamp)
$noteDir = Join-Path $root "docs\methodology"
$notePath = Join-Path $noteDir ("pre_freeze_cleanup_note_v20_{0}.md" -f $timestamp)

$plan | Export-Csv -LiteralPath $planPath -NoTypeInformation -Encoding UTF8

$summary = $plan |
    Group-Object level, action_status |
    ForEach-Object {
        $parts = $_.Name -split ', '
        [pscustomobject]@{
            level = $parts[0]
            action_status = $parts[1]
            n = $_.Count
            total_size_mb = [math]::Round(($_.Group | Measure-Object size_mb -Sum).Sum, 6)
        }
    } | Sort-Object level, action_status

$summary | Export-Csv -LiteralPath $summaryPath -NoTypeInformation -Encoding UTF8

New-Item -ItemType Directory -Force -Path $noteDir | Out-Null
$noteLines = @(
    "# Pre-freeze cleanup note",
    "",
    "Run timestamp: $timestamp",
    "Mode: $(if ($Apply) { 'APPLIED' } else { 'DRY_RUN' })",
    "Project root: $root",
    "Input audit: $($auditFile.FullName)",
    "",
    "Default cleanup archives only archive_now and archive_now_after_latest_confirmed rows.",
    "Optional switches used:",
    "- ArchiveSupersededScripts: $ArchiveSupersededScripts",
    "- ArchiveDuplicateReview: $ArchiveDuplicateReview",
    "- ArchivePageSnapshots: $ArchivePageSnapshots",
    "",
    "No files were deleted. Files were moved only when Apply was supplied.",
    "",
    "Outputs:",
    "- $planPath",
    "- $summaryPath"
)
$noteLines | Set-Content -LiteralPath $notePath -Encoding UTF8

Write-Host "Pre-freeze cleanup $(if ($Apply) { 'APPLIED' } else { 'DRY_RUN' }) complete."
Write-Host "Project root: $root"
Write-Host "Input audit:  $($auditFile.FullName)"
Write-Host "Plan:         $planPath"
Write-Host "Summary:      $summaryPath"
Write-Host "Note:         $notePath"
Write-Host ""
Write-Host "Summary:"
$summary | Format-Table -AutoSize

if (-not $Apply) {
    Write-Host ""
    Write-Host "Dry run only. Re-run with -Apply to move files."
    Write-Host "Optional: add -ArchiveSupersededScripts, -ArchivePageSnapshots, or -ArchiveDuplicateReview if you explicitly want those included."
}
