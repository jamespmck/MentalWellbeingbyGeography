<#
.SYNOPSIS
  Conservative cleanup for data\raw\abs before raw acquisition freeze.

.DESCRIPTION
  Default mode is DRY RUN. No files are moved unless -Apply is supplied.

  This script uses the latest raw_abs_duplicate_groups_v18_*.csv audit to move
  exact duplicate files that were marked archive_duplicate_copy. It also moves
  high-volume ABS source-page snapshots out of the active raw layer while
  preserving them under data\raw\abs\_archive.

  It does not delete source files. It writes a plan, summary and methodology note.

.PARAMETER Apply
  Move files/directories. Without this switch, writes a plan only.

.PARAMETER KeepHtmlSnapshotsActive
  Do not archive data\raw\abs\census_2021_quickstats_sa2\html.

.PARAMETER ArchiveExtractedCensusPack
  Also archive data\raw\abs\census_2021_gcp_sa2\extracted. This preserves the
  original ZIP in place and moves the extracted working copy to _archive.

.PARAMETER ArchiveZipFiles
  Also archive top-level ABS ZIP files where extracted/workbook versions exist.
  This is optional and usually not needed before freeze.
#>

param(
    [switch]$Apply,
    [switch]$KeepHtmlSnapshotsActive,
    [switch]$ArchiveExtractedCensusPack,
    [switch]$ArchiveZipFiles
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ProjectRoot = (Get-Location).Path
$AbsRoot = Join-Path $ProjectRoot 'data\raw\abs'
$AuditRoot = Join-Path $ProjectRoot 'outputs\audits'
$DocsRoot = Join-Path $ProjectRoot 'docs\methodology'
$Timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$Mode = if ($Apply) { 'APPLIED' } else { 'DRY_RUN' }
$ArchiveRoot = Join-Path $AbsRoot ("_archive\pre_freeze_cleanup_v19_$Timestamp")

New-Item -ItemType Directory -Force $AuditRoot | Out-Null
New-Item -ItemType Directory -Force $DocsRoot | Out-Null

$PlanPath = Join-Path $AuditRoot "raw_abs_cleanup_plan_v19_$Timestamp.csv"
$SummaryPath = Join-Path $AuditRoot "raw_abs_cleanup_summary_v19_$Timestamp.csv"
$NotePath = Join-Path $DocsRoot "raw_abs_cleanup_note_v19_$Timestamp.md"

$Plan = New-Object System.Collections.Generic.List[object]

function Add-PlanRow {
    param(
        [string]$ActionType,
        [string]$SourcePath,
        [string]$DestinationPath,
        [string]$Reason,
        [string]$Status = 'planned'
    )

    $Exists = Test-Path -LiteralPath $SourcePath
    $SizeBytes = 0
    $ItemType = 'missing'
    if ($Exists) {
        $item = Get-Item -LiteralPath $SourcePath -Force
        if ($item.PSIsContainer) {
            $ItemType = 'directory'
            $SizeBytes = (Get-ChildItem -LiteralPath $SourcePath -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
            if ($null -eq $SizeBytes) { $SizeBytes = 0 }
        } else {
            $ItemType = 'file'
            $SizeBytes = $item.Length
        }
    }

    $Plan.Add([PSCustomObject]@{
        run_timestamp = $Timestamp
        mode = $Mode
        action_type = $ActionType
        item_type = $ItemType
        source_path = $SourcePath
        destination_path = $DestinationPath
        source_exists = [int]$Exists
        size_bytes = [int64]$SizeBytes
        size_mb = [math]::Round(($SizeBytes / 1MB), 6)
        reason = $Reason
        status = $Status
    }) | Out-Null
}

function Get-RelativePathSafe {
    param([string]$BasePath, [string]$FullPath)
    $baseUri = [System.Uri]((Resolve-Path -LiteralPath $BasePath).Path.TrimEnd('\') + '\')
    $fullUri = [System.Uri]((Resolve-Path -LiteralPath $FullPath).Path)
    return [System.Uri]::UnescapeDataString($baseUri.MakeRelativeUri($fullUri).ToString()).Replace('/', '\')
}

function Add-MoveFilePlan {
    param([string]$SourcePath, [string]$ArchiveSubFolder, [string]$Reason)
    if (-not (Test-Path -LiteralPath $SourcePath)) {
        Add-PlanRow -ActionType 'move_file' -SourcePath $SourcePath -DestinationPath '' -Reason $Reason -Status 'source_missing_skipped'
        return
    }
    $rel = Get-RelativePathSafe -BasePath $ProjectRoot -FullPath $SourcePath
    $dest = Join-Path (Join-Path $ArchiveRoot $ArchiveSubFolder) $rel
    Add-PlanRow -ActionType 'move_file' -SourcePath $SourcePath -DestinationPath $dest -Reason $Reason
}

function Add-MoveDirectoryPlan {
    param([string]$SourcePath, [string]$ArchiveSubFolder, [string]$Reason)
    if (-not (Test-Path -LiteralPath $SourcePath)) {
        Add-PlanRow -ActionType 'move_directory' -SourcePath $SourcePath -DestinationPath '' -Reason $Reason -Status 'source_missing_skipped'
        return
    }
    $name = Split-Path -Leaf $SourcePath
    $dest = Join-Path (Join-Path $ArchiveRoot $ArchiveSubFolder) $name
    Add-PlanRow -ActionType 'move_directory' -SourcePath $SourcePath -DestinationPath $dest -Reason $Reason
}

if (-not (Test-Path -LiteralPath $AbsRoot)) {
    throw "ABS raw folder not found: $AbsRoot"
}

# 1. Exact duplicate files from latest duplicate audit.
$DuplicateAudit = Get-ChildItem -LiteralPath $AuditRoot -Filter 'raw_abs_duplicate_groups_v18_*.csv' -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($null -ne $DuplicateAudit) {
    $dups = Import-Csv -LiteralPath $DuplicateAudit.FullName
    foreach ($row in $dups) {
        if ($row.suggested_action -eq 'archive_duplicate_copy') {
            $src = [string]$row.file_path
            if ([string]::IsNullOrWhiteSpace($src)) { continue }
            Add-MoveFilePlan -SourcePath $src -ArchiveSubFolder 'duplicate_exact_sha_copies' -Reason "Exact duplicate sha256. Keeping audit-selected active copy; duplicate_group_id=$($row.duplicate_group_id)."
        }
    }
} else {
    Add-PlanRow -ActionType 'audit_warning' -SourcePath $AuditRoot -DestinationPath '' -Reason 'No raw_abs_duplicate_groups_v18_*.csv audit found. Duplicate cleanup skipped.' -Status 'warning_no_duplicate_audit'
}

# 2. Source-page snapshots: preserve but remove from active raw working layer.
if (-not $KeepHtmlSnapshotsActive) {
    $HtmlDir = Join-Path $AbsRoot 'census_2021_quickstats_sa2\html'
    Add-MoveDirectoryPlan -SourcePath $HtmlDir -ArchiveSubFolder 'source_page_snapshots' -Reason 'QuickStats HTML snapshots are provenance. Archive out of active raw layer to simplify folder and reduce active file count.'
}

# 3. Small extraction marker, if present.
$ExtractedMarker = Join-Path $AbsRoot 'census_2021_gcp_sa2\extracted\.extracted'
Add-MoveFilePlan -SourcePath $ExtractedMarker -ArchiveSubFolder 'extraction_markers' -Reason 'Generated extraction marker, not a raw source file.'

# 4. Optional: archive extracted Census pack. The source ZIP remains active.
if ($ArchiveExtractedCensusPack) {
    $ExtractedDir = Join-Path $AbsRoot 'census_2021_gcp_sa2\extracted'
    Add-MoveDirectoryPlan -SourcePath $ExtractedDir -ArchiveSubFolder 'derived_extracted_census_pack' -Reason 'Extracted working copy can be recreated from the retained Census GCP ZIP; preserving under archive.'
}

# 5. Optional: archive ZIP files already extracted or inventoried.
if ($ArchiveZipFiles) {
    $zipCandidates = @(
        Join-Path $AbsRoot 'census_2021_gcp_sa2\2021_GCP_SA2_for_AUS_short-header.zip',
        Join-Path $AbsRoot 'seifa\Index-data-cubes-all.zip',
        Join-Path $AbsRoot 'seifa\Population-distribution-data-cubes-all.zip',
        Join-Path $AbsRoot 'seifa\SA1-distribution-data-cubes-all.zip'
    )
    foreach ($z in $zipCandidates) {
        Add-MoveFilePlan -SourcePath $z -ArchiveSubFolder 'zip_archives_retained' -Reason 'Optional ZIP archive move requested. File preserved under ABS raw archive.'
    }
}

# Write dry-run plan before applying.
$Plan | Export-Csv -LiteralPath $PlanPath -NoTypeInformation

if ($Apply) {
    foreach ($p in $Plan) {
        if (($p.status -ne 'planned') -or ($p.source_exists -ne 1)) { continue }
        if ([string]::IsNullOrWhiteSpace($p.destination_path)) { continue }
        $destDir = Split-Path -Parent $p.destination_path
        New-Item -ItemType Directory -Force $destDir | Out-Null
        if (Test-Path -LiteralPath $p.destination_path) {
            $suffix = Get-Date -Format 'yyyyMMddHHmmssfff'
            $p.destination_path = "$($p.destination_path).duplicate_destination_$suffix"
        }
        Move-Item -LiteralPath $p.source_path -Destination $p.destination_path -Force
        $p.status = 'moved'
    }

    # Remove empty directories under abs, excluding archive root.
    $removed = 0
    $dirs = Get-ChildItem -LiteralPath $AbsRoot -Directory -Recurse -Force | Sort-Object FullName -Descending
    foreach ($d in $dirs) {
        if ($d.FullName -like "*$([IO.Path]::DirectorySeparatorChar)_archive*$([IO.Path]::DirectorySeparatorChar)*") { continue }
        if ($d.FullName -eq (Join-Path $AbsRoot '_archive')) { continue }
        $hasChildren = Get-ChildItem -LiteralPath $d.FullName -Force -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($null -eq $hasChildren) {
            Remove-Item -LiteralPath $d.FullName -Force
            $removed += 1
        }
    }
    Add-PlanRow -ActionType 'remove_empty_dirs' -SourcePath $AbsRoot -DestinationPath '' -Reason "Removed empty directories after moving files. Count=$removed" -Status "removed_empty_dirs_$removed"
}

# Re-write plan with final statuses if applied.
$Plan | Export-Csv -LiteralPath $PlanPath -NoTypeInformation

$Summary = $Plan | Group-Object action_type,status | ForEach-Object {
    $parts = $_.Name -split ', '
    [PSCustomObject]@{
        action_type = $parts[0]
        status = $parts[1]
        n = $_.Count
        total_size_mb = [math]::Round(($_.Group | Measure-Object size_mb -Sum).Sum, 6)
    }
}
$Summary | Export-Csv -LiteralPath $SummaryPath -NoTypeInformation

$noteLines = @(
    '# Raw ABS folder cleanup note',
    '',
    "Run timestamp: $Timestamp",
    "Mode: $Mode",
    "Project root: $ProjectRoot",
    "Target directory: $AbsRoot",
    '',
    'This cleanup is conservative. It moves files into data\raw\abs\_archive and does not delete raw source files.',
    '',
    'Actions included:',
    '- Archive exact duplicate files using the latest raw_abs_duplicate_groups_v18 audit.',
    '- Archive QuickStats HTML source-page snapshots unless -KeepHtmlSnapshotsActive was supplied.',
    '- Archive generated extraction marker files.',
    '- Optionally archive the extracted Census pack if -ArchiveExtractedCensusPack is supplied.',
    '- Optionally archive selected ZIP files if -ArchiveZipFiles is supplied.',
    '',
    'Outputs:',
    "- $PlanPath",
    "- $SummaryPath",
    "- $NotePath",
    '',
    'Recommended next step:',
    'Re-run the ABS raw audit and check that duplicate groups and active file count have reduced while archive folders preserve provenance.'
)
$noteLines | Set-Content -LiteralPath $NotePath -Encoding UTF8

Write-Host "Raw ABS cleanup $Mode complete."
Write-Host "Plan:    $PlanPath"
Write-Host "Summary: $SummaryPath"
Write-Host "Note:    $NotePath"
Write-Host ''
Write-Host 'Summary:'
$Summary | Format-Table -AutoSize
if (-not $Apply) {
    Write-Host ''
    Write-Host 'Dry run only. Re-run with -Apply to move files.'
    Write-Host 'Optional switches: -KeepHtmlSnapshotsActive, -ArchiveExtractedCensusPack, -ArchiveZipFiles.'
}
