"""Native macOS status-bar item and manually positioned quick panel.

This module is only imported on Darwin. It replaces the HTML popup with a
borderless AppKit panel and uses a template menu-bar icon.
"""

from __future__ import annotations

import json
import threading
import urllib.request
from typing import Any, Callable

import AppKit
import Foundation
import objc

from backend.config import FRONTEND_DIR, MANAGER_HOST, MANAGER_PORT


def set_dock_icon_visible(visible: bool) -> None:
    """Show or hide the Dock icon by changing the app activation policy."""
    app = AppKit.NSApplication.sharedApplication()
    if visible:
        app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)
        app.activateIgnoringOtherApps_(True)
    else:
        app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)


class _DockHider(Foundation.NSObject):
    @objc.namedSelector(b"hideDock:")
    def hideDock_(self, sender):
        set_dock_icon_visible(False)


def hide_dock_after_start() -> None:
    """Hide the Dock icon from the main thread after pywebview starts."""
    hider = _DockHider.alloc().init()
    hider.performSelectorOnMainThread_withObject_waitUntilDone_("hideDock:", None, True)


_STATUS_ICON_SVG = """<svg
  width="32"
  height="32"
  viewBox="0 0 32 32"
  fill="none"
  xmlns="http://www.w3.org/2000/svg"
>
  <path
    d="M5.5 15.2L13.9 7.8
       C15.1 6.75 16.9 6.75 18.1 7.8
       L26.5 15.2"
    stroke="#F5F5F5"
    stroke-width="2.9"
    stroke-linecap="round"
    stroke-linejoin="round"
  />
  <path
    d="M8.7 14.6V23.2
       C8.7 25.15 10.25 26.7 12.2 26.7
       H19.8
       C21.75 26.7 23.3 25.15 23.3 23.2
       V14.6"
    stroke="#F5F5F5"
    stroke-width="2.9"
    stroke-linecap="round"
    stroke-linejoin="round"
  />
  <path
    d="M13.5 26.4V21.5
       C13.5 20.1 14.6 19 16 19
       C17.4 19 18.5 20.1 18.5 21.5
       V26.4"
    stroke="#F5F5F5"
    stroke-width="2.9"
    stroke-linecap="round"
    stroke-linejoin="round"
  />
</svg>"""


def _template_status_image() -> AppKit.NSImage:
    """Return the status-bar icon from the provided SVG (as a template image)."""
    try:
        svg_bytes = (FRONTEND_DIR / "assets" / "macos-status-icon.svg").read_bytes()
    except OSError:
        svg_bytes = _STATUS_ICON_SVG.encode("utf-8")
    data = Foundation.NSData.dataWithBytes_length_(svg_bytes, len(svg_bytes))
    image = AppKit.NSImage.alloc().initWithData_(data)
    image.setTemplate_(True)
    return image


class ButtonTarget(Foundation.NSObject):
    """Simple NSObject target that forwards button clicks to a Python callable."""

    def initWithHandler_(self, handler: Callable[[], None]):
        self = objc.super(ButtonTarget, self).init()
        self.handler = handler
        return self

    @objc.namedSelector(b"click:")
    def click_(self, sender):
        self.handler()


class StatusItemDelegate(Foundation.NSObject):
    """Target for the menu-bar status item button."""

    def initWithCallback_(self, callback: Callable[[Any], None]):
        self = objc.super(StatusItemDelegate, self).init()
        self.callback = callback
        return self

    @objc.namedSelector(b"statusClicked:")
    def statusClicked_(self, sender):
        self.callback(sender)


class _PanelRootView(AppKit.NSView):
    """Keep the quick panel content at its explicit fitting size."""

    def initWithFrame_(self, frame):
        self = objc.super(_PanelRootView, self).initWithFrame_(frame)
        self._preferred_panel_size = frame.size
        return self

    def intrinsicContentSize(self):
        return self._preferred_panel_size

    def setPreferredPanelSize_(self, size):
        self._preferred_panel_size = size
        self.invalidateIntrinsicContentSize()


