<#
.SYNOPSIS
    Builds Cmdiqo platform agents and the optional bot container.

.DESCRIPTION
    Builds the standalone cmdiqo-agent executable, packages its platform ZIP,
    applies Authenticode signing on Windows, and builds or pushes cmdiqo-bot
    Docker images.

.PARAMETER Target
    Component to build: 'Agent', 'Installer', 'Bot', or 'All'. Installer
    builds the agent executable and ZIP package, then the matching native setup.

.PARAMETER Installer
    Shortcut for the standalone agent executable, ZIP package, and native setup.
#>

[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CliArgs
)

$ErrorActionPreference = "Stop"
$Target = $null
$Agent = $false
$Bot = $false
$Installer = $false
$All = $false
$Version = $null
$Dev = $false
$SkipSign = $false
$CopyTo = ""
$Clean = $false
$SkipAgentBuild = $false
$RebuildBuilder = $false
$NoRebuildBuilder = if ($env:NO_REBUILD_BUILDER -eq "true" -or $env:NO_REBUILD_BUILDER -eq "1") { $true } else { $false }
$Push = $false
$DockerUser = if ($env:DOCKERHUB_USERNAME) { $env:DOCKERHUB_USERNAME } else { "lhotakj" }
$Platform = "win64"
$Help = $false

for ($index = 0; $index -lt $CliArgs.Count; $index++) {
    $argument = $CliArgs[$index]

    function Get-OptionValue {
        param([string]$OptionName)
        if ($index + 1 -ge $CliArgs.Count) {
            throw "Option $OptionName requires a value."
        }
        $script:index++
        return $CliArgs[$script:index]
    }

    switch -Regex ($argument) {
        "^--platform$|^-platform$|^-p$" { $Platform = Get-OptionValue $argument; break }
        "^--platform=(.+)$|^-platform=(.+)$" { $Platform = $argument.Substring($argument.IndexOf("=") + 1); break }
        "^--agent$|^-agent$|^-a$" { $Agent = $true; break }
        "^--installer$|^-installer$|^-i$" { $Installer = $true; break }
        "^--bot$|^-bot$|^-b$|^--docker$|^-docker$" { $Bot = $true; break }
        "^--all$|^-all$" { $All = $true; break }
        "^--target$|^-target$" { $Target = Get-OptionValue $argument; break }
        "^--target=(.+)$|^-target=(.+)$" { $Target = $argument.Substring($argument.IndexOf("=") + 1); break }
        "^--clean$|^-clean$" { $Clean = $true; break }
        "^--skip-agent-build$|^-skipagentbuild$" { $SkipAgentBuild = $true; break }
        "^--rebuild-builder$|^-rebuild-builder$|^-rebuildbuilder$|^--rebuild-image$|^-rebuild-image$" { $RebuildBuilder = $true; $NoRebuildBuilder = $false; break }
        "^--no-rebuild-builder$|^-no-rebuild-builder$|^-norebuildbuilder$|^--no-build-builder$|^-no-build-builder$|^-nobuildbuilder$|^--skip-builder-build$|^-skip-builder-build$|^-skipbuilderbuild$" { $NoRebuildBuilder = $true; $RebuildBuilder = $false; break }
        "^--skip-sign$|^-skip-sign$|^-skipsign$" { $SkipSign = $true; break }
        "^--copy-to$|^-copy-to$|^-copyto$" { $CopyTo = Get-OptionValue $argument; break }
        "^--copy-to=(.+)$|^-copy-to=(.+)$|^-copyto=(.+)$" { $CopyTo = $argument.Substring($argument.IndexOf("=") + 1); break }
        "^--push$|^-push$" { $Push = $true; $Bot = $true; break }
        "^--docker-user$|^-docker-user$|^-dockeruser$" { $DockerUser = Get-OptionValue $argument; break }
        "^--docker-user=(.+)$|^-docker-user=(.+)$|^-dockeruser=(.+)$" { $DockerUser = $argument.Substring($argument.IndexOf("=") + 1); break }
        "^--dev$|^-dev$" { $Dev = $true; break }
        "^--version$|^-version$|^-v$" { $Version = Get-OptionValue $argument; break }
        "^--version=(.+)$|^-version=(.+)$" { $Version = $argument.Substring($argument.IndexOf("=") + 1); break }
        "^--help$|^-help$|^-h$|^-\?$" { $Help = $true; break }
        default { throw "Unknown argument '$argument'. Run with --help for available options." }
    }
}

