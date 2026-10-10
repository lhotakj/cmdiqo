cmake_minimum_required(VERSION 3.24)

# -----------------------------------------------------------------------------
# 1. Dynamic Version Resolution from version.txt
# -----------------------------------------------------------------------------
set(VERSION_FILE_CANDIDATE_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../version.txt")
set(VERSION_FILE_CANDIDATE_LOCAL "${CMAKE_CURRENT_SOURCE_DIR}/version.txt")

if(EXISTS "${VERSION_FILE_CANDIDATE_ROOT}")
    set(VERSION_FILE_PATH "${VERSION_FILE_CANDIDATE_ROOT}")
elseif(EXISTS "${VERSION_FILE_CANDIDATE_LOCAL}")
    set(VERSION_FILE_PATH "${VERSION_FILE_CANDIDATE_LOCAL}")
else()
    set(VERSION_FILE_PATH "")
endif()

if(VERSION_FILE_PATH)
    file(READ "${VERSION_FILE_PATH}" RAW_VERSION_FILE_CONTENT)
    string(STRIP "${RAW_VERSION_FILE_CONTENT}" RESOLVED_FILE_VERSION)
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${VERSION_FILE_PATH}")
else()
    set(RESOLVED_FILE_VERSION "1.0.0")
endif()

project(cmdiqo_agent VERSION ${RESOLVED_FILE_VERSION} LANGUAGES CXX C)

include(CTest)

# Override with cache variable if explicitly passed via -DCMDIQO_BUILD_VERSION="..."
if(NOT CMDIQO_BUILD_VERSION)
    set(CMDIQO_BUILD_VERSION "${PROJECT_VERSION}")
endif()
set(CMDIQO_AGENT_VERSION "${CMDIQO_BUILD_VERSION}")

# Resolve numeric tuple for Windows Resource File (major,minor,patch,build)
string(REGEX MATCH "^([0-9]+)\\.([0-9]+)\\.([0-9]+)" VERSION_PARSED "${CMDIQO_BUILD_VERSION}")
if(CMAKE_MATCH_1 AND CMAKE_MATCH_2 AND CMAKE_MATCH_3)
    set(CMDIQO_RC_FILEVERSION "${CMAKE_MATCH_1},${CMAKE_MATCH_2},${CMAKE_MATCH_3},0")
    set(CMDIQO_RC_PRODUCTVERSION "${CMAKE_MATCH_1},${CMAKE_MATCH_2},${CMAKE_MATCH_3},0")
else()
    set(CMDIQO_RC_FILEVERSION "1,0,0,0")
    set(CMDIQO_RC_PRODUCTVERSION "1,0,0,0")
endif()

string(TIMESTAMP CMDIQO_COPYRIGHT_YEAR "%Y")

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# -----------------------------------------------------------------------------
# 2. Third-Party Dependencies (vcpkg / CMake config packages)
# -----------------------------------------------------------------------------
find_package(nlohmann_json CONFIG REQUIRED)

find_package(CLI11 NAMES CLI11 cli11 CONFIG QUIET)
if(NOT TARGET CLI11::CLI11)
    find_package(cli11 CONFIG QUIET)
endif()
if(NOT TARGET CLI11::CLI11)
    find_path(CLI11_INCLUDE_DIRS "CLI/CLI.hpp")
    if(CLI11_INCLUDE_DIRS)
        add_library(CLI11::CLI11 INTERFACE IMPORTED)
        target_include_directories(CLI11::CLI11 INTERFACE "${CLI11_INCLUDE_DIRS}")
    else()
        find_package(CLI11 CONFIG REQUIRED)
    endif()
endif()

find_package(ixwebsocket CONFIG QUIET)
if(NOT TARGET ixwebsocket::ixwebsocket)
    find_package(IXWebSocket NAMES IXWebSocket ixwebsocket CONFIG REQUIRED)
endif()

# -----------------------------------------------------------------------------
# 3. Agent Core Library
# -----------------------------------------------------------------------------
add_library(cmdiqo_agent_core STATIC
    src/config.cpp
    src/logger.cpp
    src/ws_client.cpp
)

if(WIN32)
    target_sources(cmdiqo_agent_core PRIVATE
        src/process_win32.cpp
        src/service_win32.cpp
    )
else()
    target_sources(cmdiqo_agent_core PRIVATE
        src/process_linux.cpp
        src/service_linux.cpp
    )
endif()

target_include_directories(cmdiqo_agent_core PUBLIC include)

target_link_libraries(cmdiqo_agent_core
    PUBLIC
        nlohmann_json::nlohmann_json
        CLI11::CLI11
        ixwebsocket::ixwebsocket
)

if(NOT CMDIQO_AGENT_ARCH)
    if(WIN32)
        if(CMAKE_SYSTEM_PROCESSOR MATCHES "ARM64|aarch64")
            set(CMDIQO_AGENT_ARCH "arm64")
        else()
            set(CMDIQO_AGENT_ARCH "win64")
        endif()
    elseif(CMAKE_SYSTEM_PROCESSOR MATCHES "armv6|armv6l|armv6zk")
        set(CMDIQO_AGENT_ARCH "armv6")
    elseif(CMAKE_SYSTEM_PROCESSOR MATCHES "aarch64|arm64|ARM64")
        set(CMDIQO_AGENT_ARCH "arm64")
    elseif(VCPKG_TARGET_TRIPLET MATCHES "armv6")
        set(CMDIQO_AGENT_ARCH "armv6")
    elseif(VCPKG_TARGET_TRIPLET MATCHES "arm64")
        set(CMDIQO_AGENT_ARCH "arm64")
    elseif(VCPKG_TARGET_TRIPLET MATCHES "x64-windows")
        set(CMDIQO_AGENT_ARCH "win64")
    else()
        set(CMDIQO_AGENT_ARCH "linux64")
    endif()
endif()

target_compile_definitions(cmdiqo_agent_core PUBLIC
    CMDIQO_AGENT_VERSION="${CMDIQO_BUILD_VERSION}"
    CMDIQO_AGENT_ARCH="${CMDIQO_AGENT_ARCH}"
)

if(WIN32)
    target_link_libraries(cmdiqo_agent_core PRIVATE
        ws2_32
        advapi32
        crypt32
        secur32
        user32
        shell32
        bcrypt
    )
    target_compile_definitions(cmdiqo_agent_core PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX _WIN32_WINNT=0x0601)
else()
    find_package(Threads REQUIRED)
    target_link_libraries(cmdiqo_agent_core PUBLIC Threads::Threads)
endif()

# -----------------------------------------------------------------------------
# 4. Executable Target & Windows Resource Embedding
# -----------------------------------------------------------------------------
add_executable(cmdiqo-agent
    src/main.cpp
)

target_link_libraries(cmdiqo-agent
    PRIVATE
        cmdiqo_agent_core
)

if(WIN32)
    enable_language(RC)

    set(CMDIQO_AGENT_ICON_PATH "${CMAKE_CURRENT_SOURCE_DIR}/resources/cmdiqo-agent.ico")
    set(CMDIQO_AGENT_MANIFEST_PATH "${CMAKE_CURRENT_SOURCE_DIR}/resources/cmdiqo-agent.manifest")
    set(CMDIQO_GENERATED_RC "${CMAKE_CURRENT_BINARY_DIR}/cmdiqo-agent.rc")

    configure_file(
        "${CMAKE_CURRENT_SOURCE_DIR}/resources/cmdiqo-agent.rc.in"
        "${CMDIQO_GENERATED_RC}"
        @ONLY
    )

    target_sources(cmdiqo-agent PRIVATE "${CMDIQO_GENERATED_RC}")
endif()

# -----------------------------------------------------------------------------
# 5. Static Linking & Release Stripping
# -----------------------------------------------------------------------------
if(MINGW)
    target_link_options(cmdiqo-agent PRIVATE -static -static-libgcc -static-libstdc++)
    if(CMAKE_BUILD_TYPE STREQUAL "Release")
        if(CMAKE_STRIP)
            add_custom_command(
                TARGET cmdiqo-agent
                POST_BUILD
                COMMAND "${CMAKE_STRIP}" --strip-unneeded "$<TARGET_FILE:cmdiqo-agent>"
                COMMENT "Stripping release MinGW agent symbols"
                VERBATIM
            )
        endif()
    endif()
elseif(UNIX AND NOT APPLE)
    if(CMAKE_SYSTEM_PROCESSOR STREQUAL "armv6l" OR VCPKG_TARGET_TRIPLET MATCHES "armv6")
        target_link_options(cmdiqo-agent PRIVATE -static)
    endif()
    if(CMAKE_BUILD_TYPE STREQUAL "Release")
        if(CMAKE_STRIP)
            set(CMDIQO_STRIP_EXECUTABLE "${CMAKE_STRIP}")
        else()
            find_program(CMDIQO_STRIP_EXECUTABLE strip)
        endif()
        if(CMDIQO_STRIP_EXECUTABLE)
            add_custom_command(
                TARGET cmdiqo-agent
                POST_BUILD
                COMMAND "${CMDIQO_STRIP_EXECUTABLE}" --strip-unneeded "$<TARGET_FILE:cmdiqo-agent>"
                COMMENT "Stripping release agent symbols"
                VERBATIM
            )
        endif()
    endif()
endif()

# -----------------------------------------------------------------------------
# 6. Unit & Integration Tests
# -----------------------------------------------------------------------------
if(BUILD_TESTING)
    find_package(Catch2 3 CONFIG REQUIRED)

    add_executable(cmdiqo-agent-tests
        tests/config_tests.cpp
        tests/protocol_tests.cpp
        tests/process_tests.cpp
        tests/logger_tests.cpp
        tests/version_tests.cpp
    )
    target_link_libraries(cmdiqo-agent-tests PRIVATE cmdiqo_agent_core Catch2::Catch2WithMain)
    if(MINGW)
        target_link_options(cmdiqo-agent-tests PRIVATE -static -static-libgcc -static-libstdc++)
    elseif(CMAKE_SYSTEM_PROCESSOR STREQUAL "armv6l" OR VCPKG_TARGET_TRIPLET MATCHES "armv6")
        target_link_options(cmdiqo-agent-tests PRIVATE -static)
    endif()
    add_test(NAME cmdiqo-agent-tests COMMAND cmdiqo-agent-tests)
endif()
