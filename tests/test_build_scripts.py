"""Exercise build entry points with isolated payloads and simulated build tools."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


@pytest.fixture
def bash() -> str:
    if sys.platform == "win32":
        git_bash = (
            Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            / "Git"
            / "bin"
            / "bash.exe"
        )
        if git_bash.is_file():
            return str(git_bash)
    executable = shutil.which("bash")
    if executable is None:
        pytest.skip("Bash is required to exercise the shell build entry point.")
    return executable


def write_command(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        'printf "%s\\0" "' + name + '" "$@" >> "$MOCK_LOG"\n'
        'printf "\\0" >> "$MOCK_LOG"\n' + body,
        encoding="utf-8",
        newline="\n",
    )
    path.chmod(0o755)


class BuildHarness:
    def __init__(self, root: Path, bash: str) -> None:
        self.root = root
        self.bash = bash
        self.log = root / "commands.bin"
        self.env = os.environ.copy()
        self.env.update(
            MOCK_WORKSPACE=root.as_posix(),
            MOCK_BIN=(root / "tools").as_posix(),
            MOCK_LOG=self.log.as_posix(),
            MOCK_PLATFORM="linux x86_64 64",
            MOCK_UNAME="Linux",
            MOCK_VERSION="1.0.devel+6ABC1234",
            REAL_PYTHON=Path(sys.executable).as_posix(),
            MOCK_LOCAL_HASH=self.input_hash(),
            MOCK_REMOTE_HASH="",
            MOCK_PULL_STATUS="1",
            DOCKERHUB_USER="",
            DOCKERHUB_USERNAME="",
        )

    def input_hash(self) -> str:
        installer = self.root / "installer.c"
        files = [
            installer / "Dockerfile",
            installer / "vcpkg.json",
            *sorted((installer / "triplets").rglob("*")),
        ]
        digest = hashlib.sha256()
        for path in files:
            if path.is_file():
                digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
        return digest.hexdigest()

    def run(
        self, *args: str, script: str = "build.sh"
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                self.bash,
                "-c",
                (
                    "if command -v cygpath >/dev/null 2>&1; then "
                    'export PATH="$(cygpath -u "$MOCK_BIN"):$PATH"; '
                    'else export PATH="$MOCK_BIN:$PATH"; fi; '
                    'exec bash "$@"'
                ),
                "build-test",
                (self.root / script).as_posix(),
                *args,
            ],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=45,
        )

    def calls(self, command: str | None = None) -> list[list[str]]:
        if not self.log.exists():
            return []
        records = [
            record.decode("utf-8").split("\0")
            for record in self.log.read_bytes().split(b"\0\0")
            if record
        ]
        return [record for record in records if command is None or record[0] == command]

    def payload(self, platform: str, version: str | None = "1.2.0") -> Path:
        if platform in ("linux-arm64", "arm64"):
            path = self.root / "dist" / "cmdiqo-agent-linux-arm64.zip"
        elif platform in ("linux-armv6", "armv6", "armv6l"):
            path = self.root / "dist" / "cmdiqo-agent-linux-armv6.zip"
        else:
            path = self.root / "dist" / f"cmdiqo-agent-{platform}-x64.zip"
        path.parent.mkdir(exist_ok=True)
        with zipfile.ZipFile(path, "w") as archive:
            if version is not None:
                archive.writestr("version.txt", version)
            archive.writestr(
                "cmdiqo-agent.exe" if platform == "windows" else "cmdiqo-agent",
                "binary",
            )
        return path


@pytest.fixture
def build(tmp_path: Path, bash: str) -> BuildHarness:
    root = tmp_path / "workspace with spaces"
    root.mkdir()
    repo = Path(__file__).resolve().parents[1]
    shutil.copy2(repo / "build.sh", root / "build.sh")
    installer = root / "installer.c"
    installer.mkdir()
    for name in ("Dockerfile", "vcpkg.json", "setup-env.sh"):
        shutil.copy2(repo / "installer.c" / name, installer / name)
    shutil.copytree(repo / "installer.c" / "triplets", installer / "triplets")
    if (repo / "installer.c" / "scripts").is_dir():
        shutil.copytree(repo / "installer.c" / "scripts", installer / "scripts")
    agent = root / "agent.c"
    agent.mkdir()
    for name in (
        "CMakeLists.txt",
        "CMakePresets.json",
        "vcpkg.json",
        "install-service.sh",
        "uninstall-service.sh",
        "install-service.ps1",
        "uninstall-service.ps1",
        "cmdiqo-agent.service",
        "agent.env.example",
        "agent.json.example",
    ):
        if (repo / "agent.c" / name).is_file():
            shutil.copy2(repo / "agent.c" / name, agent / name)
    (root / "prebuilt-linux").mkdir()
    (root / "prebuilt-linux-arm64").mkdir()
    (root / "prebuilt-linux-armv6").mkdir()
    (root / "prebuilt-windows").mkdir()
    tools = root / "tools"
    tools.mkdir()
    write_command(tools, "uname", 'printf "%s\\n" "$MOCK_UNAME"\n')
    write_command(
        tools,
        "install",
        r"""
args=()
for arg in "$@"; do
    if [[ "$arg" != --directory && "$arg" != --mode=* ]]; then
        args+=("$arg")
    fi
