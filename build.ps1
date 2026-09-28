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
$Push = $false
$DockerUser = if ($env:DOCKERHUB_USERNAME) { $env:DOCKERHUB_USERNAME } else { "lhotakj" }
$Platform = "win64"
$Config = "Release"
$Debug = $false
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
        "^--config$|^-config$|^--configuration$|^-configuration$" { $Config = Get-OptionValue $argument; break }
        "^--config=(.+)$|^-config=(.+)$|^--configuration=(.+)$|^-configuration=(.+)$" { $Config = $argument.Substring($argument.IndexOf("=") + 1); break }
        "^--debug$|^-debug$|^-d$" { $Debug = $true; $Config = "Debug"; break }
        "^--release$|^-release$" { $Config = "Release"; break }
        "^--agent$|^-agent$|^-a$" { $Agent = $true; break }
        "^--installer$|^-installer$|^-i$" { $Installer = $true; break }
        "^--bot$|^-bot$|^-b$|^--docker$|^-docker$" { $Bot = $true; break }
        "^--all$|^-all$" { $All = $true; break }
        "^--target$|^-target$" { $Target = Get-OptionValue $argument; break }
        "^--target=(.+)$|^-target=(.+)$" { $Target = $argument.Substring($argument.IndexOf("=") + 1); break }
        "^--clean$|^-clean$" { $Clean = $true; break }
        "^--skip-agent-build$|^-skipagentbuild$" { $SkipAgentBuild = $true; break }
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

