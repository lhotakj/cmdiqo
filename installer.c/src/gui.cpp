#include "cmdiqo/gui.hpp"
#include "embedded_logos.hpp"

#include <algorithm>
#include <cstddef>
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
#include <wx/bitmap.h>
#include <wx/button.h>
#include <wx/checkbox.h>
#include <wx/dcmemory.h>
#include <wx/frame.h>
#include <wx/graphics.h>
#include <wx/image.h>
#include <wx/imagpng.h>
#include <wx/mstream.h>
#include <wx/msgdlg.h>
#include <wx/panel.h>
#include <wx/settings.h>
#include <wx/sizer.h>
#include <wx/statbmp.h>
#include <wx/statusbr.h>
#include <wx/stattext.h>
#include <wx/textctrl.h>
#include <wx/timer.h>
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

wxBitmap draw_eye_icon(bool show_plain, int dip_w, int dip_h, const wxColour& bg_color, const wxColour& fg_color) {
    wxBitmap bitmap(dip_w, dip_h);
    wxMemoryDC dc(bitmap);
    dc.SetBackground(wxBrush(bg_color));
    dc.Clear();

    std::unique_ptr<wxGraphicsContext> gc(wxGraphicsContext::Create(dc));
    if (gc) {
        gc->SetAntialiasMode(wxANTIALIAS_DEFAULT);

        const double cx = dip_w / 2.0;
        const double cy = dip_h / 2.0;
        const double w = dip_w * 0.72;
        const double h = dip_h * 0.44;
        const double x0 = cx - w / 2.0;
        const double x1 = cx + w / 2.0;

        if (show_plain) {
            // 1. Open eye almond contour
            auto path = gc->CreatePath();
            path.MoveToPoint(x0, cy);
            path.AddQuadCurveToPoint(cx, cy - h, x1, cy);
            path.AddQuadCurveToPoint(cx, cy + h, x0, cy);
            path.CloseSubpath();
            gc->SetBrush(*wxTRANSPARENT_BRUSH);
            gc->SetPen(wxPen(fg_color, 1.8));
            gc->StrokePath(path);

            // 2. Center pupil
            const double pr = h * 0.60;
            gc->SetBrush(wxBrush(fg_color));
            gc->SetPen(*wxTRANSPARENT_PEN);
            gc->DrawEllipse(cx - pr, cy - pr, pr * 2.0, pr * 2.0);

            // 3. Highlight dot
            const double hr = pr * 0.35;
            gc->SetBrush(wxBrush(bg_color));
            gc->DrawEllipse(cx - pr * 0.35, cy - pr * 0.35, hr * 2.0, hr * 2.0);

            // 4. Eyelashes radiating upwards from top lid
            gc->SetPen(wxPen(fg_color, 1.4));
            constexpr int num_lashes = 5;
            for (int i = 0; i < num_lashes; ++i) {
                const double t = (i + 0.5) / num_lashes;
                const double lx = x0 + t * w;
                const double ly = (1 - t) * (1 - t) * cy + 2 * (1 - t) * t * (cy - h) + t * t * cy;
                const double lash_len = dip_h * 0.22;
                const double dx = (t - 0.5) * (dip_w * 0.18);
                gc->StrokeLine(lx, ly, lx + dx, ly - lash_len);
            }
        } else {
            // 1. Closed eye downward curved arc
            auto path = gc->CreatePath();
            path.MoveToPoint(x0, cy - h * 0.25);
            path.AddQuadCurveToPoint(cx, cy + h * 0.70, x1, cy - h * 0.25);
            gc->SetBrush(*wxTRANSPARENT_BRUSH);
            gc->SetPen(wxPen(fg_color, 2.0));
            gc->StrokePath(path);

            // 2. Eyelashes radiating downwards
            gc->SetPen(wxPen(fg_color, 1.4));
            constexpr int num_lashes = 5;
            for (int i = 0; i < num_lashes; ++i) {
                const double t = (i + 0.5) / num_lashes;
                const double lx = x0 + t * w;
                const double ly = (1 - t) * (1 - t) * (cy - h * 0.25) + 2 * (1 - t) * t * (cy + h * 0.70) + t * t * (cy - h * 0.25);
                const double lash_len = dip_h * 0.22;
                const double dx = (t - 0.5) * (dip_w * 0.18);
                gc->StrokeLine(lx, ly, lx + dx, ly + lash_len);
            }
        }
    } else {
        dc.SetPen(wxPen(fg_color, 2));
        dc.SetBrush(wxBrush(fg_color));
        const int cx = dip_w / 2;
        const int cy = dip_h / 2;
        if (show_plain) {
            dc.DrawEllipse(cx - dip_w / 3, cy - dip_h / 4, (dip_w * 2) / 3, dip_h / 2);
            dc.SetBrush(wxBrush(bg_color));
            dc.DrawCircle(cx, cy, dip_h / 5);
        } else {
            dc.DrawLine(cx - dip_w / 3, cy, cx + dip_w / 3, cy);
        }
    }
    dc.SelectObject(wxNullBitmap);
    return bitmap;
}