done
mkdir -p "${args[@]}"
""",
    )
    python_body = r"""
if [[ "${1:-}" == "-c" && "${2:-}" == *"platform.system()"* ]]; then
    printf '%s\n' "$MOCK_PLATFORM"
elif [[ "${1:-}" == "development/update_version.py" ]]; then
    if [[ "${MOCK_VERSION_STATUS:-0}" != 0 ]]; then exit "$MOCK_VERSION_STATUS"; fi
    printf '%s\n' "$MOCK_VERSION"
else
    exec "$REAL_PYTHON" "$@"
fi
"""
    write_command(tools, "python3", python_body)
    write_command(tools, "python", python_body)
    write_command(
        tools,
        "cmake",
        r"""
if [[ "${1:-}" == "--build" ]]; then
    if [[ "${MOCK_CMAKE_BUILD_STATUS:-0}" != 0 ]]; then exit "$MOCK_CMAKE_BUILD_STATUS"; fi
    if [[ "${MOCK_CMAKE_OUTPUT:-1}" == 0 || "${MOCK_AGENT_OUTPUT:-1}" == 0 ]]; then exit 0; fi
    preset="${3#build-}"
    mkdir -p "out/build/$preset"
    if [[ "$PWD" == *"agent.c"* ]]; then
        artifact=cmdiqo-agent
    else
        artifact=cmdiqo-agent-setup
    fi
    if [[ "$preset" == windows-x64-mingw ]]; then artifact+=.exe; fi
    printf binary > "out/build/$preset/$artifact"
    chmod +x "out/build/$preset/$artifact"
else
    exit "${MOCK_CONFIGURE_STATUS:-0}"
fi
""",
    )
    write_command(
        tools,
        "ctest",
        r"""
if [[ "${MOCK_REMOVE_PAYLOAD:-0}" == 1 ]]; then
    rm -f "$MOCK_WORKSPACE/dist/cmdiqo-agent-linux-x64.zip"
fi
exit "${MOCK_CTEST_STATUS:-0}"
""",
    )
    write_command(
        tools,
        "docker",
        r"""
case "${1:-}" in
    info) exit "${MOCK_DOCKER_STATUS:-0}" ;;
    image)
        image="${!#}"
        hash="${MOCK_REMOTE_HASH:-}"
        if [[ "$image" == cmdiqo-installer-builder:all ]]; then hash="${MOCK_LOCAL_HASH:-}"; fi
        if [[ "$image" != cmdiqo-installer-builder:all && -f "$MOCK_WORKSPACE/pulled-hash" ]]; then
            hash="$(cat "$MOCK_WORKSPACE/pulled-hash")"
        fi
        if [[ -z "$hash" ]]; then exit 1; fi
        printf '%s\n' "$hash"
        ;;
    pull)
        if [[ "${MOCK_PULL_STATUS:-1}" != 0 ]]; then exit "$MOCK_PULL_STATUS"; fi
        printf '%s' "${MOCK_PULL_HASH:-<no value>}" > "$MOCK_WORKSPACE/pulled-hash"
        ;;
    tag) exit "${MOCK_TAG_STATUS:-0}" ;;
    push) exit "${MOCK_PUSH_STATUS:-0}" ;;
    build|buildx) exit "${MOCK_BUILD_STATUS:-0}" ;;
    run)
        if [[ "${MOCK_RUN_STATUS:-0}" != 0 ]]; then exit "$MOCK_RUN_STATUS"; fi
        shift
        workdir=/workspace
        while [[ $# -gt 0 ]]; do
            case "$1" in
                --rm) shift ;;
                --platform|--user|-v|--volume) shift 2 ;;
                -w|--workdir) workdir="$2"; shift 2 ;;
                -e|--env) export "$2"; shift 2 ;;
                *) image="$1"; shift; break ;;
            esac
        done
        export VCPKG_INSTALLED_DIR_LINUX="$MOCK_WORKSPACE/prebuilt-linux"
        export VCPKG_INSTALLED_DIR_LINUX_ARM64="$MOCK_WORKSPACE/prebuilt-linux-arm64"
        export VCPKG_INSTALLED_DIR_LINUX_ARMV6="$MOCK_WORKSPACE/prebuilt-linux-armv6"
        export VCPKG_INSTALLED_DIR_WINDOWS="$MOCK_WORKSPACE/prebuilt-windows"
        cd "$MOCK_WORKSPACE${workdir#/workspace}"
        "$@"
        ;;
    *) exit 2 ;;
