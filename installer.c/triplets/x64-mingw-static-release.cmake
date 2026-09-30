include("${VCPKG_ROOT_DIR}/triplets/community/x64-mingw-static.cmake")

set(VCPKG_BUILD_TYPE release)

if(PORT STREQUAL "wxwidgets")
    # Cross-builds default to Unix naming, but vcpkg's Windows finder expects MSW layout.
    list(APPEND VCPKG_CMAKE_CONFIGURE_OPTIONS
        "-DwxBUILD_TOOLKIT=msw"
        "-DwxBUILD_WIN32_MSVC_NAMING=ON"
    )
endif()
