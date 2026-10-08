cmake_minimum_required(VERSION 3.24)

# Dynamic Version Resolution from version.txt
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

project(cmdiqo_native_installer VERSION ${RESOLVED_FILE_VERSION} LANGUAGES CXX C)

include(CTest)

if(NOT CMDIQO_BUILD_VERSION)
    set(CMDIQO_BUILD_VERSION "${PROJECT_VERSION}")
endif()
set(CMDIQO_INSTALLER_VERSION "${CMDIQO_BUILD_VERSION}")

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

find_package(nlohmann_json CONFIG REQUIRED)
find_package(libzip CONFIG REQUIRED)
find_package(Python3 COMPONENTS Interpreter REQUIRED)
find_package(wxWidgets CONFIG)
if(wxWidgets_FOUND)
    set(CMDIQO_ENABLE_GUI 1)
else()
    set(CMDIQO_ENABLE_GUI 0)
    message(STATUS "wxWidgets not found; building CLI-only installer")
endif()

set(CMDIQO_EMBEDDED_RESOURCES_DIR "${CMAKE_CURRENT_BINARY_DIR}/generated")
set(CMDIQO_EMBEDDED_LOGOS_HEADER "${CMDIQO_EMBEDDED_RESOURCES_DIR}/embedded_logos.hpp")
set(CMDIQO_EMBEDDED_PAYLOAD_HEADER "${CMDIQO_EMBEDDED_RESOURCES_DIR}/embedded_payload.hpp")
set(CMDIQO_EMBEDDED_PAYLOAD_SOURCE "${CMDIQO_EMBEDDED_RESOURCES_DIR}/embedded_payload.cpp")
file(MAKE_DIRECTORY "${CMDIQO_EMBEDDED_RESOURCES_DIR}")

# Resolve default agent binary path if not explicitly provided
if(NOT DEFINED CMDIQO_AGENT_BINARY OR CMDIQO_AGENT_BINARY STREQUAL "")
    if(WIN32 OR MINGW)
        set(CMDIQO_AGENT_BINARY "${CMAKE_CURRENT_SOURCE_DIR}/../dist/cmdiqo-agent.exe")
    else()
        set(CMDIQO_AGENT_BINARY "${CMAKE_CURRENT_SOURCE_DIR}/../dist/cmdiqo-agent")
    endif()
endif()

if(EXISTS "${CMDIQO_AGENT_BINARY}")
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${CMDIQO_AGENT_BINARY}")
endif()

execute_process(
    COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/scripts/embed_payload.py"
        --binary "${CMDIQO_AGENT_BINARY}"
        --version "${CMDIQO_BUILD_VERSION}"
        --output-header "${CMDIQO_EMBEDDED_PAYLOAD_HEADER}"
        --output-source "${CMDIQO_EMBEDDED_PAYLOAD_SOURCE}"
        --zip
    RESULT_VARIABLE EMBED_PAYLOAD_RES
)
if(NOT EMBED_PAYLOAD_RES EQUAL 0)
    message(WARNING "embed_payload.py failed with exit code ${EMBED_PAYLOAD_RES}")
endif()

file(WRITE "${CMDIQO_EMBEDDED_LOGOS_HEADER}"
    "#pragma once\n\n#include <array>\n\nnamespace cmdiqo::installer::resources {\n")
foreach(logo IN ITEMS win linux app)
    set(logo_path "${CMAKE_CURRENT_SOURCE_DIR}/src/bitmaps/${logo}.png")
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${logo_path}")
    file(READ "${logo_path}" logo_hex HEX)
    string(REGEX MATCHALL "[0-9A-Fa-f][0-9A-Fa-f]" logo_bytes "${logo_hex}")
    list(LENGTH logo_bytes logo_size)
    file(APPEND "${CMDIQO_EMBEDDED_LOGOS_HEADER}"
        "\ninline constexpr std::array<unsigned char, ${logo_size}> ${logo}_logo = {\n")
    foreach(byte IN LISTS logo_bytes)
        file(APPEND "${CMDIQO_EMBEDDED_LOGOS_HEADER}" "0x${byte},")
    endforeach()
    file(APPEND "${CMDIQO_EMBEDDED_LOGOS_HEADER}" "\n};\n")
endforeach()
file(APPEND "${CMDIQO_EMBEDDED_LOGOS_HEADER}" "\n}  // namespace cmdiqo::installer::resources\n")