class TokenInputCtrl final : public wxPanel {
public:
    explicit TokenInputCtrl(wxWindow* parent)
        : wxPanel(parent, wxID_ANY, wxDefaultPosition, wxDefaultSize, wxBORDER_THEME | wxTAB_TRAVERSAL) {
        const wxColour bg = wxSystemSettings::GetColour(wxSYS_COLOUR_WINDOW);
        SetBackgroundColour(bg);

        auto* sizer = new wxBoxSizer(wxHORIZONTAL);
        text_ = new wxTextCtrl(this, wxID_ANY, "", wxDefaultPosition, wxDefaultSize, wxNO_BORDER | wxTE_PASSWORD);
        text_->SetBackgroundColour(bg);
        sizer->Add(text_, 1, wxALIGN_CENTER_VERTICAL | wxLEFT | wxTOP | wxBOTTOM, FromDIP(4));

        eye_button_ = new wxStaticBitmap(this, wxID_ANY, create_eye_bitmap(false));
        eye_button_->SetCursor(wxCursor(wxCURSOR_HAND));
        eye_button_->SetToolTip("Show token");
        sizer->Add(eye_button_, 0, wxALIGN_CENTER_VERTICAL | wxRIGHT | wxLEFT, FromDIP(6));

        SetSizer(sizer);

        eye_button_->Bind(wxEVT_LEFT_DOWN, [this](wxMouseEvent&) {
            toggle_visibility();
        });

        Bind(wxEVT_LEFT_DOWN, [this](wxMouseEvent&) {
            text_->SetFocus();
        });

#ifdef _WIN32
        HWND hwnd = reinterpret_cast<HWND>(text_->GetHWND());
        if (hwnd != nullptr) {
            LRESULT ch = SendMessageW(hwnd, EM_GETPASSWORDCHAR, 0, 0);
            if (ch != 0) {
                default_password_char_ = static_cast<wchar_t>(ch);
            }
        }
#endif
    }

    void toggle_visibility() {
        show_token_ = !show_token_;
        eye_button_->SetBitmap(create_eye_bitmap(show_token_));
        eye_button_->SetToolTip(show_token_ ? "Hide token" : "Show token");

#ifdef _WIN32
        HWND hwnd = reinterpret_cast<HWND>(text_->GetHWND());
        if (hwnd != nullptr) {
            SendMessageW(hwnd, EM_SETPASSWORDCHAR, show_token_ ? 0 : default_password_char_, 0);
            text_->Refresh();
        }
#else
        long style = text_->GetWindowStyleFlag();
        if (show_token_) {
            style &= ~wxTE_PASSWORD;
        } else {
            style |= wxTE_PASSWORD;
        }
        text_->SetWindowStyleFlag(style);
        text_->Refresh();
#endif
    }

    wxString GetValue() const {
        return text_->GetValue();
    }

    void SetValue(const wxString& value) {
        text_->SetValue(value);
    }

    wxTextCtrl* GetTextCtrl() {
        return text_;
    }

private:
    wxBitmap create_eye_bitmap(bool show_plain) const {
        const int width = FromDIP(24);
        const int height = FromDIP(18);
        const wxColour bg = wxSystemSettings::GetColour(wxSYS_COLOUR_WINDOW);
        const wxColour fg = wxColour(90, 95, 105);
        return draw_eye_icon(show_plain, width, height, bg, fg);
    }

    wxTextCtrl* text_{nullptr};
    wxStaticBitmap* eye_button_{nullptr};
    bool show_token_{false};
#ifdef _WIN32
    wchar_t default_password_char_{L'\u25CF'};
#endif
};

