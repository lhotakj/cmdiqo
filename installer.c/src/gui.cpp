#include "cmdiqo/gui.hpp"
#include "embedded_logos.hpp"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <functional>
#include <iterator>
#include <memory>
#include <system_error>
#include <thread>
#include <utility>
#include <vector>

#include <wx/app.h>
#include <wx/bitmap.h>
#include <wx/checkbox.h>
#include <wx/control.h>
#include <wx/dcbuffer.h>
#include <wx/dcclient.h>
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
#include <wx/stattext.h>
#include <wx/textctrl.h>
#include <wx/timer.h>
#include <wx/utils.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

namespace cmdiqo::installer {
namespace {

namespace theme {
inline const wxColour bg_main(7, 13, 24);             // #070d18 deep dark blue
inline const wxColour bg_card(12, 24, 42);            // #0c182a glass card
inline const wxColour bg_input(5, 12, 24);            // #050c18 input box
inline const wxColour border_panel(25, 51, 84);       // #193354 subtle blue border
inline const wxColour border_input(28, 56, 92);       // #1c385c text box border
inline const wxColour border_input_focus(0, 229, 255);// #00e5ff bright cyan focus
inline const wxColour cyan_accent(0, 229, 255);       // #00e5ff vibrant cyan
inline const wxColour text_white(255, 255, 255);      // #ffffff
inline const wxColour text_muted(142, 168, 204);      // #8ea8cc
inline const wxColour text_dim(80, 107, 140);         // #506b8c
inline const wxColour term_bg(3, 7, 15);              // #03070f terminal background
inline const wxColour term_header(10, 20, 36);        // #0a1424 terminal top bar
inline const wxColour term_text(56, 239, 125);        // #38ef7d vibrant green
inline const wxColour term_error(255, 82, 82);        // #ff5252 vibrant red
inline const wxColour term_ok(0, 229, 255);           // #00e5ff cyan OK badge
}  // namespace theme

enum class ButtonVariant {
    Primary,   // Vibrant cyan gradient (#00e5ff -> #0099ff)
    Accent,    // Dark glass with vibrant cyan border
    Secondary, // Dark glass with white/blue text
    Danger     // Deep crimson glass with rose-red text
};

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
            auto path = gc->CreatePath();
            path.MoveToPoint(x0, cy);
            path.AddQuadCurveToPoint(cx, cy - h, x1, cy);
            path.AddQuadCurveToPoint(cx, cy + h, x0, cy);
            path.CloseSubpath();
            gc->SetBrush(*wxTRANSPARENT_BRUSH);
            gc->SetPen(wxPen(fg_color, 1.8));
            gc->StrokePath(path);

            const double pr = h * 0.60;
            gc->SetBrush(wxBrush(fg_color));
            gc->SetPen(*wxTRANSPARENT_PEN);
            gc->DrawEllipse(cx - pr, cy - pr, pr * 2.0, pr * 2.0);

            const double hr = pr * 0.35;
            gc->SetBrush(wxBrush(bg_color));
            gc->DrawEllipse(cx - pr * 0.35, cy - pr * 0.35, hr * 2.0, hr * 2.0);

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
            auto path = gc->CreatePath();
            path.MoveToPoint(x0, cy - h * 0.25);
            path.AddQuadCurveToPoint(cx, cy + h * 0.70, x1, cy - h * 0.25);
            gc->SetBrush(*wxTRANSPARENT_BRUSH);
            gc->SetPen(wxPen(fg_color, 2.0));
            gc->StrokePath(path);

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

class ModernButton final : public wxControl {
public:
    ModernButton(
        wxWindow* parent,
        wxWindowID id,
        const wxString& label,
        ButtonVariant variant = ButtonVariant::Secondary,
        const wxSize& size = wxDefaultSize)
        : wxControl(parent, id, wxDefaultPosition, size, wxBORDER_NONE | wxTAB_TRAVERSAL),
          label_(label),
          variant_(variant) {
        SetBackgroundStyle(wxBG_STYLE_PAINT);
        SetCursor(wxCursor(wxCURSOR_HAND));

        Bind(wxEVT_PAINT, &ModernButton::OnPaint, this);
        Bind(wxEVT_ENTER_WINDOW, [this](wxMouseEvent&) { is_hovered_ = true; Refresh(); });
        Bind(wxEVT_LEAVE_WINDOW, [this](wxMouseEvent&) { is_hovered_ = false; is_pressed_ = false; Refresh(); });
        Bind(wxEVT_LEFT_DOWN, [this](wxMouseEvent&) { is_pressed_ = true; Refresh(); });
        Bind(wxEVT_LEFT_UP, [this](wxMouseEvent&) {
            if (is_pressed_ && is_hovered_) {
                is_pressed_ = false;
                Refresh();
                wxCommandEvent event(wxEVT_BUTTON, GetId());
                event.SetEventObject(this);
                ProcessWindowEvent(event);
            }
        });
    }

