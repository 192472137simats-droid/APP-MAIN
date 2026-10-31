cmake_minimum_required(VERSION 3.16)

project(ResearchAdvisorCpp LANGUAGES CXX)

find_package(PkgConfig REQUIRED)
pkg_check_modules(POPPLER_CPP REQUIRED IMPORTED_TARGET poppler-cpp)
pkg_check_modules(LIBCURL REQUIRED IMPORTED_TARGET libcurl)
pkg_check_modules(JSONCPP REQUIRED IMPORTED_TARGET jsoncpp)
find_package(SQLite3 REQUIRED)
find_package(Threads REQUIRED)

add_executable(research-advisor-server
    src/server.cpp
    src/paper_store.cpp
    src/pdf_extract.cpp
    src/analysis.cpp
)
target_compile_features(research-advisor-server PRIVATE cxx_std_17)
target_link_libraries(research-advisor-server PRIVATE PkgConfig::POPPLER_CPP
    PkgConfig::LIBCURL PkgConfig::JSONCPP SQLite3::SQLite3 Threads::Threads)

if(MSVC)
    target_compile_options(research-advisor-server PRIVATE /W4)
else()
    target_compile_options(research-advisor-server PRIVATE -Wall -Wextra -Wpedantic)
endif()

if(WIN32)
    target_link_libraries(research-advisor-server PRIVATE ws2_32)
endif()