class InstallerFrame final : public wxFrame {
public:
    InstallerFrame(
        std::optional<std::filesystem::path> payload,
        std::optional<std::filesystem::path> initial_config)
        : wxFrame(nullptr, wxID_ANY, "Cmdiqo Agent Setup", wxDefaultPosition, wxSize(720, 680)),
          service_(),
          payload_(std::move(payload)),
          initial_config_(std::move(initial_config)),
          service_status_timer_(this) {
        auto* panel = new wxPanel(this);
        auto* layout = new wxBoxSizer(wxVERTICAL);

        if (wxImage::FindHandler(wxBITMAP_TYPE_PNG) == nullptr) {
            wxImage::AddHandler(new wxPNGHandler);
        }
        wxBitmap icon_bmp = platform_logo();
        if (icon_bmp.IsOk()) {
            wxIcon frame_icon;
            frame_icon.CopyFromBitmap(icon_bmp);
            if (frame_icon.IsOk()) {
                SetIcon(frame_icon);
            }
        }
        auto* header = new wxBoxSizer(wxHORIZONTAL);
        auto* title = new wxStaticText(panel, wxID_ANY, "Cmdiqo Agent native installer");
        header->Add(title, 1, wxALL | wxALIGN_CENTER_VERTICAL, 12);
        auto* logo = new wxStaticBitmap(panel, wxID_ANY, icon_bmp);
        logo->SetToolTip(wxGetOsDescription());
        header->Add(logo, 0, wxTOP | wxRIGHT | wxBOTTOM, 12);
        layout->Add(header, 0, wxEXPAND);
        state_ = new wxStaticText(panel, wxID_ANY, "");
        layout->Add(state_, 0, wxLEFT | wxRIGHT | wxBOTTOM | wxEXPAND, 12);

        auto* form = new wxFlexGridSizer(2, 8, 10);
        form->AddGrowableCol(1, 1);
        add_field(panel, form, "Hub URL", hub_url_ = new wxTextCtrl(panel, wxID_ANY));
        add_field(panel, form, "Token", token_ = new TokenInputCtrl(panel));
        add_field(panel, form, "Agent name", agent_name_ = new wxTextCtrl(panel, wxID_ANY));
        form->Add(new wxStaticText(panel, wxID_ANY, "Slack notification"), 0, wxALIGN_CENTER_VERTICAL);
        notify_ = new wxCheckBox(panel, wxID_ANY, "Notify when the agent connects");
        form->Add(notify_, 0, wxALIGN_CENTER_VERTICAL);
        layout->Add(form, 0, wxALL | wxEXPAND, 12);

        auto* installation_buttons = new wxBoxSizer(wxHORIZONTAL);
        add_button(panel, installation_buttons, "Install / Upgrade / Repair", [this] { install(); });
        add_button(panel, installation_buttons, "Update config", [this] { update_config(); });
        add_button(panel, installation_buttons, "Test Hub TCP", [this] { test_hub(); });
        add_button(panel, installation_buttons, "Refresh State", [this] { refresh(true); });
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
        log_->SetBackgroundColour(wxColour(0, 0, 0));
        log_->SetForegroundColour(wxColour(144, 238, 144));
        layout->Add(log_, 1, wxLEFT | wxRIGHT | wxBOTTOM | wxEXPAND, 12);
        panel->SetSizer(layout);
        status_bar_ = CreateStatusBar(2);
        const int status_widths[] = {FromDIP(20), -1};
        status_bar_->SetStatusWidths(2, status_widths);
        status_icon_ = new wxStaticBitmap(status_bar_, wxID_ANY, make_status_icon(wxColour(128, 128, 128)));
        position_status_icon();
        status_bar_->Bind(wxEVT_SIZE, [this](wxSizeEvent& event) {
            position_status_icon();
            event.Skip();
        });
        Bind(wxEVT_SIZE, [this](wxSizeEvent& event) {
            layout_state_summary();
            event.Skip();
        });
        Bind(wxEVT_TIMER, &InstallerFrame::on_service_status_timer, this);

        Centre();
        Bind(wxEVT_CLOSE_WINDOW, [this](wxCloseEvent& event) {
            if (operation_in_progress_) {
                wxMessageBox("Wait for the current installer operation to finish.", "Cmdiqo Agent Setup", wxOK | wxICON_INFORMATION, this);
                event.Veto();
                return;
            }
            event.Skip();
        });
        refresh(true);
    }

private:
    wxBitmap platform_logo() const {
        const unsigned char* data = resources::linux_logo.data();
        std::size_t size = resources::linux_logo.size();
        if (current_platform() == Platform::windows) {
            data = resources::win_logo.data();
            size = resources::win_logo.size();
        }
        wxMemoryInputStream stream(data, size);
        wxImage image(stream, wxBITMAP_TYPE_PNG);
        if (!image.IsOk()) {
            return {};
        }
        const int logo_size = FromDIP(48);
        image.Rescale(logo_size, logo_size, wxIMAGE_QUALITY_HIGH);
        return wxBitmap(image);
    }