    wxSize DoGetBestSize() const override {
        wxClientDC dc(const_cast<ModernButton*>(this));
        wxFont font = GetFont();
        if (!font.IsOk()) {
            font = wxSystemSettings::GetFont(wxSYS_DEFAULT_GUI_FONT);
        }
        font.SetWeight(wxFONTWEIGHT_BOLD);
        dc.SetFont(font);
        const wxSize text_size = dc.GetTextExtent(label_);
        const int pad_x = FromDIP(24);
        const int pad_y = FromDIP(10);
        return wxSize(text_size.GetWidth() + pad_x, std::max(FromDIP(32), text_size.GetHeight() + pad_y));
    }

    void SetVariant(ButtonVariant variant) {
        variant_ = variant;
        Refresh();
    }

private:
    void OnPaint(wxPaintEvent&) {
        wxAutoBufferedPaintDC dc(this);
        dc.SetBackground(wxBrush(GetParent()->GetBackgroundColour()));
        dc.Clear();

        std::unique_ptr<wxGraphicsContext> gc(wxGraphicsContext::Create(dc));
        if (!gc) return;

        const wxRect rect = GetClientRect();
        const double radius = FromDIP(6);
        const bool enabled = IsEnabled();

        wxColour bg_start, bg_end, border_col, text_col;

        switch (variant_) {
            case ButtonVariant::Primary:
                if (!enabled) {
                    bg_start = wxColour(20, 50, 75);
                    bg_end = wxColour(15, 35, 55);
                    border_col = wxColour(30, 65, 95);
                    text_col = wxColour(100, 130, 160);
                } else if (is_pressed_) {
                    bg_start = wxColour(0, 180, 220);
                    bg_end = wxColour(0, 120, 200);
                    border_col = wxColour(0, 220, 255);
                    text_col = wxColour(2, 16, 36);
                } else if (is_hovered_) {
                    bg_start = wxColour(56, 235, 255);
                    bg_end = wxColour(20, 170, 255);
                    border_col = wxColour(180, 245, 255);
                    text_col = wxColour(2, 16, 36);
                } else {
                    bg_start = wxColour(0, 229, 255);
                    bg_end = wxColour(0, 140, 255);
                    border_col = wxColour(0, 229, 255);
                    text_col = wxColour(2, 16, 36);
                }
                break;

            case ButtonVariant::Accent:
                if (!enabled) {
                    bg_start = bg_end = wxColour(10, 22, 38);
                    border_col = wxColour(25, 50, 80);
                    text_col = wxColour(80, 120, 160);
                } else if (is_pressed_) {
                    bg_start = bg_end = wxColour(16, 45, 75);
                    border_col = wxColour(0, 229, 255);
                    text_col = wxColour(0, 229, 255);
                } else if (is_hovered_) {
                    bg_start = bg_end = wxColour(18, 50, 85);
                    border_col = wxColour(56, 235, 255);
                    text_col = wxColour(56, 235, 255);
                } else {
                    bg_start = bg_end = wxColour(10, 28, 50);
                    border_col = wxColour(0, 200, 230);
                    text_col = wxColour(0, 229, 255);
                }
                break;

            case ButtonVariant::Danger:
                if (!enabled) {
                    bg_start = bg_end = wxColour(20, 12, 16);
                    border_col = wxColour(40, 20, 26);
                    text_col = wxColour(120, 60, 70);
                } else if (is_pressed_) {
                    bg_start = bg_end = wxColour(50, 15, 25);
                    border_col = wxColour(220, 60, 80);
                    text_col = wxColour(255, 120, 140);
                } else if (is_hovered_) {
                    bg_start = bg_end = wxColour(45, 14, 22);
                    border_col = wxColour(255, 80, 100);
                    text_col = wxColour(255, 140, 160);
                } else {
                    bg_start = bg_end = wxColour(30, 12, 18);
                    border_col = wxColour(120, 35, 50);
                    text_col = wxColour(255, 107, 129);
                }
                break;

            case ButtonVariant::Secondary:
            default:
                if (!enabled) {
                    bg_start = bg_end = wxColour(10, 20, 34);
                    border_col = wxColour(20, 40, 64);
                    text_col = wxColour(70, 100, 130);
                } else if (is_pressed_) {
                    bg_start = bg_end = wxColour(20, 45, 78);
                    border_col = wxColour(45, 95, 160);
                    text_col = wxColour(255, 255, 255);
                } else if (is_hovered_) {
                    bg_start = bg_end = wxColour(24, 55, 95);
                    border_col = wxColour(50, 110, 180);
                    text_col = wxColour(255, 255, 255);
                } else {
                    bg_start = bg_end = wxColour(15, 34, 61);
                    border_col = wxColour(30, 62, 107);
                    text_col = wxColour(204, 224, 255);
                }
                break;
        }

        auto path = gc->CreatePath();
        path.AddRoundedRectangle(rect.x + 0.5, rect.y + 0.5, rect.width - 1.0, rect.height - 1.0, radius);

        if (variant_ == ButtonVariant::Primary && enabled) {
            auto brush = gc->CreateLinearGradientBrush(
                rect.x, rect.y, rect.x, rect.y + rect.height, bg_start, bg_end);
            gc->SetBrush(brush);
        } else {
            gc->SetBrush(wxBrush(bg_start));
        }

        gc->SetPen(wxPen(border_col, 1.0));
        gc->DrawPath(path);

        wxFont font = GetFont();
        if (!font.IsOk()) {
            font = wxSystemSettings::GetFont(wxSYS_DEFAULT_GUI_FONT);
        }
        font.SetWeight(variant_ == ButtonVariant::Primary || variant_ == ButtonVariant::Accent ? wxFONTWEIGHT_BOLD : wxFONTWEIGHT_MEDIUM);
        gc->SetFont(font, text_col);

        double tw, th;
        gc->GetTextExtent(label_, &tw, &th);
        const double tx = rect.x + (rect.width - tw) / 2.0;
        const double ty = rect.y + (rect.height - th) / 2.0;
        gc->DrawText(label_, tx, ty);
    }