esac
""",
    )
    harness = BuildHarness(root, bash)
    for platform in ("linux", "linux-arm64", "linux-armv6", "windows"):
        shutil.copy2(harness.payload(platform), root / f"seed-{platform}.zip")
    return harness


@pytest.mark.parametrize(
    "args",
    [
        ("--platform",),
        ("--target",),
        ("--version",),
        ("--copy-to",),
        ("--docker-user",),
        ("--version=",),
        ("--copy-to=",),
        ("--docker-user=",),
        ("--platform=",),
        ("--target=",),
        ("--platform", "--installer"),
        ("--unknown",),
    ],
)
def test_invalid_options_fail_before_building(
    build: BuildHarness, args: tuple[str, ...]
) -> None:
    result = build.run(*args)
    assert result.returncode != 0
    assert not build.calls("docker")
    assert not build.calls("python3")


@pytest.mark.parametrize(
    "platform,preset,dep_dir",
    [
        ("linux64", "linux-x64", "prebuilt-linux"),
        ("linux-arm64", "linux-arm64", "prebuilt-linux-arm64"),
        ("linux-armv6", "linux-armv6", "prebuilt-linux-armv6"),
        ("Windows", "windows-x64-mingw", "prebuilt-windows"),
    ],
)
def test_installer_uses_unified_prebuilt_image(
    build: BuildHarness, platform: str, preset: str, dep_dir: str
) -> None:
    result = build.run("--installer", "--skip-agent-build", "--platform", platform)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not [
        call for call in build.calls("docker") if call[1] in ("pull", "build", "buildx")
    ]
    run = next(call for call in build.calls("docker") if call[1] == "run")
    assert "cmdiqo-installer-builder:all" in run
    assert run[run.index("--platform") + 1] == "linux/amd64"
    assert "HOME=/tmp" in run
    assert "CMDIQO_BUILD_VERSION=1.2.0" in run
    configure = next(call for call in build.calls("cmake") if "--fresh" in call)
    assert configure[configure.index("--preset") + 1] == preset
    assert "-DVCPKG_MANIFEST_INSTALL=OFF" in configure
    assert (
        f"-DVCPKG_INSTALLED_DIR={build.root.as_posix()}/{dep_dir}"
        in configure
    )
    assert [
        "ctest",
        "--preset",
        f"test-{preset}",
        "-C",
        "Release",
        "--output-on-failure",
    ] in build.calls()


def test_agent_linux_arm64_build(build: BuildHarness) -> None:
    result = build.run("--agent", "--platform", "linux-arm64")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (build.root / "dist" / "cmdiqo-agent-linux-arm64").is_file()
    assert (build.root / "dist" / "cmdiqo-agent-linux-arm64.zip").is_file()
    configure = next(call for call in build.calls("cmake") if "--fresh" in call)
    assert configure[configure.index("--preset") + 1] == "linux-arm64"
    assert (
        f"-DVCPKG_INSTALLED_DIR={build.root.as_posix()}/prebuilt-linux-arm64"
        in configure
    )
    assert [
        "ctest",
        "--preset",
        "test-linux-arm64",
        "-C",
        "Release",
        "--output-on-failure",
    ] in build.calls()


def test_agent_linux_arm64_copy_to(build: BuildHarness) -> None:
    dest = build.root / "target" / "my-arm-agent"
    result = build.run("--agent", "--platform", "linux-arm64", "--copy-to", str(dest))
    assert result.returncode == 0, result.stdout + result.stderr
    assert dest.is_file()
    assert (build.root / "target" / "cmdiqo-agent-linux-arm64.zip").is_file()


def test_agent_linux_armv6_build(build: BuildHarness) -> None:
    result = build.run("--agent", "--platform", "linux-armv6")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (build.root / "dist" / "cmdiqo-agent-linux-armv6").is_file()
    assert (build.root / "dist" / "cmdiqo-agent-linux-armv6.zip").is_file()
    configure = next(call for call in build.calls("cmake") if "--fresh" in call)
    assert configure[configure.index("--preset") + 1] == "linux-armv6"
    assert (
        f"-DVCPKG_INSTALLED_DIR={build.root.as_posix()}/prebuilt-linux-armv6"
        in configure
    )
    assert [
        "ctest",
        "--preset",
        "test-linux-armv6",
        "-C",
        "Release",
        "--output-on-failure",
    ] in build.calls()


def test_agent_linux_armv6_copy_to(build: BuildHarness) -> None:
    dest = build.root / "target" / "my-armv6-agent"
    result = build.run("--agent", "--platform", "linux-armv6", "--copy-to", str(dest))
    assert result.returncode == 0, result.stdout + result.stderr
    assert dest.is_file()
    assert (build.root / "target" / "cmdiqo-agent-linux-armv6.zip").is_file()


def test_combined_installer_reuses_one_image_and_preserves_zips(
    build: BuildHarness,
) -> None:
    payloads = {path: path.read_bytes() for path in (build.root / "dist").glob("*.zip")}
    result = build.run(
        "--installer", "--skip-agent-build", "--platform", "all", "--clean"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert len([call for call in build.calls("docker") if call[1] == "run"]) == 5
    assert (
        len(
            [
                call
                for call in build.calls("docker")
                if call[1] == "image" and "--format" in call
            ]
        )
        == 1
    )
    assert all(path.read_bytes() == content for path, content in payloads.items())
    assert (build.root / "dist" / "cmdiqo-agent-linux-x64-setup").is_file()
    assert (build.root / "dist" / "cmdiqo-agent-linux-x64-setup-cli").is_file()
    assert (build.root / "dist" / "cmdiqo-agent-linux-arm64-setup").is_file()
    assert (build.root / "dist" / "cmdiqo-agent-linux-armv6-setup").is_file()
    assert (build.root / "dist" / "cmdiqo-agent-windows-x64-setup.exe").is_file()


@pytest.mark.parametrize("pulled_hash", ["stale", "<no value>"])
def test_untrusted_pulled_image_is_rebuilt(
    build: BuildHarness, pulled_hash: str
) -> None:
    build.env.update(
        MOCK_LOCAL_HASH="", MOCK_PULL_STATUS="0", MOCK_PULL_HASH=pulled_hash
    )
    result = build.run("--installer", "--skip-agent-build")
    assert result.returncode == 0, result.stdout + result.stderr
    fallback = next(
        call for call in build.calls("docker") if call[1:3] == ["buildx", "build"]
    )
    assert "--load" in fallback
    assert fallback[fallback.index("--platform") + 1] == "linux/amd64"
    assert fallback[fallback.index("--target") + 1] == "all"
    assert f"BUILDER_INPUT_SHA={build.input_hash()}" in fallback


def test_matching_remote_image_is_retagged_without_building(
    build: BuildHarness,
) -> None:
    build.env.update(MOCK_LOCAL_HASH="", MOCK_REMOTE_HASH=build.input_hash())
    result = build.run("--installer", "--skip-agent-build")
    assert result.returncode == 0, result.stdout + result.stderr
    assert [
        "docker",
        "tag",
        "lhotakj/cmdiqo-installer-builder:all",
        "cmdiqo-installer-builder:all",
    ] in build.calls()
    assert not [call for call in build.calls("docker") if call[1] in ("pull", "buildx")]


def test_matching_pulled_image_is_retagged(build: BuildHarness) -> None:
    build.env.update(
        MOCK_LOCAL_HASH="", MOCK_PULL_STATUS="0", MOCK_PULL_HASH=build.input_hash()
    )
    result = build.run("--installer", "--skip-agent-build")
    assert result.returncode == 0, result.stdout + result.stderr
    assert [call for call in build.calls("docker") if call[1] == "tag"]
    assert not [call for call in build.calls("docker") if call[1] == "buildx"]


def test_registry_failure_builds_locally(build: BuildHarness) -> None:
    build.env["MOCK_LOCAL_HASH"] = ""
    result = build.run("--installer", "--skip-agent-build")
    assert result.returncode == 0, result.stdout + result.stderr
    assert [call for call in build.calls("docker") if call[1] == "buildx"]


@pytest.mark.parametrize("phase", ["MOCK_TAG_STATUS", "MOCK_BUILD_STATUS"])
def test_image_preparation_errors_are_not_ignored(
    build: BuildHarness, phase: str
) -> None:
    build.env.update(MOCK_LOCAL_HASH="", **{phase: "7"})
    if phase == "MOCK_TAG_STATUS":
        build.env["MOCK_REMOTE_HASH"] = build.input_hash()
    result = build.run("--installer", "--skip-agent-build")
    assert result.returncode != 0
    assert not [call for call in build.calls("docker") if call[1] == "run"]


@pytest.mark.parametrize(
    "phase", ["MOCK_CONFIGURE_STATUS", "MOCK_CMAKE_BUILD_STATUS", "MOCK_CTEST_STATUS"]
)
def test_windows_installer_errors_prevent_publication(
    build: BuildHarness, phase: str
) -> None:
    build.env[phase] = "8"
    result = build.run("--installer", "--skip-agent-build", "--platform", "win64")
    assert result.returncode != 0
    assert not (build.root / "dist" / "cmdiqo-agent-windows-x64-setup.exe").exists()
    assert "Build complete successfully" not in result.stdout


@pytest.mark.parametrize("version", [None, "invalid", ""])
def test_bad_payload_fails_before_image_selection(
    build: BuildHarness, version: str | None
) -> None:
    build.payload("windows", version)
    result = build.run("--installer", "--skip-agent-build", "--platform", "all")
    assert result.returncode != 0
    assert not [call for call in build.calls("docker") if call[1] != "info"]


def test_input_hash_is_independent_of_checkout_line_endings(
    build: BuildHarness,
) -> None:
    for path in [
        build.root / "installer.c" / "Dockerfile",
        *list((build.root / "installer.c" / "triplets").glob("*.cmake")),
    ]:
        path.write_bytes(
            path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        )
    assert build.input_hash() == build.env["MOCK_LOCAL_HASH"]
    result = build.run("--installer", "--skip-agent-build")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not [call for call in build.calls("docker") if call[1] in ("pull", "buildx")]


def test_setup_script_hash_matches_build_entrypoint(build: BuildHarness) -> None:
    result = build.run("--platform", "all", script="installer.c/setup-env.sh")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not [
        call for call in build.calls("docker") if call[1] in ("build", "buildx")
    ]


def test_setup_script_retag_errors_are_not_ignored(build: BuildHarness) -> None:
    build.env["MOCK_TAG_STATUS"] = "3"
    result = build.run("--platform", "all", script="installer.c/setup-env.sh")
    assert result.returncode != 0
    assert "[SUCCESS]" not in result.stdout


def test_agent_builds_windows_via_mingw(
    build: BuildHarness,
) -> None:
    result = build.run("--agent", "--platform", "win64")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (build.root / "dist" / "cmdiqo-agent.exe").is_file()
    assert (build.root / "dist" / "cmdiqo-agent-windows-x64.zip").is_file()
    configure = next(call for call in build.calls("cmake") if "--fresh" in call)
    assert configure[configure.index("--preset") + 1] == "windows-x64-mingw"


def test_agent_builds_all_platforms(
    build: BuildHarness,
) -> None:
    result = build.run("--agent", "--platform", "all")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (build.root / "dist" / "cmdiqo-agent").is_file()
    assert (build.root / "dist" / "cmdiqo-agent-linux-x64.zip").is_file()
    assert (build.root / "dist" / "cmdiqo-agent-linux-arm64").is_file()
    assert (build.root / "dist" / "cmdiqo-agent-linux-arm64.zip").is_file()
    assert (build.root / "dist" / "cmdiqo-agent-linux-armv6").is_file()
    assert (build.root / "dist" / "cmdiqo-agent-linux-armv6.zip").is_file()
    assert (build.root / "dist" / "cmdiqo-agent.exe").is_file()
    assert (build.root / "dist" / "cmdiqo-agent-windows-x64.zip").is_file()
    assert len([call for call in build.calls("docker") if call[1] == "run"]) == 4


def test_build_all_target_and_platform(
    build: BuildHarness,
) -> None:
    result = build.run("--build-all")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (build.root / "dist" / "cmdiqo-agent").is_file()
    assert (build.root / "dist" / "cmdiqo-agent-linux-x64.zip").is_file()
    assert (build.root / "dist" / "cmdiqo-agent-linux-x64-setup").is_file()
    assert (build.root / "dist" / "cmdiqo-agent-linux-x64-setup-cli").is_file()
    assert (build.root / "dist" / "cmdiqo-agent-linux-arm64").is_file()
    assert (build.root / "dist" / "cmdiqo-agent-linux-arm64.zip").is_file()
    assert (build.root / "dist" / "cmdiqo-agent-linux-arm64-setup").is_file()
    assert (build.root / "dist" / "cmdiqo-agent-linux-armv6").is_file()
    assert (build.root / "dist" / "cmdiqo-agent-linux-armv6.zip").is_file()
    assert (build.root / "dist" / "cmdiqo-agent-linux-armv6-setup").is_file()
    assert (build.root / "dist" / "cmdiqo-agent.exe").is_file()
    assert (build.root / "dist" / "cmdiqo-agent-windows-x64.zip").is_file()
    assert (build.root / "dist" / "cmdiqo-agent-windows-x64-setup.exe").is_file()
    assert len([call for call in build.calls("docker") if call[1] == "run"]) == 9
    docker_build = next(
        call for call in build.calls("docker") if call[1:3] == ["buildx", "build"]
    )
    assert "--load" in docker_build


def test_push_all_rebuilds_and_pushes(
    build: BuildHarness,
) -> None:
    result = build.run("--push-all")
    assert result.returncode == 0, result.stdout + result.stderr
    builder_build = next(
        call
        for call in build.calls("docker")
        if call[1:3] == ["buildx", "build"] and "--target" in call
    )
    assert builder_build[builder_build.index("--target") + 1] == "all"
    assert [
        "docker",
        "push",
        "lhotakj/cmdiqo-installer-builder:all",
    ] in build.calls()
    bot_build = next(
        call
        for call in build.calls("docker")
        if call[1:3] == ["buildx", "build"] and "--target" not in call
    )
    assert "--push" in bot_build
    assert bot_build[bot_build.index("--platform") + 1] == "linux/amd64,linux/arm64"
    assert (build.root / "dist" / "cmdiqo-agent-linux-x64-setup").is_file()
    assert (build.root / "dist" / "cmdiqo-agent-linux-x64-setup-cli").is_file()
    assert (build.root / "dist" / "cmdiqo-agent-linux-arm64-setup").is_file()
    assert (build.root / "dist" / "cmdiqo-agent-linux-armv6-setup").is_file()
    assert (build.root / "dist" / "cmdiqo-agent-windows-x64-setup.exe").is_file()


def test_no_rebuild_builder_reuses_pulled_image_on_checksum_mismatch(
    build: BuildHarness,
) -> None:
    build.env["MOCK_LOCAL_HASH"] = ""
    build.env["MOCK_REMOTE_HASH"] = ""
    build.env["MOCK_PULL_STATUS"] = "0"
    build.env["MOCK_PULL_HASH"] = "mismatched-sha256-abc123"
    result = build.run("--installer", "--skip-agent-build", "--no-rebuild-builder")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "builder rebuild is disabled; reusing pulled image" in result.stdout
    assert not [
        call for call in build.calls("docker") if call[1] in ("build", "buildx")
    ]
    assert [
        "docker",
        "tag",
        "lhotakj/cmdiqo-installer-builder:all",
        "cmdiqo-installer-builder:all",
    ] in build.calls()


def test_no_rebuild_builder_fails_when_image_cannot_be_pulled(
    build: BuildHarness,
) -> None:
    build.env["MOCK_LOCAL_HASH"] = ""
    build.env["MOCK_REMOTE_HASH"] = ""
    build.env["MOCK_PULL_STATUS"] = "1"
    result = build.run("--installer", "--skip-agent-build", "--no-rebuild-builder")
    assert result.returncode != 0
    assert "rebuilding is disabled (--no-rebuild-builder)" in result.stderr
    assert not [
        call for call in build.calls("docker") if call[1] in ("build", "buildx")
    ]


def test_missing_prebuilt_dependency_root_is_reported(build: BuildHarness) -> None:
    (build.root / "prebuilt-windows").rmdir()
    result = build.run("--installer", "--skip-agent-build", "--platform", "win64")
    assert result.returncode != 0
    assert "Pre-built dependency root is missing" in result.stderr
    assert not build.calls("cmake")


def test_missing_builder_input_fails_before_image_selection(
    build: BuildHarness,
) -> None:
    (build.root / "installer.c" / "Dockerfile").unlink()
    result = build.run("--installer", "--skip-agent-build")
    assert result.returncode != 0
    assert not [call for call in build.calls("docker") if call[1] != "info"]


def test_summary_does_not_silently_skip_missing_artifacts(build: BuildHarness) -> None:
    build.env["MOCK_REMOVE_PAYLOAD"] = "1"
    result = build.run("--installer", "--skip-agent-build")
    assert result.returncode != 0
    assert "Requested artifact is missing" in result.stderr


def test_agent_copy_works(
    build: BuildHarness,
) -> None:
    copy = build.root / "copied artifacts" / "renamed-agent"
    result = build.run("--agent", "--platform", "linux64", "--copy-to", copy.as_posix())
    assert result.returncode == 0, result.stdout + result.stderr
    assert copy.read_text() == "binary"
    assert (copy.parent / "cmdiqo-agent-linux-x64.zip").is_file()
    run = next(call for call in build.calls("docker") if call[1] == "run")
    assert run[run.index("--platform") + 1] == "linux/amd64"


def test_success_without_agent_output_does_not_accept_stale_files(
    build: BuildHarness,
) -> None:
    build.env["MOCK_AGENT_OUTPUT"] = "0"
    (build.root / "dist" / "cmdiqo-agent").write_text("stale", encoding="utf-8")
    result = build.run("--agent")
    assert result.returncode != 0
    assert "not produced" in result.stderr


def test_success_without_installer_output_is_an_error(build: BuildHarness) -> None:
    build.env["MOCK_CMAKE_OUTPUT"] = "0"
    result = build.run("--installer", "--skip-agent-build")
    assert result.returncode != 0
    assert "not produced" in result.stderr


@pytest.mark.parametrize("target", ["--agent", "--installer", "--bot"])
def test_requested_docker_target_requires_a_running_daemon(
    build: BuildHarness, target: str
) -> None:
    build.env["MOCK_DOCKER_STATUS"] = "1"
    args = (target, "--skip-agent-build") if target == "--installer" else (target,)
    result = build.run(*args)
    assert result.returncode != 0
    assert "running Linux-container Docker daemon" in result.stderr


def test_corrupt_payload_fails_before_building(build: BuildHarness) -> None:
    (build.root / "dist" / "cmdiqo-agent-linux-x64.zip").write_text(
        "not a ZIP", encoding="utf-8"
    )
    result = build.run("--installer", "--skip-agent-build")
    assert result.returncode != 0
    assert "Cannot read version.txt" in result.stderr
    assert not [call for call in build.calls("docker") if call[1] != "info"]


def test_version_updater_failure_is_not_hidden_by_pipeline(build: BuildHarness) -> None:
    build.env["MOCK_VERSION_STATUS"] = "4"
    result = build.run("--agent")
    assert result.returncode != 0
    assert not [call for call in build.calls("docker") if call[1] != "info"]


def test_macos_without_docker_cannot_emit_macho_as_linux(build: BuildHarness) -> None:
    build.env.update(
        MOCK_PLATFORM="darwin arm64", MOCK_UNAME="Darwin", MOCK_DOCKER_STATUS="1"
    )
    result = build.run("--agent", "--platform", "linux64")
    assert result.returncode != 0
    assert "Docker daemon is required" in result.stderr or "requires Docker" in result.stderr


def test_bot_development_version_is_a_valid_docker_tag(build: BuildHarness) -> None:
    result = build.run("--bot")
    assert result.returncode == 0, result.stdout + result.stderr
    docker_build = next(
        call for call in build.calls("docker") if call[1:3] == ["buildx", "build"]
    )
    assert "--load" in docker_build
    assert "lhotakj/cmdiqo-bot:1.0.devel-6ABC1234" in docker_build
    assert not any("devel+" in argument for argument in docker_build)


def test_bot_push_preserves_multiarchitecture_output(build: BuildHarness) -> None:
    result = build.run("--bot", "--push")
    assert result.returncode == 0, result.stdout + result.stderr
    docker_build = next(
        call for call in build.calls("docker") if call[1:3] == ["buildx", "build"]
    )
    assert "--push" in docker_build
    assert "--load" not in docker_build
    assert (
        docker_build[docker_build.index("--platform") + 1] == "linux/amd64,linux/arm64"
    )


def test_selected_clean_does_not_remove_other_platform_payload(
    build: BuildHarness,
) -> None:
    windows = build.root / "dist" / "cmdiqo-agent-windows-x64.zip"
    previous = windows.read_bytes()
    result = build.run("--agent", "--clean", "--platform", "linux64")
    assert result.returncode == 0, result.stdout + result.stderr
    assert windows.read_bytes() == previous


def test_selected_clean_linux_arm64(build: BuildHarness) -> None:
    arm_zip = build.root / "dist" / "cmdiqo-agent-linux-arm64.zip"
    previous_arm = arm_zip.read_bytes()
    result = build.run("--agent", "--clean", "--platform", "linux64")
    assert result.returncode == 0, result.stdout + result.stderr
    assert arm_zip.read_bytes() == previous_arm

    linux_zip = build.root / "dist" / "cmdiqo-agent-linux-x64.zip"
    previous_linux = linux_zip.read_bytes()
    result = build.run("--agent", "--clean", "--platform", "linux-arm64")
    assert result.returncode == 0, result.stdout + result.stderr
    assert linux_zip.read_bytes() == previous_linux


def test_selected_clean_linux_armv6(build: BuildHarness) -> None:
    armv6_zip = build.root / "dist" / "cmdiqo-agent-linux-armv6.zip"
    previous_armv6 = armv6_zip.read_bytes()
    result = build.run("--agent", "--clean", "--platform", "linux64")
    assert result.returncode == 0, result.stdout + result.stderr
    assert armv6_zip.read_bytes() == previous_armv6

    linux_zip = build.root / "dist" / "cmdiqo-agent-linux-x64.zip"
    previous_linux = linux_zip.read_bytes()
    result = build.run("--agent", "--clean", "--platform", "linux-armv6")
    assert result.returncode == 0, result.stdout + result.stderr
    assert linux_zip.read_bytes() == previous_linux


@pytest.mark.parametrize("script", ["build.sh", "installer.c/setup-env.sh"])
def test_bash_syntax(bash: str, script: str) -> None:
    path = Path(__file__).resolve().parents[1] / script
    result = subprocess.run(
        [bash, "-n", path.as_posix()],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.fixture
def powershell() -> str:
    if sys.platform != "win32":
        pytest.skip("Native Windows PowerShell entry-point coverage requires Windows.")
    executable = shutil.which("powershell")
    if executable is None:
        pytest.skip("Windows PowerShell is unavailable.")
    return executable


@pytest.mark.parametrize("platform", ["Linux", "Windows", "win", "all"])
def test_powershell_platform_aliases(powershell: str, platform: str) -> None:
    script = Path(__file__).resolve().parents[1] / "build.ps1"
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Platform",
            platform,
            "-Help",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Usage:" in result.stdout


@pytest.mark.parametrize(
    "scenario",
    [
        "combined",
        "missing-hash",
        "retag-error",
        "build-error",
        "version-error",
        "linux-agent-copy",
        "no-rebuild-builder",
    ],
)
def test_powershell_installer_routing(
    build: BuildHarness, powershell: str, scenario: str
) -> None:
    repo = Path(__file__).resolve().parents[1]
    shutil.copy2(repo / "build.ps1", build.root / "build.ps1")
    log = build.root / "powershell-calls.jsonl"
    runner = build.root / "run-powershell-tests.ps1"
    runner.write_text(
        r"""