    static void add_field(wxWindow* parent, wxFlexGridSizer* form, const wxString& label, wxWindow* control) {
        form->Add(new wxStaticText(parent, wxID_ANY, label), 0, wxALIGN_CENTER_VERTICAL);
        form->Add(control, 1, wxEXPAND);
    }

    void add_button(wxPanel* panel, wxBoxSizer* container, const wxString& label, const std::function<void()>& handler) {
        auto* button = new wxButton(panel, wxID_ANY, label);
        button->Bind(wxEVT_BUTTON, [handler](wxCommandEvent&) { handler(); });
        buttons_.push_back(button);
        container->Add(button, 0, wxRIGHT, 8);
    }

    void append_log_segment(const wxString& text, const wxColour& color, bool bold) {
        wxTextAttr style(color, wxColour(0, 0, 0));
        wxFont font = log_->GetFont();
        font.SetWeight(bold ? wxFONTWEIGHT_BOLD : wxFONTWEIGHT_NORMAL);
        style.SetFont(font);
        log_->SetDefaultStyle(style);
        log_->AppendText(text);
    }

    void append_log(const std::string& message) {
        constexpr std::string_view ok_prefix = "[OK] ";
        constexpr std::string_view error_prefix = "[ERROR] ";
        if (message.starts_with(ok_prefix)) {
            append_log_segment("[OK]", wxColour(144, 238, 144), true);
            append_log_segment(wxString::FromUTF8(message.substr(ok_prefix.size())), wxColour(144, 238, 144), false);
        } else if (message.starts_with(error_prefix)) {
            append_log_segment("[ERROR]", wxColour(255, 99, 71), true);
            append_log_segment(wxString::FromUTF8(message.substr(error_prefix.size())), wxColour(144, 238, 144), false);
        } else {
            append_log_segment(wxString::FromUTF8(message), wxColour(144, 238, 144), false);
        }
        append_log_segment("\n", wxColour(144, 238, 144), false);
        append_gui_log(message);
    }

    void layout_state_summary() {
        wxWindow* parent = state_->GetParent();
        const int available_width = parent->GetClientSize().GetWidth() - FromDIP(24);
        state_->Wrap(std::max(FromDIP(250), available_width));
        parent->Layout();
    }

    wxBitmap make_status_icon(const wxColour& color) const {
        const int size = FromDIP(12);
        wxBitmap bitmap(size, size);
        wxMemoryDC context(bitmap);
        context.SetBackground(wxBrush(status_bar_->GetBackgroundColour()));
        context.Clear();
        context.SetBrush(wxBrush(color));
        context.SetPen(wxPen(color));
        context.DrawCircle(size / 2, size / 2, size / 2 - 1);
        context.SelectObject(wxNullBitmap);
        return bitmap;
    }

    void position_status_icon() {
        if (status_bar_ == nullptr || status_icon_ == nullptr) {
            return;
        }
        wxRect field;
        status_bar_->GetFieldRect(0, field);
        const int icon_size = FromDIP(12);
        status_icon_->SetSize(
            field.x + (field.width - icon_size) / 2,
            field.y + (field.height - icon_size) / 2,
            icon_size,
            icon_size);
    }

    void set_service_status_text(const std::string& text, const wxColour& color) {
        status_icon_->SetBitmap(make_status_icon(color));
        status_icon_->Show();
        status_bar_->SetStatusText(wxString::FromUTF8(text), 1);
    }

    void show_starting_service_status() {
        service_starting_ = true;
        starting_icon_visible_ = true;
        service_status_timer_.Start(500);
        set_service_status_text("service is being started ...", wxColour(255, 140, 0));
    }

    void update_service_status(ServiceStatus status, const std::optional<std::string>& version) {
        service_starting_ = false;
        service_status_timer_.Stop();
        if (status == ServiceStatus::running) {
            set_service_status_text("service running version " + version.value_or("unknown"), wxColour(50, 205, 50));
        } else if (status == ServiceStatus::stopped) {
            set_service_status_text("service stopped version " + version.value_or("unknown"), wxColour(30, 144, 255));
        } else {
            set_service_status_text("service not installed ...", wxColour(192, 192, 192));
        }
    }