    wxString label_;
    ButtonVariant variant_;
    bool is_hovered_{false};
    bool is_pressed_{false};
};

class ModernTextInputCtrl final : public wxPanel {
public:
    ModernTextInputCtrl(
        wxWindow* parent,
        bool is_password = false)
        : wxPanel(parent, wxID_ANY, wxDefaultPosition, wxDefaultSize, wxBORDER_NONE | wxTAB_TRAVERSAL),
          is_password_(is_password) {
        SetBackgroundStyle(wxBG_STYLE_PAINT);

        auto* sizer = new wxBoxSizer(wxHORIZONTAL);
        long text_style = wxNO_BORDER;
        if (is_password_) {
            text_style |= wxTE_PASSWORD;
        }

        text_ = new wxTextCtrl(this, wxID_ANY, "", wxDefaultPosition, wxDefaultSize, text_style);
        text_->SetBackgroundColour(theme::bg_input);
        text_->SetForegroundColour(theme::text_white);

        wxFont font = text_->GetFont();
        font.SetPointSize(font.GetPointSize() + 1);
        text_->SetFont(font);

        sizer->Add(text_, 1, wxALIGN_CENTER_VERTICAL | wxLEFT | wxRIGHT | wxTOP | wxBOTTOM, FromDIP(6));

        if (is_password_) {
            eye_button_ = new wxStaticBitmap(this, wxID_ANY, create_eye_bitmap(false));
            eye_button_->SetCursor(wxCursor(wxCURSOR_HAND));
            eye_button_->SetToolTip("Show token");
            sizer->Add(eye_button_, 0, wxALIGN_CENTER_VERTICAL | wxRIGHT, FromDIP(8));

            eye_button_->Bind(wxEVT_LEFT_DOWN, [this](wxMouseEvent&) {
                toggle_visibility();
            });
        }

        SetSizer(sizer);

        Bind(wxEVT_PAINT, &ModernTextInputCtrl::OnPaint, this);
        text_->Bind(wxEVT_SET_FOCUS, [this](wxFocusEvent& event) { is_focused_ = true; Refresh(); event.Skip(); });
        text_->Bind(wxEVT_KILL_FOCUS, [this](wxFocusEvent& event) { is_focused_ = false; Refresh(); event.Skip(); });
        Bind(wxEVT_LEFT_DOWN, [this](wxMouseEvent&) { text_->SetFocus(); });

#ifdef _WIN32
        if (is_password_) {
            HWND hwnd = reinterpret_cast<HWND>(text_->GetHWND());
            if (hwnd != nullptr) {
                LRESULT ch = SendMessageW(hwnd, EM_GETPASSWORDCHAR, 0, 0);
                if (ch != 0) {
                    default_password_char_ = static_cast<wchar_t>(ch);
                }
            }
        }
#endif
    }

