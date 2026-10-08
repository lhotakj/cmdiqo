#ifndef CMDIQO_ENABLE_GUI
#define CMDIQO_ENABLE_GUI 1
#endif

#if CMDIQO_ENABLE_GUI
#include "cmdiqo/gui.hpp"
#endif
#include "cmdiqo/installer.hpp"

#include <fstream>
#include <iostream>
#include <iterator>
#include <string>
#include <vector>

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#endif

#ifndef CMDIQO_INSTALLER_VERSION
#define CMDIQO_INSTALLER_VERSION "unknown"
#endif

namespace cmdiqo::installer {
namespace {

struct Options {
    bool cli{false};
    bool gui{false};
    bool install{false};
    bool fresh{false};
    bool configure{false};
    bool locate_config{false};
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
    std::optional<std::filesystem::path> result_file;
};

void print_usage(std::ostream& output) {
    output
        << "Cmdiqo Agent native installer\n\n"
        << "GUI: cmdiqo-agent-setup [--gui] [--payload PATH]\n"
        << "CLI: cmdiqo-agent-setup --cli ACTION [options]\n\n"
        << "Actions:\n"
        << "  --install                 Install, upgrade, or repair the agent service\n"
        << "  --fresh                   Interactive fresh install (prompts for configuration)\n"
        << "  --configure               Interactively edit and update the agent configuration\n"
        << "  --locate-config           Print the full path to the configuration file\n"
        << "  --uninstall [--purge]     Remove the service and binary; --purge removes config and logs\n"
        << "  --status                  Show installed version and service state\n"
        << "  --test-hub                Test TCP connectivity to the Hub without changing the system\n"
        << "  --start | --restart | --stop\n\n"
        << "Configuration options:\n"
        << "  --hub URL --token TOKEN --name NAME --notify | --no-notify\n"
        << "  --config PATH             Read JSON configuration instead of placing a token on the command line\n"
        << "  --payload PATH            Must be the exact platform payload filename\n\n"
        << "System-changing actions require Administrator/root privileges. The GUI requests Windows UAC\n"
        << "or Linux pkexec. For a headless Linux host use: sudo ./cmdiqo-agent-setup --cli --install --config config.json\n\n"
        << "GUI tips (short):\n"
        << "  Local desktop: run ./cmdiqo-agent-linux-x64-setup --gui\n"
        << "  As root on desktop: xhost +SI:localuser:root && sudo --preserve-env=DISPLAY,XAUTHORITY ./cmdiqo-agent-linux-x64-setup --gui\n"
        << "  SSH with X11 forwarding: ssh -Y -C user@host && ./cmdiqo-agent-linux-x64-setup --gui\n"
        << "  Headless (Xvfb): sudo apt-get install -y xvfb && Xvfb :99 -screen 0 1280x720x24 & export DISPLAY=:99 && ./cmdiqo-agent-linux-x64-setup --gui\n";
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
        } else if (option == "--fresh") {
            options.fresh = true;
            options.install = true;
        } else if (option == "--configure" || option == "--config-edit") {
            options.configure = true;
        } else if (option == "--locate-config" || option == "--config-path") {
            options.locate_config = true;
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
        } else if (option == "--result-file" && needs_value(index, argc, "--result-file", error)) {
            options.result_file = std::filesystem::path(argv[++index]);
        } else if (option == "--help" || option == "-h" || option == "-?" || option == "/?") {
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
    return options.install || options.fresh || options.configure || options.locate_config
        || options.uninstall || options.status || options.test_hub
        || options.start || options.restart || options.stop
        || options.hub_url.has_value() || options.token.has_value() || options.agent_name.has_value()
        || options.config.has_value() || options.notify_specified;
}

bool needs_privilege(const Options& options) {
    return options.install || options.configure || options.uninstall || options.start || options.restart || options.stop;
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
    const std::string bundled = service.bundled_version(options.payload);
    std::cout << "Bundled version: " << bundled << '\n';
    if (installed) {
        std::cout << version_relation_message(compare_versions(*installed, bundled)) << '\n';
    }
}

int run_cli(const Options& options) {
    InstallerService service;
    if (options.locate_config) {
        std::cout << service.paths().config_file.string() << '\n';
        return 0;
    }
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
    const auto write_result_file = [&](const OperationResult& res) {
        if (options.result_file) {
            std::ofstream out(*options.result_file, std::ios::binary | std::ios::trunc);
            if (out) {
                out << (res.ok ? "OK:" : "ERROR:") << res.message << '\n';
            }
        }
    };

    if (needs_privilege(options) && !PlatformOperations::is_elevated()) {
        const std::string priv_msg = (current_platform() == Platform::linux)
            ? "This action requires root. Re-run with sudo. To avoid exposing a token in shell history, use --config with a root-readable JSON file."
            : "This action requires Administrator privileges. Open an elevated terminal or launch the GUI and accept UAC.";
        std::cerr << priv_msg << '\n';
        write_result_file(OperationResult::failure(priv_msg));
        return 1;
    }

    if (options.configure) {
        std::string error;
        Config config = load_config(service.paths());
        if (options.config) {
            std::ifstream input(*options.config);
            if (!input) {
                const std::string msg = "Could not open configuration file " + options.config->string() + ".";
                std::cerr << msg << '\n';
                write_result_file(OperationResult::failure(msg));
                return 2;
            }
            std::string document((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
            const auto parsed = config_from_json(document, error);
            if (!parsed) {
                std::cerr << error << '\n';
                write_result_file(OperationResult::failure(error));
                return 2;
            }
            config = *parsed;
        }

        if (!options.config && !options.token && !options.hub_url && !options.agent_name) {
            config = prompt_config(config, std::cin, std::cout);
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

        if (config.token.empty()) {
            constexpr const char* msg = "AGENT_TOKEN cannot be empty.";
            std::cerr << "[ERROR] " << msg << '\n';
            write_result_file(OperationResult::failure(msg));
            return 2;
        }

        const OperationResult save_result = save_config(service.paths(), config);
        if (!save_result.ok) {
            std::cerr << "[ERROR] " << save_result.message << '\n';
            write_result_file(save_result);
            return 1;
        }

        std::cout << "[OK] Configuration saved to " << service.paths().config_file.string() << '\n';

        if (PlatformOperations::service_status(service.paths()) == ServiceStatus::running) {
            std::cout << "[*] Restarting agent service to apply configuration...\n";
            const auto restart_result = PlatformOperations::restart_service(service.paths());
            if (restart_result.ok) {
                std::cout << "[OK] " << restart_result.message << '\n';
                write_result_file(OperationResult::success("Configuration saved and service restarted."));
            } else {
                std::cerr << "[WARN] " << restart_result.message << '\n';
                write_result_file(OperationResult::failure("Configuration saved, but service restart failed: " + restart_result.message));
                return 1;
            }
        } else {
            write_result_file(OperationResult::success("Configuration saved to " + service.paths().config_file.string() + "."));
        }
        return 0;
    }

    const auto progress = [](std::string_view message) {
        std::cout << "[*] " << message << '\n';
    };
    OperationResult result;
    if (options.install) {
        std::string error;
        Config config;
        if (options.fresh) {
            config = load_config(service.paths());
            if (options.config) {
                std::ifstream input(*options.config);
                if (!input) {
                    std::cerr << "Could not open configuration file " << options.config->string() << ".\n";
                    return 2;
                }
                std::string document((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
                const auto parsed = config_from_json(document, error);
                if (!parsed) {
                    std::cerr << error << '\n';
                    return 2;
                }
                config = *parsed;
            }
            config = prompt_config(config, std::cin, std::cout);
            if (options.hub_url) config.hub_url = *options.hub_url;
            if (options.token) config.token = *options.token;
            if (options.agent_name) config.agent_name = *options.agent_name;
            if (options.notify_specified) config.agent_connection_notify = options.notify;
        } else {
            config = configured_values(options, service.paths(), error);
            if (!error.empty()) {
                std::cerr << error << '\n';
                return 2;
            }
        }
        if (config.token.empty()) {
            const std::string err = "AGENT_TOKEN cannot be empty. Specify via prompt, --token, or --config.";
            std::cerr << "[ERROR] " << err << "\n";
            write_result_file(OperationResult::failure(err));
            return 2;
        }
        if (!service.has_payload(options.payload)) {
            const std::string err = "No embedded agent payload found and no payload archive was specified.";
            std::cerr << "[ERROR] " << err << "\n";
            write_result_file(OperationResult::failure(err));
            return 2;
        }
        result = service.install_or_upgrade(config, options.payload, progress);
    } else if (options.uninstall) {
        result = service.uninstall(options.purge, progress);
    } else if (options.start) {
        result = PlatformOperations::start_service(service.paths());
    } else if (options.restart) {
        result = PlatformOperations::restart_service(service.paths());
    } else {
        result = PlatformOperations::stop_service(service.paths());
    }
    write_result_file(result);
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

    const bool any_cli_arg = (argc > 1 && !options->gui);
    const bool cli = options->cli || has_cli_action(*options) || any_cli_arg
        || (!options->gui && current_platform() == Platform::linux && !PlatformOperations::has_graphical_session());
    if (cli) {
        return run_cli(*options);
    }

#if CMDIQO_ENABLE_GUI
#ifdef _WIN32
    FreeConsole();
#endif

    return run_gui(argc, argv, options->payload, options->elevation_config);
#else
    std::cerr << "Graphical GUI is not supported on this platform/architecture. Please run with --cli.\n";
    return 1;
#endif
}
