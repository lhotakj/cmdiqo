#include "cmdiqo/gui.hpp"

#include <cstdlib>
#include <fstream>
#include <iostream>
#include <functional>
#include <iterator>
#include <system_error>
#include <thread>
#include <utility>
#include <vector>

#include <wx/app.h>
#include <wx/button.h>
#include <wx/checkbox.h>
#include <wx/frame.h>
#include <wx/msgdlg.h>
#include <wx/panel.h>
#include <wx/sizer.h>
#include <wx/stattext.h>
#include <wx/textctrl.h>
#include <wx/utils.h>

namespace cmdiqo::installer {
namespace {

bool is_staged_elevation_config(const std::filesystem::path& path) {
#ifdef _WIN32
    return path.filename() == "elevation-config.json" && path.parent_path().filename() == "Installer"
        && path.parent_path().parent_path().filename() == "Cmdiqo";
#else
    return path.filename() == "elevation-config.json" && path.parent_path().filename() == "cmdiqo-installer";
#endif
}

std::optional<std::filesystem::path> gui_state_directory() {
#ifdef _WIN32
    const char* local_app_data = std::getenv("LOCALAPPDATA");
    if (local_app_data == nullptr || *local_app_data == '\0') {
        return std::nullopt;
    }
    return std::filesystem::path(local_app_data) / "Cmdiqo" / "Installer";
#else
    const char* configured = std::getenv("XDG_CONFIG_HOME");
    const char* home = std::getenv("HOME");
    if (configured != nullptr && *configured != '\0') {
        return std::filesystem::path(configured) / "cmdiqo-installer";
    }
    if (home != nullptr && *home != '\0') {
        return std::filesystem::path(home) / ".config" / "cmdiqo-installer";
    }
    return std::nullopt;
#endif
}

void append_gui_log(std::string_view message) {
    const auto directory = gui_state_directory();
    if (!directory) {
        return;
    }

    std::error_code error;
    std::filesystem::create_directories(*directory, error);
    if (error) {
        return;
    }
    std::ofstream output(*directory / "installer.log", std::ios::app);
    if (output) {
        output << message << '\n';
    }
}

class InstallerFrame final : public wxFrame {
public:
    InstallerFrame(
        std::optional<std::filesystem::path> payload,
        std::optional<std::filesystem::path> initial_config)
        : wxFrame(nullptr, wxID_ANY, "Cmdiqo Agent Setup", wxDefaultPosition, wxSize(720, 680)),
          service_(),
          payload_(std::move(payload)),
          initial_config_(std::move(initial_config)) {
        auto* panel = new wxPanel(this);
        auto* layout = new wxBoxSizer(wxVERTICAL);

        auto* title = new wxStaticText(panel, wxID_ANY, "Cmdiqo Agent native installer");
        wxFont title_font = title->GetFont();
        title_font.SetPointSize(title_font.GetPointSize() + 4);
        title_font.MakeBold();
        title->SetFont(title_font);
        layout->Add(title, 0, wxALL, 12);
        state_ = new wxStaticText(panel, wxID_ANY, "");
        state_->Wrap(650);
        layout->Add(state_, 0, wxLEFT | wxRIGHT | wxBOTTOM | wxEXPAND, 12);

        auto* form = new wxFlexGridSizer(2, 8, 10);
        form->AddGrowableCol(1, 1);
        add_field(panel, form, "Hub URL", hub_url_ = new wxTextCtrl(panel, wxID_ANY));
        add_field(panel, form, "Token", token_ = new wxTextCtrl(panel, wxID_ANY, "", wxDefaultPosition, wxDefaultSize, wxTE_PASSWORD));
        add_field(panel, form, "Agent name", agent_name_ = new wxTextCtrl(panel, wxID_ANY));
        form->Add(new wxStaticText(panel, wxID_ANY, "Slack notification"), 0, wxALIGN_CENTER_VERTICAL);
        notify_ = new wxCheckBox(panel, wxID_ANY, "Notify when the agent connects");
        form->Add(notify_, 0, wxALIGN_CENTER_VERTICAL);
        layout->Add(form, 0, wxALL | wxEXPAND, 12);

        auto* installation_buttons = new wxBoxSizer(wxHORIZONTAL);
        add_button(panel, installation_buttons, "Install / Upgrade / Repair", [this] { install(); });
        add_button(panel, installation_buttons, "Test Hub TCP", [this] { test_hub(); });
        add_button(panel, installation_buttons, "Refresh State", [this] { refresh(); });
        layout->Add(installation_buttons, 0, wxLEFT | wxRIGHT | wxBOTTOM, 12);

        auto* service_buttons = new wxBoxSizer(wxHORIZONTAL);
        add_button(panel, service_buttons, "Start", [this] { service_action("start"); });
        add_button(panel, service_buttons, "Restart", [this] { service_action("restart"); });
        add_button(panel, service_buttons, "Stop", [this] { service_action("stop"); });
        add_button(panel, service_buttons, "Uninstall (keep config)", [this] { uninstall(false); });
        add_button(panel, service_buttons, "Purge and uninstall", [this] { uninstall(true); });
        layout->Add(service_buttons, 0, wxLEFT | wxRIGHT | wxBOTTOM, 12);

        layout->Add(new wxStaticText(panel, wxID_ANY, "Progress and log"), 0, wxLEFT | wxRIGHT | wxBOTTOM, 12);
        log_ = new wxTextCtrl(panel, wxID_ANY, "", wxDefaultPosition, wxDefaultSize,
                              wxTE_MULTILINE | wxTE_READONLY | wxTE_RICH2);
        layout->Add(log_, 1, wxLEFT | wxRIGHT | wxBOTTOM | wxEXPAND, 12);
        panel->SetSizer(layout);

        Centre();
        Bind(wxEVT_CLOSE_WINDOW, [this](wxCloseEvent& event) {
            if (operation_in_progress_) {
                wxMessageBox("Wait for the current installer operation to finish.", "Cmdiqo Agent Setup", wxOK | wxICON_INFORMATION, this);
                event.Veto();
                return;
            }
            event.Skip();
        });
        refresh();
    }

private:
    static void add_field(wxWindow* parent, wxFlexGridSizer* form, const wxString& label, wxTextCtrl* control) {
        form->Add(new wxStaticText(parent, wxID_ANY, label), 0, wxALIGN_CENTER_VERTICAL);
        form->Add(control, 1, wxEXPAND);
    }