$Platform = $Platform.ToLowerInvariant()
switch ($Platform) {
    "linux" { $Platform = "linux64" }
    "windows" { $Platform = "win64" }
    "win" { $Platform = "win64" }
}
if ($Platform -notin @("win64", "linux64", "all")) {
    throw "Invalid platform '$Platform'. Expected win64, linux64, or all."
}
if ($Target) {
    $Target = $Target.ToLower()
    if ($Target -notin @("agent", "installer", "bot", "all")) {
        throw "Invalid target '$Target'. Expected agent, installer, bot, or all."
    }
}

function Write-Info($msg) {
    Write-Host "[INFO] $msg" -ForegroundColor Green
}

function Write-Warn($msg) {
    Write-Host "[WARN] $msg" -ForegroundColor Yellow
}

function Write-Err($msg) {
    Write-Host "[ERROR] $msg" -ForegroundColor Red
}

function Remove-GeneratedFile([string]$Path) {
    # File.Delete is idempotent, so an artifact removed between discovery and cleanup is harmless.
    [System.IO.File]::Delete($Path)
}

function Show-Usage {
    Write-Host @"
Usage: .\build.ps1 [options]

Build Targets:
  -Agent, --agent          Build standalone agent executable and ZIP package (default)
  -Installer, --installer  Build the agent executable, ZIP package, and native setup
  -Bot, --bot, --docker    Build Docker container image for cmdiqo-bot
  -All, --all              Build the agent package, native setup, and bot container
  -Target <string>         Explicit target: 'agent', 'installer', 'bot', or 'all'

Platform Selection:
  -Platform, --platform <str>  Target platform: 'win64', 'linux64', or 'all' (default: win64)

Versioning:
  -Version, --version <str>    Override version string (e.g. '1.2.0')
  -Dev, --dev                  Tag version as development build (<major>.<minor>.devel+<HEX>)

Compilation & Packaging:
  -Clean, --clean              Clean only selected agent and native installer build outputs
  -SkipAgentBuild, --skip-agent-build
                               With -Installer, reuse the existing agent ZIP and build only the native setup
  -SkipSign, --skip-sign       Skip Authenticode signing on Windows
  -CopyTo, --copy-to <path>    Copy compiled binary to destination path

Docker Options:
  -Push, --push                Build and push multi-arch Docker images
  -DockerUser, --docker-user   Docker Hub registry username
  -RebuildBuilder, --rebuild-builder
                               Force rebuilding the C++ builder Docker image locally
  -NoRebuildBuilder, --no-rebuild-builder
                               Do not build or rebuild the C++ builder image; reuse existing or pulled image

Examples:
  .\build.ps1 -Agent
  .\build.ps1 -Installer                  # Windows agent ZIP and native setup (Release)
  .\build.ps1 -Installer -SkipAgentBuild  # Rebuild only the native setup
  .\build.ps1 -Installer -Platform Linux  # Linux agent ZIP and native setup via Docker
  .\build.ps1 -Target Installer
  .\build.ps1 -All -Clean
"@ -ForegroundColor Cyan
}

if ($Help) {
    Show-Usage
    exit 0
}

$rootDir = $PSScriptRoot
Set-Location $rootDir

$buildAgent = $false
$buildInstaller = $false
$buildBot = $false
if ($All -or ($Target -eq "all")) {
    $buildAgent = $true
    $buildInstaller = $true
    $buildBot = $true
} elseif ($Bot -or ($Target -eq "bot")) {
    $buildBot = $true
} elseif ($Installer -or ($Target -eq "installer")) {
    $buildAgent = $true
    $buildInstaller = $true
} elseif ($Agent -or ($Target -eq "agent")) {
    $buildAgent = $true
} else {
    $buildAgent = $true
}

if ($SkipAgentBuild) {
    if (-not $buildInstaller -or $buildBot) {
        throw "-SkipAgentBuild is only valid with -Installer."
    }
    $buildAgent = $false
}