    void toggle_visibility() {
        show_token_ = !show_token_;
        if (eye_button_) {
            eye_button_->SetBitmap(create_eye_bitmap(show_token_));
            eye_button_->SetToolTip(show_token_ ? "Hide token" : "Show token");
        }
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

    wxString GetValue() const { return text_->GetValue(); }
    void SetValue(const wxString& value) { text_->SetValue(value); }
    wxTextCtrl* GetTextCtrl() { return text_; }

private:
    void OnPaint(wxPaintEvent&) {
        wxAutoBufferedPaintDC dc(this);
        dc.SetBackground(wxBrush(GetParent()->GetBackgroundColour()));
        dc.Clear();

        std::unique_ptr<wxGraphicsContext> gc(wxGraphicsContext::Create(dc));
        if (!gc) return;

        const wxRect rect = GetClientRect();
        const double radius = FromDIP(5);

        auto path = gc->CreatePath();
        path.AddRoundedRectangle(rect.x + 0.5, rect.y + 0.5, rect.width - 1.0, rect.height - 1.0, radius);

        gc->SetBrush(wxBrush(theme::bg_input));
        gc->SetPen(wxPen(is_focused_ ? theme::border_input_focus : theme::border_input, is_focused_ ? 1.5 : 1.0));
        gc->DrawPath(path);
    }

    wxBitmap create_eye_bitmap(bool show_plain) const {
        const int width = FromDIP(24);
        const int height = FromDIP(18);
        return draw_eye_icon(show_plain, width, height, theme::bg_input, theme::cyan_accent);
    }

    wxTextCtrl* text_{nullptr};
    wxStaticBitmap* eye_button_{nullptr};
    bool is_password_{false};
    bool show_token_{false};
    bool is_focused_{false};
#ifdef _WIN32
    wchar_t default_password_char_{L'\u25CF'};
#endif
};

class HeaderBannerPanel final : public wxPanel {
public:
    HeaderBannerPanel(wxWindow* parent, const wxString& version_text)
        : wxPanel(parent, wxID_ANY, wxDefaultPosition, wxDefaultSize, wxBORDER_NONE),
          version_text_(version_text) {
        SetBackgroundStyle(wxBG_STYLE_PAINT);
        SetMinSize(wxSize(-1, FromDIP(66)));
        Bind(wxEVT_PAINT, &HeaderBannerPanel::OnPaint, this);
    }

    void SetVersionText(const wxString& text) {
        version_text_ = text;
        Refresh();
    }

private:
    void OnPaint(wxPaintEvent&) {
        wxAutoBufferedPaintDC dc(this);
        std::unique_ptr<wxGraphicsContext> gc(wxGraphicsContext::Create(dc));
        if (!gc) return;

        const wxRect rect = GetClientRect();

        auto bg_brush = gc->CreateLinearGradientBrush(
            rect.x, rect.y, rect.x, rect.y + rect.height,
            wxColour(11, 23, 40), wxColour(7, 13, 24));
        gc->SetBrush(bg_brush);
        gc->SetPen(*wxTRANSPARENT_PEN);
        gc->DrawRectangle(rect.x, rect.y, rect.width, rect.height);

        gc->SetPen(wxPen(theme::border_panel, 1.0));
        gc->StrokeLine(rect.x, rect.y + rect.height - 1, rect.x + rect.width, rect.y + rect.height - 1);

        const double icon_x = rect.x + FromDIP(14);
        const double icon_y = rect.y + FromDIP(15);
        const double icon_size = FromDIP(36);
        const double hcx = icon_x + icon_size / 2.0;
        const double hcy = icon_y + icon_size / 2.0;
        const double r = icon_size / 2.0;

        auto hex_path = gc->CreatePath();
        for (int i = 0; i < 6; ++i) {
            const double angle = i * (M_PI / 3.0) - (M_PI / 6.0);
            const double px = hcx + r * std::cos(angle);
            const double py = hcy + r * std::sin(angle);
            if (i == 0) hex_path.MoveToPoint(px, py);
            else hex_path.AddLineToPoint(px, py);
        }
        hex_path.CloseSubpath();

        auto hex_brush = gc->CreateLinearGradientBrush(
            icon_x, icon_y, icon_x + icon_size, icon_y + icon_size,
            theme::cyan_accent, wxColour(0, 119, 182));
        gc->SetBrush(hex_brush);
        gc->SetPen(wxPen(theme::cyan_accent, 1.2));
        gc->DrawPath(hex_path);

        gc->SetBrush(*wxWHITE_BRUSH);
        gc->SetPen(*wxTRANSPARENT_PEN);
        gc->DrawCircle(hcx, hcy, r * 0.35);

        const double text_x = icon_x + icon_size + FromDIP(12);
        
        wxFont title_font = wxSystemSettings::GetFont(wxSYS_DEFAULT_GUI_FONT);
        title_font.SetPointSize(title_font.GetPointSize() + 6);
        title_font.SetWeight(wxFONTWEIGHT_BOLD);
        gc->SetFont(title_font, theme::text_white);
        gc->DrawText("Cmdiqo", text_x, icon_y - FromDIP(1));

        double cmdiqo_w, cmdiqo_h;
        gc->GetTextExtent("Cmdiqo", &cmdiqo_w, &cmdiqo_h);

        wxFont dot_dev_font = title_font;
        gc->SetFont(dot_dev_font, theme::cyan_accent);
        gc->DrawText(".dev", text_x + cmdiqo_w, icon_y - FromDIP(1));

        wxFont sub_font = wxSystemSettings::GetFont(wxSYS_DEFAULT_GUI_FONT);
        sub_font.SetPointSize(std::max(7, sub_font.GetPointSize() - 2));
        sub_font.SetWeight(wxFONTWEIGHT_BOLD);
        gc->SetFont(sub_font, theme::text_muted);
        gc->DrawText("LAN REMOTE COMMAND AGENT \xe2\x80\xa2 NATIVE SETUP", text_x, icon_y + FromDIP(20));

        if (!version_text_.empty()) {
            wxFont pill_font = wxSystemSettings::GetFont(wxSYS_DEFAULT_GUI_FONT);
            pill_font.SetPointSize(std::max(8, pill_font.GetPointSize() - 1));
            pill_font.SetWeight(wxFONTWEIGHT_MEDIUM);
            gc->SetFont(pill_font, theme::text_white);

            double vtw, vth;
            gc->GetTextExtent(version_text_, &vtw, &vth);

            const double pill_pad_x = FromDIP(12);
            const double pill_h = FromDIP(26);
            const double dot_w = FromDIP(14);
            const double pill_w = vtw + pill_pad_x * 2 + dot_w;
            const double pill_x = rect.width - pill_w - FromDIP(16);
            const double pill_y = rect.y + (rect.height - pill_h) / 2.0;

            auto pill_path = gc->CreatePath();
            pill_path.AddRoundedRectangle(pill_x, pill_y, pill_w, pill_h, pill_h / 2.0);

            gc->SetBrush(wxBrush(wxColour(10, 24, 45, 200)));
            gc->SetPen(wxPen(wxColour(0, 229, 255, 120), 1.0));
            gc->DrawPath(pill_path);

            const double dot_cx = pill_x + pill_pad_x + FromDIP(4);
            const double dot_cy = pill_y + pill_h / 2.0;
            gc->SetBrush(wxBrush(theme::cyan_accent));
            gc->SetPen(*wxTRANSPARENT_PEN);
            gc->DrawCircle(dot_cx, dot_cy, FromDIP(3.5));

            gc->SetFont(pill_font, wxColour(200, 225, 255));
            gc->DrawText(version_text_, dot_cx + FromDIP(8), pill_y + (pill_h - vth) / 2.0);
        }
    }