add_library(cmdiqo_installer_core STATIC
    src/core.cpp
    src/installer.cpp
    src/platform.cpp
    "${CMDIQO_EMBEDDED_PAYLOAD_SOURCE}"
)
target_include_directories(cmdiqo_installer_core PUBLIC
    include
    "${CMDIQO_EMBEDDED_RESOURCES_DIR}"
)
target_link_libraries(cmdiqo_installer_core
    PUBLIC
        nlohmann_json::nlohmann_json
        libzip::zip
)
target_compile_definitions(cmdiqo_installer_core PUBLIC CMDIQO_INSTALLER_VERSION="${CMDIQO_BUILD_VERSION}")

if(WIN32)
    target_link_libraries(cmdiqo_installer_core PRIVATE ws2_32 shell32 advapi32)
endif()

add_executable(cmdiqo-agent-setup
    src/main.cpp
)
target_link_libraries(cmdiqo-agent-setup
    PRIVATE
        cmdiqo_installer_core
)
target_compile_definitions(cmdiqo-agent-setup PRIVATE CMDIQO_ENABLE_GUI=${CMDIQO_ENABLE_GUI})
if(CMDIQO_ENABLE_GUI)
    target_sources(cmdiqo-agent-setup PRIVATE src/gui.cpp)
    target_link_libraries(cmdiqo-agent-setup PRIVATE wx::core wx::base)
endif()
target_include_directories(cmdiqo-agent-setup PRIVATE "${CMDIQO_EMBEDDED_RESOURCES_DIR}")

if(WIN32)
    enable_language(RC)

    set(CMDIQO_INSTALLER_ICON_PATH "${CMAKE_CURRENT_SOURCE_DIR}/resources/cmdiqo-agent-setup.ico")
    set(CMDIQO_INSTALLER_MANIFEST_PATH "${CMAKE_CURRENT_SOURCE_DIR}/resources/cmdiqo-agent-setup.manifest")
    set(CMDIQO_GENERATED_RC "${CMAKE_CURRENT_BINARY_DIR}/cmdiqo-agent-setup.rc")

    configure_file(
        "${CMAKE_CURRENT_SOURCE_DIR}/resources/cmdiqo-agent-setup.rc.in"
        "${CMDIQO_GENERATED_RC}"
        @ONLY
    )

    target_sources(cmdiqo-agent-setup PRIVATE "${CMDIQO_GENERATED_RC}")
endif()

set_target_properties(cmdiqo-agent-setup PROPERTIES WIN32_EXECUTABLE FALSE)

if(MINGW)
    target_link_options(cmdiqo-agent-setup PRIVATE -mconsole -Wl,--subsystem,console:6.0 -static -static-libgcc -static-libstdc++)
    if(CMAKE_BUILD_TYPE STREQUAL "Release")
        if(CMAKE_STRIP)
            add_custom_command(
                TARGET cmdiqo-agent-setup
                POST_BUILD
                COMMAND "${CMAKE_STRIP}" --strip-unneeded "$<TARGET_FILE:cmdiqo-agent-setup>"
                COMMENT "Stripping release MinGW installer symbols"
                VERBATIM
            )
        endif()
    endif()
elseif(UNIX AND NOT APPLE AND CMAKE_BUILD_TYPE STREQUAL "Release")
    if(CMAKE_STRIP)
        set(CMDIQO_STRIP_EXECUTABLE "${CMAKE_STRIP}")
    else()
        find_program(CMDIQO_STRIP_EXECUTABLE strip)
    endif()
    if(CMDIQO_STRIP_EXECUTABLE)
        add_custom_command(
            TARGET cmdiqo-agent-setup
            POST_BUILD
            COMMAND "${CMDIQO_STRIP_EXECUTABLE}" --strip-unneeded "$<TARGET_FILE:cmdiqo-agent-setup>"
            COMMENT "Stripping release installer symbols"
            VERBATIM
        )
    endif()
endif()

if(BUILD_TESTING)
    find_package(Catch2 3 CONFIG REQUIRED)

    add_executable(cmdiqo-installer-tests
        tests/core_tests.cpp
    )
    target_link_libraries(cmdiqo-installer-tests PRIVATE cmdiqo_installer_core Catch2::Catch2WithMain)
    if(MINGW)
        target_link_options(cmdiqo-installer-tests PRIVATE -static -static-libgcc -static-libstdc++)
    endif()
    add_test(NAME cmdiqo-installer-tests COMMAND cmdiqo-installer-tests)
endif()