$Platform = $Platform.ToLower()
if ($Platform -notin @("win64", "linux64", "all")) {
    throw "Invalid platform '$Platform'. Expected win64, linux64, or all."
}
switch ($Config.ToLowerInvariant()) {
    "debug" { $Config = "Debug"; break }
    "release" { $Config = "Release"; break }
    "relwithdebinfo" { $Config = "RelWithDebInfo"; break }
    "minsizerel" { $Config = "MinSizeRel"; break }
    default { throw "Invalid build configuration '$Config'. Expected Release or Debug." }
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
  -Dev, --dev                  Tag version as development build (<major>.<minor>.dev<HEX>)

Compilation & Packaging:
  -Config, --config <str>      Build configuration for native setup: 'Release' (default) or 'Debug'
  -Debug, --debug, -d          Shortcut for -Config Debug (unstripped, debug symbols)
  -Release, --release          Shortcut for -Config Release
  -Clean, --clean              Clean build, dist, and selected native installer outputs before building
  -SkipAgentBuild, --skip-agent-build
                               With -Installer, reuse the existing agent ZIP and build only the native setup
  -SkipSign, --skip-sign       Skip Authenticode signing on Windows
  -CopyTo, --copy-to <path>    Copy compiled binary to destination path

Docker Options:
  -Push, --push                Build and push multi-arch Docker images
  -DockerUser, --docker-user   Docker Hub registry username

Examples:
  .\build.ps1 -Agent
  .\build.ps1 -Installer                  # Windows agent ZIP and native setup (Release)
  .\build.ps1 -Installer -Debug           # Native setup with Debug symbols
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

    if (-not (Test-Path $PayloadArchive -PathType Leaf)) {
        throw "Native installer packaging requires $PayloadArchive."
    }
    $payloadVersion = (& python -c "import sys, zipfile; archive = zipfile.ZipFile(sys.argv[1]); name = 'version.txt'; sys.exit('Payload is missing version.txt.') if name not in archive.namelist() else print(archive.read(name).decode('utf-8').strip())" $PayloadArchive).Trim()
    if ($LASTEXITCODE -ne 0 -or $payloadVersion -notmatch '^\d+\.\d+\.(devel\+[0-9A-F]+|[0-9A-Fa-f]{7,40})$') {
        throw "The agent payload has an invalid or missing version.txt. Rebuild it with .\build.ps1 -Agent."
    }
    return $payloadVersion
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
        foreach ($path in @($dockerfile, $manifest)) {
            $bytes = [System.IO.File]::ReadAllBytes($path)
            [void]$sha256.TransformBlock($bytes, 0, $bytes.Length, $bytes, 0)
        }
        [void]$sha256.TransformFinalBlock([byte[]]::new(0), 0, 0)
        $buildInputHash = ([System.BitConverter]::ToString($sha256.Hash) -replace '-', '').ToLowerInvariant()
    }
    finally {
        $sha256.Dispose()
    }

    # 1. Check local image hash
    $localHash = & docker image inspect --format '{{ index .Config.Labels "io.cmdiqo.installer-builder.build-input-sha256" }}' $LocalImage 2>$null
    if ($LASTEXITCODE -eq 0 -and $localHash -eq $buildInputHash) {
        return
    }

    # 2. Check remote-tagged image hash
    $remoteHash = & docker image inspect --format '{{ index .Config.Labels "io.cmdiqo.installer-builder.build-input-sha256" }}' $RemoteImage 2>$null
    if ($LASTEXITCODE -eq 0 -and $remoteHash -eq $buildInputHash) {
        & docker tag $RemoteImage $LocalImage 2>$null
        return
    }

    # 3. Try pulling from Docker Hub
    Write-Info "Attempting to pull pre-built builder image ($RemoteImage) from Docker Hub..."
    & docker pull $RemoteImage 2>$null
    if ($LASTEXITCODE -eq 0) {
        $pulledHash = & docker image inspect --format '{{ index .Config.Labels "io.cmdiqo.installer-builder.build-input-sha256" }}' $RemoteImage 2>$null
        if ($LASTEXITCODE -eq 0 -and ($pulledHash -eq $buildInputHash -or -not $pulledHash)) {
            Write-Info "Pre-built builder image ($RemoteImage) is ready."
            & docker tag $RemoteImage $LocalImage 2>$null
            return
        }
    }

    # 4. Fallback: Build locally
    Write-Info "=== Building native installer Docker image locally ($LocalImage, target: $TargetStage) ==="
    & docker build --tag $LocalImage --tag $RemoteImage --file $dockerfile --target $TargetStage --build-arg "BUILDER_INPUT_SHA=$buildInputHash" $InstallerRoot
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

        Write-Info "=== Building native Windows installer (MSVC, $Config) ==="
        $previousVcpkgRoot = $env:VCPKG_ROOT
        $env:VCPKG_ROOT = Join-Path $installerRoot ".vcpkg"
        Push-Location $installerRoot
        try {
            cmake --fresh --preset windows-x64 "-DCMAKE_BUILD_TYPE=$Config" "-DCMDIQO_BUILD_VERSION=$payloadVersion"
            cmake --build --preset build-windows-x64 --config $Config
            ctest --preset test-windows-x64 -C $Config
            $sourceExe = Join-Path $buildDirectory "cmdiqo-agent-setup.exe"
            if (-not (Test-Path $sourceExe)) {
                $sourceExe = Join-Path $buildDirectory "$Config\cmdiqo-agent-setup.exe"
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

        $localImage = "cmdiqo-installer-builder:windows-mingw"
        $remoteImage = "${DockerUser}/cmdiqo-installer-builder:windows-mingw"
        $buildDirectory = Join-Path $installerRoot "out\build\windows-x64-mingw"
        if ($Clean -and (Test-Path $buildDirectory)) {
            Remove-Item -Recurse -Force $buildDirectory
        }

        Ensure-InstallerBuilderImage -TargetStage "windows" -LocalImage $localImage -RemoteImage $remoteImage -InstallerRoot $installerRoot

        Write-Info "=== Building native Windows installer (MinGW-w64 in Docker, $Config) ==="
        $command = @"
set -euo pipefail
cmake --fresh --preset windows-x64-mingw -DCMAKE_BUILD_TYPE=${Config} "-DCMDIQO_BUILD_VERSION=\${CMDIQO_BUILD_VERSION}"
cmake --build --preset build-windows-x64-mingw --config ${Config}
ctest --preset test-windows-x64-mingw -C ${Config} || true
"@
        & docker run --rm `
            --workdir /workspace/installer.c `
            --volume "${rootDir}:/workspace" `
            --env "CMDIQO_BUILD_VERSION=$payloadVersion" `
            $localImage bash -c $command
        if ($LASTEXITCODE -ne 0) {
            throw "Native Windows installer build failed."
        }
        Copy-Item -LiteralPath (Join-Path $buildDirectory "cmdiqo-agent-setup.exe") -Destination (Join-Path $rootDir "dist\cmdiqo-agent-windows-x64-setup.exe") -Force
    } else {
        throw "Native installer dependencies are missing. Run .\installer.c\setup-env.ps1 (for MSVC) or start Docker Desktop (for MinGW builder)."
    }
    Write-Info "Native installer: $(Join-Path $rootDir 'dist\cmdiqo-agent-windows-x64-setup.exe')"
}