    void add_button(wxPanel* panel, wxBoxSizer* container, const wxString& label, const std::function<void()>& handler) {
        auto* button = new wxButton(panel, wxID_ANY, label);
        button->Bind(wxEVT_BUTTON, [handler](wxCommandEvent&) { handler(); });
        buttons_.push_back(button);
        container->Add(button, 0, wxRIGHT, 8);
    }

    void append_log(const std::string& message) {
        log_->AppendText(wxString::FromUTF8(message) + "\n");
        append_gui_log(message);
    }

    Config current_config() const {
        return {
            .hub_url = hub_url_->GetValue().ToStdString(),
            .token = token_->GetValue().ToStdString(),
            .agent_name = agent_name_->GetValue().ToStdString(),
            .agent_connection_notify = notify_->GetValue(),
        };
    }

    bool require_elevation() {
        if (PlatformOperations::is_elevated()) {
            return true;
        }
        std::string staging_error;
        const auto staged_config = stage_elevation_config(staging_error);
        if (!staged_config) {
            append_log("Elevation configuration staging failed: " + staging_error);
            const std::string elevation_message =
                "Could not safely stage the current form for elevation: " + staging_error
                + "\nSee ~/.config/cmdiqo-installer/installer.log for diagnostics.";
            wxMessageBox(
                wxString::FromUTF8(elevation_message.c_str()),
                "Cmdiqo Agent Setup", wxOK | wxICON_ERROR, this);
            return false;
        }
        const wxString explanation = current_platform() == Platform::linux
            ? "Installation changes system paths and systemd. Cmdiqo will request permission through your system's pkexec dialog and reopen the native GUI with this form."
            : "Installation changes Program Files and the Service Control Manager. Cmdiqo will request Administrator permission and reopen the native GUI with this form.";
        append_log(explanation.ToStdString());
        if (!PlatformOperations::relaunch_elevated({"--gui", "--elevation-config", staged_config->string()}, Logger{})) {
            wxMessageBox("Elevation was not granted. Use the CLI fallback from an elevated terminal.", "Cmdiqo Agent Setup", wxOK | wxICON_ERROR, this);
        }
        return false;
    }

