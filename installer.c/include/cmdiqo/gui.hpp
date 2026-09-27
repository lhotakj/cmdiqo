#pragma once

#include <optional>

#include "cmdiqo/installer.hpp"

namespace cmdiqo::installer {

int run_gui(
    int& argc,
    char** argv,
    const std::optional<std::filesystem::path>& payload,
    const std::optional<std::filesystem::path>& initial_config);

}  // namespace cmdiqo::installer
