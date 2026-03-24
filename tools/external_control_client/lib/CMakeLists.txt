cmake_minimum_required(VERSION 3.0)

project(librenode_api)

file (GLOB SOURCES CONFIGURE_DEPENDS
    ${CMAKE_CURRENT_LIST_DIR}/*.c
    )

# Allow overriding library type via command line
if(CMAKE_BUILD_SHARED_LIBS)
    add_library(renode_api SHARED ${SOURCES})
else()
    add_library(renode_api STATIC ${SOURCES})
endif()

target_compile_options(renode_api
    PRIVATE
    -Wall
    -Werror
    -Wextra
    -fno-strict-aliasing
    )
set_property(TARGET renode_api PROPERTY POSITION_INDEPENDENT_CODE ON)

include_directories(
    ${CMAKE_CURRENT_LIST_DIR}/../include
    )
