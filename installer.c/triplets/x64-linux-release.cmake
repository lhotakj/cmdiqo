include("${VCPKG_ROOT_DIR}/triplets/x64-linux.cmake")

set(VCPKG_BUILD_TYPE release)

if(PORT STREQUAL "wxwidgets")
    list(APPEND VCPKG_CMAKE_CONFIGURE_OPTIONS "-DwxBUILD_TOOLKIT=gtk3")
endif()