    wxString version_text_;
};

class GlassContainerPanel final : public wxPanel {
public:
    GlassContainerPanel(wxWindow* parent)
        : wxPanel(parent, wxID_ANY, wxDefaultPosition, wxDefaultSize, wxBORDER_NONE | wxTAB_TRAVERSAL) {
        SetBackgroundStyle(wxBG_STYLE_PAINT);
        SetBackgroundColour(theme::bg_card);
        Bind(wxEVT_PAINT, &GlassContainerPanel::OnPaint, this);
    }

private:
    void OnPaint(wxPaintEvent&) {
        wxAutoBufferedPaintDC dc(this);
        std::unique_ptr<wxGraphicsContext> gc(wxGraphicsContext::Create(dc));
        if (!gc) return;

        const wxRect rect = GetClientRect();
        const double radius = FromDIP(8);

        auto path = gc->CreatePath();
        path.AddRoundedRectangle(rect.x + 0.5, rect.y + 0.5, rect.width - 1.0, rect.height - 1.0, radius);

        auto brush = gc->CreateLinearGradientBrush(
            rect.x, rect.y, rect.x, rect.y + rect.height,
            wxColour(12, 24, 42), wxColour(9, 18, 33));
        gc->SetBrush(brush);
        gc->SetPen(wxPen(theme::border_panel, 1.0));
        gc->DrawPath(path);
    }
};

class ModernTerminalPanel final : public wxPanel {
public:
    ModernTerminalPanel(wxWindow* parent)
        : wxPanel(parent, wxID_ANY, wxDefaultPosition, wxDefaultSize, wxBORDER_NONE) {
        SetBackgroundStyle(wxBG_STYLE_PAINT);

        auto* sizer = new wxBoxSizer(wxVERTICAL);
        sizer->AddSpacer(FromDIP(26));

        log_ = new wxTextCtrl(
            this, wxID_ANY, "", wxDefaultPosition, wxDefaultSize,
            wxTE_MULTILINE | wxTE_READONLY | wxTE_RICH2 | wxNO_BORDER);
        log_->SetBackgroundColour(theme::term_bg);
        log_->SetForegroundColour(theme::term_text);

        wxFont font(FromDIP(9), wxFONTFAMILY_TELETYPE, wxFONTSTYLE_NORMAL, wxFONTWEIGHT_NORMAL, false, "Consolas");
        if (!font.IsOk()) {
            font = wxFont(FromDIP(9), wxFONTFAMILY_MODERN, wxFONTSTYLE_NORMAL, wxFONTWEIGHT_NORMAL);
        }
        log_->SetFont(font);

        sizer->Add(log_, 1, wxEXPAND | wxLEFT | wxRIGHT | wxBOTTOM, FromDIP(6));
        SetSizer(sizer);

        Bind(wxEVT_PAINT, &ModernTerminalPanel::OnPaint, this);
    }