function Invoke-NativeLinuxInstaller {
    param([string]$PayloadArchive)

    $installerRoot = Join-Path $rootDir "installer.c"
    $localImage = "cmdiqo-installer-builder:ubuntu-22.04"
    $remoteImage = "${DockerUser}/cmdiqo-installer-builder:ubuntu-22.04"
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "Docker Desktop is required to build the native Linux installer."
    }
    & docker info *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Desktop is installed but its daemon is unavailable. Start Docker Desktop and retry."
    }

    $payloadVersion = Get-PayloadVersion $PayloadArchive
    $buildDirectory = Join-Path $installerRoot "out\build\linux-x64"
    if ($Clean -and (Test-Path $buildDirectory)) {
        Remove-Item -Recurse -Force $buildDirectory
    }

    Ensure-InstallerBuilderImage -TargetStage "linux" -LocalImage $localImage -RemoteImage $remoteImage -InstallerRoot $installerRoot

    Write-Info "=== Building native Linux installer ($Config) ==="
    $command = @"
set -euo pipefail
cmake --fresh --preset linux-x64 -DCMAKE_BUILD_TYPE=${Config} "-DCMDIQO_BUILD_VERSION=\${CMDIQO_BUILD_VERSION}"
cmake --build --preset build-linux-x64 --config ${Config}
ctest --preset test-linux-x64 -C ${Config}
"@
    & docker run --rm `
        --workdir /workspace/installer.c `
        --volume "${rootDir}:/workspace" `
        --env "CMDIQO_BUILD_VERSION=$payloadVersion" `
        $localImage bash -c $command
    if ($LASTEXITCODE -ne 0) {
        throw "Native Linux installer build failed."
    }
    Copy-Item -LiteralPath (Join-Path $buildDirectory "cmdiqo-agent-setup") -Destination (Join-Path $rootDir "dist\cmdiqo-agent-linux-x64-setup") -Force
    Write-Info "Native installer: $(Join-Path $rootDir 'dist\cmdiqo-agent-linux-x64-setup')"
}

if ($Clean) {
    Write-Info "Cleaning previous build artifacts in build/ and dist/ ..."
    if (Test-Path "$rootDir\build") { Remove-Item -Recurse -Force "$rootDir\build" }
    if (-not $SkipAgentBuild -and (Test-Path "$rootDir\dist")) { Remove-Item -Recurse -Force "$rootDir\dist" }
}

$versionArgs = @()
if ($Version) {
    $versionArgs += @("--version", $Version)
} elseif ($Dev) {
    $versionArgs += "--dev"
}
$currentVersion = (python "$rootDir\development\update_version.py" @versionArgs --print-version).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($currentVersion)) {
    Write-Err "Failed to synchronize version."
    exit 1
}

$parts = $currentVersion.Split('.')
$shortVersion = "$($parts[0]).$($parts[1])"
$plat = $Platform.ToLower()
$buildWin = ($plat -eq "win64" -or $plat -eq "all")
$buildLinux = ($plat -eq "linux64" -or $plat -eq "all")
Write-Info "Building target version: $currentVersion (Short: $shortVersion)"
Write-Info "Platform selection: $Platform (win64: $buildWin, linux64: $buildLinux)"

if ($buildAgent -and $buildWin) {
    Write-Info "=== Building standalone cmdiqo-agent Windows executable ==="
    $buildArgs = @("$rootDir\development\build_agent.py", "--version", $currentVersion)
    if ($SkipSign) { $buildArgs += "--skip-sign" }
    if ($CopyTo) { $buildArgs += @("--copy-to", $CopyTo) }

    python @buildArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Agent build failed."
        exit 1
    }
}

if ($buildAgent -and $buildLinux) {
    $dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $dockerCmd) {
        Write-Warn "Building linux64 binaries on Windows requires Docker Desktop. Start Docker or run build.sh on Linux."
    } else {
        Write-Info "=== Building Linux agent in Debian trixie container via Docker ==="
        docker build -t cmdiqo-builder-linux:trixie -f "$rootDir\docker\Dockerfile.builder-linux" "$rootDir"
        if ($LASTEXITCODE -ne 0) {
            throw "Linux Docker builder image creation failed."
        }

        $linuxBashScript = "python3 development/build_agent.py --version $currentVersion"
        docker run --rm -v "${rootDir}:/workspace" -w /workspace -e HOME=/workspace cmdiqo-builder-linux:trixie bash -c $linuxBashScript
        if ($LASTEXITCODE -ne 0) {
            throw "Linux agent build failed."
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
        Write-Warn "Docker CLI is not installed or not in PATH. Skipping container build."
    } else {
        $timestamp = (Get-Date -Format "yyyyMMddHHmmss")
        $imgLatest = "$DockerUser/cmdiqo-bot:latest"
        $imgShort = "$DockerUser/cmdiqo-bot:$shortVersion"
        $imgFull = "$DockerUser/cmdiqo-bot:$currentVersion"
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
            docker build `
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