function Get-PayloadVersion {
    param([string]$PayloadArchive)

    if (Test-Path $PayloadArchive -PathType Leaf) {
        if ($PayloadArchive.EndsWith(".zip")) {
            $payloadVersionOutput = & python -c "import sys, zipfile; archive = zipfile.ZipFile(sys.argv[1]); name = 'version.txt'; sys.exit('Payload is missing version.txt.') if name not in archive.namelist() else print(archive.read(name).decode('utf-8').strip())" $PayloadArchive
            if ($LASTEXITCODE -ne 0) {
                throw "Unable to read version.txt from the agent payload. Rebuild it with .\build.ps1 -Agent."
            }
            $payloadVersion = ([string]$payloadVersionOutput).Trim()
            if ($payloadVersion -notmatch '^\d+\.\d+\.(\d+|devel\+[0-9A-Fa-f]+|[0-9A-Fa-f]{7,40})$') {
                throw "The agent payload has an invalid or missing version.txt. Rebuild it with .\build.ps1 -Agent."
            }
            return $payloadVersion
        }
    }
    $buildVersionFile = Join-Path $rootDir "dist\build-version.txt"
    if (Test-Path $buildVersionFile -PathType Leaf) {
        $payloadVersion = ([string](Get-Content $buildVersionFile -Raw)).Trim()
        if ($payloadVersion -match '^\d+\.\d+\.(\d+|devel\+[0-9A-Fa-f]+|[0-9A-Fa-f]{7,40})$') {
            return $payloadVersion
        }
    }
    if ($currentVersion) {
        return $currentVersion
    }
    $versionFile = Join-Path $rootDir "version.txt"
    if (Test-Path $versionFile -PathType Leaf) {
        return ([string](Get-Content $versionFile -Raw)).Trim()
    }
    throw "Native installer packaging requires $PayloadArchive."
}

function Ensure-InstallerBuilderImage {
    param(
        [string]$TargetStage,
        [string]$LocalImage,
        [string]$RemoteImage,
        [string]$InstallerRoot
    )

    $dockerfile = Join-Path $InstallerRoot "Dockerfile"
    $manifest = Join-Path $InstallerRoot "vcpkg.json"
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $triplets = Get-ChildItem -Path (Join-Path $InstallerRoot "triplets") -File -Recurse |
            Sort-Object FullName |
            Select-Object -ExpandProperty FullName
        foreach ($path in @($dockerfile, $manifest) + @($triplets)) {
            $text = [System.IO.File]::ReadAllText($path).Replace("`r`n", "`n")
            $bytes = [System.Text.Encoding]::UTF8.GetBytes($text)
            [void]$sha256.TransformBlock($bytes, 0, $bytes.Length, $bytes, 0)
        }
        [void]$sha256.TransformFinalBlock([byte[]]::new(0), 0, 0)
        $buildInputHash = ([System.BitConverter]::ToString($sha256.Hash) -replace '-', '').ToLowerInvariant()
    }
    finally {
        $sha256.Dispose()
    }

    # PowerShell 5 must let redirected native stderr reach the exit-code checks.
    $ErrorActionPreference = "Continue"

    # 1. Check local image
    $localLabels = & docker image inspect --format '{{json .Config.Labels}}' $LocalImage 2>$null
    if ($LASTEXITCODE -eq 0) {
        $localObj = ($localLabels | ConvertFrom-Json -ErrorAction SilentlyContinue)
        $localHash = if ($localObj) { $localObj.'io.cmdiqo.installer-builder.build-input-sha256' } else { $null }
        if ($localHash -eq $buildInputHash) {
            Write-Info "Pre-built builder image ($LocalImage) matches source checksum."
            return
        }
        if ($localHash -and ($localHash -ne "") -and -not $RebuildBuilder) {
            Write-Info "Found existing local builder image ($LocalImage); reusing without rebuilding."
            return
        }
    }

    # 2. Check remote-tagged image
    $remoteLabels = & docker image inspect --format '{{json .Config.Labels}}' $RemoteImage 2>$null
    if ($LASTEXITCODE -eq 0) {
        $remoteObj = ($remoteLabels | ConvertFrom-Json -ErrorAction SilentlyContinue)
        $remoteHash = if ($remoteObj) { $remoteObj.'io.cmdiqo.installer-builder.build-input-sha256' } else { $null }
        if ($remoteHash -eq $buildInputHash) {
            & docker tag $RemoteImage $LocalImage 2>$null
            if ($LASTEXITCODE -ne 0) {
                throw "Failed to tag native installer builder $RemoteImage as $LocalImage."
            }
            return
        }
        if ($remoteHash -and ($remoteHash -ne "") -and -not $RebuildBuilder) {
            Write-Info "Found existing remote builder image ($RemoteImage); tagging as $LocalImage and reusing."
            & docker tag $RemoteImage $LocalImage 2>$null
            if ($LASTEXITCODE -ne 0) {
                throw "Failed to tag native installer builder $RemoteImage as $LocalImage."
            }
            return
        }
    }

    # 3. Try pulling from Docker Hub
    if (-not $RebuildBuilder) {
        Write-Info "Attempting to pull pre-built builder image ($RemoteImage) from Docker Hub..."
        & docker pull $RemoteImage 2>$null
        if ($LASTEXITCODE -eq 0) {
            $pulledLabels = & docker image inspect --format '{{json .Config.Labels}}' $RemoteImage 2>$null
            if ($LASTEXITCODE -eq 0) {
                $pulledHash = ($pulledLabels | ConvertFrom-Json -ErrorAction Stop).'io.cmdiqo.installer-builder.build-input-sha256'
                if ($pulledHash -eq $buildInputHash) {
                    Write-Info "Pre-built builder image ($RemoteImage) is ready."
                    & docker tag $RemoteImage $LocalImage 2>$null
                    if ($LASTEXITCODE -ne 0) {
                        throw "Failed to tag native installer builder $RemoteImage as $LocalImage."
                    }
                    return
                }
                if ($NoRebuildBuilder) {
                    Write-Warn "Pulled builder image ($RemoteImage) checksum differs, but builder rebuild is disabled; reusing pulled image."
                    & docker tag $RemoteImage $LocalImage 2>$null
                    if ($LASTEXITCODE -ne 0) {
                        throw "Failed to tag native installer builder $RemoteImage as $LocalImage."
                    }
                    return
                }
                Write-Warn "Pulled builder image does not match the Dockerfile, manifest, and triplets; rebuilding locally."
            }
        } else {
            if ($NoRebuildBuilder) {
                throw "Pre-built builder image ($RemoteImage) could not be pulled and rebuilding is disabled (--no-rebuild-builder)."
            }
            Write-Warn "Pre-built builder image is unavailable; rebuilding locally."
        }
    }

    # 4. Fallback: Build locally
    if ($NoRebuildBuilder) {
        throw "Builder image ($LocalImage) is missing or mismatched and rebuilding is disabled (--no-rebuild-builder)."
    }
    Write-Info "=== Building native installer Docker image locally ($LocalImage, target: $TargetStage) ==="
    & docker buildx build --load --platform linux/amd64 --tag $LocalImage --tag $RemoteImage --file $dockerfile --target $TargetStage --build-arg "BUILDER_INPUT_SHA=$buildInputHash" $InstallerRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Native installer Docker image build failed ($LocalImage)."
    }
}

