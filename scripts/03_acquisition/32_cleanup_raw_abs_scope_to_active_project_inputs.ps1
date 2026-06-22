param(
    [switch]$Apply,
    [switch]$ArchiveOptionalSA1AreaFile
)

$ErrorActionPreference = 'Stop'

$projectRoot = 'D:\Good Measure\MentalWellbeingbyGeography'
$absRoot = Join-Path $projectRoot 'data\raw\abs'
$archiveRoot = Join-Path $projectRoot 'data\raw\_archive\abs_not_active_for_v01_scope'
$auditDir = Join-Path $projectRoot 'outputs\audits'
$methodDir = Join-Path $projectRoot 'docs\methodology'
$timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$planPath = Join-Path $auditDir ("raw_abs_scope_cleanup_plan_v19_{0}.csv" -f $timestamp)
$summaryPath = Join-Path $auditDir ("raw_abs_scope_cleanup_summary_v19_{0}.csv" -f $timestamp)
$notePath = Join-Path $methodDir ("raw_abs_scope_cleanup_note_v19_{0}.md" -f $timestamp)

New-Item -ItemType Directory -Force $auditDir | Out-Null
New-Item -ItemType Directory -Force $methodDir | Out-Null
New-Item -ItemType Directory -Force $archiveRoot | Out-Null

if (-not (Test-Path $absRoot)) {
    throw "ABS raw folder not found: $absRoot"
}

$plan = New-Object System.Collections.Generic.List[object]