class _QuickPanelChromeView(AppKit.NSView):
    """Transparent window chrome that draws the status-item pointer."""

    ARROW_HEIGHT = 10
    ARROW_WIDTH = 18

    def initWithFrame_color_(self, frame, color):
        self = objc.super(_QuickPanelChromeView, self).initWithFrame_(frame)
        self._panel_color = color
        self._arrow_center = Foundation.NSMidX(frame)
        return self

    def isOpaque(self):
        return False

    def setArrowCenter_(self, value: float) -> None:
        self._arrow_center = value
        self.setNeedsDisplay_(True)

    def drawRect_(self, dirty_rect):
        objc.super(_QuickPanelChromeView, self).drawRect_(dirty_rect)
        height = self.bounds().size.height
        base_y = height - self.ARROW_HEIGHT
        half_width = self.ARROW_WIDTH / 2
        arrow = AppKit.NSBezierPath.bezierPath()
        arrow.moveToPoint_(Foundation.NSPoint(self._arrow_center, height))
        arrow.lineToPoint_(Foundation.NSPoint(self._arrow_center - half_width, base_y))
        arrow.lineToPoint_(Foundation.NSPoint(self._arrow_center + half_width, base_y))
        arrow.closePath()
        self._panel_color.setFill()
        arrow.fill()