    void refresh() {
        Config config = load_config(service_.paths());
        if (initial_config_) {
            std::error_code staged_status_error;
            if (!is_staged_elevation_config(*initial_config_)
                || std::filesystem::is_symlink(*initial_config_, staged_status_error)
                || staged_status_error) {
                append_log("Ignored an invalid staged elevation configuration path.");
            } else {
                std::ifstream input(*initial_config_);
                std::string document((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
                std::string error;
                if (input) {
                    if (const auto staged = config_from_json(document, error)) {
                        config = *staged;
                        std::error_code remove_error;
                        std::filesystem::remove(*initial_config_, remove_error);
                        if (remove_error) {
                            append_log("Could not remove staged elevation configuration.");
                        }
                    } else {
                        append_log("Could not read staged configuration: " + error);
                    }
                } else {
                    append_log("Could not open requested configuration file.");
                }
            }
            initial_config_.reset();
        }
        hub_url_->SetValue(wxString::FromUTF8(config.hub_url));
        token_->SetValue(wxString::FromUTF8(config.token));
        agent_name_->SetValue(wxString::FromUTF8(config.agent_name));
        notify_->SetValue(config.agent_connection_notify);

        const auto payload = service_.locate_payload(payload_);
        const std::string bundled = payload ? service_.bundled_version(*payload) : "payload not found";
        const auto installed = service_.installed_version();
        std::string summary = "Platform: " + platform_name(current_platform()) + "\n";
        summary += "Bundled version: " + bundled + "\n";
        summary += "Installed version: " + installed.value_or("not installed") + "\n";
        if (installed && payload) {
            summary += version_relation_message(compare_versions(*installed, bundled)) + "\n";
        } else if (!installed) {
            summary += "Fresh install: configure the Hub and select Install.\n";
        } else {
            summary += "Version comparison is unavailable until the expected platform payload is beside this installer.\n";
        }

        const ServiceStatus service_status = PlatformOperations::service_status(service_.paths());
        const char* status = service_status == ServiceStatus::running
            ? "running" : service_status == ServiceStatus::stopped ? "stopped" : "not installed";
        summary += "Service " + service_.paths().service_name + ": " + status;
        state_->SetLabel(wxString::FromUTF8(summary));
        append_log("State refreshed.");
    }

    std::optional<std::filesystem::path> stage_elevation_config(std::string& failure) const {
        const auto base = gui_state_directory();
        if (!base) {
            failure = "the user configuration directory is unavailable";
            return std::nullopt;
        }
        std::error_code error;
        std::filesystem::create_directories(*base, error);
        if (error) {
            failure = "could not create " + base->string() + ": " + error.message();
            return std::nullopt;
        }
        const std::filesystem::path staged = *base / "elevation-config.json";
        const bool is_link = std::filesystem::is_symlink(staged, error);
        if (error && error != std::errc::no_such_file_or_directory) {
            failure = "could not inspect " + staged.string() + ": " + error.message();
            return std::nullopt;
        }
        if (is_link) {
            failure = "refusing to replace symbolic link " + staged.string();
            return std::nullopt;
        }
        std::ofstream output(staged, std::ios::binary | std::ios::trunc);
        if (!output) {
            failure = "could not open " + staged.string() + " for writing";
            return std::nullopt;
        }
        output << config_to_json(current_config());
        output.close();
        if (!output) {
            failure = "could not write " + staged.string();
            return std::nullopt;
        }
#ifndef _WIN32
        std::filesystem::permissions(
            staged,
            std::filesystem::perms::owner_read | std::filesystem::perms::owner_write,
            std::filesystem::perm_options::replace,
            error);
        if (error) {
            failure = "could not secure " + staged.string() + ": " + error.message();
            return std::nullopt;
        }
#endif
        return staged;
    }

    void run_background(const std::function<OperationResult()>& work) {
        operation_in_progress_ = true;
        for (auto* button : buttons_) {
            button->Disable();
        }
        std::thread([this, work] {
            const OperationResult result = work();
            wxTheApp->CallAfter([this, result] {
                operation_in_progress_ = false;
                append_log((result.ok ? "[OK] " : "[ERROR] ") + result.message);
                for (auto* button : buttons_) {
                    button->Enable();
                }
                refresh();
            });
        }).detach();
    }

    void install() {
        const Config config = current_config();
        if (!parse_hub_endpoint(config.hub_url)) {
            wxMessageBox("Enter a valid ws:// or wss:// Hub URL.", "Cmdiqo Agent Setup", wxOK | wxICON_WARNING, this);
            return;
        }
        if (!require_elevation()) {
            return;
        }
        const auto payload = service_.locate_payload(payload_);
        if (!payload) {
            const wxString missing_payload = "The exact platform payload was not found. Put "
                + wxString::FromUTF8(payload_file_name(current_platform()))
                + " beside the installer or pass --payload from the CLI.";
            wxMessageBox(
                missing_payload, "Cmdiqo Agent Setup", wxOK | wxICON_ERROR, this);
            return;
        }
        run_background([this, config, payload] {
            return service_.install_or_upgrade(config, *payload, [this](std::string_view message) {
                const std::string copy(message);
                wxTheApp->CallAfter([this, copy] { append_log(copy); });
            });
        });
    }

    void test_hub() {
        const std::string url = hub_url_->GetValue().ToStdString();
        run_background([url] { return test_hub_tcp(url); });
    }

    void service_action(const std::string& action) {
        if (!require_elevation()) {
            return;
        }
        run_background([this, action] {
            if (action == "start") {
                return PlatformOperations::start_service(service_.paths());
            }
            if (action == "restart") {
                return PlatformOperations::restart_service(service_.paths());
            }
            return PlatformOperations::stop_service(service_.paths());
        });
    }

    void uninstall(bool purge) {
        const wxString message = purge
            ? "Permanently remove the Cmdiqo Agent, configuration, token, and logs?"
            : "Remove the Cmdiqo Agent service and executable but keep configuration?";
        if (wxMessageBox(message, "Confirm uninstall", wxYES_NO | wxNO_DEFAULT | wxICON_WARNING, this) != wxYES) {
            return;
        }
        if (!require_elevation()) {
            return;
        }
        run_background([this, purge] {
            return service_.uninstall(purge, [this](std::string_view message) {
                const std::string copy(message);
                wxTheApp->CallAfter([this, copy] { append_log(copy); });
            });
        });
    }

    InstallerService service_;
    std::optional<std::filesystem::path> payload_;
    std::optional<std::filesystem::path> initial_config_;
    wxStaticText* state_{};
    wxTextCtrl* hub_url_{};
    wxTextCtrl* token_{};
    wxTextCtrl* agent_name_{};
    wxCheckBox* notify_{};
    wxTextCtrl* log_{};
    std::vector<wxButton*> buttons_;
    bool operation_in_progress_{false};
};

class InstallerApp final : public wxApp {
public:
    InstallerApp(
        std::optional<std::filesystem::path> payload,
        std::optional<std::filesystem::path> initial_config)
        : payload_(std::move(payload)), initial_config_(std::move(initial_config)) {}

    bool OnInit() override {
        auto* frame = new InstallerFrame(payload_, initial_config_);
        frame->Show(true);
        return true;
    }

private:
    std::optional<std::filesystem::path> payload_;
    std::optional<std::filesystem::path> initial_config_;
};

}  // namespace

int run_gui(
    int& argc,
    char** argv,
    const std::optional<std::filesystem::path>& payload,
    const std::optional<std::filesystem::path>& initial_config) {
    // If the user explicitly requested a GUI on a headless Linux host, fail fast
    // with a clear message instead of letting wxWidgets attempt initialization
    // (which can lead to allocator errors / double-free on some setups).
    if (current_platform() == Platform::linux && !PlatformOperations::has_graphical_session()) {
        std::cerr << "No graphical session detected; run without --gui for CLI mode, or start an X/Wayland session (or use X forwarding / Xvfb) to use the GUI." << std::endl;
        return 1;
    }

    auto* app = new InstallerApp(payload, initial_config);
    wxApp::SetInstance(app);
    if (!wxEntryStart(argc, argv)) {
        // Avoid deleting 'app' here: wxEntryStart may already free internal resources
        // and deleting it again can cause a double-free in some wxWidgets builds.
        return 1;
    }
    if (!wxTheApp->CallOnInit()) {
        wxEntryCleanup();
        return 1;
    }
    const int code = wxTheApp->OnRun();
    wxTheApp->OnExit();
    wxEntryCleanup();
    return code;
}

}  // namespace cmdiqo::installer