function Invoke-NativeWindowsInstaller {
    param([string]$PayloadArchive)

    $installerRoot = Join-Path $rootDir "installer.c"
    $vcpkgExe = Join-Path $installerRoot ".vcpkg\vcpkg.exe"
    $hasLocalVcpkg = (Test-Path $vcpkgExe -PathType Leaf)
    $hasCmake = (Get-Command cmake -ErrorAction SilentlyContinue)
    $hasDocker = (Get-Command docker -ErrorAction SilentlyContinue)

    $payloadVersion = Get-PayloadVersion $PayloadArchive

    if ($hasLocalVcpkg -and $hasCmake) {
        $buildDirectory = Join-Path $installerRoot "out\build\windows-x64"
        if ($Clean -and (Test-Path $buildDirectory)) {
            Remove-Item -Recurse -Force $buildDirectory
        }

        Write-Info "=== Building native Windows installer (MSVC, Release) ==="
        $previousVcpkgRoot = $env:VCPKG_ROOT
        $env:VCPKG_ROOT = Join-Path $installerRoot ".vcpkg"
        Push-Location $installerRoot
        try {
            cmake --fresh --preset windows-x64 "-DCMDIQO_BUILD_VERSION=$payloadVersion"
            if ($LASTEXITCODE -ne 0) {
                throw "Native Windows installer configuration failed."
            }
            cmake --build --preset build-windows-x64 --config Release
            if ($LASTEXITCODE -ne 0) {
                throw "Native Windows installer compilation failed."
            }
            ctest --preset test-windows-x64 -C Release --output-on-failure
            if ($LASTEXITCODE -ne 0) {
                throw "Native Windows installer tests failed."
            }
            $sourceExe = Join-Path $buildDirectory "cmdiqo-agent-setup.exe"
            if (-not (Test-Path $sourceExe)) {
                $sourceExe = Join-Path $buildDirectory "Release\cmdiqo-agent-setup.exe"
            }
            Copy-Item -LiteralPath $sourceExe -Destination (Join-Path $rootDir "dist\cmdiqo-agent-windows-x64-setup.exe") -Force
        }
        finally {
            $env:VCPKG_ROOT = $previousVcpkgRoot
            Pop-Location
        }
    } elseif ($hasDocker) {
        & docker info *> $null
        if ($LASTEXITCODE -ne 0) {
            throw "Docker Desktop was found but its daemon is unavailable. Start Docker Desktop and retry."
        }

        $localImage = "cmdiqo-installer-builder:all"
        $remoteImage = "${DockerUser}/cmdiqo-installer-builder:all"
        $buildDirectory = Join-Path $installerRoot "out\build\windows-x64-mingw"
        if ($Clean -and (Test-Path $buildDirectory)) {
            Remove-Item -Recurse -Force $buildDirectory
        }

        Ensure-InstallerBuilderImage -TargetStage "all" -LocalImage $localImage -RemoteImage $remoteImage -InstallerRoot $installerRoot

        Write-Info "=== Building native Windows installer (MinGW-w64 in Docker, Release) ==="
        $command = @'
set -euo pipefail
export TMPDIR="/tmp/cmdiqo-tmp-$$"
export WINEPREFIX="/tmp/cmdiqo-wine-$$"
install --directory --mode=700 "$TMPDIR" "$WINEPREFIX"
cmake --fresh --preset windows-x64-mingw -DVCPKG_MANIFEST_INSTALL=OFF "-DVCPKG_INSTALLED_DIR=${VCPKG_INSTALLED_DIR_WINDOWS}" "-DCMDIQO_BUILD_VERSION=${CMDIQO_BUILD_VERSION}"
cmake --build --preset build-windows-x64-mingw --config Release
ctest --preset test-windows-x64-mingw -C Release --output-on-failure
'@
        & docker run --rm `
            --platform linux/amd64 `
            --workdir /workspace/installer.c `
            --volume "${rootDir}:/workspace" `
            --env HOME=/tmp `
            --env WINEDEBUG=-all `
            --env "CMDIQO_BUILD_VERSION=$payloadVersion" `
            $localImage bash -c $command
        if ($LASTEXITCODE -ne 0) {
            throw "Native Windows installer build failed."
        }
        Copy-Item -LiteralPath (Join-Path $buildDirectory "cmdiqo-agent-setup.exe") -Destination (Join-Path $rootDir "dist\cmdiqo-agent-windows-x64-setup.exe") -Force
    } else {
        throw "Native installer dependencies are missing. Configure local MSVC with vcpkg, or start Docker Desktop and run .\installer.c\setup-env.ps1."
    }
    Write-Info "Native installer: $(Join-Path $rootDir 'dist\cmdiqo-agent-windows-x64-setup.exe')"
}

function Invoke-NativeLinuxInstaller {
    param([string]$PayloadArchive)

    $installerRoot = Join-Path $rootDir "installer.c"
    $localImage = "cmdiqo-installer-builder:all"
    $remoteImage = "${DockerUser}/cmdiqo-installer-builder:all"
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "Docker Desktop is required to build the native Linux installer."
    }
    & docker info *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Desktop is installed but its daemon is unavailable. Start Docker Desktop and retry."
    }

    $payloadVersion = Get-PayloadVersion $PayloadArchive
    $buildDirectory = Join-Path $installerRoot "out\build\linux-x64"
    $buildDirectoryCli = Join-Path $installerRoot "out\build\linux-x64-cli"
    if ($Clean) {
        if (Test-Path $buildDirectory) { Remove-Item -Recurse -Force $buildDirectory }
        if (Test-Path $buildDirectoryCli) { Remove-Item -Recurse -Force $buildDirectoryCli }
    }

    Ensure-InstallerBuilderImage -TargetStage "all" -LocalImage $localImage -RemoteImage $remoteImage -InstallerRoot $installerRoot

    Write-Info "=== Building native Linux installer (Release) ==="
    $command = @'
set -euo pipefail
export TMPDIR="/tmp/cmdiqo-tmp-$$"
export WINEPREFIX="/tmp/cmdiqo-wine-$$"
install --directory --mode=700 "$TMPDIR" "$WINEPREFIX"
cmake --fresh --preset linux-x64 -DVCPKG_MANIFEST_INSTALL=OFF "-DVCPKG_INSTALLED_DIR=${VCPKG_INSTALLED_DIR_LINUX}" "-DCMDIQO_BUILD_VERSION=${CMDIQO_BUILD_VERSION}"
cmake --build --preset build-linux-x64 --config Release
ctest --preset test-linux-x64 -C Release --output-on-failure
cmake --fresh --preset linux-x64-cli -DVCPKG_MANIFEST_INSTALL=OFF "-DVCPKG_INSTALLED_DIR=${VCPKG_INSTALLED_DIR_LINUX}" "-DCMDIQO_BUILD_VERSION=${CMDIQO_BUILD_VERSION}"
cmake --build --preset build-linux-x64-cli --config Release
ctest --preset test-linux-x64-cli -C Release --output-on-failure
'@
    & docker run --rm `
        --platform linux/amd64 `
        --workdir /workspace/installer.c `
        --volume "${rootDir}:/workspace" `
        --env HOME=/tmp `
        --env "CMDIQO_BUILD_VERSION=$payloadVersion" `
        $localImage bash -c $command
    if ($LASTEXITCODE -ne 0) {
        throw "Native Linux installer build failed."
    }
    Copy-Item -LiteralPath (Join-Path $buildDirectory "cmdiqo-agent-setup") -Destination (Join-Path $rootDir "dist\cmdiqo-agent-linux-x64-setup") -Force
    Copy-Item -LiteralPath (Join-Path $buildDirectoryCli "cmdiqo-agent-setup") -Destination (Join-Path $rootDir "dist\cmdiqo-agent-linux-x64-setup-cli") -Force
    Write-Info "Native installer: $(Join-Path $rootDir 'dist\cmdiqo-agent-linux-x64-setup')"
    Write-Info "Native installer (CLI): $(Join-Path $rootDir 'dist\cmdiqo-agent-linux-x64-setup-cli')"
}

$buildWin = ($Platform -eq "win64" -or $Platform -eq "all")
$buildLinux = ($Platform -eq "linux64" -or $Platform -eq "all")
if ($CopyTo -and (-not $buildAgent -or $Platform -eq "all")) {
    throw "-CopyTo requires an agent build for a single platform."
}
if ($buildAgent -and $buildWin) {
    $pythonPlatformOutput = & python -c "import platform, struct; print(platform.system().lower() + ':' + platform.machine().lower() + ':' + str(struct.calcsize('P') * 8))"
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to identify the Python platform for the Windows x64 agent build."
    }
    $pythonPlatform = ([string]$pythonPlatformOutput).Trim()
    if ($pythonPlatform -notmatch '^windows:(amd64|x86_64):64$') {
        throw "Windows x64 agent builds require native Windows x64 Python (detected '$pythonPlatform')."
    }
}
if ($Clean) {
    Write-Info "Cleaning selected build artifacts ..."
    $artifacts = @()
    if ($buildAgent) {
        $agentBuildDirectory = Join-Path $rootDir "build\cmdiqo-agent"
        if (Test-Path $agentBuildDirectory -PathType Container) {
            Remove-Item -LiteralPath $agentBuildDirectory -Recurse -Force
        }
        if ($buildWin) {
            $artifacts += @("cmdiqo-agent.exe", "cmdiqo-agent-windows-x64.zip")
        }
        if ($buildLinux) {
            $artifacts += @("cmdiqo-agent", "cmdiqo-agent-linux-x64.zip")
        }
    }
    if ($buildInstaller -and $buildWin) {
        $artifacts += "cmdiqo-agent-windows-x64-setup.exe"
    }
    if ($buildInstaller -and $buildLinux) {
        $artifacts += "cmdiqo-agent-linux-x64-setup"
    }
    foreach ($artifact in $artifacts) {
        $artifactPath = Join-Path $rootDir "dist\$artifact"
        Remove-GeneratedFile $artifactPath
    }
}

$versionArgs = @()
if ($Version) {
    $versionArgs += @("--version", $Version)
} elseif ($Dev) {
    $versionArgs += "--dev"
}
$currentVersionOutput = & python "$rootDir\development\update_version.py" @versionArgs --print-version
if ($LASTEXITCODE -ne 0) {
    Write-Err "Failed to synchronize version."
    exit 1
}
$currentVersion = ([string]$currentVersionOutput).Trim()
if ([string]::IsNullOrWhiteSpace($currentVersion)) {
    Write-Err "Failed to synchronize version."
    exit 1
}

$parts = $currentVersion.Split('.')
$shortVersion = "$($parts[0]).$($parts[1])"
Write-Info "Building target version: $currentVersion (Short: $shortVersion)"
Write-Info "Platform selection: $Platform (win64: $buildWin, linux64: $buildLinux)"

if ($buildAgent -and $buildWin) {
    Write-Info "=== Building standalone cmdiqo-agent Windows executable ==="
    $buildArgs = @("$rootDir\development\build_agent.py", "--version", $currentVersion)
    if ($SkipSign) { $buildArgs += "--skip-sign" }
    if ($CopyTo) { $buildArgs += @("--copy-to", $CopyTo) }

    $windowsAgentArtifacts = @(
        (Join-Path $rootDir "dist\cmdiqo-agent.exe"),
        (Join-Path $rootDir "dist\cmdiqo-agent-windows-x64.zip")
    )
    foreach ($artifact in $windowsAgentArtifacts) {
        Remove-GeneratedFile $artifact
    }
    python @buildArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Agent build failed."
        exit 1
    }
    foreach ($artifact in $windowsAgentArtifacts) {
        if (-not (Test-Path $artifact -PathType Leaf)) {
            throw "Windows agent build did not produce $artifact."
        }
    }
}

if ($buildAgent -and $buildLinux) {
    $dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $dockerCmd) {
        throw "Building linux64 binaries on Windows requires Docker Desktop. Start Docker or run build.sh on Linux."
    } else {
        & docker info *> $null
        if ($LASTEXITCODE -ne 0) {
            throw "Docker Desktop was found but its daemon is unavailable. Start Docker Desktop and retry."
        }
        Write-Info "=== Building Linux agent in Debian trixie container via Docker ==="
        docker buildx build --load --platform linux/amd64 -t cmdiqo-builder-linux:trixie -f "$rootDir\docker\Dockerfile.builder-linux" "$rootDir"
        if ($LASTEXITCODE -ne 0) {
            throw "Linux Docker builder image creation failed."
        }

        $linuxAgentArtifacts = @(
            (Join-Path $rootDir "dist\cmdiqo-agent"),
            (Join-Path $rootDir "dist\cmdiqo-agent-linux-x64.zip")
        )
        foreach ($artifact in $linuxAgentArtifacts) {
            Remove-GeneratedFile $artifact
        }
        $linuxBashScript = 'python3 development/build_agent.py --version "$CMDIQO_BUILD_VERSION"'
        docker run --rm --platform linux/amd64 -v "${rootDir}:/workspace" -w /workspace -e HOME=/tmp -e "CMDIQO_BUILD_VERSION=$currentVersion" cmdiqo-builder-linux:trixie bash -c $linuxBashScript
        if ($LASTEXITCODE -ne 0) {
            throw "Linux agent build failed."
        }
        foreach ($artifact in $linuxAgentArtifacts) {
            if (-not (Test-Path $artifact -PathType Leaf)) {
                throw "Linux agent build did not produce $artifact."
            }
        }
        if ($CopyTo) {
            $copyDestination = if (Test-Path $CopyTo -PathType Container) {
                Join-Path $CopyTo "cmdiqo-agent"
            } else {
                $CopyTo
            }
            $copyDestination = [System.IO.Path]::GetFullPath($copyDestination)
            $copyDirectory = Split-Path -Parent $copyDestination
            [void](New-Item -ItemType Directory -Force -Path $copyDirectory)
            if ($copyDestination -ne $linuxAgentArtifacts[0]) {
                Copy-Item -LiteralPath $linuxAgentArtifacts[0] -Destination $copyDestination -Force
            }
            $copyArchive = Join-Path $copyDirectory "cmdiqo-agent-linux-x64.zip"
            if ($copyArchive -ne $linuxAgentArtifacts[1]) {
                Copy-Item -LiteralPath $linuxAgentArtifacts[1] -Destination $copyArchive -Force
            }
        }
    }
}

if ($buildInstaller -and $buildWin) {
    Invoke-NativeWindowsInstaller (Join-Path $rootDir "dist\cmdiqo-agent-windows-x64.zip")
}
if ($buildInstaller -and $buildLinux) {
    Invoke-NativeLinuxInstaller (Join-Path $rootDir "dist\cmdiqo-agent-linux-x64.zip")
}

if ($buildBot) {
    Write-Info "=== Building Docker image for cmdiqo-bot ==="
    $dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $dockerCmd) {
        throw "Docker CLI is required to build the bot container."
    } else {
        & docker info *> $null
        if ($LASTEXITCODE -ne 0) {
            throw "Docker Desktop was found but its daemon is unavailable. Start Docker Desktop and retry."
        }
        $timestamp = (Get-Date -Format "yyyyMMddHHmmss")
        $imgLatest = "$DockerUser/cmdiqo-bot:latest"
        $imgShort = "$DockerUser/cmdiqo-bot:$shortVersion"
        $dockerVersion = $currentVersion.Replace('+', '-')
        $imgFull = "$DockerUser/cmdiqo-bot:$dockerVersion"
        $imgTs = "$DockerUser/cmdiqo-bot:$timestamp"

        if ($Push) {
            Write-Info "Building and pushing multi-arch images ($imgFull) via buildx ..."
            docker buildx build `
                --platform linux/amd64,linux/arm64 `
                --no-cache `
                -t $imgLatest `
                -t $imgShort `
                -t $imgFull `
                -t $imgTs `
                -f docker/Dockerfile `
                --push `
                .
        } else {
            Write-Info "Building local container image ($imgFull) ..."
            docker buildx build --load `
                -t $imgLatest `
                -t $imgShort `
                -t $imgFull `
                -t $imgTs `
                -f docker/Dockerfile `
                .
        }
        if ($LASTEXITCODE -ne 0) {
            throw "Docker build failed."
        }
    }
}

