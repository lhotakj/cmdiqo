set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR armv6l)

set(CMAKE_C_COMPILER arm-linux-gnueabihf-gcc)
set(CMAKE_CXX_COMPILER arm-linux-gnueabihf-g++)

string(APPEND CMAKE_C_FLAGS_INIT " -marm -march=armv6zk -mfloat-abi=hard -mfpu=vfp")
string(APPEND CMAKE_CXX_FLAGS_INIT " -marm -march=armv6zk -mfloat-abi=hard -mfpu=vfp")
string(APPEND CMAKE_ASM_FLAGS_INIT " -marm -march=armv6zk -mfloat-abi=hard -mfpu=vfp")

set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)
