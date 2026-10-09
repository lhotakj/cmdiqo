set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR armv6l)

set(CMAKE_C_COMPILER armv6-linux-musleabihf-gcc)
set(CMAKE_CXX_COMPILER armv6-linux-musleabihf-g++)
set(CMAKE_STRIP armv6-linux-musleabihf-strip)

string(APPEND CMAKE_C_FLAGS_INIT " -marm -march=armv6zk -mfloat-abi=hard -mfpu=vfp")
string(APPEND CMAKE_CXX_FLAGS_INIT " -marm -march=armv6zk -mfloat-abi=hard -mfpu=vfp")
string(APPEND CMAKE_ASM_FLAGS_INIT " -marm -march=armv6zk -mfloat-abi=hard -mfpu=vfp")
string(APPEND CMAKE_EXE_LINKER_FLAGS_INIT " -static")

set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)