$ErrorActionPreference = "Stop"
function global:python {
    $global:LASTEXITCODE = 0
    if ([string]$args[0] -eq "-c") {
        if ([string]$args[1] -like "*platform*") { return "windows amd64" }
        return "1.2.0"
    }
    if ($env:MOCK_SCENARIO -eq "version-error") { $global:LASTEXITCODE = 5; return }
    return "1.0.devel+6ABC1234"
}
function global:docker {
    $global:LASTEXITCODE = 0
    ConvertTo-Json -InputObject @($args) -Compress | Add-Content -LiteralPath $env:PS_CALLS -Encoding UTF8
    switch ([string]$args[0]) {
        "image" {
            if ($env:MOCK_SCENARIO -eq "missing-hash") { return "{}" }
            if ($env:MOCK_SCENARIO -eq "no-rebuild-builder") {
                if ($args[-1] -eq "cmdiqo-installer-builder:all") {
                    $global:LASTEXITCODE = 1
                    return
                }
                return (@{"io.cmdiqo.installer-builder.build-input-sha256" = "mismatched"} | ConvertTo-Json -Compress)
            }
            if ($env:MOCK_SCENARIO -eq "retag-error" -and $args[-1] -eq "cmdiqo-installer-builder:all") {
                $global:LASTEXITCODE = 1
                return
            }
            return (@{"io.cmdiqo.installer-builder.build-input-sha256" = $env:MOCK_LOCAL_HASH} | ConvertTo-Json -Compress)
        }
        "tag" {
            if ($env:MOCK_SCENARIO -eq "retag-error") { $global:LASTEXITCODE = 7 }
        }
        "run" {
            if ($env:MOCK_SCENARIO -eq "build-error") { $global:LASTEXITCODE = 8; return }
            if ($args -contains "cmdiqo-builder-linux:trixie") {
                [IO.File]::WriteAllText((Join-Path $env:MOCK_WORKSPACE "dist\cmdiqo-agent"), "binary")
                Copy-Item -LiteralPath (Join-Path $env:MOCK_WORKSPACE "seed-linux.zip") -Destination (Join-Path $env:MOCK_WORKSPACE "dist\cmdiqo-agent-linux-x64.zip") -Force
                return
            }
            foreach ($preset in @("linux-x64", "linux-x64-cli", "windows-x64-mingw")) {
                $directory = Join-Path $env:MOCK_WORKSPACE "installer.c\out\build\$preset"
                [void](New-Item -ItemType Directory -Force -Path $directory)
                $artifact = if ($preset -eq "linux-x64" -or $preset -eq "linux-x64-cli") { "cmdiqo-agent-setup" } else { "cmdiqo-agent-setup.exe" }
                [IO.File]::WriteAllText((Join-Path $directory $artifact), "setup")
            }
        }
    }
}
if ($env:MOCK_SCENARIO -eq "linux-agent-copy") {
    & (Join-Path $env:MOCK_WORKSPACE "build.ps1") -Agent -Platform Linux -CopyTo (Join-Path $env:MOCK_WORKSPACE "copied\linux-agent")
} elseif ($env:MOCK_SCENARIO -eq "no-rebuild-builder") {
    & (Join-Path $env:MOCK_WORKSPACE "build.ps1") -Installer -SkipAgentBuild -Platform All -Clean -NoRebuildBuilder
} else {
    & (Join-Path $env:MOCK_WORKSPACE "build.ps1") -Installer -SkipAgentBuild -Platform All -Clean
}
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
""",
        encoding="utf-8",
        newline="\n",
    )
    env = build.env | {
        "MOCK_SCENARIO": scenario,
        "PS_CALLS": str(log),
    }
    result = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(runner)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if scenario in ("retag-error", "build-error", "version-error"):
        assert result.returncode != 0
        assert not (build.root / "dist" / "cmdiqo-agent-windows-x64-setup.exe").exists()
        return
    assert result.returncode == 0, result.stdout + result.stderr
    calls = [
        json.loads(line) for line in log.read_text(encoding="utf-8-sig").splitlines()
    ]
    runs = [call for call in calls if call[0] == "run"]
    if scenario == "linux-agent-copy":
        assert (build.root / "copied" / "linux-agent").read_text() == "binary"
        assert (build.root / "copied" / "cmdiqo-agent-linux-x64.zip").is_file()
        assert len(runs) == 1
        assert "HOME=/tmp" in runs[0]
        return
    assert len(runs) == 2
    for run in runs:
        assert "cmdiqo-installer-builder:all" in run
        assert "HOME=/tmp" in run
        command = run[-1]
        assert 'export TMPDIR="/tmp/cmdiqo-tmp-$$"' in command
        assert 'export WINEPREFIX="/tmp/cmdiqo-wine-$$"' in command
        assert 'install --directory --mode=700 "$TMPDIR" "$WINEPREFIX"' in command
        assert "-DVCPKG_MANIFEST_INSTALL=OFF" in command
        assert "|| true" not in command
    if scenario == "missing-hash":
        fallback = next(call for call in calls if call[:2] == ["buildx", "build"])
        assert "--load" in fallback
        assert fallback[fallback.index("--target") + 1] == "all"
    else:
        assert not [call for call in calls if call[0] == "buildx"]
    assert (build.root / "dist" / "cmdiqo-agent-linux-x64.zip").exists()
    assert (build.root / "dist" / "cmdiqo-agent-windows-x64.zip").exists()


def test_dockerfile_bot_multistage_structure() -> None:
    dockerfile = Path(__file__).resolve().parent.parent / "docker" / "Dockerfile"
    assert dockerfile.is_file(), "docker/Dockerfile must exist"
    content = dockerfile.read_text(encoding="utf-8")

    # Verify multi-stage build definitions
    assert "FROM python:3.12-alpine AS builder" in content
    assert "FROM python:3.12-alpine AS runtime" in content

    # Verify asset compression step
    assert "compress_assets.py" in content
    assert "python compress_assets.py bot" in content

    # Verify virtualenv copying and isolation
    assert "COPY --from=builder /opt/venv /opt/venv" in content
    assert 'ENV PATH="/opt/venv/bin:$PATH"' in content

    # Verify size optimizations & non-root user
    assert "PYTHONDONTWRITEBYTECODE=1" in content
    assert "USER cmdiqo" in content


def test_compress_assets_utility(tmp_path: Path) -> None:
    from docker.compress_assets import compress_directory

    sample_html = """<!DOCTYPE html>
<html>
  <head>
    <style>
      body {
        margin: 0;
        background: #050914;
      }
    </style>
  </head>
  <body>
    <h1>Title</h1>
    <script>
      function test() {
        const a = 1;
        return a + 2;
      }
    </script>
  </body>
</html>"""
    sample_css = """
body {
  margin: 0;
  padding: 0;
}
"""
    sample_js = """
function calculate(x, y) {
  const result = x + y;
  return result;
}
"""
    (tmp_path / "page.html").write_text(sample_html, encoding="utf-8")
    (tmp_path / "style.css").write_text(sample_css, encoding="utf-8")
    (tmp_path / "app.js").write_text(sample_js, encoding="utf-8")

    stats = compress_directory(tmp_path)
    assert "page.html" in stats
    assert "style.css" in stats
    assert "app.js" in stats

    orig_html, min_html = stats["page.html"]
    assert min_html < orig_html

    orig_css, min_css = stats["style.css"]
    assert min_css < orig_css

    orig_js, min_js = stats["app.js"]
    assert min_js < orig_js

