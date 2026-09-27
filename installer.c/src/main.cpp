#include "cmdiqo/gui.hpp"
#include "cmdiqo/installer.hpp"

#include <fstream>
#include <iostream>
#include <iterator>
#include <string>
#include <vector>

#ifndef CMDIQO_INSTALLER_VERSION
#define CMDIQO_INSTALLER_VERSION "unknown"
#endif

namespace cmdiqo::installer {
namespace {

struct Options {
    bool cli{false};
    bool gui{false};
    bool install{false};
    bool uninstall{false};
    bool purge{false};
    bool status{false};
    bool test_hub{false};
    bool start{false};
    bool restart{false};
    bool stop{false};
    bool notify_specified{false};
    bool notify{false};
    bool help{false};
    std::optional<std::string> hub_url;
    std::optional<std::string> token;
    std::optional<std::string> agent_name;
    std::optional<std::filesystem::path> payload;
    std::optional<std::filesystem::path> config;
    std::optional<std::filesystem::path> elevation_config;
};

void print_usage(std::ostream& output) {
    output
        << "Cmdiqo Agent native installer\n\n"
        << "GUI: cmdiqo-agent-setup [--gui] [--payload PATH]\n"
        << "CLI: cmdiqo-agent-setup --cli ACTION [options]\n\n"
        << "Actions:\n"
        << "  --install                 Fresh install, upgrade, or repair\n"
        << "  --uninstall [--purge]     Remove the service and binary; --purge removes config and logs\n"
        << "  --status                  Show installed version and service state\n"
        << "  --test-hub                Test TCP connectivity to the Hub without changing the system\n"
        << "  --start | --restart | --stop\n\n"
        << "Configuration options:\n"
        << "  --hub URL --token TOKEN --name NAME --notify | --no-notify\n"
        << "  --config PATH             Read JSON configuration instead of placing a token on the command line\n"
        << "  --payload PATH            Must be the exact platform payload filename\n\n"
        << "System-changing actions require Administrator/root privileges. The GUI requests Windows UAC\n"
        << "or Linux pkexec. For a headless Linux host use: sudo ./cmdiqo-agent-setup --cli --install --config config.json\n";
}

bool needs_value(int index, int argc, const char* option, std::string& error) {
    if (index + 1 < argc) {
        return true;
    }
    error = std::string("Missing value for ") + option + ".";
    return false;
}

std::optional<Options> parse_options(int argc, char** argv, std::string& error) {
    Options options;
    for (int index = 1; index < argc; ++index) {
        const std::string option(argv[index]);
        if (option == "--cli") {
            options.cli = true;
        } else if (option == "--gui") {
            options.gui = true;
        } else if (option == "--install" || option == "--repair") {
            options.install = true;
        } else if (option == "--uninstall") {
            options.uninstall = true;
        } else if (option == "--purge") {
            options.purge = true;
        } else if (option == "--status") {
            options.status = true;
        } else if (option == "--test-hub") {
            options.test_hub = true;
        } else if (option == "--start") {
            options.start = true;
        } else if (option == "--restart") {
            options.restart = true;
        } else if (option == "--stop") {
            options.stop = true;
        } else if (option == "--notify") {
            options.notify_specified = true;
            options.notify = true;
        } else if (option == "--no-notify") {
            options.notify_specified = true;
            options.notify = false;
        } else if (option == "--hub" && needs_value(index, argc, "--hub", error)) {
            options.hub_url = argv[++index];
        } else if (option == "--token" && needs_value(index, argc, "--token", error)) {
            options.token = argv[++index];
        } else if (option == "--name" && needs_value(index, argc, "--name", error)) {
            options.agent_name = argv[++index];
        } else if (option == "--payload" && needs_value(index, argc, "--payload", error)) {
            options.payload = std::filesystem::path(argv[++index]);
        } else if (option == "--config" && needs_value(index, argc, "--config", error)) {
            options.config = std::filesystem::path(argv[++index]);
        } else if (option == "--elevation-config" && needs_value(index, argc, "--elevation-config", error)) {
            options.elevation_config = std::filesystem::path(argv[++index]);
        } else if (option == "--help" || option == "-h") {
            options.help = true;
        } else if (option == "--version") {
            std::cout << "Cmdiqo native installer " << CMDIQO_INSTALLER_VERSION << '\n';
            return std::nullopt;
        } else {
            error = "Unknown option: " + option;
            return std::nullopt;
        }
        if (!error.empty()) {
            return std::nullopt;
        }
    }
    return options;
}

bool has_cli_action(const Options& options) {
    return options.install || options.uninstall || options.status || options.test_hub
        || options.start || options.restart || options.stop;
}

bool needs_privilege(const Options& options) {
    return options.install || options.uninstall || options.start || options.restart || options.stop;
}

Config configured_values(const Options& options, const InstallerPaths& paths, std::string& error) {
    Config config = load_config(paths);
    if (options.config) {
        std::ifstream input(*options.config);
        if (!input) {
            error = "Could not open configuration file " + options.config->string() + ".";
            return config;
        }
        std::string document((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
        const auto parsed = config_from_json(document, error);
        if (!parsed) {
            return config;
        }
        config = *parsed;
    }
    if (options.hub_url) {
        config.hub_url = *options.hub_url;
    }
    if (options.token) {
        config.token = *options.token;
    }
    if (options.agent_name) {
        config.agent_name = *options.agent_name;
    }
    if (options.notify_specified) {
        config.agent_connection_notify = options.notify;
    }
    return config;
}

void show_status(InstallerService& service, const Options& options) {
    const auto payload = service.locate_payload(options.payload);
    const auto installed = service.installed_version();
    std::cout << "Platform: " << platform_name(current_platform()) << '\n';
    std::cout << "Installed version: " << installed.value_or("not installed") << '\n';
    std::cout << "Service: ";
    switch (PlatformOperations::service_status(service.paths())) {
        case ServiceStatus::running:
            std::cout << "running\n";
            break;
        case ServiceStatus::stopped:
            std::cout << "stopped\n";
            break;
        case ServiceStatus::not_installed:
            std::cout << "not installed\n";
            break;
    }
    if (!payload) {
        std::cout << "Bundled version: payload not found (expected "
                  << payload_file_name(current_platform()) << ")\n";
        return;
    }
    const std::string bundled = service.bundled_version(*payload);
    std::cout << "Bundled version: " << bundled << '\n';
    if (installed) {
        std::cout << version_relation_message(compare_versions(*installed, bundled)) << '\n';
    }
}

int run_cli(const Options& options) {
    InstallerService service;
    if (options.status || !has_cli_action(options)) {
        show_status(service, options);
        return 0;
    }
    if (options.test_hub) {
        std::string error;
        const Config config = configured_values(options, service.paths(), error);
        if (!error.empty()) {
            std::cerr << error << '\n';
            return 2;
        }
        const OperationResult result = test_hub_tcp(config.hub_url);
        std::cout << (result.ok ? "[OK] " : "[ERROR] ") << result.message << '\n';
        return result.ok ? 0 : 1;
    }
    if (needs_privilege(options) && !PlatformOperations::is_elevated()) {
        if (current_platform() == Platform::linux) {
            std::cerr << "This action requires root. Re-run with sudo. To avoid exposing a token in shell history, use --config with a root-readable JSON file.\n";
        } else {
            std::cerr << "This action requires Administrator privileges. Open an elevated terminal or launch the GUI and accept UAC.\n";
        }
        return 1;
    }

    const auto progress = [](std::string_view message) {
        std::cout << "[*] " << message << '\n';
    };
    OperationResult result;
    if (options.install) {
        std::string error;
        const Config config = configured_values(options, service.paths(), error);
        if (!error.empty()) {
            std::cerr << error << '\n';
            return 2;
        }
        const auto payload = service.locate_payload(options.payload);
        if (!payload) {
            std::cerr << "Expected exact payload " << payload_file_name(current_platform())
                      << " beside the installer, in payloads/, or with --payload.\n";
            return 2;
        }
        result = service.install_or_upgrade(config, *payload, progress);
    } else if (options.uninstall) {
        result = service.uninstall(options.purge, progress);
    } else if (options.start) {
        result = PlatformOperations::start_service(service.paths());
    } else if (options.restart) {
        result = PlatformOperations::restart_service(service.paths());
    } else {
        result = PlatformOperations::stop_service(service.paths());
    }
    std::cout << (result.ok ? "[OK] " : "[ERROR] ") << result.message << '\n';
    return result.ok ? 0 : 1;
}

}  // namespace
}  // namespace cmdiqo::installer

int main(int argc, char** argv) {
    using namespace cmdiqo::installer;
    std::string error;
    const auto options = parse_options(argc, argv, error);
    if (!options) {
        if (!error.empty()) {
            std::cerr << error << "\n\n";
            print_usage(std::cerr);
            return 2;
        }
        return 0;
    }
    if (options->help) {
        print_usage(std::cout);
        return 0;
    }
    if (current_platform() == Platform::unsupported) {
        std::cerr << "This native installer currently supports Windows and Linux only.\n";
        return 1;
    }

    const bool cli = options->cli || has_cli_action(*options)
        || (!options->gui && current_platform() == Platform::linux && !PlatformOperations::has_graphical_session());
    if (cli) {
        return run_cli(*options);
    }
    return run_gui(argc, argv, options->payload, options->elevation_config);
}
