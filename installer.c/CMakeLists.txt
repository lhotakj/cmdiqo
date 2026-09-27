cmake_minimum_required(VERSION 3.24)

project(cmdiqo_native_installer VERSION 0.9.0 LANGUAGES CXX)

include(CTest)

set(CMDIQO_BUILD_VERSION "${PROJECT_VERSION}" CACHE STRING "Cmdiqo build version embedded in the installer")

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(nlohmann_json CONFIG REQUIRED)
find_package(libzip CONFIG REQUIRED)
find_package(wxWidgets CONFIG REQUIRED)

add_library(cmdiqo_installer_core STATIC
    src/core.cpp
    src/installer.cpp
    src/platform.cpp
)
target_include_directories(cmdiqo_installer_core PUBLIC include)
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
    src/gui.cpp
    src/main.cpp
)
target_link_libraries(cmdiqo-agent-setup
    PRIVATE
        cmdiqo_installer_core
        wx::core
        wx::base
)

if(UNIX AND NOT APPLE AND CMAKE_BUILD_TYPE STREQUAL "Release")
    find_program(CMDIQO_STRIP_EXECUTABLE strip REQUIRED)
    add_custom_command(
        TARGET cmdiqo-agent-setup
        POST_BUILD
        COMMAND "${CMDIQO_STRIP_EXECUTABLE}" --strip-unneeded "$<TARGET_FILE:cmdiqo-agent-setup>"
        COMMENT "Stripping release installer symbols"
        VERBATIM
    )
endif()

if(BUILD_TESTING)
    find_package(Catch2 3 CONFIG REQUIRED)

    add_executable(cmdiqo-installer-tests
        tests/core_tests.cpp
    )
    target_link_libraries(cmdiqo-installer-tests PRIVATE cmdiqo_installer_core Catch2::Catch2WithMain)
    add_test(NAME cmdiqo-installer-tests COMMAND cmdiqo-installer-tests)
endif()