    wxTextCtrl* GetLogCtrl() { return log_; }

private:
    void OnPaint(wxPaintEvent&) {
        wxAutoBufferedPaintDC dc(this);
        std::unique_ptr<wxGraphicsContext> gc(wxGraphicsContext::Create(dc));
        if (!gc) return;

        const wxRect rect = GetClientRect();
        const double radius = FromDIP(6);

        auto card_path = gc->CreatePath();
        card_path.AddRoundedRectangle(rect.x + 0.5, rect.y + 0.5, rect.width - 1.0, rect.height - 1.0, radius);
        gc->SetBrush(wxBrush(theme::term_bg));
        gc->SetPen(wxPen(theme::border_panel, 1.0));
        gc->DrawPath(card_path);

        const double bar_h = FromDIP(24);
        auto header_path = gc->CreatePath();
        header_path.MoveToPoint(rect.x + radius, rect.y + 0.5);
        header_path.AddLineToPoint(rect.x + rect.width - radius, rect.y + 0.5);
        header_path.AddArc(rect.x + rect.width - radius, rect.y + radius, radius, -M_PI / 2.0, 0, true);
        header_path.AddLineToPoint(rect.x + rect.width, rect.y + bar_h);
        header_path.AddLineToPoint(rect.x, rect.y + bar_h);
        header_path.AddLineToPoint(rect.x, rect.y + radius);
        header_path.AddArc(rect.x + radius, rect.y + radius, radius, M_PI, -M_PI / 2.0, true);
        header_path.CloseSubpath();

        gc->SetBrush(wxBrush(theme::term_header));
        gc->SetPen(wxPen(theme::border_panel, 1.0));
        gc->DrawPath(header_path);

        const double dot_r = FromDIP(3.5);
        const double dot_y = rect.y + bar_h / 2.0;
        const double dot_start_x = rect.x + FromDIP(12);
        const double dot_spacing = FromDIP(11);

        gc->SetPen(*wxTRANSPARENT_PEN);
        gc->SetBrush(wxBrush(wxColour(255, 95, 86)));
        gc->DrawCircle(dot_start_x, dot_y, dot_r);

        gc->SetBrush(wxBrush(wxColour(255, 189, 46)));
        gc->DrawCircle(dot_start_x + dot_spacing, dot_y, dot_r);

        gc->SetBrush(wxBrush(wxColour(39, 201, 63)));
        gc->DrawCircle(dot_start_x + dot_spacing * 2, dot_y, dot_r);

        wxFont hfont = wxSystemSettings::GetFont(wxSYS_DEFAULT_GUI_FONT);
        hfont.SetPointSize(std::max(8, hfont.GetPointSize() - 2));
        gc->SetFont(hfont, theme::text_dim);
        double tw, th;
        gc->GetTextExtent("bash \xe2\x80\x94 daemon activity & installer log", &tw, &th);
        gc->DrawText("bash \xe2\x80\x94 daemon activity & installer log", rect.x + (rect.width - tw) / 2.0, rect.y + (bar_h - th) / 2.0);
    }

    wxTextCtrl* log_{nullptr};
};

class ModernStatusBarPanel final : public wxPanel {
public:
    ModernStatusBarPanel(wxWindow* parent)
        : wxPanel(parent, wxID_ANY, wxDefaultPosition, wxDefaultSize, wxBORDER_NONE) {
        SetBackgroundStyle(wxBG_STYLE_PAINT);
        SetMinSize(wxSize(-1, FromDIP(32)));
        Bind(wxEVT_PAINT, &ModernStatusBarPanel::OnPaint, this);
    }

    void SetStatus(const wxString& text, const wxColour& dot_color, bool visible = true) {
        text_ = text;
        dot_color_ = dot_color;
        dot_visible_ = visible;
        Refresh();
    }

private:
    void OnPaint(wxPaintEvent&) {
        wxAutoBufferedPaintDC dc(this);
        std::unique_ptr<wxGraphicsContext> gc(wxGraphicsContext::Create(dc));
        if (!gc) return;

        const wxRect rect = GetClientRect();

        gc->SetBrush(wxBrush(theme::bg_main));
        gc->SetPen(wxPen(theme::border_panel, 1.0));
        gc->DrawRectangle(rect.x, rect.y, rect.width, rect.height);
        gc->StrokeLine(rect.x, rect.y, rect.x + rect.width, rect.y);

        if (dot_visible_) {
            const double dot_x = rect.x + FromDIP(14);
            const double dot_y = rect.y + rect.height / 2.0;
            const double dot_r = FromDIP(4.5);

            gc->SetBrush(wxBrush(wxColour(dot_color_.Red(), dot_color_.Green(), dot_color_.Blue(), 60)));
            gc->SetPen(*wxTRANSPARENT_PEN);
            gc->DrawCircle(dot_x, dot_y, dot_r + FromDIP(2.5));

            gc->SetBrush(wxBrush(dot_color_));
            gc->DrawCircle(dot_x, dot_y, dot_r);

            wxFont font = wxSystemSettings::GetFont(wxSYS_DEFAULT_GUI_FONT);
            font.SetPointSize(std::max(8, font.GetPointSize() - 1));
            font.SetWeight(wxFONTWEIGHT_MEDIUM);
            gc->SetFont(font, theme::text_white);

            double tw, th;
            gc->GetTextExtent(text_, &tw, &th);
            gc->DrawText(text_, dot_x + dot_r + FromDIP(8), rect.y + (rect.height - th) / 2.0);
        }
    }

