set(VCPKG_TARGET_ARCHITECTURE arm)
set(VCPKG_CRT_LINKAGE static)
set(VCPKG_LIBRARY_LINKAGE static)
set(VCPKG_CMAKE_SYSTEM_NAME Linux)

set(VCPKG_CHAINLOAD_TOOLCHAIN_FILE "${CMAKE_CURRENT_LIST_DIR}/toolchains/linux-armv6.cmake")
set(VCPKG_BUILD_TYPE release)
set(VCPKG_C_FLAGS "-marm -march=armv6zk -mfloat-abi=hard -mfpu=vfp")
set(VCPKG_CXX_FLAGS "-marm -march=armv6zk -mfloat-abi=hard -mfpu=vfp -Wno-psabi")
set(VCPKG_LINKER_FLAGS "-static")

if(PORT STREQUAL "wxwidgets")
    list(APPEND VCPKG_CMAKE_CONFIGURE_OPTIONS "-DwxBUILD_TOOLKIT=gtk3")
endif()