class NativeQuickPanel:
    """A compact, card-based AppKit panel listing managed services."""

    WIDTH = 326
    HEIGHT = 238
    _EDGE_INSETS = AppKit.NSEdgeInsetsMake(12, 12, 12, 12)
    _STACK_SPACING = 8
    _HEADER_HEIGHT = 28
    _CARD_HEIGHT = 80
    _EMPTY_HEIGHT = 82
    _FOOTER_HEIGHT = 42
    _CARD_RADIUS = 12

    def __init__(self, app):
        self.app = app
        self.on_resize: Callable[[], None] | None = None
        self.content_height = self.HEIGHT
        self._targets: list[ButtonTarget] = []
        self._icon_refreshing = False
        self._icon_refresh_attempted: set[str] = set()
        self._image_cache: dict[str, AppKit.NSImage] = {}
        self._colors = {
            "text": self._color("#F4F5FB"),
            "secondary": self._color("#A4ABD0"),
            "muted": self._color("#7E86AF"),
            "panel": self._color("#202331"),
            "card": self._color("#2A2F57"),
            "card_alt": self._color("#252A4B"),
            "card_border": self._color("#3D4677", 0.72),
            "accent": self._color("#4B9BFF"),
            "success": self._color("#38D69A"),
            "danger": self._color("#FF7D88"),
        }

        self.root_view = _PanelRootView.alloc().initWithFrame_(
            Foundation.NSRect((0, 0), (self.WIDTH, self.HEIGHT))
        )
        self.root_view.setWantsLayer_(True)
        root_layer = self.root_view.layer()
        root_layer.setBackgroundColor_(self._colors["panel"].CGColor())
        root_layer.setCornerRadius_(16)
        root_layer.setMasksToBounds_(True)

        self.stack = self._new_stack(self.HEIGHT)
        self.root_view.addSubview_(self.stack)

    def _new_stack(self, height: float):
        stack = AppKit.NSStackView.alloc().initWithFrame_(
            Foundation.NSRect((0, 0), (self.WIDTH, height))
        )
        stack.setOrientation_(AppKit.NSUserInterfaceLayoutOrientationVertical)
        stack.setAlignment_(AppKit.NSLayoutAttributeCenterX)
        stack.setDistribution_(AppKit.NSStackViewDistributionFill)
        stack.setSpacing_(self._STACK_SPACING)
        stack.setEdgeInsets_(self._EDGE_INSETS)
        return stack

    # ---------- HTTP helpers ----------

    def _api_json(self, path: str) -> Any:
        url = f"http://{MANAGER_HOST}:{MANAGER_PORT}{path}"
        with urllib.request.urlopen(url, timeout=2) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _api_post(self, path: str) -> None:
        url = f"http://{MANAGER_HOST}:{MANAGER_PORT}{path}"
        req = urllib.request.Request(url, method="POST")
        with urllib.request.urlopen(req, timeout=3):
            pass

    # ---------- UI construction ----------

    @staticmethod
    def _color(value: str, alpha: float = 1.0):
        value = value.lstrip("#")
        red = int(value[0:2], 16) / 255
        green = int(value[2:4], 16) / 255
        blue = int(value[4:6], 16) / 255
        return AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(red, green, blue, alpha)

    def _label(
        self,
        text: str,
        *,
        bold: bool = False,
        size: float = 13,
        secondary: bool = False,
        alignment=AppKit.NSTextAlignmentLeft,
        color=None,
    ):
        label = AppKit.NSTextField.alloc().initWithFrame_(Foundation.NSRect((0, 0), (0, 0)))
        label.setStringValue_(text)
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        font = AppKit.NSFont.boldSystemFontOfSize_(size) if bold else AppKit.NSFont.systemFontOfSize_(size)
        label.setFont_(font)
        label.setAlignment_(alignment)
        label.setUsesSingleLineMode_(True)
        label.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
        if color is not None:
            label.setTextColor_(color)
        elif secondary:
            label.setTextColor_(self._colors["secondary"])
        return label

    def _spacer(self):
        return AppKit.NSView.alloc().init()

    def _set_fixed_height(self, view, height: float):
        view.setTranslatesAutoresizingMaskIntoConstraints_(False)
        view.addConstraint_(
            AppKit.NSLayoutConstraint.constraintWithItem_attribute_relatedBy_toItem_attribute_multiplier_constant_(
                view,
                AppKit.NSLayoutAttributeHeight,
                AppKit.NSLayoutRelationEqual,
                None,
                AppKit.NSLayoutAttributeNotAnAttribute,
                1,
                height,
            )
        )
        return view

    def _full_width_container(self, content, height: float):
        container = AppKit.NSBox.alloc().initWithFrame_(
            Foundation.NSRect((0, 0), (self.WIDTH - self._EDGE_INSETS.left - self._EDGE_INSETS.right, height))
        )
        container.setBoxType_(AppKit.NSBoxCustom)
        container.setBorderType_(AppKit.NSNoBorder)
        container.setFillColor_(AppKit.NSColor.clearColor())
        container.setTitlePosition_(AppKit.NSNoTitle)
        container.setContentView_(content)
        if hasattr(container, "setContentViewMargins_"):
            container.setContentViewMargins_(Foundation.NSSize(0, 0))
        return self._set_fixed_height(container, height)

    def _card(self, content, height: float, *, alternate: bool = False):
        box = AppKit.NSBox.alloc().initWithFrame_(
            Foundation.NSRect((0, 0), (self.WIDTH - self._EDGE_INSETS.left - self._EDGE_INSETS.right, height))
        )
        box.setBoxType_(AppKit.NSBoxCustom)
        box.setBorderType_(AppKit.NSLineBorder)
        box.setBorderWidth_(1)
        box.setCornerRadius_(self._CARD_RADIUS)
        box.setFillColor_(self._colors["card_alt"] if alternate else self._colors["card"])
        box.setBorderColor_(self._colors["card_border"])
        box.setTitlePosition_(AppKit.NSNoTitle)
        box.setContentView_(content)
        if hasattr(box, "setContentViewMargins_"):
            box.setContentViewMargins_(Foundation.NSSize(0, 0))
        return self._set_fixed_height(box, height)

    def _button(
        self,
        title: str,
        handler: Callable[[], None],
        width: float = 48,
        *,
        color=None,
        enabled: bool = True,
    ):
        btn = AppKit.NSButton.alloc().initWithFrame_(Foundation.NSRect((0, 0), (width, 26)))
        btn.setTitle_(title)
        btn.setBordered_(False)
        btn.setFont_(AppKit.NSFont.systemFontOfSize_(11))
        btn.setContentTintColor_(color or self._colors["secondary"])
        btn.setControlSize_(AppKit.NSControlSizeSmall)
        btn.setEnabled_(enabled)
        btn.setAlphaValue_(1.0 if enabled else 0.38)
        target = ButtonTarget.alloc().initWithHandler_(handler)
        self._targets.append(target)
        btn.setTarget_(target)
        btn.setAction_("click:")
        return btn

    def _fallback_icon(self):
        image = _template_status_image()
        image.setTemplate_(False)
        image.setSize_(Foundation.NSSize(28, 28))
        return image

    def _service_icon(self, svc: dict):
        icon_url = svc.get("icon_url")
        if not svc.get("webui_url") or not icon_url:
            return self._fallback_icon()
        cached = self._image_cache.get(icon_url)
        if cached is not None:
            return cached
        try:
            with urllib.request.urlopen(
                f"http://{MANAGER_HOST}:{MANAGER_PORT}{icon_url}", timeout=2
            ) as resp:
                data = resp.read()
            image_data = Foundation.NSData.dataWithBytes_length_(data, len(data))
            image = AppKit.NSImage.alloc().initWithData_(image_data)
            if image is None:
                return self._fallback_icon()
            image.setSize_(Foundation.NSSize(30, 30))
            self._image_cache[icon_url] = image
            return image
        except Exception:
            return self._fallback_icon()

    def _icon_view(self, svc: dict):
        view = AppKit.NSImageView.alloc().initWithFrame_(Foundation.NSRect((0, 0), (32, 32)))
        view.setImage_(self._service_icon(svc))
        view.setImageScaling_(AppKit.NSImageScaleProportionallyUpOrDown)
        view.setImageAlignment_(AppKit.NSImageAlignCenter)
        return view

    def _status_dot(self, running: bool):
        image = AppKit.NSImage.alloc().initWithSize_(Foundation.NSSize(8, 8))
        image.lockFocus()
        self._colors["success" if running else "muted"].setFill()
        AppKit.NSBezierPath.bezierPathWithOvalInRect_(Foundation.NSRect((0, 0), (8, 8))).fill()
        image.unlockFocus()
        dot = AppKit.NSImageView.alloc().initWithFrame_(Foundation.NSRect((0, 0), (8, 8)))
        dot.setImage_(image)
        dot.setImageScaling_(AppKit.NSImageScaleProportionallyUpOrDown)
        return dot

    def _header(self, services: list[dict]):
        running = sum(1 for svc in services if svc.get("running"))
        available_width = self.WIDTH - self._EDGE_INSETS.left - self._EDGE_INSETS.right
        header = AppKit.NSView.alloc().initWithFrame_(
            Foundation.NSRect((0, 0), (available_width, self._HEADER_HEIGHT))
        )
        title = self._label("本地服务管理", bold=True, size=16, color=self._colors["text"])
        title.setFrame_(Foundation.NSRect((0, 2), (180, 22)))
        header.addSubview_(title)
        summary = self._label(
            f"{running} / {len(services)} 运行中",
            size=11,
            color=self._colors["secondary"],
            alignment=AppKit.NSTextAlignmentRight,
        )
        summary.setFrame_(Foundation.NSRect((180, 3), (available_width - 180, 20)))
        header.addSubview_(summary)
        return self._full_width_container(header, self._HEADER_HEIGHT)

    def _make_card(self, svc: dict):
        name = svc.get("name") or "未命名"
        port = int(svc.get("port") or 0)
        running = bool(svc.get("running"))
        status_text = "运行中" if running else "已停止"
        status_color = self._colors["success"] if running else self._colors["muted"]

        content = AppKit.NSStackView.alloc().init()
        content.setOrientation_(AppKit.NSUserInterfaceLayoutOrientationVertical)
        content.setAlignment_(AppKit.NSLayoutAttributeWidth)
        content.setSpacing_(6)
        content.setEdgeInsets_(AppKit.NSEdgeInsetsMake(8, 10, 8, 10))

        title_row = AppKit.NSStackView.alloc().init()
        title_row.setOrientation_(AppKit.NSUserInterfaceLayoutOrientationHorizontal)
        title_row.setAlignment_(AppKit.NSLayoutAttributeCenterY)
        title_row.setSpacing_(8)
        title_row.addArrangedSubview_(self._icon_view(svc))

        text_stack = AppKit.NSStackView.alloc().init()
        text_stack.setOrientation_(AppKit.NSUserInterfaceLayoutOrientationVertical)
        text_stack.setSpacing_(2)
        text_stack.addArrangedSubview_(self._label(name, bold=True, size=13, color=self._colors["text"]))
        text_stack.addArrangedSubview_(
            self._label(f"端口 {port}", size=11, color=self._colors["secondary"])
        )
        title_row.addArrangedSubview_(text_stack)
        title_row.addArrangedSubview_(self._spacer())

        status_group = AppKit.NSStackView.alloc().init()
        status_group.setOrientation_(AppKit.NSUserInterfaceLayoutOrientationHorizontal)
        status_group.setAlignment_(AppKit.NSLayoutAttributeCenterY)
        status_group.setSpacing_(5)
        status_group.addArrangedSubview_(self._status_dot(running))
        status_group.addArrangedSubview_(self._label(status_text, size=11, color=status_color))
        title_row.addArrangedSubview_(status_group)
        content.addArrangedSubview_(title_row)

        action_row = AppKit.NSStackView.alloc().init()
        action_row.setOrientation_(AppKit.NSUserInterfaceLayoutOrientationHorizontal)
        action_row.setAlignment_(AppKit.NSLayoutAttributeCenterY)
        action_row.setSpacing_(4)
        action_row.addArrangedSubview_(
            self._label("WebUI" if svc.get("webui_url") else "本地服务", size=11, color=self._colors["muted"])
        )
        action_row.addArrangedSubview_(self._spacer())

        webui = svc.get("webui_url")
        if webui:
            action_row.addArrangedSubview_(
                self._button("打开", lambda s=svc: self._open_service(s), width=42, color=self._colors["accent"])
            )

        if running:
            action_row.addArrangedSubview_(
                self._button("停止", lambda s=svc: self._stop_service(s), width=42, color=self._colors["danger"])
            )
        else:
            action_row.addArrangedSubview_(
                self._button("启动", lambda s=svc: self._start_service(s), width=42, color=self._colors["success"])
            )

        content.addArrangedSubview_(action_row)
        return self._card(content, self._CARD_HEIGHT)

    def _empty_card(self):
        content = AppKit.NSStackView.alloc().init()
        content.setOrientation_(AppKit.NSUserInterfaceLayoutOrientationVertical)
        content.setAlignment_(AppKit.NSLayoutAttributeCenterX)
        content.setSpacing_(4)
        content.setEdgeInsets_(AppKit.NSEdgeInsetsMake(18, 10, 18, 10))
        content.addArrangedSubview_(self._label("暂无已登记服务", bold=True, size=13, color=self._colors["text"]))
        content.addArrangedSubview_(self._label("在主窗口添加一个 WebUI 服务", size=11, color=self._colors["muted"]))
        return self._card(content, self._EMPTY_HEIGHT)

    def _footer(self):
        content = AppKit.NSStackView.alloc().init()
        content.setOrientation_(AppKit.NSUserInterfaceLayoutOrientationHorizontal)
        content.setAlignment_(AppKit.NSLayoutAttributeCenterY)
        content.setSpacing_(4)
        content.setEdgeInsets_(AppKit.NSEdgeInsetsMake(6, 10, 6, 10))
        content.addArrangedSubview_(self._label("快捷操作", size=11, color=self._colors["muted"]))
        content.addArrangedSubview_(self._spacer())
        content.addArrangedSubview_(
            self._button("打开主窗口", self.app.show_main_window, width=76, color=self._colors["accent"])
        )
        content.addArrangedSubview_(
            self._button("退出", self.app.quit_app, width=38, color=self._colors["secondary"])
        )
        return self._card(content, self._FOOTER_HEIGHT, alternate=True)

    def _schedule_icon_refresh(self, services: list[dict]) -> None:
        missing = [
            svc for svc in services
            if (
                svc.get("webui_url")
                and not svc.get("has_icon")
                and str(svc.get("id") or "") not in self._icon_refresh_attempted
            )
        ]
        if not missing or self._icon_refreshing:
            return
        self._icon_refresh_attempted.update(str(svc.get("id") or "") for svc in missing)
        self._icon_refreshing = True

        def refresh_one(service: dict) -> None:
            try:
                self._api_post(f"/api/services/{service['id']}/refresh-icon")
            except Exception:
                pass

        def worker() -> None:
            threads = [threading.Thread(target=refresh_one, args=(svc,), daemon=True) for svc in missing]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self._icon_refreshing = False

        threading.Thread(target=worker, name="lsm-icon-refresh", daemon=True).start()

    def _build(self, services: list[dict]) -> None:
        old_stack = self.stack
        next_stack = self._new_stack(self.content_height)
        next_stack.setHidden_(True)
        self.stack = next_stack
        self._targets = []
        self.stack.addArrangedSubview_(self._header(services))
        if services:
            for svc in services:
                self.stack.addArrangedSubview_(self._make_card(svc))
        else:
            self.stack.addArrangedSubview_(self._empty_card())
        self.stack.addArrangedSubview_(self._footer())

        service_height = self._EMPTY_HEIGHT if not services else len(services) * self._CARD_HEIGHT
        section_count = 1 if not services else len(services)
        gaps = section_count + 1
        height = (
            self._EDGE_INSETS.top
            + self._HEADER_HEIGHT
            + service_height
            + self._FOOTER_HEIGHT
            + gaps * self._STACK_SPACING
            + self._EDGE_INSETS.bottom
        )
        self._resize(height)
        self.root_view.addSubview_(self.stack)
        self.stack.layoutSubtreeIfNeeded()
        self.stack.setHidden_(False)
        old_stack.removeFromSuperview()
        self._schedule_icon_refresh(services)

    def _resize(self, height: float) -> None:
        size_changed = height != self.content_height
        self.content_height = height
        size = Foundation.NSSize(self.WIDTH, height)
        self.root_view.setPreferredPanelSize_(size)
        self.root_view.setFrame_(Foundation.NSRect((0, 0), (self.WIDTH, height)))
        self.stack.setFrame_(Foundation.NSRect((0, 0), (self.WIDTH, height)))
        # Avoid re-running window geometry when only the service state changed.
        if size_changed and self.on_resize is not None:
            self.on_resize()

    def refresh(self) -> None:
        try:
            services = self._api_json("/api/services")
            if not isinstance(services, list):
                services = []
        except Exception:
            services = []
        self._build(services)

    # ---------- Actions ----------

    def _open_service(self, svc: dict) -> None:
        import webbrowser

        url = svc.get("webui_url") or f"http://127.0.0.1:{svc.get('port')}/"
        webbrowser.open(url)

    def _start_service(self, svc: dict) -> None:
        try:
            self._api_post(f"/api/services/{svc['id']}/start")
        except Exception:
            pass
        self.refresh()

    def _stop_service(self, svc: dict) -> None:
        try:
            self._api_post(f"/api/services/{svc['id']}/stop")
        except Exception:
            pass
        self.refresh()