    wxString text_{"Service status initializing..."};
    wxColour dot_color_{theme::text_dim};
    bool dot_visible_{true};
};

class InstallerFrame final : public wxFrame {
public:
    InstallerFrame(
        std::optional<std::filesystem::path> payload,
        std::optional<std::filesystem::path> initial_config)
        : wxFrame(nullptr, wxID_ANY, "Cmdiqo Agent Setup", wxDefaultPosition, wxSize(760, 720)),
          service_(),
          payload_(std::move(payload)),
          initial_config_(std::move(initial_config)),
          service_status_timer_(this) {
        SetBackgroundColour(theme::bg_main);
        SetMinSize(wxSize(680, 620));

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

        auto* main_panel = new wxPanel(this, wxID_ANY);
        main_panel->SetBackgroundColour(theme::bg_main);
        auto* layout = new wxBoxSizer(wxVERTICAL);

        header_banner_ = new HeaderBannerPanel(main_panel, "Loading version...");
        layout->Add(header_banner_, 0, wxEXPAND);

        state_summary_ = new wxStaticText(main_panel, wxID_ANY, "");
        state_summary_->SetForegroundColour(theme::text_muted);
        layout->Add(state_summary_, 0, wxLEFT | wxRIGHT | wxTOP | wxEXPAND, FromDIP(14));

        auto* glass_card = new GlassContainerPanel(main_panel);
        auto* card_sizer = new wxBoxSizer(wxVERTICAL);

        auto* form = new wxFlexGridSizer(2, FromDIP(8), FromDIP(10));
        form->AddGrowableCol(1, 1);

        add_field(glass_card, form, "Hub WebSocket URL", hub_url_ = new ModernTextInputCtrl(glass_card, false));
        add_field(glass_card, form, "Shared Agent Token", token_ = new ModernTextInputCtrl(glass_card, true));
        add_field(glass_card, form, "Agent Hostname", agent_name_ = new ModernTextInputCtrl(glass_card, false));

        auto* notify_label = new wxStaticText(glass_card, wxID_ANY, "Slack Connectivity");
        notify_label->SetForegroundColour(theme::text_muted);
        form->Add(notify_label, 0, wxALIGN_CENTER_VERTICAL);

        notify_ = new wxCheckBox(glass_card, wxID_ANY, "Notify Slack channel on agent connect / disconnect");
        notify_->SetForegroundColour(theme::text_white);
        form->Add(notify_, 0, wxALIGN_CENTER_VERTICAL);

        card_sizer->Add(form, 1, wxALL | wxEXPAND, FromDIP(12));
        glass_card->SetSizer(card_sizer);
        layout->Add(glass_card, 0, wxLEFT | wxRIGHT | wxTOP | wxEXPAND, FromDIP(14));

        auto* installation_buttons = new wxBoxSizer(wxHORIZONTAL);
        add_button(main_panel, installation_buttons, "Install / Upgrade / Repair", ButtonVariant::Primary, [this] { install(); });
        add_button(main_panel, installation_buttons, "Update config", ButtonVariant::Accent, [this] { update_config(); });
        add_button(main_panel, installation_buttons, "Test Hub TCP", ButtonVariant::Secondary, [this] { test_hub(); });
        add_button(main_panel, installation_buttons, "Refresh State", ButtonVariant::Secondary, [this] { refresh(true); });
        layout->Add(installation_buttons, 0, wxLEFT | wxRIGHT | wxTOP, FromDIP(14));

        auto* service_buttons = new wxBoxSizer(wxHORIZONTAL);
        add_button(main_panel, service_buttons, "Start", ButtonVariant::Secondary, [this] { service_action("start"); });
        add_button(main_panel, service_buttons, "Restart", ButtonVariant::Secondary, [this] { service_action("restart"); });
        add_button(main_panel, service_buttons, "Stop", ButtonVariant::Secondary, [this] { service_action("stop"); });
        add_button(main_panel, service_buttons, "Uninstall (keep config)", ButtonVariant::Danger, [this] { uninstall(false); });
        add_button(main_panel, service_buttons, "Purge and uninstall", ButtonVariant::Danger, [this] { uninstall(true); });
        layout->Add(service_buttons, 0, wxLEFT | wxRIGHT | wxTOP, FromDIP(14));

        terminal_ = new ModernTerminalPanel(main_panel);
        log_ = terminal_->GetLogCtrl();
        layout->Add(terminal_, 1, wxLEFT | wxRIGHT | wxTOP | wxEXPAND, FromDIP(14));

        status_bar_ = new ModernStatusBarPanel(main_panel);
        layout->Add(status_bar_, 0, wxEXPAND | wxTOP, FromDIP(10));

        main_panel->SetSizer(layout);

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
        auto* label_ctrl = new wxStaticText(parent, wxID_ANY, label);
        label_ctrl->SetForegroundColour(theme::text_muted);
        wxFont font = label_ctrl->GetFont();
        font.SetWeight(wxFONTWEIGHT_MEDIUM);
        label_ctrl->SetFont(font);
        form->Add(label_ctrl, 0, wxALIGN_CENTER_VERTICAL);
        form->Add(control, 1, wxEXPAND);
    }