function Show-AgentArtifact([string]$path, [string]$label) {
    if (Test-Path $path -PathType Leaf) {
        $size = [math]::Round((Get-Item $path).Length / 1MB, 2)
        $hash = (Get-FileHash -Path $path -Algorithm SHA256).Hash
        Write-Info "  • ${label}: $path ($size MB)"
        Write-Info "    SHA256: $hash"
    } else {
        Write-Warn "  • ${label}: Missing"
    }
}

Write-Host ""
Write-Info "=== Distribution Agent Artifacts (dist\) ==="
if (($buildAgent -or $buildInstaller) -and $buildWin) {
    if ($buildAgent) {
        Show-AgentArtifact "$rootDir\dist\cmdiqo-agent.exe" "Windows agent binary"
    }
    Show-AgentArtifact "$rootDir\dist\cmdiqo-agent-windows-x64.zip" "Windows agent ZIP"
    if ($buildInstaller) {
        Show-AgentArtifact "$rootDir\dist\cmdiqo-agent-windows-x64-setup.exe" "Windows native installer"
    }
    if ($buildAgent -and -not (Test-Path "$rootDir\dist\cmdiqo-agent.exe" -PathType Leaf)) {
        throw "Requested Windows agent binary was not produced."
    }
    if (-not (Test-Path "$rootDir\dist\cmdiqo-agent-windows-x64.zip" -PathType Leaf)) {
        throw "Requested Windows agent ZIP package was not produced."
    }
}
if (($buildAgent -or $buildInstaller) -and $buildLinux) {
    if ($buildAgent) {
        Show-AgentArtifact "$rootDir\dist\cmdiqo-agent" "Linux agent binary"
    }
    Show-AgentArtifact "$rootDir\dist\cmdiqo-agent-linux-x64.zip" "Linux agent ZIP"
    if ($buildInstaller) {
        Show-AgentArtifact "$rootDir\dist\cmdiqo-agent-linux-x64-setup" "Linux native installer"
    }
    if ($buildAgent -and -not (Test-Path "$rootDir\dist\cmdiqo-agent" -PathType Leaf)) {
        throw "Requested Linux agent binary was not produced. Docker Desktop must be running."
    }
    if (-not (Test-Path "$rootDir\dist\cmdiqo-agent-linux-x64.zip" -PathType Leaf)) {
        throw "Requested Linux agent ZIP package was not produced. Docker Desktop must be running."
    }
}

Write-Info "[+] Build complete successfully for version $currentVersion!"