    void on_service_status_timer(wxTimerEvent&) {
        if (!service_starting_) {
            return;
        }
        starting_icon_visible_ = !starting_icon_visible_;
        if (starting_icon_visible_) {
            set_service_status_text("service is being started ...", wxColour(255, 140, 0));
        } else {
            status_icon_->Hide();
        }
    }

    Config current_config() const {
        return {
            .hub_url = hub_url_->GetValue().ToStdString(),
            .token = token_->GetValue().ToStdString(),
            .agent_name = agent_name_->GetValue().ToStdString(),
            .agent_connection_notify = notify_->GetValue(),
        };
    }

    void refresh(bool reload_form_fields = false) {
        if (reload_form_fields || initial_config_) {
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
                    input.close();
                    std::string error;
                    if (!document.empty()) {
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
            if (!config.hub_url.empty() || hub_url_->GetValue().empty()) {
                hub_url_->SetValue(wxString::FromUTF8(config.hub_url));
            }
            if (!config.token.empty() || token_->GetValue().empty()) {
                token_->SetValue(wxString::FromUTF8(config.token));
            }
            if (!config.agent_name.empty() || agent_name_->GetValue().empty()) {
                agent_name_->SetValue(wxString::FromUTF8(config.agent_name));
            }
            notify_->SetValue(config.agent_connection_notify);
        }

        const std::string bundled = service_.bundled_version(payload_);
        const auto installed = service_.installed_version();

        std::string summary;
        if (!installed || compare_versions(*installed, bundled) == VersionRelation::older) {
            if (!bundled.empty()) {
                summary = "New version " + bundled + " can be installed\n";
            } else {
                summary = "New version can be installed\n";
            }
        } else if (compare_versions(*installed, bundled) == VersionRelation::same) {
            summary = "You're running the latest version\n";
        } else {
            summary = "Installed version is newer than this installer\n";
        }

        summary += "Configuration file: " + service_.paths().config_file.string();

        state_->SetLabel(wxString::FromUTF8(summary));
        layout_state_summary();
        update_service_status(PlatformOperations::service_status(service_.paths()), installed);
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

    void run_background(const std::function<OperationResult()>& work, bool reload_form_fields = false) {
        operation_in_progress_ = true;
        for (auto* button : buttons_) {
            button->Disable();
        }
        std::thread([this, work, reload_form_fields] {
            const OperationResult result = work();
            wxTheApp->CallAfter([this, result, reload_form_fields] {
                operation_in_progress_ = false;
                append_log((result.ok ? "[OK] " : "[ERROR] ") + result.message);
                for (auto* button : buttons_) {
                    button->Enable();
                }
                refresh(reload_form_fields);
            });
        }).detach();
    }

    void install() {
        const Config config = current_config();
        if (!parse_hub_endpoint(config.hub_url)) {
            wxMessageBox("Enter a valid ws:// or wss:// Hub URL.", "Cmdiqo Agent Setup", wxOK | wxICON_WARNING, this);
            return;
        }
        if (config.token.empty()) {
            wxMessageBox("Token cannot be empty. Please enter an agent token.", "Cmdiqo Agent Setup", wxOK | wxICON_WARNING, this);
            return;
        }
        if (!service_.has_payload(payload_)) {
            wxMessageBox(
                "No embedded agent payload found and no payload archive was specified.",
                "Cmdiqo Agent Setup", wxOK | wxICON_ERROR, this);
            return;
        }
        show_starting_service_status();
        run_background([this, config] {
            if (PlatformOperations::is_elevated()) {
                return service_.install_or_upgrade(config, payload_, [this](std::string_view message) {
                    const std::string copy(message);
                    wxTheApp->CallAfter([this, copy] { append_log(copy); });
                });
            }
            std::string staging_error;
            const auto staged = stage_elevation_config(staging_error);
            if (!staged) {
                return OperationResult::failure("Could not stage configuration: " + staging_error);
            }
            std::vector<std::string> args{
                "--cli", "--install",
                "--config", staged->string()
            };
            if (payload_) {
                args.push_back("--payload");
                args.push_back(std::filesystem::absolute(*payload_).string());
            }
            const auto res = PlatformOperations::run_elevated(args);
            std::error_code ec;
            std::filesystem::remove(*staged, ec);
            return res;
        }, true);
    }

    void update_config() {
        const Config config = current_config();
        if (!parse_hub_endpoint(config.hub_url)) {
            wxMessageBox("Enter a valid ws:// or wss:// Hub URL.", "Cmdiqo Agent Setup", wxOK | wxICON_WARNING, this);
            return;
        }
        if (config.token.empty()) {
            wxMessageBox("Token cannot be empty. Please enter an agent token.", "Cmdiqo Agent Setup", wxOK | wxICON_WARNING, this);
            return;
        }
        if (config.agent_name.empty()) {
            wxMessageBox("Agent name cannot be empty.", "Cmdiqo Agent Setup", wxOK | wxICON_WARNING, this);
            return;
        }

        append_log("Saving updated configuration...");
        run_background([this, config] {
            if (PlatformOperations::is_elevated()) {
                const auto save_res = save_config(service_.paths(), config);
                if (!save_res.ok) {
                    return save_res;
                }
                if (PlatformOperations::service_status(service_.paths()) == ServiceStatus::running) {
                    const auto restart_res = PlatformOperations::restart_service(service_.paths());
                    if (!restart_res.ok) {
                        return OperationResult::failure("Configuration saved, but service restart failed: " + restart_res.message);
                    }
                    return OperationResult::success("Configuration saved and service restarted.");
                }
                return OperationResult::success("Configuration saved successfully.");
            }

            std::string staging_error;
            const auto staged = stage_elevation_config(staging_error);
            if (!staged) {
                return OperationResult::failure("Could not stage configuration: " + staging_error);
            }
            std::vector<std::string> args{
                "--cli", "--configure",
                "--config", staged->string()
            };
            const auto res = PlatformOperations::run_elevated(args);
            std::error_code ec;
            std::filesystem::remove(*staged, ec);
            return res;
        }, true);
    }

    void test_hub() {
        const std::string url = hub_url_->GetValue().ToStdString();
        run_background([url] { return test_hub_tcp(url); });
    }

    void service_action(const std::string& action) {
        if (action == "start" && PlatformOperations::service_status(service_.paths()) == ServiceStatus::running) {
            append_log("[OK] Service " + service_.paths().service_name + " is already running.");
            return;
        }
        if (action == "stop" && PlatformOperations::service_status(service_.paths()) == ServiceStatus::stopped) {
            append_log("[OK] Service " + service_.paths().service_name + " is already stopped.");
            return;
        }
        if (action == "start" || action == "restart") {
            show_starting_service_status();
        }
        run_background([this, action] {
            if (PlatformOperations::is_elevated()) {
                if (action == "start") {
                    return PlatformOperations::start_service(service_.paths());
                }
                if (action == "restart") {
                    return PlatformOperations::restart_service(service_.paths());
                }
                return PlatformOperations::stop_service(service_.paths());
            }
            return PlatformOperations::run_elevated({"--cli", "--" + action});
        });
    }

    void uninstall(bool purge) {
        const wxString message = purge
            ? "Permanently remove the Cmdiqo Agent, configuration, token, and logs?"
            : "Remove the Cmdiqo Agent service and executable but keep configuration?";
        if (wxMessageBox(message, "Confirm uninstall", wxYES_NO | wxNO_DEFAULT | wxICON_WARNING, this) != wxYES) {
            return;
        }
        run_background([this, purge] {
            if (PlatformOperations::is_elevated()) {
                return service_.uninstall(purge, [this](std::string_view message) {
                    const std::string copy(message);
                    wxTheApp->CallAfter([this, copy] { append_log(copy); });
                });
            }
            std::vector<std::string> args{"--cli", "--uninstall"};
            if (purge) {
                args.push_back("--purge");
            }
            const auto res = PlatformOperations::run_elevated(args);
            if (!res.ok) {
                return res;
            }
            return OperationResult::success("Uninstalled service " + service_.paths().service_name + ".");
        });
    }

    InstallerService service_;
    std::optional<std::filesystem::path> payload_;
    std::optional<std::filesystem::path> initial_config_;
    wxStaticText* state_{};
    wxTextCtrl* hub_url_{};
    TokenInputCtrl* token_{};
    wxTextCtrl* agent_name_{};
    wxCheckBox* notify_{};
    wxTextCtrl* log_{};
    wxStatusBar* status_bar_{};
    wxStaticBitmap* status_icon_{};
    wxTimer service_status_timer_;
    std::vector<wxButton*> buttons_;
    bool operation_in_progress_{false};
    bool service_starting_{false};
    bool starting_icon_visible_{false};
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