    ModernButton* add_button(
        wxWindow* parent,
        wxBoxSizer* container,
        const wxString& label,
        ButtonVariant variant,
        const std::function<void()>& handler) {
        auto* button = new ModernButton(parent, wxID_ANY, label, variant);
        button->Bind(wxEVT_BUTTON, [handler](wxCommandEvent&) { handler(); });
        buttons_.push_back(button);
        container->Add(button, 0, wxRIGHT, FromDIP(8));
        return button;
    }

    void append_log_segment(const wxString& text, const wxColour& color, bool bold) {
        wxTextAttr style(color, theme::term_bg);
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
            append_log_segment("[OK]", theme::cyan_accent, true);
            append_log_segment(wxString::FromUTF8(message.substr(ok_prefix.size())), theme::cyan_accent, false);
        } else if (message.starts_with(error_prefix)) {
            append_log_segment("[ERROR]", theme::term_error, true);
            append_log_segment(wxString::FromUTF8(message.substr(error_prefix.size())), theme::term_error, false);
        } else {
            append_log_segment(wxString::FromUTF8(message), theme::term_text, false);
        }
        append_log_segment("\n", theme::term_text, false);
        append_gui_log(message);
    }

    void layout_state_summary() {
        if (state_summary_ == nullptr) return;
        wxWindow* parent = state_summary_->GetParent();
        const int available_width = parent->GetClientSize().GetWidth() - FromDIP(28);
        state_summary_->Wrap(std::max(FromDIP(250), available_width));
        parent->Layout();
    }

    void set_service_status_text(const std::string& text, const wxColour& color) {
        status_bar_->SetStatus(wxString::FromUTF8(text), color, true);
    }

    void show_starting_service_status() {
        service_starting_ = true;
        starting_icon_visible_ = true;
        service_status_timer_.Start(500);
        set_service_status_text("service is being started ...", wxColour(255, 189, 46));
    }

    void update_service_status(ServiceStatus status, const std::optional<std::string>& version) {
        service_starting_ = false;
        service_status_timer_.Stop();
        if (status == ServiceStatus::running) {
            set_service_status_text("service running version " + version.value_or("unknown"), wxColour(39, 201, 63));
        } else if (status == ServiceStatus::stopped) {
            set_service_status_text("service stopped version " + version.value_or("unknown"), wxColour(56, 189, 248));
        } else {
            set_service_status_text("service not installed ...", theme::text_dim);
        }
    }

    void on_service_status_timer(wxTimerEvent&) {
        if (!service_starting_) {
            return;
        }
        starting_icon_visible_ = !starting_icon_visible_;
        if (starting_icon_visible_) {
            set_service_status_text("service is being started ...", wxColour(255, 189, 46));
        } else {
            status_bar_->SetStatus("service is being started ...", wxColour(255, 189, 46), false);
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

        state_summary_->SetLabel(wxString::FromUTF8(summary));
        layout_state_summary();

        const std::string ver_badge = !installed.value_or("").empty()
            ? ("Installed v" + *installed + " \xe2\x80\xa2 Bundled v" + bundled)
            : ("Bundled v" + bundled + " \xe2\x80\xa2 Zero Inbound Ports");
        header_banner_->SetVersionText(wxString::FromUTF8(ver_badge));

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
    HeaderBannerPanel* header_banner_{};
    wxStaticText* state_summary_{};
    ModernTextInputCtrl* hub_url_{};
    ModernTextInputCtrl* token_{};
    ModernTextInputCtrl* agent_name_{};
    wxCheckBox* notify_{};
    ModernTerminalPanel* terminal_{};
    wxTextCtrl* log_{};
    ModernStatusBarPanel* status_bar_{};
    wxTimer service_status_timer_;
    std::vector<ModernButton*> buttons_;
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
    if (current_platform() == Platform::linux && !PlatformOperations::has_graphical_session()) {
        std::cerr << "No graphical session detected; run without --gui for CLI mode, or start an X/Wayland session (or use X forwarding / Xvfb) to use the GUI." << std::endl;
        return 1;
    }

    auto* app = new InstallerApp(payload, initial_config);
    wxApp::SetInstance(app);
    if (!wxEntryStart(argc, argv)) {
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
