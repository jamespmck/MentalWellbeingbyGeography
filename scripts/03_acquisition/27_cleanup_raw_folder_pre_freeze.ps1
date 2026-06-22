<#
.SYNOPSIS
  Conservative cleanup of data\raw before freezing raw acquisition.

.DESCRIPTION
  Moves duplicated, exploratory, or browser-capture artefacts out of the active raw source layer.
  The script does not delete data. By default it runs as a dry run. Use -Apply to perform moves.

  Recommended run from PowerShell:
    cd "D:\Good Measure\MentalWellbeingbyGeography"
    powershell -ExecutionPolicy Bypass -File "D:\Good Measure\MentalWellbeingbyGeography\scripts\03_acquisition\27_cleanup_raw_folder_pre_freeze.ps1"
    powershell -ExecutionPolicy Bypass -File "D:\Good Measure\MentalWellbeingbyGeography\scripts\03_acquisition\27_cleanup_raw_folder_pre_freeze.ps1" -Apply

.PARAMETER ProjectRoot
  Project root. Defaults to D:\Good Measure\MentalWellbeingbyGeography.

.PARAMETER Apply
  Actually perform the moves. Without -Apply, only writes a cleanup plan.

.PARAMETER ArchiveLargeCaches
  Also move large NDIA full public download cache out of the active raw layer.
  This keeps raw\ndia\public_poc_selected active. Use only if you are comfortable treating the full NDIA download cache as archived provenance.

.PARAMETER RemoveEmptyDirs
  Remove empty directories after moves. Default is true when -Apply is used.
#>

param(
    [string]$ProjectRoot = "D:\Good Measure\MentalWellbeingbyGeography",
    [switch]$Apply,
    [switch]$ArchiveLargeCaches,
    [bool]$RemoveEmptyDirs = $true
)

$ErrorActionPreference = "Stop"

$RawRoot = Join-Path $ProjectRoot "data\raw"
$AuditDir = Join-Path $ProjectRoot "outputs\audits"
$MethodDir = Join-Path $ProjectRoot "docs\methodology"
$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$Mode = if ($Apply) { "APPLIED" } else { "DRY_RUN" }
$PlanPath = Join-Path $AuditDir "raw_folder_cleanup_plan_v16_$Timestamp.csv"
$SummaryPath = Join-Path $AuditDir "raw_folder_cleanup_summary_v16_$Timestamp.csv"
$NotePath = Join-Path $MethodDir "raw_folder_cleanup_note_v16_$Timestamp.md"

if (-not (Test-Path $RawRoot)) {
    throw "Raw folder not found: $RawRoot"
}

New-Item -ItemType Directory -Force $AuditDir | Out-Null
New-Item -ItemType Directory -Force $MethodDir | Out-Null

$Actions = New-Object System.Collections.Generic.List[object]

function Get-ItemTypeSafe {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return "missing" }
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer) { return "directory" }
    return "file"
}

function Get-SizeBytesSafe {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return 0 }
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer) {
        return (Get-ChildItem -LiteralPath $Path -Recurse -Force -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
    }
    return $item.Length
}

function Get-UniqueDestination {
    param([string]$Destination)
    if (-not (Test-Path -LiteralPath $Destination)) { return $Destination }

    $parent = Split-Path -Parent $Destination
    $leaf = Split-Path -Leaf $Destination
    $stem = [System.IO.Path]::GetFileNameWithoutExtension($leaf)
    $ext = [System.IO.Path]::GetExtension($leaf)

    for ($i = 1; $i -le 999; $i++) {
        $candidateLeaf = if ($ext) { "{0}_{1:D3}{2}" -f $stem, $i, $ext } else { "{0}_{1:D3}" -f $leaf, $i }
        $candidate = Join-Path $parent $candidateLeaf
        if (-not (Test-Path -LiteralPath $candidate)) { return $candidate }
    }
    throw "Could not create unique destination for $Destination"
}