class MacStatusBar:
    """Native status item with a manually positioned borderless panel."""

    _STATUS_ICON_SIZE = 24
    _SCREEN_MARGIN = 8
    _ARROW_MARGIN = 20

    def __init__(self, app):
        self.app = app
        self.status_item = None
        self.delegate = None
        self.quick_window = None
        self.chrome_view = None
        self._local_mouse_monitor = None
        self._global_mouse_monitor = None
        self._pending_show_timer = None
        self._pending_show_attempts = 0
        self._tracking_timer = None
        self.panel = NativeQuickPanel(app)
        self.panel.on_resize = self._resize_window
        self._setup_status_item()
        self._setup_window()
        self._install_dismiss_monitors()

    def _setup_status_item(self) -> None:
        bar = AppKit.NSStatusBar.systemStatusBar()
        self.status_item = bar.statusItemWithLength_(AppKit.NSSquareStatusItemLength)
        button = self.status_item.button()
        status_image = _template_status_image()
        status_image.setSize_(Foundation.NSSize(self._STATUS_ICON_SIZE, self._STATUS_ICON_SIZE))
        button.setImage_(status_image)
        button.setImagePosition_(AppKit.NSImageOnly)
        button.setImageScaling_(AppKit.NSImageScaleProportionallyDown)
        self.status_item.setToolTip_("本地服务管理")
        self.delegate = StatusItemDelegate.alloc().initWithCallback_(self._on_clicked)
        button.setTarget_(self.delegate)
        button.setAction_("statusClicked:")

    def _setup_window(self) -> None:
        total_height = self.panel.content_height + _QuickPanelChromeView.ARROW_HEIGHT
        content_rect = Foundation.NSRect((0, 0), (self.panel.WIDTH, total_height))
        style = AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel
        self.quick_window = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            content_rect, style, AppKit.NSBackingStoreBuffered, False
        )
        self.quick_window.setOpaque_(False)
        self.quick_window.setBackgroundColor_(AppKit.NSColor.clearColor())
        self.quick_window.setHasShadow_(True)
        self.quick_window.setLevel_(AppKit.NSPopUpMenuWindowLevel)
        self.quick_window.setReleasedWhenClosed_(False)
        self.quick_window.setHidesOnDeactivate_(False)
        self.quick_window.setMovable_(False)
        self.quick_window.setBecomesKeyOnlyIfNeeded_(True)
        self.quick_window.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorTransient
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        self.chrome_view = _QuickPanelChromeView.alloc().initWithFrame_color_(
            content_rect, self.panel._colors["panel"]
        )
        self.quick_window.setContentView_(self.chrome_view)
        self.panel.root_view.setFrame_(
            Foundation.NSRect((0, 0), (self.panel.WIDTH, self.panel.content_height))
        )
        self.chrome_view.addSubview_(self.panel.root_view)
        self.quick_window.orderOut_(None)

    def _resize_window(self) -> None:
        total_height = self.panel.content_height + _QuickPanelChromeView.ARROW_HEIGHT
        size = Foundation.NSSize(self.panel.WIDTH, total_height)
        self.panel.root_view.setFrame_(
            Foundation.NSRect((0, 0), (self.panel.WIDTH, self.panel.content_height))
        )
        self.chrome_view.setFrame_(Foundation.NSRect((0, 0), size))
        self.quick_window.setContentSize_(size)
        if self.quick_window.isVisible():
            self._position_window()

    def _button_screen_rect(self):
        button = self.status_item.button()
        window = button.window()
        if window is None:
            return None
        local_rect = button.convertRect_toView_(button.bounds(), None)
        return window.convertRectToScreen_(local_rect)

    @staticmethod
    def _screen_containing_rect(rect):
        center = Foundation.NSPoint(Foundation.NSMidX(rect), Foundation.NSMidY(rect))
        for screen in AppKit.NSScreen.screens():
            if Foundation.NSPointInRect(center, screen.frame()):
                return screen
        return None

    def _status_item_is_on_screen(self) -> bool:
        rect = self._button_screen_rect()
        return rect is not None and self._screen_containing_rect(rect) is not None

    @staticmethod
    def _align_to_screen_pixel(value: float, screen) -> float:
        scale = float(screen.backingScaleFactor() or 1)
        return round(value * scale) / scale

    def _target_window_geometry(self):
        button_rect = self._button_screen_rect()
        if button_rect is None:
            return None
        screen = self._screen_containing_rect(button_rect)
        if screen is None:
            return None

        visible = screen.visibleFrame()
        width = self.panel.WIDTH
        height = self.panel.content_height + _QuickPanelChromeView.ARROW_HEIGHT
        anchor_x = Foundation.NSMidX(button_rect)
        minimum_x = Foundation.NSMinX(visible) + self._SCREEN_MARGIN
        maximum_x = Foundation.NSMaxX(visible) - width - self._SCREEN_MARGIN
        x = max(minimum_x, min(anchor_x - width / 2, maximum_x))
        top = min(Foundation.NSMinY(button_rect), Foundation.NSMaxY(visible))
        y = max(Foundation.NSMinY(visible) + self._SCREEN_MARGIN, top - height)
        x = self._align_to_screen_pixel(x, screen)
        y = self._align_to_screen_pixel(y, screen)
        arrow_center = max(self._ARROW_MARGIN, min(anchor_x - x, width - self._ARROW_MARGIN))
        frame = Foundation.NSRect((x, y), (width, height))
        return frame, arrow_center, button_rect

    def _position_window(self) -> bool:
        geometry = self._target_window_geometry()
        if geometry is None:
            return False
        frame, arrow_center, _ = geometry
        self.chrome_view.setArrowCenter_(arrow_center)
        current = self.quick_window.frame()
        if not Foundation.NSEqualRects(current, frame):
            self.quick_window.setFrame_display_(frame, True)
        return True

    def _cancel_pending_show(self) -> None:
        timer = self._pending_show_timer
        self._pending_show_timer = None
        self._pending_show_attempts = 0
        if timer is not None:
            timer.invalidate()

    def _show_when_status_item_is_ready(self) -> None:
        if self._pending_show_timer is not None:
            return
        self._pending_show_attempts = 0

        def check_status_item(timer) -> None:
            self._pending_show_attempts += 1
            if self._status_item_is_on_screen():
                self._cancel_pending_show()
                self.show_panel()
            elif self._pending_show_attempts >= 20:
                self._cancel_pending_show()

        self._pending_show_timer = Foundation.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            0.05, True, check_status_item
        )

    def _start_tracking_status_item(self) -> None:
        self._stop_tracking_status_item()

        def follow_status_item(timer) -> None:
            if not self.quick_window.isVisible():
                self._stop_tracking_status_item()
                return
            self._position_window()

        self._tracking_timer = Foundation.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            0.1, True, follow_status_item
        )

    def _stop_tracking_status_item(self) -> None:
        timer = self._tracking_timer
        self._tracking_timer = None
        if timer is not None:
            timer.invalidate()

    @staticmethod
    def _event_window(event):
        try:
            return event.window()
        except Exception:
            return None

    @staticmethod
    def _point_inside_window(point, window) -> bool:
        if window is None:
            return False
        try:
            return Foundation.NSPointInRect(point, window.frame())
        except Exception:
            return False

    def _event_is_inside_panel_or_status_item(self, event) -> bool:
        event_window = self._event_window(event)
        if event_window is self.quick_window or self._point_inside_window(
            AppKit.NSEvent.mouseLocation(), self.quick_window
        ):
            return True
        try:
            status_window = self.status_item.button().window()
        except Exception:
            status_window = None
        return event_window is status_window or self._point_inside_window(
            AppKit.NSEvent.mouseLocation(), status_window
        )

    def _dismiss_for_mouse_event(self, event) -> None:
        if not self.quick_window.isVisible():
            return
        if not self._event_is_inside_panel_or_status_item(event):
            self.hide_panel()

    def _handle_local_mouse_event(self, event):
        self._dismiss_for_mouse_event(event)
        return event

    def _handle_global_mouse_event(self, event) -> None:
        self._dismiss_for_mouse_event(event)

    def _install_dismiss_monitors(self) -> None:
        mask = (
            AppKit.NSEventMaskLeftMouseDown
            | AppKit.NSEventMaskRightMouseDown
            | AppKit.NSEventMaskOtherMouseDown
        )
        self._local_mouse_monitor = AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            mask, self._handle_local_mouse_event
        )
        self._global_mouse_monitor = AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            mask, self._handle_global_mouse_event
        )

    def _set_popup_visible(self, visible: bool) -> None:
        try:
            self.app._popup_visible = visible
        except Exception:
            pass

    def _on_clicked(self, sender) -> None:
        if self._pending_show_timer is not None:
            self._cancel_pending_show()
        elif self.quick_window.isVisible():
            self.hide_panel()
        else:
            self.show_panel()

    def show_panel(self) -> None:
        if not self._status_item_is_on_screen():
            self._show_when_status_item_is_ready()
            return
        self.panel.refresh()
        self._resize_window()
        if not self._position_window():
            return
        self.quick_window.orderFrontRegardless()
        self._set_popup_visible(True)
        self._start_tracking_status_item()

    def hide_panel(self) -> None:
        self._cancel_pending_show()
        self._stop_tracking_status_item()
        if self.quick_window is not None and self.quick_window.isVisible():
            self.quick_window.orderOut_(None)
        self._set_popup_visible(False)

    def toggle_panel(self) -> None:
        if self.quick_window.isVisible():
            self.hide_panel()
        else:
            self.show_panel()

    def stop(self) -> None:
        self._cancel_pending_show()
        self._stop_tracking_status_item()
        try:
            self.hide_panel()
        except Exception:
            pass
        for monitor in (self._local_mouse_monitor, self._global_mouse_monitor):
            if monitor is not None:
                try:
                    AppKit.NSEvent.removeMonitor_(monitor)
                except Exception:
                    pass
        self._local_mouse_monitor = None
        self._global_mouse_monitor = None
        try:
            if self.status_item is not None:
                AppKit.NSStatusBar.systemStatusBar().removeStatusItem_(self.status_item)
        except Exception:
            pass