function Add-MovePlan {
    param(
        [string]$Path,
        [string]$Reason,
        [string]$Category
    )
    if ([string]::IsNullOrWhiteSpace($Path)) { return }
    if (-not (Test-Path -LiteralPath $Path)) { return }

    $item = Get-Item -LiteralPath $Path -Force
    $rel = Resolve-Path -LiteralPath $Path | ForEach-Object { $_.Path.Substring($absRoot.Length).TrimStart('\') }
    $dest = Join-Path $archiveRoot $rel
    $sizeBytes = 0
    if ($item.PSIsContainer) {
        $sizeBytes = (Get-ChildItem -LiteralPath $Path -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
        if ($null -eq $sizeBytes) { $sizeBytes = 0 }
    } else {
        $sizeBytes = $item.Length
    }

    $plan.Add([pscustomobject]@{
        action_type = 'archive_move'
        category = $Category
        reason = $Reason
        source_path = $Path
        destination_path = $dest
        source_exists = $true
        is_directory = $item.PSIsContainer
        size_mb = [math]::Round(($sizeBytes / 1MB), 6)
        status = if ($Apply) { 'planned_apply' } else { 'planned_dry_run' }
    }) | Out-Null
}

function Add-PatternPlan {
    param(
        [string]$Pattern,
        [string]$Reason,
        [string]$Category
    )
    $matches = Get-ChildItem -Path (Join-Path $absRoot $Pattern) -Force -ErrorAction SilentlyContinue
    foreach ($m in $matches) {
        Add-MovePlan -Path $m.FullName -Reason $Reason -Category $Category
    }
}

# 1. Electoral recodes and electoral boundary concordances are not active inputs for this project.
Add-PatternPlan -Pattern 'geography\SA1_2016_CED_2021_TB_RECODES*' -Category 'electoral_sa1_recodes' -Reason 'Archive SA1-to-electorate TableBuilder recodes. Not used in SA2/SA3/LGA/PHN modelling scope.'
Add-PatternPlan -Pattern 'geography\SA1_2016_SED_2021_TB_RECODES*' -Category 'electoral_sa1_recodes' -Reason 'Archive SA1-to-state-electorate TableBuilder recodes. Not used in SA2/SA3/LGA/PHN modelling scope.'
Add-PatternPlan -Pattern 'geography\CED_*' -Category 'electoral_boundaries' -Reason 'Archive Commonwealth Electoral Division boundary files. Not active for geography-scoped ML inputs.'
Add-PatternPlan -Pattern 'geography\SED_*' -Category 'electoral_boundaries' -Reason 'Archive State Electoral Division boundary files. Not active for geography-scoped ML inputs.'
Add-PatternPlan -Pattern 'geography\CG_CED_*' -Category 'electoral_concordances' -Reason 'Archive CED concordance files. Not active for current 2021 geography-scoped inputs.'
Add-PatternPlan -Pattern 'geography\CG_SED_*' -Category 'electoral_concordances' -Reason 'Archive SED concordance files. Not active for current 2021 geography-scoped inputs.'

# 2. Later-year geography and linking files are outside the 2021-aligned analysis scope.
Add-PatternPlan -Pattern 'geography\*2022*' -Category 'outside_2021_scope' -Reason 'Archive 2022 geography files. Current project is 2021-aligned.'
Add-PatternPlan -Pattern 'geography\*2023*' -Category 'outside_2021_scope' -Reason 'Archive 2023 geography files. Current project is 2021-aligned.'
Add-PatternPlan -Pattern 'geography\*2024*' -Category 'outside_2021_scope' -Reason 'Archive 2024 geography files. Current project is 2021-aligned.'
Add-PatternPlan -Pattern 'geography\*2025*' -Category 'outside_2021_scope' -Reason 'Archive 2025 geography files. Current project is 2021-aligned.'

# 3. Fine-grained, electoral, indigenous, postal and urban-structure files are not active source families.
$nonCoreGeographyNames = @(
    'ADD_2021_AUST.xlsx',
    'DZN_SA2_2021_AUST.xlsx',
    'GCCSA_2021_AUST.xlsx',
    'IARE_2021_AUST.xlsx',
    'ILOC_2021_AUST.xlsx',
    'INDIGENOUS_STRUCTURE_ALLOCATION_2021.xlsx',
    'IREG_2021_AUST.xlsx',
    'MB_2021_AUST.xlsx',
    'MB_DZN_2021_AUST.xlsx',
    'POA_2021_AUST.xlsx',
    'SAL_2021_AUST.xlsx',
    'SUA_2021_AUST.xlsx',
    'SUA_association_2016_2021.xlsx',
    'TR_2021_AUST.xlsx',
    'UCL_SOSR_SOS_2021_AUST.xlsx',
    'UCL_association_2016_2021.xlsx'
)
foreach ($name in $nonCoreGeographyNames) {
    Add-MovePlan -Path (Join-Path $absRoot (Join-Path 'geography' $name)) -Category 'non_core_geography' -Reason 'Archive non-core ABS geography file. Not an active SA2/SA3/LGA/PHN/NDIS bridge input.'
}

$nonCoreConcordancePatterns = @(
    'geography\CG_DZN_*',
    'geography\CG_GCCSA_*',
    'geography\CG_IARE_*',
    'geography\CG_ILOC_*',
    'geography\CG_IREG_*',
    'geography\CG_MB_*',
    'geography\CG_POA_*',
    'geography\CG_RA_*',
    'geography\CG_SOS*',
    'geography\CG_SSC_*',
    'geography\CG_SUA_*',
    'geography\CG_TR_*',
    'geography\CG_UCL_*'
)
foreach ($pattern in $nonCoreConcordancePatterns) {
    Add-PatternPlan -Pattern $pattern -Category 'non_core_concordance' -Reason 'Archive non-core 2016-to-2021 concordance. Not active for current model assembly.'
}

# 4. SA1-level concordance files are not active. Keep SA2/SA3/SA4 2016-to-2021 bridges for NDIA historical allocation reproducibility.
Add-PatternPlan -Pattern 'geography\CG_SA1_*' -Category 'sa1_linking_not_active' -Reason 'Archive SA1 concordance. Current workflow does not model or assemble at SA1 level.'

if ($ArchiveOptionalSA1AreaFile) {
    Add-MovePlan -Path (Join-Path $absRoot 'geography\SA1_2021_AUST.xlsx') -Category 'optional_sa1_area_file' -Reason 'Archive SA1 area file by request. Current active analysis is SA2 and above.'
}

# 5. SEIFA distribution/extracted lower-level files are not active. Keep SA2 and LGA index files plus original ZIPs unless explicitly archived later.
$seifaArchivePatterns = @(
    'seifa\*SA1_Distributions*',
    'seifa\*Population_Distributions*',
    'seifa\Commonwealth_Electoral_Division_*',
    'seifa\State_Electoral_Division_*',
    'seifa\Postal_Area_*',
    'seifa\Suburbs_and_Localities_*',
    'seifa\Statistical_Area_Level_1_*',
    'seifa\Statistical_Area_Level_3_*',
    'seifa\Statistical_Area_Level_4_*'
)
foreach ($pattern in $seifaArchivePatterns) {
    Add-PatternPlan -Pattern $pattern -Category 'seifa_non_active_extract' -Reason 'Archive SEIFA distribution or non-active geography extract. Keep active SA2/LGA index inputs only.'
}

# 6. Deduplicate plan rows by source path, because broad patterns can overlap.
$deduped = $plan | Sort-Object source_path, category -Unique

# Apply moves if requested.
$results = New-Object System.Collections.Generic.List[object]
foreach ($row in $deduped) {
    $status = if ($Apply) { 'pending' } else { 'dry_run' }
    if ($Apply) {
        try {
            $destDir = Split-Path -Parent $row.destination_path
            New-Item -ItemType Directory -Force $destDir | Out-Null
            if (Test-Path -LiteralPath $row.destination_path) {
                $base = [System.IO.Path]::GetFileNameWithoutExtension($row.destination_path)
                $ext = [System.IO.Path]::GetExtension($row.destination_path)
                $parent = Split-Path -Parent $row.destination_path
                $row.destination_path = Join-Path $parent ("{0}_MOVED_{1}{2}" -f $base, $timestamp, $ext)
            }
            Move-Item -LiteralPath $row.source_path -Destination $row.destination_path -Force
            $status = 'moved'
        } catch {
            $status = 'error: ' + $_.Exception.Message
        }
    }
    $results.Add([pscustomobject]@{
        action_type = $row.action_type
        category = $row.category
        reason = $row.reason
        source_path = $row.source_path
        destination_path = $row.destination_path
        is_directory = $row.is_directory
        size_mb = $row.size_mb
        status = $status
    }) | Out-Null
}

$results | Export-Csv -NoTypeInformation -Encoding UTF8 $planPath

$summary = $results | Group-Object category, status | ForEach-Object {
    $parts = $_.Name -split ', '
    [pscustomobject]@{
        category = $parts[0]
        status = $parts[1]
        n = $_.Count
        total_size_mb = [math]::Round(($_.Group | Measure-Object -Property size_mb -Sum).Sum, 6)
    }
}
$summary | Export-Csv -NoTypeInformation -Encoding UTF8 $summaryPath

$noteLines = @()
$noteLines += '# Raw ABS scope cleanup note'
$noteLines += ''
$noteLines += ('Run timestamp: {0}' -f $timestamp)
$noteLines += ('Mode: {0}' -f ($(if ($Apply) { 'APPLIED' } else { 'DRY_RUN' })))
$noteLines += ('Project root: {0}' -f $projectRoot)
$noteLines += ('Target folder: {0}' -f $absRoot)
$noteLines += ('Archive folder: {0}' -f $archiveRoot)
$noteLines += ''
$noteLines += 'Purpose: archive ABS raw files that are not active inputs for the 2021-aligned SA2/SA3/LGA/PHN/NDIS modelling project.'
$noteLines += ''
$noteLines += 'The script archives rather than deletes. It keeps core 2021 geography files, SA2/SA3/SA4 2016-to-2021 bridges used for historical allocation reproducibility, active NSMHW workbooks, Census GCP SA2 ZIP, and core SEIFA SA2/LGA inputs.'
$noteLines += ''
$noteLines += 'Use -ArchiveOptionalSA1AreaFile only if the SA1 2021 area workbook is no longer needed for remoteness or provenance.'
$noteLines += ''
$noteLines += ('Plan CSV: {0}' -f $planPath)
$noteLines += ('Summary CSV: {0}' -f $summaryPath)
$noteLines | Set-Content -Path $notePath -Encoding UTF8

Write-Host ("Raw ABS scope cleanup {0} complete." -f ($(if ($Apply) { 'APPLIED' } else { 'DRY_RUN' })))
Write-Host "Plan:    $planPath"
Write-Host "Summary: $summaryPath"
Write-Host "Note:    $notePath"
Write-Host ''
Write-Host 'Summary:'
if ($summary) {
    $summary | Format-Table -AutoSize
} else {
    Write-Host '(no matching files to archive)'
}
if (-not $Apply) {
    Write-Host ''
    Write-Host 'Dry run only. Re-run with -Apply to move files.'
    Write-Host 'Optional: add -ArchiveOptionalSA1AreaFile to archive geography\SA1_2021_AUST.xlsx.'
}
