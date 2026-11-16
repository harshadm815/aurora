# AURORA's on-phone model runner: llama.cpp (MIT licence) built into libaurora_brain.so.
# llama.cpp's source isn't kept in this repository: its folder comes from local.properties
# (llama.dir=D:/Android/src/llama.cpp-b12414), passed in as LLAMA_DIR. Pinned release: b12414.
cmake_minimum_required(VERSION 3.22.1)
project(aurora_brain C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(NOT DEFINED LLAMA_DIR OR NOT EXISTS "${LLAMA_DIR}/include/llama.h")
    message(FATAL_ERROR "Set llama.dir in android/local.properties to the llama.cpp source folder")
endif()

# Only the library: no tools, servers, web UI or network code, and nothing fetched during the build.
set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_COMMON OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_TOOLS OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_SERVER OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_APP OFF CACHE BOOL "" FORCE)
set(LLAMA_OPENSSL OFF CACHE BOOL "" FORCE)
set(LLAMA_CURL OFF CACHE BOOL "" FORCE)
set(GGML_NATIVE OFF CACHE BOOL "" FORCE)
set(GGML_OPENMP OFF CACHE BOOL "" FORCE)          # ggml's own thread pool
set(GGML_CPU_KLEIDIAI OFF CACHE BOOL "" FORCE)    # would download another library during the build

add_subdirectory(${LLAMA_DIR} llama-build)

add_library(aurora_brain SHARED aurora_brain.cpp)
target_link_libraries(aurora_brain llama android log)