function Add-MoveAction {
    param(
        [string]$SourceRel,
        [string]$DestRel,
        [string]$Reason,
        [string]$ActionType = "move"
    )

    $src = Join-Path $RawRoot $SourceRel
    $dst = Join-Path $RawRoot $DestRel
    $exists = Test-Path -LiteralPath $src
    $itemType = Get-ItemTypeSafe -Path $src
    $sizeBytes = Get-SizeBytesSafe -Path $src
    $status = "planned"
    $actualDst = $dst

    if (-not $exists) {
        $status = "source_missing_skipped"
    } elseif ($Apply) {
        $parent = Split-Path -Parent $dst
        New-Item -ItemType Directory -Force $parent | Out-Null
        $actualDst = Get-UniqueDestination -Destination $dst
        Move-Item -LiteralPath $src -Destination $actualDst -Force
        $status = "moved"
    }

    $Actions.Add([pscustomobject]@{
        run_timestamp = (Get-Date).ToString("s")
        mode = $Mode
        action_type = $ActionType
        source_relative_path = $SourceRel
        destination_relative_path = $DestRel
        actual_destination_relative_path = if ($actualDst.StartsWith($RawRoot)) { $actualDst.Substring($RawRoot.Length).TrimStart('\') } else { $actualDst }
        item_type = $itemType
        size_bytes = $sizeBytes
        size_mb = [math]::Round(($sizeBytes / 1MB), 3)
        status = $status
        reason = $Reason
    }) | Out-Null
}

function Add-RenameAction {
    param(
        [string]$SourceRel,
        [string]$DestRel,
        [string]$Reason
    )
    Add-MoveAction -SourceRel $SourceRel -DestRel $DestRel -Reason $Reason -ActionType "rename_or_rehome"
}

# -----------------------------------------------------------------------------
# 1. ABS homelessness: keep the complete discovered workbook set as active data;
#    move duplicated root workbooks and rehome source pages.
# -----------------------------------------------------------------------------
Add-RenameAction "abs_homelessness\abs_estimating_homelessness_latest_release.html" "abs_homelessness\source_pages\abs_estimating_homelessness_latest_release.html" "Source-page provenance. Keep, but move out of source-family root."
Add-RenameAction "abs_homelessness\abs_tablebuilder_estimating_homelessness_page.html" "abs_homelessness\source_pages\abs_tablebuilder_estimating_homelessness_page.html" "Source-page provenance. Keep, but move out of source-family root."
Add-MoveAction "abs_homelessness\abs_estimating_homelessness_2021_data_cube_005.xlsx" "_archive\duplicates\abs_homelessness\abs_estimating_homelessness_2021_data_cube_005_DUPLICATE_ROOT_COPY.xlsx" "Root copy duplicates the discovered data cube set. Preserve in archive."
Add-MoveAction "abs_homelessness\abs_estimating_homelessness_2021_data_cube_006.xlsx" "_archive\duplicates\abs_homelessness\abs_estimating_homelessness_2021_data_cube_006_DUPLICATE_ROOT_COPY.xlsx" "Root copy duplicates the discovered data cube set. Preserve in archive."
Add-RenameAction "abs_homelessness\discovered_downloads" "abs_homelessness\data_cubes_2021" "Rename generic discovered_downloads to explicit ABS homelessness data cube folder."

# -----------------------------------------------------------------------------
# 2. AEDC: separate cached HTML source pages from workbook downloads.
# -----------------------------------------------------------------------------
Add-RenameAction "aedc\aedc_2024_results_page.html" "aedc\source_pages\aedc_2024_results_page.html" "Source-page provenance."
Add-RenameAction "aedc\aedc_accessing_data_page.html" "aedc\source_pages\aedc_accessing_data_page.html" "Source-page provenance."
Add-RenameAction "aedc\aedc_community_profiles_page.html" "aedc\source_pages\aedc_community_profiles_page.html" "Source-page provenance."
Add-RenameAction "aedc\discovered_downloads" "aedc\data_workbooks_2024" "Rename generic discovered_downloads to explicit AEDC workbook folder."

# -----------------------------------------------------------------------------
# 3. AIHW: consolidate duplicate top-level source-family folders into raw\aihw.
# -----------------------------------------------------------------------------
Add-RenameAction "aihw_mental_health_regional_activity\aihw_regional_activity_data_page.html" "aihw\regional_activity_data_downloads\source_pages\aihw_regional_activity_data_page.html" "Consolidate AIHW regional activity page under raw\aihw."
Add-MoveAction "aihw_mental_health_regional_activity\discovered_downloads" "_archive\duplicates\aihw_mental_health_regional_activity_duplicate_zips" "Duplicate of raw\aihw\regional_activity_data_downloads\zips. Preserve duplicate set in archive."

Add-RenameAction "aihw_mbs_primary_care_geography\aihw_mbs_gp_allied_specialist_geography_page.html" "aihw\mbs_primary_care_geography\source_pages\aihw_mbs_gp_allied_specialist_geography_page.html" "Consolidate AIHW MBS geography source page under raw\aihw."
Add-RenameAction "aihw_mbs_primary_care_geography\discovered_downloads" "aihw\mbs_primary_care_geography\downloads" "Consolidate AIHW MBS geography downloads under raw\aihw."

Add-RenameAction "aihw_shs\aihw_shs_annual_report_2024_25_data_page.html" "aihw\specialist_homelessness_services\source_pages\aihw_shs_annual_report_2024_25_data_page.html" "Consolidate AIHW SHS source page under raw\aihw."
Add-RenameAction "aihw_shs\discovered_downloads" "aihw\specialist_homelessness_services\downloads" "Consolidate AIHW SHS downloads under raw\aihw."

Add-RenameAction "aihw_mental_health_data_tables\aihw_mental_health_data_tables_page.html" "aihw\mental_health_data_tables\source_pages\aihw_mental_health_data_tables_page.html" "Consolidate AIHW mental health data table page under raw\aihw."
Add-RenameAction "aihw_mental_health_data_tables\discovered_downloads" "aihw\mental_health_data_tables\downloads" "Consolidate AIHW mental health data table downloads under raw\aihw."

# -----------------------------------------------------------------------------
# 4. PHIDU: keep official LGA/PHN workbooks active; archive exploratory and duplicate workbooks.
# -----------------------------------------------------------------------------
Add-RenameAction "phidu\phidu_official_lga_australia.xlsx" "phidu\official_lga_phn_context\phidu_official_lga_australia.xlsx" "Official PHIDU LGA context workbook remains active."
Add-RenameAction "phidu\phidu_official_phn_with_component_lgas.xlsx" "phidu\official_lga_phn_context\phidu_official_phn_with_component_lgas.xlsx" "Official PHIDU PHN with component LGA workbook remains active."
Add-RenameAction "phidu\phidu_official_phn_with_component_phas.xlsx" "phidu\official_lga_phn_context\phidu_official_phn_with_component_phas.xlsx" "Official PHIDU PHN with component PHA workbook remains active."

Add-MoveAction "phidu\02_pha_unknown_phn_data_with_component_phas_xlsx.xlsx" "_archive\duplicates\phidu\02_pha_unknown_phn_data_with_component_phas_DUPLICATE_OF_OFFICIAL_PHN_PHA.xlsx" "Exact duplicate of official PHN-with-component-PHAs workbook."
Add-MoveAction "phidu\03_lga_unknown_phn_data_with_component_lgas_xlsx.xlsx" "_archive\duplicates\phidu\03_lga_unknown_phn_data_with_component_lgas_DUPLICATE_OF_OFFICIAL_PHN_LGA.xlsx" "Exact duplicate of official PHN-with-component-LGAs workbook."

Add-MoveAction "phidu\01_pha_health_status_disability_carers_deaths_health_status_disease_prevention_disability_carers_and_deaths.xlsx" "_archive\exploratory\phidu_pha_workbooks\01_pha_health_status_disability_carers_deaths_health_status_disease_prevention_disability_carers_and_deaths.xlsx" "Exploratory PHA workbook. Keep out of active LGA/PHN raw context."
Add-MoveAction "phidu\04_pha_health_welfare_services_use_and_provision_of_health_and_welfare_services.xlsx" "_archive\exploratory\phidu_pha_workbooks\04_pha_health_welfare_services_use_and_provision_of_health_and_welfare_services.xlsx" "Exploratory PHA workbook."
Add-MoveAction "phidu\05_pha_demographic_social_demographic_and_social_indicators.xlsx" "_archive\exploratory\phidu_pha_workbooks\05_pha_demographic_social_demographic_and_social_indicators.xlsx" "Exploratory PHA workbook."
Add-MoveAction "phidu\06_phn_socioeconomic_disadvantage_socioeconomic_disadvantage_of_area_within_primary_health_networks_phns_data_xlsx.xlsx" "_archive\exploratory\phidu_nonselected_workbooks\06_phn_socioeconomic_disadvantage_socioeconomic_disadvantage_of_area_within_primary_health_networks_phns_data_xlsx.xlsx" "Exploratory PHN workbook superseded by official LGA/PHN extraction path."
Add-MoveAction "phidu\07_phn_first_nations_aboriginal_torres_strait_islander_data_by_primary_health_network_incl_component_iares_xlsx.xlsx" "_archive\exploratory\phidu_nonselected_workbooks\07_phn_first_nations_aboriginal_torres_strait_islander_data_by_primary_health_network_incl_component_iares_xlsx.xlsx" "Exploratory PHN workbook superseded by official LGA/PHN extraction path."
Add-MoveAction "phidu\09_unknown_socioeconomic_disadvantage_indigenous_status_comparison_by_socioeconomic_outcomes_of_area_data_xlsx.xlsx" "_archive\exploratory\phidu_nonselected_workbooks\09_unknown_socioeconomic_disadvantage_indigenous_status_comparison_by_socioeconomic_outcomes_of_area_data_xlsx.xlsx" "Exploratory workbook."
Add-MoveAction "phidu\10_seifa_quintile_socioeconomic_disadvantage_socioeconomic_disadvantage_of_area_data_xlsx.xlsx" "_archive\exploratory\phidu_nonselected_workbooks\10_seifa_quintile_socioeconomic_disadvantage_socioeconomic_disadvantage_of_area_data_xlsx.xlsx" "Exploratory workbook."

# -----------------------------------------------------------------------------
# 5. NDIA/NDIS: keep selected POC active; archive browser captures and historical probes.
# -----------------------------------------------------------------------------
Add-RenameAction "ndis_service_area\ndis_explore_data_page.html" "ndia\service_area_candidate\source_pages\ndis_explore_data_page.html" "Consolidate NDIS service-area candidate page under raw\ndia."
Add-MoveAction "ndia\explore_data_tool_capture" "_archive\browser_captures\ndia_explore_data_tool_capture" "Browser/network capture debris. Preserve outside active raw source layer."
Add-MoveAction "ndia\explore_data_tool_historical_probe" "_archive\browser_captures\ndia_explore_data_tool_historical_probe" "Historical browser/API probe debris. Preserve outside active raw source layer."

if ($ArchiveLargeCaches) {
    Add-MoveAction "ndia\public_data_downloads" "_archive\large_caches\ndia_public_data_downloads" "Large full NDIA public download cache. raw\ndia\public_poc_selected remains active for current POC processing."
}

# -----------------------------------------------------------------------------
# 6. State health and geography bridges: make folder names clearer, no deletion.
# -----------------------------------------------------------------------------
Add-RenameAction "state_health_geography\nsw_health_lhd_page.html" "state_health_geography\source_pages\nsw_health_lhd_page.html" "Source-page provenance."
Add-RenameAction "state_health_geography\discovered_downloads" "state_health_geography\downloads" "Rename generic discovered_downloads to downloads."
Add-RenameAction "health\phn_concordance" "geography_bridges\phn_concordance" "PHN concordance is a geography bridge, not a general health raw source."

# -----------------------------------------------------------------------------
# Write plan and optional cleanup of empty directories.
# -----------------------------------------------------------------------------
$Actions | Export-Csv -NoTypeInformation -Path $PlanPath

if ($Apply -and $RemoveEmptyDirs) {
    $emptyRemoved = 0
    Get-ChildItem -LiteralPath $RawRoot -Directory -Recurse -Force -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending |
        ForEach-Object {
            if ($_.FullName -eq $RawRoot) { return }
            if ($_.FullName -like "*\_archive") { return }
            $children = Get-ChildItem -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue
            if (-not $children) {
                Remove-Item -LiteralPath $_.FullName -Force
                $emptyRemoved++
            }
        }
    $Actions.Add([pscustomobject]@{
        run_timestamp = (Get-Date).ToString("s")
        mode = $Mode
        action_type = "remove_empty_dirs"
        source_relative_path = ""
        destination_relative_path = ""
        actual_destination_relative_path = ""
        item_type = "directory"
        size_bytes = 0
        size_mb = 0
        status = "removed_empty_dirs_$emptyRemoved"
        reason = "Removed empty directories after cleanup."
    }) | Out-Null
    $Actions | Export-Csv -NoTypeInformation -Path $PlanPath
}

$summary = $Actions |
    Group-Object action_type, status |
    ForEach-Object {
        [pscustomobject]@{
            action_type = ($_.Name -split ', ')[0]
            status = ($_.Name -split ', ')[1]
            n = $_.Count
            total_size_mb = [math]::Round((($_.Group | Measure-Object size_bytes -Sum).Sum / 1MB), 3)
        }
    }
$summary | Export-Csv -NoTypeInformation -Path $SummaryPath

$activeTop = Get-ChildItem -LiteralPath $RawRoot -Directory -Force -ErrorAction SilentlyContinue |
    Sort-Object Name |
    ForEach-Object { $_.Name }


$activeTopLines = @()
foreach ($folderName in $activeTop) {
    $activeTopLines += "- $folderName"
}

$noteLines = @()
$noteLines += "# Raw folder cleanup note v16"
$noteLines += ""
$noteLines += "Run timestamp: $(Get-Date -Format s)"
$noteLines += "Mode: $Mode"
$noteLines += "Project root: $ProjectRoot"
$noteLines += "Raw root: $RawRoot"
$noteLines += ""
$noteLines += "This cleanup was conservative. It moved duplicated, exploratory, browser-capture and generic-discovery artefacts out of the active raw source layer. It did not delete raw data."
$noteLines += ""
$noteLines += "Active top-level raw folders after this run or dry-run baseline:"
$noteLines += ""
$noteLines += $activeTopLines
$noteLines += ""
$noteLines += "Key design decisions:"
$noteLines += ""
$noteLines += "- Keep source-page HTML as provenance, but rehome it under source_pages."
$noteLines += "- Keep official PHIDU LGA/PHN workbooks active under raw\phidu\official_lga_phn_context."
$noteLines += "- Archive exploratory PHIDU workbooks and exact duplicates."
$noteLines += "- Consolidate AIHW source-family folders under raw\aihw."
$noteLines += "- Keep AIHW regional activity extracted data active and archive duplicate zip downloads from the second acquisition path."
$noteLines += "- Keep raw\ndia\public_poc_selected active."
$noteLines += "- Move NDIA browser/network captures to raw\_archive\browser_captures."
$noteLines += "- Move ndis_service_area into raw\ndia\service_area_candidate."
$noteLines += "- Do not touch the large raw\abs Census folder, which may be absent from shared archives."
$noteLines += ""
$noteLines += "Plan/audit file:"
$noteLines += ""
$noteLines += $PlanPath
$noteLines += ""
$noteLines += "Summary file:"
$noteLines += ""
$noteLines += $SummaryPath
$noteLines += ""
$note = $noteLines -join [Environment]::NewLine
$note | Set-Content -Path $NotePath -Encoding UTF8

Write-Host "Raw cleanup $Mode complete."
Write-Host "Plan:    $PlanPath"
Write-Host "Summary: $SummaryPath"
Write-Host "Note:    $NotePath"
Write-Host ""
Write-Host "Summary:"
$summary | Format-Table -AutoSize

if (-not $Apply) {
    Write-Host ""
    Write-Host "Dry run only. Re-run with -Apply to move files."
    Write-Host "Optional: add -ArchiveLargeCaches to move raw\ndia\public_data_downloads into raw\_archive\large_caches."
}
